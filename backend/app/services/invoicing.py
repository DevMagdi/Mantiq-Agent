"""Three-way match (PO ↔ goods receipt ↔ invoice), payment suggestion and the supplier ledger.

Safety contract, inherited from the agent design:

* the *match* is deterministic arithmetic — no model output, no heuristics;
* an invoice can be approved for payment only by a human holding ``invoice:approve`` and only when
  the match is clean (or a named human overrides it with a written reason, which is audited);
* ProcureAI never moves money. ``record_payment`` is bookkeeping for a transfer that was executed
  in the bank/ERP, and no agent tool exists that can call it.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.entities import (
    RFQ,
    PurchaseOrder,
    Supplier,
    SupplierInvoice,
    SupplierLedgerEntry,
    Tenant,
)
from app.services import numbers
from app.services.common import notify, notify_role
from app.services.receiving import EPOCH, po_lines, received_totals

log = logging.getLogger("procureai.invoicing")

# Invoice lifecycle: received -> matched | mismatched -> approved -> partially_paid | paid (or disputed)
MATCHED_STATUSES = ("matched", "approved", "partially_paid", "paid")
PAYABLE_STATUSES = ("approved", "partially_paid")
ALREADY_APPROVED_STATUSES = ("approved", "partially_paid", "paid")
DEFAULT_PRICE_TOLERANCE_PCT = Decimal("0.5")
DEFAULT_LC_THRESHOLD = Decimal("250000")

ISSUE_TEXT = {
    "QTY_EXCEEDS_RECEIPT": ("Invoiced quantity exceeds the confirmed goods receipt.",
                            "الكمية المفوترة تتجاوز الاستلام المؤكد."),
    "PRICE_VARIANCE": ("Unit price differs from the purchase order beyond tolerance.",
                       "سعر الوحدة يختلف عن أمر الشراء بما يتجاوز الحد المسموح."),
    "NO_CONFIRMED_RECEIPT": ("No confirmed goods receipt covers this line.",
                             "لا يوجد استلام مؤكد يغطي هذا البند."),
    "UNKNOWN_LINE": ("The invoice bills a line that is not on the purchase order.",
                     "الفاتورة تحتوي بنداً غير موجود في أمر الشراء."),
    "CURRENCY_MISMATCH": ("Invoice currency differs from the purchase order.",
                          "عملة الفاتورة تختلف عن عملة أمر الشراء."),
    "SUBTOTAL_MISMATCH": ("Invoice subtotal does not equal the sum of its lines.",
                          "إجمالي الفاتورة لا يساوي مجموع بنودها."),
    "TOTAL_MISMATCH": ("Invoice total does not equal subtotal plus tax.",
                       "إجمالي الفاتورة لا يساوي الصافي بعد الضريبة."),
    "INVALID_QUANTITY": ("Invoice line quantity must be positive.", "كمية بند الفاتورة يجب أن تكون موجبة."),
    "OVER_INVOICED": ("This invoice would bill more than was received across all invoices.",
                      "هذه الفاتورة ستتجاوز إجمالي ما تم استلامه."),
}


def _tolerance_pct(tenant: Tenant) -> Decimal:
    raw = (tenant.settings or {}).get("invoice_price_tolerance_pct")
    parsed = numbers.parse_decimal(raw)
    return parsed if parsed is not None and parsed >= 0 else DEFAULT_PRICE_TOLERANCE_PCT


def _issue(code: str, line_no: int, detail: str = "") -> dict:
    text_en, text_ar = ISSUE_TEXT.get(code, (code.replace("_", " ").title(), code))
    return {"code": code, "line_no": line_no, "message": text_en,
            "message_ar": text_ar, "detail": detail}


async def _previous_invoiced(session: AsyncSession, tenant_id: str, po_id: str,
                             exclude_id: str | None) -> dict[int, Decimal]:
    totals: dict[int, Decimal] = {}
    query = select(SupplierInvoice).where(
        SupplierInvoice.tenant_id == tenant_id, SupplierInvoice.po_id == po_id,
        SupplierInvoice.status.in_(MATCHED_STATUSES),
    )
    if exclude_id:
        query = query.where(SupplierInvoice.id != exclude_id)
    for invoice in (await session.execute(query)).scalars().all():
        for line in invoice.lines or []:
            line_no = int(line.get("line_no") or 0)
            totals[line_no] = totals.get(line_no, Decimal("0")) + (
                numbers.parse_decimal(line.get("quantity")) or Decimal("0"))
    return totals


async def compute_match(session: AsyncSession, tenant: Tenant, invoice: SupplierInvoice, *,
                        persist: bool = True) -> dict:
    """Compare invoice lines against the PO and the requester-confirmed receipts."""
    po = (await session.execute(
        select(PurchaseOrder).where(PurchaseOrder.id == invoice.po_id, PurchaseOrder.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if not po:
        return {"status": "mismatch", "issues": [_issue("UNKNOWN_LINE", 0, "Purchase order not found")],
                "lines": [], "totals": {}, "can_approve": False, "tolerance_pct": str(_tolerance_pct(tenant))}

    tolerance = _tolerance_pct(tenant)
    ordered = {row["line_no"]: row for row in po_lines(po)}
    confirmed = await received_totals(session, tenant.id, po.id)
    previously = await _previous_invoiced(session, tenant.id, po.id, invoice.id)

    issues: list[dict] = []
    rows: list[dict] = []
    invoiced_total = Decimal("0")
    for index, raw in enumerate(invoice.lines or []):
        line_no = int(raw.get("line_no") or index + 1)
        quantity = numbers.parse_decimal(raw.get("quantity")) or Decimal("0")
        unit_price = numbers.parse_decimal(raw.get("unit_price")) or Decimal("0")
        line_total = numbers.money(quantity * unit_price)
        invoiced_total += line_total
        ordered_row = ordered.get(line_no)
        row = {
            "line_no": line_no, "description": raw.get("description") or (ordered_row or {}).get("description", ""),
            "uom": raw.get("uom") or (ordered_row or {}).get("uom", ""),
            "invoiced_qty": str(quantity), "unit_price": str(unit_price), "line_total": str(line_total),
            "ordered_qty": str(ordered_row["quantity"]) if ordered_row else "0",
            "po_unit_price": str(ordered_row["unit_price"]) if ordered_row else "0",
            "received_qty": str(confirmed.get(line_no, Decimal("0"))),
            "previously_invoiced_qty": str(previously.get(line_no, Decimal("0"))),
            "status": "ok", "issues": [],
        }
        if not ordered_row:
            issues.append(_issue("UNKNOWN_LINE", line_no))
            row["issues"].append("UNKNOWN_LINE")
        else:
            available = max(Decimal("0"), confirmed.get(line_no, Decimal("0")) - previously.get(line_no, Decimal("0")))
            row["available_qty"] = str(numbers.money(available))
            if quantity <= 0:
                issues.append(_issue("INVALID_QUANTITY", line_no))
                row["issues"].append("INVALID_QUANTITY")
            if available <= EPOCH:
                issues.append(_issue("NO_CONFIRMED_RECEIPT", line_no))
                row["issues"].append("NO_CONFIRMED_RECEIPT")
            elif quantity > available + EPOCH:
                issues.append(_issue("QTY_EXCEEDS_RECEIPT", line_no,
                                     f"invoiced {quantity} > available {numbers.money(available)}"))
                row["issues"].append("QTY_EXCEEDS_RECEIPT")
            po_price = ordered_row["unit_price"] or Decimal("0")
            if po_price > 0:
                variance_pct = (unit_price - po_price) / po_price * 100
                row["price_variance_pct"] = round(float(variance_pct), 4)
                if abs(variance_pct) > tolerance:
                    issues.append(_issue("PRICE_VARIANCE", line_no,
                                         f"{numbers.money(unit_price)} vs PO {numbers.money(po_price)} "
                                         f"({row['price_variance_pct']:+.2f}% > {tolerance}%)"))
                    row["issues"].append("PRICE_VARIANCE")
        if row["issues"]:
            row["status"] = "mismatch"
        rows.append(row)

    if invoice.currency != po.currency:
        issues.append(_issue("CURRENCY_MISMATCH", 0, f"{invoice.currency} vs {po.currency}"))
    subtotal = numbers.money(numbers.parse_decimal(invoice.subtotal) or Decimal("0"))
    if abs(subtotal - invoiced_total) > Decimal("0.05"):
        issues.append(_issue("SUBTOTAL_MISMATCH", 0,
                             f"subtotal {subtotal} != lines {numbers.money(invoiced_total)}"))
    total = numbers.money(numbers.parse_decimal(invoice.total) or Decimal("0"))
    tax = numbers.money(numbers.parse_decimal(invoice.tax_amount) or Decimal("0"))
    if abs(total - (subtotal + tax)) > Decimal("0.05"):
        issues.append(_issue("TOTAL_MISMATCH", 0, f"total {total} != {numbers.money(subtotal + tax)}"))

    over_invoiced = any(issue["code"] in {"QTY_EXCEEDS_RECEIPT", "NO_CONFIRMED_RECEIPT"} for issue in issues)
    if not confirmed:
        status = "incomplete"
    elif issues:
        status = "mismatch"
    else:
        status = "matched"

    result = {
        "status": status,
        "issues": issues,
        "lines": rows,
        "tolerance_pct": str(tolerance),
        "totals": {
            "po_amount": str(numbers.money(numbers.parse_decimal(po.amount) or Decimal("0"))),
            "invoiced_amount": str(numbers.money(invoiced_total)),
            "received_value": str(numbers.money(sum(
                (confirmed.get(line_no, Decimal("0")) * row["unit_price"] for line_no, row in ordered.items()),
                Decimal("0")))),
            "currency": invoice.currency,
        },
        "receipts_confirmed": bool(confirmed),
        "can_approve": status == "matched",
        "over_invoiced": over_invoiced,
        "checked_at": datetime.now(UTC).isoformat(),
    }
    if persist:
        invoice.match_result = result
        if invoice.status in {"received", "matched", "mismatched", "incomplete", "disputed"}:
            invoice.status = "matched" if status == "matched" else "mismatched"
        await session.flush()
    return result


async def create_invoice(session: AsyncSession, tenant: Tenant, po: PurchaseOrder, *, number: str,
                         lines: list[dict], received_by: str | None, currency: str = "EGP",
                         tax_amount: Decimal | str | None = None, invoice_date: datetime | None = None,
                         due_date: datetime | None = None, notes: str = "",
                         subtotal: Decimal | str | None = None, total: Decimal | str | None = None) -> dict:
    """Record a supplier invoice and immediately run the three-way match."""
    if not number.strip():
        return {"ok": False, "error": "An invoice number is required."}
    if not lines:
        return {"ok": False, "error": "An invoice needs at least one line."}
    duplicate = (await session.execute(
        select(SupplierInvoice).where(SupplierInvoice.tenant_id == tenant.id,
                                      SupplierInvoice.number == number.strip())
    )).scalar_one_or_none()
    if duplicate:
        return {"ok": False, "error": f"Invoice {number.strip()} is already recorded.", "invoice_id": duplicate.id}

    normalised: list[dict] = []
    computed = Decimal("0")
    for index, raw in enumerate(lines):
        quantity = numbers.parse_decimal(raw.get("quantity")) or Decimal("0")
        unit_price = numbers.parse_decimal(raw.get("unit_price")) or Decimal("0")
        line_total = numbers.money(quantity * unit_price)
        computed += line_total
        normalised.append({
            "line_no": int(raw.get("line_no") or index + 1),
            "description": str(raw.get("description") or "")[:400],
            "uom": str(raw.get("uom") or "")[:16],
            "quantity": str(quantity), "unit_price": str(unit_price), "line_total": str(line_total),
        })
    tax = numbers.money(numbers.parse_decimal(tax_amount) or Decimal("0"))
    sub = numbers.money(numbers.parse_decimal(subtotal) if subtotal is not None else computed)
    tot = numbers.money(numbers.parse_decimal(total) if total is not None else sub + tax)

    invoice = SupplierInvoice(
        tenant_id=tenant.id, po_id=po.id, supplier_id=po.supplier_id, number=number.strip()[:60],
        currency=(currency or po.currency).upper()[:3], subtotal=sub, tax_amount=tax, total=tot,
        status="received", lines=normalised, notes=notes[:2000], created_by=received_by,
        invoice_date=invoice_date or datetime.now(UTC), due_date=due_date,
    )
    session.add(invoice)
    await session.flush()
    match = await compute_match(session, tenant, invoice)
    if match["status"] == "mismatch":
        await notify(session, tenant_id=tenant.id, user_id=po.created_by, kind="invoice_mismatch",
                     title=f"Invoice {invoice.number} does not match {po.code}",
                     title_ar=f"الفاتورة {invoice.number} لا تطابق أمر الشراء {po.code}",
                     body="; ".join(issue["message"] for issue in match["issues"][:3]),
                     link=f"/purchase-orders/{po.id}", severity="warning")
    return {"ok": True, "invoice_id": invoice.id, "number": invoice.number, "status": invoice.status,
            "match": match}


def suggest_payment(*, invoice: SupplierInvoice, po: PurchaseOrder, supplier: Supplier | None,
                    tenant: Tenant, rfq: RFQ | None = None, outstanding: Decimal | None = None,
                    now: datetime | None = None) -> dict:
    """Deterministic suggestion: rules over supplier data and tenant policy, never a model guess."""
    now = now or datetime.now(UTC)
    settings = tenant.settings or {}
    local_country = str(settings.get("country") or "EG").upper()
    threshold = numbers.parse_decimal(settings.get("lc_threshold")) or DEFAULT_LC_THRESHOLD
    total = numbers.money(numbers.parse_decimal(invoice.total) or Decimal("0"))
    paid = numbers.money(numbers.parse_decimal(invoice.paid_amount) or Decimal("0"))
    due = total - paid if outstanding is None else outstanding
    cross_border = bool(supplier and (supplier.country or "EG").upper() != local_country) \
        or (invoice.currency or tenant.currency) != tenant.currency
    due_date = invoice.due_date
    days_until_due = numbers.days_between(now, due_date) if due_date else None
    preferred = bool(supplier and supplier.status == "preferred")
    reasons: list[dict] = []

    if cross_border and due >= threshold:
        method, priority = "letter_of_credit", "high"
        reasons.append({"code": "CROSS_BORDER_HIGH_VALUE",
                        "text_en": f"Cross-border invoice above {numbers.fmt_amount(threshold, invoice.currency, 'en')}; "
                                   "an LC protects both sides.",
                        "text_ar": f"فاتورة دولية تتجاوز {numbers.fmt_amount(threshold, invoice.currency, 'ar')}؛ "
                                   "خطاب الاعتماد يحمي الطرفين."})
    elif cross_border:
        method, priority = "swift_wire", "normal"
        reasons.append({"code": "CROSS_BORDER",
                        "text_en": "Supplier is outside the local market, so an international wire is required.",
                        "text_ar": "المورد خارج السوق المحلي، لذا يلزم تحويل دولي."})
    else:
        method, priority = "bank_transfer", "normal"
        reasons.append({"code": "LOCAL_SUPPLIER",
                        "text_en": "Local supplier in the tenant currency: a domestic bank transfer is cheapest.",
                        "text_ar": "مورد محلي بنفس عملة الشركة: التحويل البنكي المحلي هو الأقل تكلفة."})

    if preferred and priority == "normal":
        priority = "high"
        reasons.append({"code": "PREFERRED_SUPPLIER",
                        "text_en": "Preferred supplier: settle on time to protect negotiated terms and ratings.",
                        "text_ar": "مورد مفضل: السداد في الوقت يحافظ على الشروط والتقييم."})
    if days_until_due is not None and days_until_due < 0:
        priority = "high"
        reasons.append({"code": "OVERDUE",
                        "text_en": f"Invoice is {-days_until_due} day(s) past due.",
                        "text_ar": f"الفاتورة متأخرة {-days_until_due} يوم."})
    elif days_until_due is not None and days_until_due <= 7:
        priority = "high"
        reasons.append({"code": "DUE_SOON",
                        "text_en": f"Due in {days_until_due} day(s).",
                        "text_ar": f"مستحقة خلال {days_until_due} يوم."})

    terms = (rfq.payment_terms if rfq and rfq.payment_terms else "") or "As per purchase order"
    reasons.append({"code": "HUMAN_APPROVAL",
                    "text_en": "A human approves and executes the payment; ProcureAI only recommends.",
                    "text_ar": "الموافقة والتنفيذ بشريان؛ ProcureAI يقدم التوصية فقط."})
    return {
        "method": method,
        "method_label_en": method.replace("_", " ").title(),
        "method_label_ar": {"bank_transfer": "تحويل بنكي محلي", "swift_wire": "تحويل دولي (SWIFT)",
                            "letter_of_credit": "خطاب اعتماد"}.get(method, method),
        "terms": terms,
        "currency": invoice.currency,
        "amount_due": str(numbers.money(due)),
        "total": str(total),
        "paid_amount": str(paid),
        "due_date": due_date.isoformat() if due_date else None,
        "days_until_due": days_until_due,
        "priority": priority,
        "cross_border": cross_border,
        "requires_fx": (invoice.currency or tenant.currency) != tenant.currency,
        "requires_human_approval": True,
        "execution": "outside_platform",
        "reasons": reasons,
        "generated_at": now.isoformat(),
    }


async def approve_invoice(session: AsyncSession, tenant: Tenant, invoice: SupplierInvoice, *,
                          approved_by: str | None, comment: str = "", override_reason: str = "") -> dict:
    """Human sign-off. A mismatch needs an explicit, written override."""
    if invoice.status in ALREADY_APPROVED_STATUSES:
        return {"ok": False, "error": f"Invoice {invoice.number} is already {invoice.status}."}
    if invoice.status == "disputed":
        return {"ok": False, "error": "Disputed invoices must be re-matched before approval."}
    match = await compute_match(session, tenant, invoice)
    if not match["can_approve"] and not override_reason.strip():
        return {"ok": False, "error": "The three-way match is not clean; an override reason is required.",
                "match": match}
    po = (await session.execute(
        select(PurchaseOrder).where(PurchaseOrder.id == invoice.po_id))).scalar_one_or_none()
    supplier = (await session.execute(select(Supplier).where(Supplier.id == invoice.supplier_id))).scalar_one_or_none()
    rfq = None
    if po:
        rfq = (await session.execute(select(RFQ).where(RFQ.id == po.rfq_id))).scalar_one_or_none()
    outstanding = numbers.money((numbers.parse_decimal(invoice.total) or Decimal("0"))
                                - (numbers.parse_decimal(invoice.paid_amount) or Decimal("0")))
    suggestion = suggest_payment(invoice=invoice, po=po, supplier=supplier, tenant=tenant, rfq=rfq,
                                 outstanding=outstanding) if po else {}

    invoice.status = "approved"
    invoice.approved_by = approved_by
    invoice.approved_at = datetime.now(UTC)
    invoice.payment_suggestion = suggestion
    notes = invoice.notes or ""
    if override_reason.strip():
        notes = f"{notes}\n[match override] {override_reason.strip()}".strip()
        invoice.notes = notes[:2000]

    # The ledger is the source of truth for what the tenant owes this supplier.
    session.add(SupplierLedgerEntry(
        tenant_id=tenant.id, supplier_id=invoice.supplier_id, po_id=invoice.po_id, invoice_id=invoice.id,
        kind="invoice", entry_date=invoice.approved_at,
        amount=numbers.money(numbers.parse_decimal(invoice.total) or Decimal("0")),
        currency=invoice.currency, reference=invoice.number, created_by=approved_by,
        note=comment[:2000],
    ))
    await session.flush()
    await notify(session, tenant_id=tenant.id, user_id=po.created_by if po else None, kind="invoice_approved",
                 title=f"Invoice {invoice.number} approved for payment",
                 title_ar=f"تم اعتماد الفاتورة {invoice.number} للسداد",
                 body=f"{suggestion.get('method_label_en', '')} · {suggestion.get('terms', '')}".strip(" ·"),
                 link=f"/purchase-orders/{invoice.po_id}")
    await notify_role(session, tenant_id=tenant.id, role_code="finance_approver", kind="payment_due",
                      title=f"Payment due: {invoice.number}", title_ar=f"سداد مستحق: {invoice.number}",
                      body=f"{numbers.fmt_amount(outstanding, invoice.currency, 'en')} via "
                           f"{suggestion.get('method_label_en', 'bank transfer')}",
                      link=f"/purchase-orders/{invoice.po_id}", severity="warning")
    return {"ok": True, "invoice_id": invoice.id, "status": invoice.status, "match": match,
            "payment_suggestion": suggestion, "overridden": bool(override_reason.strip())}


async def record_payment(session: AsyncSession, tenant: Tenant, invoice: SupplierInvoice, *,
                         amount: Decimal | str, method: str, reference: str = "",
                         paid_at: datetime | None = None, actor_id: str | None = None, note: str = "") -> dict:
    """Book a payment that was executed outside the platform (bank, ERP, cheque)."""
    if invoice.status not in PAYABLE_STATUSES:
        return {"ok": False, "error": f"Invoice {invoice.number} is {invoice.status}; approve it before recording a payment."}
    total = numbers.money(numbers.parse_decimal(invoice.total) or Decimal("0"))
    paid = numbers.money(numbers.parse_decimal(invoice.paid_amount) or Decimal("0"))
    outstanding = numbers.money(total - paid)
    value = numbers.money(numbers.parse_decimal(amount) or Decimal("0"))
    if value <= 0:
        return {"ok": False, "error": "A payment amount must be positive."}
    if value > outstanding + Decimal("0.01"):
        return {"ok": False, "error": f"Payment {value} exceeds the outstanding {outstanding}."}
    if not method.strip():
        return {"ok": False, "error": "A payment method is required."}
    if not reference.strip():
        return {"ok": False, "error": "A bank/cheque reference is required for the audit trail."}

    entry = SupplierLedgerEntry(
        tenant_id=tenant.id, supplier_id=invoice.supplier_id, po_id=invoice.po_id, invoice_id=invoice.id,
        kind="payment", entry_date=paid_at or datetime.now(UTC), amount=-value, currency=invoice.currency,
        method=method.strip()[:40], reference=reference.strip()[:120], note=note[:2000], created_by=actor_id,
    )
    session.add(entry)
    invoice.paid_amount = numbers.money(paid + value)
    invoice.status = "paid" if invoice.paid_amount >= total - Decimal("0.01") else "partially_paid"
    await session.flush()
    await notify(session, tenant_id=tenant.id, user_id=invoice.approved_by, kind="payment_recorded",
                 title=f"Payment recorded for {invoice.number}",
                 title_ar=f"تم تسجيل سداد للفاتورة {invoice.number}",
                 body=f"{numbers.fmt_amount(value, invoice.currency, 'en')} · ref {entry.reference}",
                 link=f"/purchase-orders/{invoice.po_id}")
    return {"ok": True, "ledger_entry_id": entry.id, "status": invoice.status,
            "paid_amount": str(invoice.paid_amount), "outstanding": str(numbers.money(total - invoice.paid_amount))}


async def supplier_balance(session: AsyncSession, tenant_id: str, supplier_id: str, *,
                           now: datetime | None = None) -> dict:
    """Outstanding balance, ageing and the raw ledger for one supplier."""
    now = now or datetime.now(UTC)
    entries = list((await session.execute(
        select(SupplierLedgerEntry).where(SupplierLedgerEntry.tenant_id == tenant_id,
                                          SupplierLedgerEntry.supplier_id == supplier_id)
        .order_by(SupplierLedgerEntry.entry_date.desc())
    )).scalars().all())
    invoices = list((await session.execute(
        select(SupplierInvoice).where(SupplierInvoice.tenant_id == tenant_id,
                                      SupplierInvoice.supplier_id == supplier_id)
        .order_by(SupplierInvoice.created_at.desc())
    )).scalars().all())
    invoiced = sum((numbers.parse_decimal(row.amount) or Decimal("0") for row in entries if row.amount > 0),
                   Decimal("0"))
    settled = sum((-numbers.parse_decimal(row.amount) or Decimal("0") for row in entries if row.amount < 0),
                  Decimal("0"))
    outstanding = numbers.money(invoiced - settled)
    overdue = Decimal("0")
    open_invoices = 0
    for invoice in invoices:
        total = numbers.parse_decimal(invoice.total) or Decimal("0")
        paid = numbers.parse_decimal(invoice.paid_amount) or Decimal("0")
        remaining = total - paid
        if remaining <= Decimal("0.01"):
            continue
        if invoice.status in PAYABLE_STATUSES:
            open_invoices += 1
            if invoice.due_date and invoice.due_date < now:
                overdue += remaining
    currency = invoices[0].currency if invoices else ""
    return {
        "supplier_id": supplier_id,
        "invoiced_total": str(numbers.money(invoiced)),
        "paid_total": str(numbers.money(settled)),
        "outstanding": str(outstanding),
        "overdue": str(numbers.money(overdue)),
        "open_invoices": open_invoices,
        "currency": currency,
        "last_payment_at": next((row.entry_date for row in entries if row.kind == "payment"), None),
        "entries": [{
            "id": row.id, "kind": row.kind, "amount": str(row.amount), "currency": row.currency,
            "entry_date": row.entry_date, "method": row.method, "reference": row.reference,
            "invoice_id": row.invoice_id, "po_id": row.po_id, "note": row.note,
        } for row in entries[:50]],
    }


async def supplier_balances(session: AsyncSession, tenant: Tenant) -> list[dict]:
    """Every supplier with ledger movement, most outstanding first."""
    rows = (await session.execute(
        select(SupplierLedgerEntry.supplier_id,
               func.sum(SupplierLedgerEntry.amount).label("balance"))
        .where(SupplierLedgerEntry.tenant_id == tenant.id)
        .group_by(SupplierLedgerEntry.supplier_id)
    )).all()
    out: list[dict] = []
    for supplier_id, balance in rows:
        supplier = (await session.execute(select(Supplier).where(Supplier.id == supplier_id))).scalar_one_or_none()
        detail = await supplier_balance(session, tenant.id, supplier_id)
        out.append({
            "supplier_id": supplier_id,
            "supplier_name": supplier.name if supplier else supplier_id,
            "supplier_name_ar": (supplier.name_ar if supplier else "") or "",
            "status": supplier.status if supplier else "",
            "balance": str(numbers.money(balance or 0)),
            "outstanding": detail["outstanding"], "overdue": detail["overdue"],
            "open_invoices": detail["open_invoices"], "currency": detail["currency"],
            "last_payment_at": detail["last_payment_at"],
        })
    out.sort(key=lambda row: Decimal(row["outstanding"]), reverse=True)
    return out


async def list_invoices(session: AsyncSession, tenant_id: str, po_id: str) -> list[SupplierInvoice]:
    return list((await session.execute(
        select(SupplierInvoice).where(SupplierInvoice.tenant_id == tenant_id, SupplierInvoice.po_id == po_id)
        .order_by(SupplierInvoice.created_at)
    )).scalars().all())


def invoice_view(invoice: SupplierInvoice) -> dict:
    return {
        "id": invoice.id, "number": invoice.number, "po_id": invoice.po_id,
        "supplier_id": invoice.supplier_id, "status": invoice.status, "currency": invoice.currency,
        "subtotal": str(invoice.subtotal), "tax_amount": str(invoice.tax_amount), "total": str(invoice.total),
        "paid_amount": str(invoice.paid_amount),
        "outstanding": str(numbers.money((numbers.parse_decimal(invoice.total) or Decimal("0"))
                                         - (numbers.parse_decimal(invoice.paid_amount) or Decimal("0")))),
        "invoice_date": invoice.invoice_date, "due_date": invoice.due_date, "lines": invoice.lines or [],
        "match_result": invoice.match_result or {}, "payment_suggestion": invoice.payment_suggestion or {},
        "approved_by": invoice.approved_by, "approved_at": invoice.approved_at, "notes": invoice.notes,
    }
