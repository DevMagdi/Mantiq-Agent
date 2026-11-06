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


def po_pdf(html: str, po: PurchaseOrder, *, tenant=None, rfq=None, supplier=None) -> bytes:
    """Render a simple, print-ready PDF (ReportLab; no external binary needed).

    Arabic needs more than a font: the letters must be shaped and the line must read right-to-left.
    `services.pdf_fonts` bundles Noto Naskh Arabic and shapes through HarfBuzz, so an Arabic line is
    drawn with that face right-aligned; Latin-only text keeps Helvetica and is drawn exactly as before
    (and, because the font is subset, the Arabic face is not embedded in a Latin-only document).

    The description column and each numeric column are anchored independently on purpose. Shaping a
    mixed Arabic/ASCII string as one run would reverse the ASCII digits with it (HarfBuzz shapes one
    direction per buffer), which is how Arabic PDFs end up with mirrored prices — and drawing a long
    numbers run from the same origin as an end-anchored description stacks them on top of each other.

    ``tenant``/``rfq``/``supplier`` are optional so the many callers that only hold a PO row still get
    a correct document; when they are absent the meta block is simply shorter, never invented.
    """
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    from app.services.numbers import parse_decimal, plain
    from app.services.pdf_fonts import layout, paragraph_is_rtl, shaped

    NAVY, GOLD, GRAY, LINE = HexColor("#16283f"), HexColor("#a8841a"), HexColor("#7a8494"), HexColor("#d9dce2")

    def num(value, decimals: int = 2) -> str:
        parsed = parse_decimal(value)
        return f"{parsed:,.{decimals}f}" if parsed is not None else str(value)

    issued = getattr(po, "issued_at", None)
    issued_text = issued.strftime("%Y-%m-%d") if issued else ""

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    left, right = 18 * mm, width - 18 * mm
    usable = right - left

    desc_end = left + usable * 0.50
    qty_end = left + usable * 0.63
    unit_end = left + usable * 0.80
    total_end = right
    description_width = desc_end - left - 4 * mm

    def runs_width(text: str, size: float, *, bold: bool = False) -> float:
        return sum(w for _, _, w in layout(text, size, bold=bold))

    def fit(text: str, size: float, max_width: float, *, bold: bool = False) -> str:
        """Trim a line to its column, measuring the shaped runs (Arabic widths differ from raw ones)."""
        if runs_width(text, size, bold=bold) <= max_width:
            return text
        while text and runs_width(f"{text}...", size, bold=bold) > max_width:
            text = text[:-1]
        return f"{text}..."

    def draw_mixed(text: str, y: float, size: float, *, bold: bool = False, color=NAVY,
                   anchor: str = "auto", end: float | None = None,
                   max_width: float | None = None) -> None:
        """Draw one mixed-script line.

        ``anchor='auto'`` follows the paragraph direction; ``anchor='right'`` pins the line to the
        right margin; ``end=<x>`` pins the line to end exactly at x (table columns, header items).
        ``max_width`` trims the line before drawing; defaults to the usable width.
        """
        limit = max_width if max_width is not None else (end - left if end is not None else usable)
        text = fit(text, size, limit, bold=bold)
        runs = layout(text, size, bold=bold)
        total = sum(w for _, _, w in runs)
        if end is not None:
            x = end - total
        elif anchor == "right":
            x = right - total
        elif anchor == "left":
            x = left
        else:
            x = right - total if paragraph_is_rtl(text) else left
        pdf.setFillColor(color)
        for run, font, width in runs:
            pdf.setFont(font, size)
            pdf.drawString(x, y, shaped(run, font, size))
            x += width

    def hr(y: float, *, color=LINE, width_pt: float = 0.5) -> None:
        pdf.setStrokeColor(color)
        pdf.setLineWidth(width_pt)
        pdf.line(left, y, right, y)

    def table_header(y: float) -> float:
        draw_mixed("Description · البيان", y, 7.5, bold=True, color=GRAY, end=desc_end)
        draw_mixed("Qty · الكمية", y, 7.5, bold=True, color=GRAY, end=qty_end)
        draw_mixed("Price · السعر", y, 7.5, bold=True, color=GRAY, end=unit_end)
        draw_mixed("Total · الإجمالي", y, 7.5, bold=True, color=GRAY, end=total_end)
        hr(y - 2 * mm)
        return y - 6 * mm

    # ---------------------------------------------------------------- header
    pdf.setTitle(f"Purchase Order {po.code}")
    pdf.setAuthor("ProcureAI")
    pdf.setSubject("Purchase order")
    draw_mixed("MANTIQ — PROCUREMENT INTELLIGENCE", height - 19 * mm, 10.5, bold=True)
    draw_mixed("أمر شراء", height - 20 * mm, 17, bold=True, color=GOLD, anchor="right")
    hr(height - 24 * mm, color=GOLD, width_pt=1.1)
    draw_mixed("PURCHASE ORDER", height - 32 * mm, 8, color=GRAY)
    draw_mixed(po.code, height - 42 * mm, 19, bold=True)
    if issued_text:
        draw_mixed(f"Issued · تاريخ الإصدار: {issued_text}", height - 31.5 * mm, 8.5,
                   color=GRAY, anchor="right")

    # ---------------------------------------------------------------- meta block (only what is true)
    meta_y = height - 52 * mm
    if tenant is not None and getattr(tenant, "name", None):
        draw_mixed(f"Tenant · المنشأة: {tenant.name}", meta_y, 8.5)
        meta_y -= 4.6 * mm
    if supplier is not None and getattr(supplier, "name", None):
        line = f"Supplier · المورد: {supplier.name}"
        if getattr(supplier, "name_ar", None):
            line += f" — {supplier.name_ar}"
        city = getattr(supplier, "city", None)
        country = getattr(supplier, "country", None)
        if city or country:
            line += f" · {', '.join(part for part in (city, country) if part)}"
        draw_mixed(line, meta_y, 8.5)
        meta_y -= 4.6 * mm
    rfq_bits = []
    if rfq is not None and getattr(rfq, "code", None):
        rfq_bits.append(f"RFQ · طلب عرض: {rfq.code}")
    incoterm = getattr(rfq, "incoterm", None) if rfq is not None else None
    delivery = getattr(rfq, "delivery_location", None) if rfq is not None else None
    payment = getattr(rfq, "payment_terms", None) if rfq is not None else None
    if incoterm or delivery:
        rfq_bits.append(f"Delivery · التسليم: {' — '.join(p for p in (incoterm, delivery or 'Buyer premises') if p)}")
    if payment:
        rfq_bits.append(f"Payment · الدفع: {payment}")
    if rfq_bits:
        draw_mixed("  ·  ".join(rfq_bits), meta_y, 8.5)
        meta_y -= 4.6 * mm
    meta_y -= 1.5 * mm
    hr(meta_y)

    # ---------------------------------------------------------------- lines table
    y = table_header(meta_y - 6 * mm)
    for line in po.lines:
        quantity = parse_decimal(line.get("quantity"))
        qty_text = f"{plain(quantity)} {line.get('uom', '')}".strip() if quantity is not None else str(line.get("quantity"))
        draw_mixed(fit(str(line.get("description", "")), 8.5, description_width), y, 8.5, end=desc_end)
        draw_mixed(qty_text, y, 8.5, end=qty_end, max_width=qty_end - desc_end - 3 * mm)
        draw_mixed(f"{num(line.get('unit_price'))} {po.currency}", y, 8.5, end=unit_end,
                   max_width=unit_end - qty_end - 3 * mm)
        draw_mixed(num(line.get("line_total")), y, 8.5, end=total_end,
                   max_width=total_end - unit_end - 3 * mm)
        y -= 5.5 * mm
        if y < 28 * mm:
            pdf.showPage()
            y = table_header(height - 20 * mm)
    hr(y - 1 * mm)

    # ---------------------------------------------------------------- totals
    total_text = f"Total · الإجمالي   {num(po.amount)} {po.currency}"
    draw_mixed(total_text, y - 8 * mm, 12.5, bold=True, color=NAVY, anchor="right")
    draw_mixed("Included taxes are stated on the supplier invoice, not on this order.",
               y - 13 * mm, 6.5, color=GRAY, anchor="right")

    # ---------------------------------------------------------------- footer
    hr(16 * mm)
    draw_mixed("Generated by ProcureAI after human approval. Not a payment instruction.",
               11 * mm, 6.5, color=GRAY)
    draw_mixed("ليست تعليمات دفع", 11 * mm, 6.5, color=GRAY, anchor="right")
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
