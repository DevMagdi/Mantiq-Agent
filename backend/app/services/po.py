"""Purchase order documents (PDF/Excel) and ERP hand-off (FR-CMP-5)."""
from __future__ import annotations

import io
import logging
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import registry
from app.core.audit import audit
from app.db.models.entities import (
    RFQ,
    Offer,
    OfferLine,
    PurchaseOrder,
    Supplier,
    Tenant,
)
from app.services import numbers, p2p
from app.services.common import next_code, notify

log = logging.getLogger("procureai.po")


async def create_po_for_offer(session: AsyncSession, tenant: Tenant, rfq: RFQ, offer: Offer, *,
                              created_by: str | None, share: Decimal = Decimal("1")) -> PurchaseOrder:
    supplier = (await session.execute(select(Supplier).where(Supplier.id == offer.supplier_id))).scalar_one()
    lines = list((await session.execute(
        select(OfferLine).where(OfferLine.offer_id == offer.id).order_by(OfferLine.created_at)
    )).scalars().all())
    po_lines = []
    for line in lines:
        quantity = (line.quantity or Decimal("0")) * share
        po_lines.append({
            "description": line.description or "Quoted item",
            "quantity": numbers.plain(quantity),
            "uom": line.uom,
            "unit_price": str(line.unit_price),
            "line_total": str(numbers.money(line.unit_price * quantity)),
            "rfq_line_id": line.rfq_line_id,
        })
    amount = numbers.money(sum((Decimal(item["line_total"]) for item in po_lines), Decimal("0")))
    po = PurchaseOrder(
        tenant_id=tenant.id, code=await next_code(session, PurchaseOrder, tenant.id, "PO"),
        rfq_id=rfq.id, supplier_id=supplier.id, offer_id=offer.id, amount=amount,
        currency=offer.currency, status="issued", issued_at=datetime.now(UTC), lines=po_lines,
        erp_status="not_exported",
    )
    session.add(po)
    await session.flush()
    po.document_html = render_po_html(tenant, rfq, supplier, po)
    session.add(po)
    await session.flush()
    # The issue of a purchase order is a status transition of its own and must be in the trail.
    await audit(session, tenant_id=tenant.id, action="po.issued", actor_id=created_by,
                entity_type="purchase_order", entity_id=po.id,
                after={"code": po.code, "amount": str(amount), "currency": po.currency,
                       "supplier_id": supplier.id, "rfq_id": rfq.id, "share": str(share)})
    await notify(session, tenant_id=tenant.id, user_id=rfq.buyer_id, kind="po_created",
                 title=f"PO {po.code} issued to {supplier.name}",
                 title_ar=f"تم إصدار أمر شراء {po.code} للمورد {supplier.name}",
                 link=f"/purchase-orders/{po.id}")
    await p2p.touch(session, tenant.id, po, actor_id=created_by, source="po_issued")
    return po


def render_po_html(tenant: Tenant, rfq: RFQ, supplier: Supplier, po: PurchaseOrder) -> str:
    rows = "".join(
        f"<tr><td>{line['description']}</td><td style='text-align:end'>{line['quantity']} {line['uom']}</td>"
        f"<td style='text-align:end'>{numbers.fmt_amount(Decimal(line['unit_price']), po.currency, 'en')}</td>"
        f"<td style='text-align:end'>{numbers.fmt_amount(Decimal(line['line_total']), po.currency, 'en')}</td></tr>"
        for line in po.lines
    )
    return f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<title>{po.code}</title>
