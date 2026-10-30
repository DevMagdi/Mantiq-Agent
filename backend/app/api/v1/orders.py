"""Post-award endpoints: purchase orders, goods receipts, invoices, 3-way match and the ledger.

Everything here is human-driven. The agent has no tool that touches these routes, and the
invoice → payment path always requires an explicit approval call by a named user.
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import Context, require
from app.core.audit import audit
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.db.models.entities import (
    RFQ,
    GoodsReceipt,
    PurchaseOrder,
    Supplier,
    SupplierInvoice,
)
from app.services import invoicing, numbers, receiving
from app.services.po import export_to_erp, po_excel, po_pdf

log = logging.getLogger("procureai.api.orders")
router = APIRouter(tags=["orders"])

PO_STATUS_LABELS = {
    "issued": ("Issued", "صادر"),
    "in_transit": ("In transit", "في الطريق"),
    "partially_received": ("Partially received", "مستلم جزئياً"),
    "received": ("Received by warehouse", "تم استلام المخزن"),
    "confirmed": ("Confirmed by requester", "مؤكد من الطالب"),
    "closed": ("Closed", "مغلق"),
}


async def _tenant_po(ctx: Context, po_id: str) -> PurchaseOrder:
    po = (await ctx.session.execute(
        select(PurchaseOrder).where(PurchaseOrder.id == po_id, PurchaseOrder.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not po:
        raise NotFound("Purchase order not found.")
    return po


async def _tenant_invoice(ctx: Context, invoice_id: str) -> SupplierInvoice:
    invoice = (await ctx.session.execute(
        select(SupplierInvoice).where(SupplierInvoice.id == invoice_id,
                                      SupplierInvoice.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not invoice:
        raise NotFound("Invoice not found.")
    return invoice


async def _tenant_receipt(ctx: Context, receipt_id: str) -> GoodsReceipt:
    receipt = (await ctx.session.execute(
        select(GoodsReceipt).where(GoodsReceipt.id == receipt_id, GoodsReceipt.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not receipt:
        raise NotFound("Goods receipt not found.")
    return receipt


def _line_view(line: dict) -> dict:
    """PO lines as decimal strings so the JSON contract stays exact (API.md §Conventions)."""
    return {
        "line_no": line["line_no"], "description": line.get("description", ""),
        "uom": line.get("uom", ""), "quantity": numbers.plain(line["quantity"]),
        "unit_price": str(numbers.money(line["unit_price"])),
        "line_total": str(numbers.money((line["quantity"] or 0) * (line["unit_price"] or 0))),
        "rfq_line_id": line.get("rfq_line_id"),
    }


def _po_summary(po: PurchaseOrder) -> dict:
    label_en, label_ar = PO_STATUS_LABELS.get(po.status, (po.status, po.status))
    return {
        "id": po.id, "code": po.code, "rfq_id": po.rfq_id, "supplier_id": po.supplier_id,
        "amount": str(po.amount), "currency": po.currency, "status": po.status,
        "status_label": label_en, "status_label_ar": label_ar,
        "issued_at": po.issued_at, "closed_at": po.closed_at, "erp_status": po.erp_status,
        "lines": len(po.lines or []),
    }


async def _timeline(ctx: Context, po: PurchaseOrder, receipts: list[GoodsReceipt],
                    invoices: list[SupplierInvoice]) -> list[dict]:
    """Ordered, bilingual audit of the delivery: what happened, when and by whom."""
    events: list[dict] = [{
        "kind": "issued", "at": po.issued_at, "actor": po.created_by,
        "label_en": "Purchase order issued", "label_ar": "تم إصدار أمر الشراء",
        "status": "issued", "done": True,
    }]
    for receipt in receipts:
        events.append({
            "kind": "received", "at": receipt.received_at, "actor": receipt.received_by,
            "label_en": f"Goods receipt {receipt.code} recorded at the warehouse",
            "label_ar": f"تم تسجيل استلام {receipt.code} بالمخزن",
            "status": receipt.status, "done": True, "ref": receipt.id,
        })
        if receipt.confirmed_at:
            events.append({
                "kind": "confirmed" if receipt.status == "confirmed" else "rejected",
                "at": receipt.confirmed_at, "actor": receipt.confirmed_by,
                "label_en": ("Requester confirmed the delivery" if receipt.status == "confirmed"
                             else "Requester rejected the delivery"),
                "label_ar": ("أكد طالب الشراء الاستلام" if receipt.status == "confirmed"
                             else "رفض طالب الشراء الاستلام"),
                "status": receipt.status, "done": True, "ref": receipt.id,
            })
    for invoice in invoices:
        events.append({
            "kind": "invoice", "at": invoice.created_at, "actor": invoice.created_by,
            "label_en": f"Invoice {invoice.number} recorded ({invoice.status})",
            "label_ar": f"تم تسجيل الفاتورة {invoice.number} ({invoice.status})",
            "status": invoice.status, "done": True, "ref": invoice.id,
        })
        if invoice.approved_at:
            events.append({
                "kind": "approved", "at": invoice.approved_at, "actor": invoice.approved_by,
                "label_en": f"Invoice {invoice.number} approved for payment",
                "label_ar": f"تم اعتماد الفاتورة {invoice.number} للسداد",
                "status": "approved", "done": True, "ref": invoice.id,
            })
    if po.closed_at:
        events.append({"kind": "closed", "at": po.closed_at, "actor": po.created_by,
                       "label_en": "Purchase order closed", "label_ar": "تم إغلاق أمر الشراء",
                       "status": "closed", "done": True})
    events.sort(key=lambda row: (row["at"] is None, row["at"] or datetime.min))
    return events


# ------------------------------------------------------------------ purchase orders
@router.get("/purchase-orders", summary="List purchase orders")
async def list_pos(ctx: Context = Depends(require("po:read")), status: str | None = None,
                   supplier_id: str | None = None, page: int = 1, page_size: int = 25):
    filters = [PurchaseOrder.tenant_id == ctx.tenant_id]
    if status:
        filters.append(PurchaseOrder.status == status)
    if supplier_id:
        filters.append(PurchaseOrder.supplier_id == supplier_id)
    total = (await ctx.session.execute(
        select(func.count()).select_from(PurchaseOrder).where(*filters)
    )).scalar_one()
    page_size, offset = 25 if not page_size else min(page_size, 200), (max(page, 1) - 1) * (page_size or 25)
    rows = list((await ctx.session.execute(
        select(PurchaseOrder).where(*filters).order_by(PurchaseOrder.created_at.desc())
        .limit(page_size).offset(offset)
    )).scalars().all())
    suppliers = {}
    if rows:
        suppliers = {
            supplier.id: supplier for supplier in (await ctx.session.execute(
                select(Supplier).where(Supplier.id.in_([row.supplier_id for row in rows]))
            )).scalars().all()
        }
    items = []
    for po in rows:
        summary = _po_summary(po)
        supplier = suppliers.get(po.supplier_id)
        summary["supplier_name"] = supplier.name if supplier else ""
        summary["supplier_name_ar"] = (supplier.name_ar if supplier else "") or ""
        items.append(summary)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/purchase-orders/{po_id}", summary="Purchase order detail with the delivery lifecycle")
async def get_po(po_id: str, ctx: Context = Depends(require("po:read"))):
    po = await _tenant_po(ctx, po_id)
    supplier = (await ctx.session.execute(select(Supplier).where(Supplier.id == po.supplier_id))).scalar_one_or_none()
    rfq = (await ctx.session.execute(select(RFQ).where(RFQ.id == po.rfq_id))).scalar_one_or_none()
    receipts = await receiving.list_receipts(ctx.session, ctx.tenant_id, po.id)
    invoices = await invoicing.list_invoices(ctx.session, ctx.tenant_id, po.id)
    discrepancy = await receiving.discrepancy_summary(ctx.session, ctx.tenant_id, po)
    # Commercial balances are financial data: only ledger:read holders receive them.
    balance = (
        await invoicing.supplier_balance(ctx.session, ctx.tenant_id, po.supplier_id)
        if "ledger:read" in ctx.permissions
        else None
    )
    return {
        **_po_summary(po),
        "amount": str(po.amount), "currency": po.currency,
        "lines": [_line_view(line) for line in receiving.po_lines(po)],
        "supplier": {"id": supplier.id, "name": supplier.name, "name_ar": supplier.name_ar,
                     "status": supplier.status} if supplier else None,
        "rfq": {"id": rfq.id, "code": rfq.code, "title": rfq.title,
                "payment_terms": rfq.payment_terms, "incoterm": rfq.incoterm} if rfq else None,
        "document_html": po.document_html,
        "timeline": await _timeline(ctx, po, receipts, invoices),
        "receipts": [receiving.receipt_view(row) for row in receipts],
        "invoices": [invoicing.invoice_view(row) for row in invoices],
        "discrepancy": discrepancy,
        "supplier_balance": balance,
        "next_status": {"issued": "in_transit", "in_transit": "partially_received",
                        "partially_received": "received", "received": "confirmed",
                        "confirmed": "closed", "closed": None}.get(po.status),
        "can_close": po.status == "confirmed",
    }


@router.post("/purchase-orders/{po_id}/ship", summary="Mark the purchase order as shipped by the supplier")
async def mark_shipped(po_id: str, ctx: Context = Depends(require("po:update")),
                       carrier: str = Query(default=""), expected_at: datetime | None = None):
    po = await _tenant_po(ctx, po_id)
    result = await receiving.mark_in_transit(ctx.session, ctx.tenant, po, actor_id=ctx.user_id,
                                             carrier=carrier, expected_at=expected_at)
    if not result.get("ok"):
        raise Conflict(result.get("error", "Cannot mark this purchase order as shipped."))
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="po.in_transit", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="purchase_order", entity_id=po.id,
                after=result, ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return result


@router.post("/purchase-orders/{po_id}/close", summary="Close a fully confirmed purchase order")
async def close_purchase_order(po_id: str, ctx: Context = Depends(require("po:update")), reason: str = ""):
    po = await _tenant_po(ctx, po_id)
    result = await receiving.close_po(ctx.session, ctx.tenant, po, actor_id=ctx.user_id, reason=reason)
    if not result.get("ok"):
        raise Conflict(result.get("error", "Cannot close this purchase order."))
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="po.closed", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="purchase_order", entity_id=po.id,
                after=result, ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return result


# ------------------------------------------------------------------ goods receipts
class ReceiptLineIn(BaseModel):
    line_no: int = Field(ge=1)
    received_qty: str
    accepted_qty: str | None = None
    rejected_qty: str = "0"
    condition: str = "ok"
    note: str = ""


class ReceiptIn(BaseModel):
    lines: list[ReceiptLineIn] = Field(min_length=1)
    delivery_note_ref: str = ""
    carrier: str = ""
    warehouse: str = ""
    notes: str = ""
    received_at: datetime | None = None


@router.get("/purchase-orders/{po_id}/receipts", summary="Goods receipts for a purchase order")
async def list_receipts(po_id: str, ctx: Context = Depends(require("po:read"))):
    po = await _tenant_po(ctx, po_id)
    rows = await receiving.list_receipts(ctx.session, ctx.tenant_id, po.id)
    return {"items": [receiving.receipt_view(row) for row in rows]}


@router.post("/purchase-orders/{po_id}/receipts", status_code=201,
             summary="Record a goods receipt at the warehouse")
async def create_receipt(po_id: str, payload: ReceiptIn, ctx: Context = Depends(require("receipt:create"))):
    po = await _tenant_po(ctx, po_id)
    result = await receiving.create_receipt(
        ctx.session, ctx.tenant, po,
        lines=[line.model_dump() for line in payload.lines], received_by=ctx.user_id,
        delivery_note_ref=payload.delivery_note_ref, carrier=payload.carrier,
        warehouse=payload.warehouse, notes=payload.notes, received_at=payload.received_at,
    )
    if not result.get("ok"):
        raise ValidationFailed(result.get("error", "Invalid goods receipt."), extra={"issues": result.get("issues", [])})
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="goods_receipt.created", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="purchase_order", entity_id=po.id,
                after={"code": result["code"], "po_status": result["po_status"]}, ip=ctx.ip,
                request_id=ctx.request_id)
    await ctx.session.commit()
    return result


class ConfirmationIn(BaseModel):
    decision: str = "confirm"
    quantity_ok: bool = True
    quality_ok: bool = True
    spec_ok: bool = True
    documents_ok: bool = False
    comment: str = ""


@router.post("/receipts/{receipt_id}/confirm", summary="Requester confirms the received items match the need")
async def confirm_receipt(receipt_id: str, payload: ConfirmationIn,
                          ctx: Context = Depends(require("receipt:confirm"))):
    receipt = await _tenant_receipt(ctx, receipt_id)
    result = await receiving.confirm_receipt(
        ctx.session, ctx.tenant, receipt, confirmed_by=ctx.user_id,
        checks={"quantity_ok": payload.quantity_ok, "quality_ok": payload.quality_ok,
                "spec_ok": payload.spec_ok, "documents_ok": payload.documents_ok},
        comment=payload.comment, decision=payload.decision,
    )
    if not result.get("ok"):
        raise ValidationFailed(result.get("error", "Cannot confirm this receipt."))
    await audit(ctx.session, tenant_id=ctx.tenant_id, action=f"goods_receipt.{result['status']}",
                actor_id=ctx.user_id, actor_label=ctx.user.email, entity_type="goods_receipt",
                entity_id=receipt.id, after=result, ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return result


# ------------------------------------------------------------------ invoices & 3-way match
class InvoiceLineIn(BaseModel):
    line_no: int | None = None
    description: str = ""
    uom: str = ""
    quantity: str
    unit_price: str


class InvoiceIn(BaseModel):
    number: str = Field(min_length=1, max_length=60)
    lines: list[InvoiceLineIn] = Field(min_length=1)
    currency: str = ""
    tax_amount: str = "0"
    subtotal: str | None = None
    total: str | None = None
    invoice_date: datetime | None = None
    due_date: datetime | None = None
    notes: str = ""


@router.get("/purchase-orders/{po_id}/invoices", summary="Supplier invoices for a purchase order")
async def list_invoices(po_id: str, ctx: Context = Depends(require("po:read"))):
    po = await _tenant_po(ctx, po_id)
    rows = await invoicing.list_invoices(ctx.session, ctx.tenant_id, po.id)
    return {"items": [invoicing.invoice_view(row) for row in rows]}


@router.post("/purchase-orders/{po_id}/invoices", status_code=201,
             summary="Record a supplier invoice and run the three-way match")
async def create_invoice(po_id: str, payload: InvoiceIn, ctx: Context = Depends(require("invoice:create"))):
    po = await _tenant_po(ctx, po_id)
    result = await invoicing.create_invoice(
        ctx.session, ctx.tenant, po, number=payload.number,
        lines=[line.model_dump() for line in payload.lines], received_by=ctx.user_id,
        currency=payload.currency or po.currency, tax_amount=payload.tax_amount,
        invoice_date=payload.invoice_date, due_date=payload.due_date, notes=payload.notes,
        subtotal=payload.subtotal, total=payload.total,
    )
    if not result.get("ok"):
        raise Conflict(result.get("error", "Cannot record this invoice."))
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="invoice.created", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="supplier_invoice", entity_id=result["invoice_id"],
                after={"number": result["number"], "match": result["match"]["status"]}, ip=ctx.ip,
                request_id=ctx.request_id)
    await ctx.session.commit()
    return result


@router.post("/invoices/{invoice_id}/match", summary="Re-run the three-way match")
async def rematch_invoice(invoice_id: str, ctx: Context = Depends(require("po:read"))):
    invoice = await _tenant_invoice(ctx, invoice_id)
    match = await invoicing.compute_match(ctx.session, ctx.tenant, invoice)
    await ctx.session.commit()
    return {"invoice_id": invoice.id, "status": invoice.status, "match": match}


class ApprovalIn(BaseModel):
    comment: str = ""
    override_reason: str = ""


@router.post("/invoices/{invoice_id}/approve", summary="Human approval of a matched invoice")
async def approve_invoice(invoice_id: str, payload: ApprovalIn,
                          ctx: Context = Depends(require("invoice:approve"))):
    invoice = await _tenant_invoice(ctx, invoice_id)
    result = await invoicing.approve_invoice(ctx.session, ctx.tenant, invoice, approved_by=ctx.user_id,
                                             comment=payload.comment,
                                             override_reason=payload.override_reason)
    if not result.get("ok"):
        raise Conflict(result.get("error", "Cannot approve this invoice."), extra={"match": result.get("match")})
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="invoice.approved", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="supplier_invoice", entity_id=invoice.id,
                after={"status": invoice.status, "overridden": result["overridden"],
                       "method": result["payment_suggestion"].get("method")},
                ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return result


class PaymentIn(BaseModel):
    amount: str
    method: str
    reference: str
    paid_at: datetime | None = None
    note: str = ""


@router.post("/invoices/{invoice_id}/payments", summary="Record a payment executed outside the platform")
async def record_payment(invoice_id: str, payload: PaymentIn, ctx: Context = Depends(require("payment:record"))):
    invoice = await _tenant_invoice(ctx, invoice_id)
    result = await invoicing.record_payment(ctx.session, ctx.tenant, invoice, amount=payload.amount,
                                            method=payload.method, reference=payload.reference,
                                            paid_at=payload.paid_at, actor_id=ctx.user_id, note=payload.note)
    if not result.get("ok"):
        raise ValidationFailed(result.get("error", "Cannot record this payment."))
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="payment.recorded", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="supplier_invoice", entity_id=invoice.id,
                after={"amount": payload.amount, "method": payload.method, "status": result["status"]},
                ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return result


# ------------------------------------------------------------------ supplier ledger
@router.get("/supplier-balances", summary="Outstanding balance per supplier")
async def supplier_balances(ctx: Context = Depends(require("ledger:read"))):
    rows = await invoicing.supplier_balances(ctx.session, ctx.tenant)
    outstanding = sum((Decimal(row["outstanding"]) for row in rows), Decimal("0"))
    overdue = sum((Decimal(row["overdue"]) for row in rows), Decimal("0"))
    return {"items": rows, "total_outstanding": str(numbers.money(outstanding)),
            "total_overdue": str(numbers.money(overdue)), "currency": ctx.tenant.currency}


@router.get("/suppliers/{supplier_id}/balance", summary="Ledger and balance for one supplier")
async def supplier_balance(supplier_id: str, ctx: Context = Depends(require("ledger:read"))):
    supplier = (await ctx.session.execute(
        select(Supplier).where(Supplier.id == supplier_id, Supplier.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not supplier:
        raise NotFound("Supplier not found.")
    balance = await invoicing.supplier_balance(ctx.session, ctx.tenant_id, supplier.id)
    return {**balance, "supplier_name": supplier.name, "supplier_name_ar": supplier.name_ar}


# ------------------------------------------------------------------ documents (moved from rfq)
@router.get("/purchase-orders/{po_id}/pdf", summary="Download the PO as PDF")
async def po_pdf_endpoint(po_id: str, ctx: Context = Depends(require("po:export"))):
    po = await _tenant_po(ctx, po_id)
    return Response(content=po_pdf(po.document_html, po), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{po.code}.pdf"'})


@router.get("/purchase-orders/{po_id}/excel", summary="Download the PO as Excel")
async def po_excel_endpoint(po_id: str, ctx: Context = Depends(require("po:export"))):
    po = await _tenant_po(ctx, po_id)
    return Response(content=po_excel(po), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{po.code}.xlsx"'})


@router.post("/purchase-orders/{po_id}/export-erp", summary="Push the PO to the ERP connector")
async def po_export_erp(po_id: str, ctx: Context = Depends(require("po:export"))):
    po = await _tenant_po(ctx, po_id)
    result = await export_to_erp(ctx.session, ctx.tenant, po)
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="po.exported", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="purchase_order", entity_id=po.id,
                after=result, ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return result