<style>
 body{{font-family:'IBM Plex Sans Arabic',Arial,sans-serif;color:#16283f;margin:32px}}
 .brand{{letter-spacing:.14em;font-weight:700;color:#16283f}}
 .gold{{color:#a8841a}}
 table{{width:100%;border-collapse:collapse;margin-top:24px}}
 th,td{{border-bottom:1px solid #d9dce2;padding:10px;font-size:13px}}
 th{{text-align:start;color:#7a8494;font-weight:600}}
 .totals{{margin-top:16px;font-size:16px;font-weight:700}}
 .muted{{color:#7a8494;font-size:12px}}
</style></head><body>
<div class="brand">MANTIQ <span class="muted">PROCUREMENT INTELLIGENCE</span></div>
<h1 style="font-size:22px">Purchase Order / أمر شراء — <span class="gold">{po.code}</span></h1>
<p class="muted">Tenant: {tenant.name} · RFQ: {rfq.code} · Issued: {po.issued_at.strftime('%Y-%m-%d') if po.issued_at else ''}</p>
<p><strong>Supplier:</strong> {supplier.name} ({supplier.name_ar or supplier.name}) · {supplier.city}, {supplier.country}</p>
<p><strong>Delivery:</strong> {rfq.incoterm} — {rfq.delivery_location or 'Buyer premises'} · <strong>Payment:</strong> {rfq.payment_terms}</p>
<table><thead><tr><th>Description / البيان</th><th style="text-align:end">Qty</th>
<th style="text-align:end">Unit price</th><th style="text-align:end">Total</th></tr></thead>
<tbody>{rows}</tbody></table>
<p class="totals">Total: <span class="gold">{numbers.fmt_amount(po.amount, po.currency, 'en')}</span></p>
<p class="muted">Issued after human approval. This document is not a payment instruction.
ProcureAI never sends a binding contract without human approval.</p>
</body></html>"""


def po_pdf(html: str, po: PurchaseOrder) -> bytes:
    """Render a simple, print-ready PDF (ReportLab; no external binary needed).

    Arabic needs more than a font: the letters must be shaped and the line must read right-to-left.
    `services.pdf_fonts` bundles Noto Naskh Arabic and shapes through HarfBuzz, so an Arabic line is
    drawn with that face right-aligned; Latin-only text keeps Helvetica and is drawn exactly as before
    (and, because the font is subset, the Arabic face is not embedded in a Latin-only document).

    The description and the numbers are drawn as two separate runs on purpose. Shaping a mixed
    Arabic/ASCII string as one run would reverse the ASCII digits with it (HarfBuzz shapes one
    direction per buffer), which is how Arabic PDFs end up with mirrored prices.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    from app.services.pdf_fonts import layout, paragraph_is_rtl, shaped

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    left, right = 20 * mm, width - 20 * mm
    description_width = 95 * mm

    def fits(text: str, size: float, max_width: float) -> bool:
        return sum(width for _, _, width in layout(text, size)) <= max_width

    def fit(text: str, size: float, max_width: float) -> str:
        """Trim a line to its column, measuring the shaped runs (Arabic widths differ from raw ones)."""
        if fits(text, size, max_width):
            return text
        while text and not fits(f"{text}...", size, max_width):
            text = text[:-1]
        return f"{text}..."

    def draw_right(text: str, y: float, size: float, *, bold: bool = False) -> None:
        """Draw one column: Arabic runs with the bundled face, everything else with Helvetica.

        The runs arrive in visual order, so drawing them left to right lays out both a right-to-left
        Arabic line and a left-to-right Latin one correctly.
        """
        runs = layout(text, size, bold=bold)
        x = right - sum(width for _, _, width in runs) if paragraph_is_rtl(text) else left
        for run, font, width in runs:
            pdf.setFont(font, size)
            pdf.drawString(x, y, shaped(run, font, size))
            x += width

    def draw_left(text: str, y: float, size: float, *, bold: bool = False) -> None:
        pdf.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        pdf.drawString(left, y, text)

    pdf.setTitle(f"Purchase Order {po.code}")
    draw_left(f"Purchase Order {po.code}", height - 25 * mm, 14, bold=True)
    draw_left("MANTIQ - ProcureMent Intelligence", height - 32 * mm, 9)
    draw_right("أمر شراء", height - 44 * mm, 12, bold=True)
    y = height - 57 * mm
    draw_right("البيان", y, 9, bold=True)
    draw_left("Qty  |  Unit price  |  Line total", y, 9, bold=True)
    y -= 6 * mm
    for line in po.lines:
        draw_right(fit(str(line["description"]), 9, description_width), y, 9)
        draw_left(f"{line['quantity']} {line['uom']}  |  {line['unit_price']} {po.currency}  |  "
                  f"{line['line_total']}", y, 9)
        y -= 6 * mm
        if y < 30 * mm:
            pdf.showPage()
            y = height - 30 * mm
    draw_left(f"Total: {po.amount} {po.currency}", y - 8 * mm, 11, bold=True)
    draw_left("Generated by ProcureAI after human approval. Not a payment instruction.", 18 * mm, 7)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def po_excel(po: PurchaseOrder) -> bytes:
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = po.code
    sheet.append([f"Purchase Order {po.code}", "", "", "", ""])
    sheet.append(["Description", "Quantity", "UOM", "Unit price", "Line total"])
    for line in po.lines:
        sheet.append([line["description"], line["quantity"], line["uom"], line["unit_price"], line["line_total"]])
    sheet.append(["Total", "", "", "", str(po.amount)])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


async def export_to_erp(session: AsyncSession, tenant: Tenant, po: PurchaseOrder) -> dict:
    connector = registry.resolve("erp")
    payload = {"code": po.code, "rfq_code": "", "currency": po.currency, "amount": str(po.amount),
               "lines": po.lines, "supplier_id": po.supplier_id}
    result = await connector.export_purchase_order(payload)
    po.erp_status = "exported" if result.ok else "failed"
    po.document_html = f"{po.document_html}\n<!-- erp: {result.reference or result.error} -->"
    await session.flush()
    return {"ok": result.ok, "reference": result.reference, "error": result.error}
