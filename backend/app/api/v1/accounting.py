"""Accounting endpoints: chart of accounts, cost centres, journal entries and cost reports.

Every route is human-only. The LLM has no tool that can draft, post or reverse an entry, and the
posting routes require ``accounting:post`` — a permission no purchasing role holds. Document-derived
postings additionally require the source invoice to be approved *by a human* (enforced in the
service layer, not just here).
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import Context, require
from app.core.audit import audit
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.db.models.entities import (
    AuditPackage,
    CostCenter,
    GLAccount,
    JournalEntry,
    PurchaseOrder,
    Supplier,
    SupplierInvoice,
)
from app.services import accounting, audit_package, invoicing, numbers, p2p

log = logging.getLogger("procureai.api.accounting")
router = APIRouter(tags=["accounting"])


def _accounting_error(exc: accounting.AccountingError) -> ValidationFailed:
    return ValidationFailed(str(exc), extra={"code": exc.code, **exc.extra})


async def _tenant_po(ctx: Context, po_id: str) -> PurchaseOrder:
    po = (await ctx.session.execute(
        select(PurchaseOrder).where(PurchaseOrder.id == po_id, PurchaseOrder.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not po:
        raise NotFound("Purchase order not found.")
    return po


async def _tenant_entry(ctx: Context, entry_id: str) -> JournalEntry:
    entry = (await ctx.session.execute(
        select(JournalEntry).where(JournalEntry.id == entry_id, JournalEntry.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not entry:
        raise NotFound("Journal entry not found.")
    return entry


async def _tenant_invoice(ctx: Context, invoice_id: str) -> SupplierInvoice:
    invoice = (await ctx.session.execute(
        select(SupplierInvoice).where(SupplierInvoice.id == invoice_id,
                                      SupplierInvoice.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not invoice:
        raise NotFound("Invoice not found.")
    return invoice


# ------------------------------------------------------------------ chart of accounts
class AccountIn(BaseModel):
    code: str = Field(min_length=3, max_length=20)
    name: str = Field(min_length=2, max_length=160)
    name_ar: str = ""
    kind: str = Field(pattern="^(asset|liability|equity|revenue|expense)$")
    normal_balance: str | None = Field(default=None, pattern="^(debit|credit)$")
    parent_code: str | None = None
    is_control: bool = False
    description: str = ""


@router.get("/accounting/accounts", summary="Chart of accounts")
async def list_accounts(ctx: Context = Depends(require("accounting:read")),
                        kind: str | None = None, include_inactive: bool = False):
    rows = await accounting.list_accounts(ctx.session, ctx.tenant_id, kind=kind,
                                          active_only=not include_inactive)
    return {"items": [{
        "code": row.code, "name": row.name, "name_ar": row.name_ar, "kind": row.kind,
        "normal_balance": row.normal_balance, "parent_code": row.parent_code,
        "is_control": row.is_control, "is_active": row.is_active, "description": row.description,
    } for row in rows], "total": len(rows)}


@router.post("/accounting/accounts", status_code=201, summary="Add a GL account")
async def create_account(payload: AccountIn, ctx: Context = Depends(require("accounting:write"))):
    try:
        account = await accounting.create_account(
            ctx.session, ctx.tenant, code=payload.code, name=payload.name, name_ar=payload.name_ar,
            kind=payload.kind, normal_balance=payload.normal_balance, parent_code=payload.parent_code,
            is_control=payload.is_control, description=payload.description, created_by=ctx.user_id,
        )
    except accounting.AccountingError as exc:
        raise _accounting_error(exc) from exc
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="gl_account.created", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="gl_account", entity_id=account.id,
                after={"code": account.code, "kind": account.kind}, ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return {"id": account.id, "code": account.code, "name": account.name, "kind": account.kind}


class AccountPatch(BaseModel):
    name: str | None = None
    name_ar: str | None = None
    is_active: bool | None = None
    description: str | None = None


@router.patch("/accounting/accounts/{code}", summary="Rename or deactivate a GL account")
async def patch_account(code: str, payload: AccountPatch, ctx: Context = Depends(require("accounting:write"))):
    account = (await ctx.session.execute(
        select(GLAccount).where(GLAccount.tenant_id == ctx.tenant_id, GLAccount.code == code)
    )).scalar_one_or_none()
    if not account:
        raise NotFound("Account not found.")
    before = {"name": account.name, "is_active": account.is_active}
    try:
        await accounting.update_account(ctx.session, ctx.tenant, account, name=payload.name,
                                        name_ar=payload.name_ar, is_active=payload.is_active,
                                        description=payload.description)
    except accounting.AccountingError as exc:
        raise _accounting_error(exc) from exc
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="gl_account.updated", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="gl_account", entity_id=account.id,
                before=before, after={"name": account.name, "is_active": account.is_active},
                ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return {"code": account.code, "name": account.name, "is_active": account.is_active}


# ------------------------------------------------------------------ cost centres
class CostCenterIn(BaseModel):
    code: str = Field(min_length=2, max_length=20)
    name: str = Field(min_length=2, max_length=160)
    name_ar: str = ""
    plant_id: str | None = None
    description: str = ""


@router.get("/accounting/cost-centers", summary="Cost centres (analytical dimension)")
async def list_cost_centers(ctx: Context = Depends(require("cost:read")), include_inactive: bool = False):
    rows = await accounting.list_cost_centers(ctx.session, ctx.tenant_id, active_only=not include_inactive)
    return {"items": [{
        "code": row.code, "name": row.name, "name_ar": row.name_ar, "plant_id": row.plant_id,
        "manager_id": row.manager_id, "is_active": row.is_active, "description": row.description,
    } for row in rows], "total": len(rows)}


@router.post("/accounting/cost-centers", status_code=201, summary="Create a cost centre")
async def create_cost_center(payload: CostCenterIn, ctx: Context = Depends(require("cost:write"))):
    try:
        center = await accounting.create_cost_center(
            ctx.session, ctx.tenant, code=payload.code, name=payload.name, name_ar=payload.name_ar,
            plant_id=payload.plant_id, description=payload.description, created_by=ctx.user_id,
        )
    except accounting.AccountingError as exc:
        raise _accounting_error(exc) from exc
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="cost_center.created", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="cost_center", entity_id=center.id,
                after={"code": center.code}, ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return {"code": center.code, "name": center.name, "is_active": center.is_active}


class CostCenterPatch(BaseModel):
    name: str | None = None
    name_ar: str | None = None
    is_active: bool | None = None
    manager_id: str | None = None
    description: str | None = None


@router.patch("/accounting/cost-centers/{code}", summary="Update a cost centre")
async def patch_cost_center(code: str, payload: CostCenterPatch,
                            ctx: Context = Depends(require("cost:write"))):
    center = (await ctx.session.execute(
        select(CostCenter).where(CostCenter.tenant_id == ctx.tenant_id, CostCenter.code == code)
    )).scalar_one_or_none()
    if not center:
        raise NotFound("Cost centre not found.")
    await accounting.update_cost_center(ctx.session, center, name=payload.name, name_ar=payload.name_ar,
                                        is_active=payload.is_active, manager_id=payload.manager_id,
                                        description=payload.description)
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="cost_center.updated", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="cost_center", entity_id=center.id,
                after={"code": center.code, "is_active": center.is_active}, ip=ctx.ip,
                request_id=ctx.request_id)
    await ctx.session.commit()
    return {"code": center.code, "name": center.name, "is_active": center.is_active}


# ------------------------------------------------------------------ journal entries
class JournalLineIn(BaseModel):
    account_code: str
    description: str = ""
    debit: str = "0"
    credit: str = "0"
    cost_center_code: str | None = None
    unit_number: str | None = None
    item_id: str | None = None


class JournalIn(BaseModel):
    entry_date: datetime | None = None
    memo: str = ""
    memo_ar: str = ""
    currency: str = ""
    lines: list[JournalLineIn] = Field(min_length=2)
    po_id: str | None = None
    supplier_id: str | None = None


@router.get("/accounting/journal-entries", summary="Journal entries with filters")
async def list_entries(ctx: Context = Depends(require("accounting:read")), status: str | None = None,
                       po_id: str | None = None, invoice_id: str | None = None,
                       source_type: str | None = None, limit: int = Query(default=100, le=500)):
    rows = await accounting.list_entries(ctx.session, ctx.tenant_id, status=status, po_id=po_id,
                                         invoice_id=invoice_id, source_type=source_type, limit=limit)
    names = {row.code: row.name for row in await accounting.list_accounts(
        ctx.session, ctx.tenant_id, active_only=False)}
    posted = sum(1 for row in rows if row.status == "posted")
    debits = sum((Decimal(str(row.total_debit)) for row in rows if row.status == "posted"), Decimal("0"))
    return {"items": [accounting.entry_view(row, account_names=names) for row in rows],
            "total": len(rows), "posted": posted, "posted_total": str(numbers.money(debits))}


@router.post("/accounting/journal-entries", status_code=201, summary="Draft a manual journal entry")
async def create_entry(payload: JournalIn, ctx: Context = Depends(require("accounting:write"))):
    supplier = None
    if payload.supplier_id:
        supplier = (await ctx.session.execute(
            select(Supplier).where(Supplier.id == payload.supplier_id, Supplier.tenant_id == ctx.tenant_id)
        )).scalar_one_or_none()
        if not supplier:
            raise NotFound("Supplier not found.")
    try:
        entry = await accounting.create_entry(
            ctx.session, ctx.tenant, entry_date=payload.entry_date, currency=payload.currency or None,
            memo=payload.memo or "Manual journal entry", memo_ar=payload.memo_ar or "قيد يدوي",
            lines=[line.model_dump() for line in payload.lines], source_type="manual",
            po_id=payload.po_id, supplier_id=payload.supplier_id, created_by=ctx.user_id,
        )
    except accounting.AccountingError as exc:
        raise _accounting_error(exc) from exc
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="journal_entry.drafted", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="journal_entry", entity_id=entry.id,
                after={"number": entry.number, "total": str(entry.total_debit)},
                ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return accounting.entry_view(entry)


@router.get("/accounting/journal-entries/{entry_id}", summary="Journal entry detail")
async def get_entry(entry_id: str, ctx: Context = Depends(require("accounting:read"))):
    entry = await _tenant_entry(ctx, entry_id)
    names = {row.code: row.name for row in await accounting.list_accounts(
        ctx.session, ctx.tenant_id, active_only=False)}
    return accounting.entry_view(entry, account_names=names)


@router.post("/accounting/journal-entries/{entry_id}/post", summary="Post a draft to the ledger")
async def post_entry(entry_id: str, ctx: Context = Depends(require("accounting:post"))):
    entry = await _tenant_entry(ctx, entry_id)
    # Automatic entries are prepared by the system from documents; only a manual entry typed by a
    # person needs a second pair of eyes (segregation of duties on the risky path).
    allow_own = entry.source_type != "manual" or ctx.user.is_superadmin
    try:
        result = await accounting.post_entry(ctx.session, ctx.tenant, entry, posted_by=ctx.user_id,
                                             allow_own_draft=allow_own)
    except accounting.AccountingError as exc:
        raise _accounting_error(exc) from exc
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="journal_entry.posted", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="journal_entry", entity_id=entry.id,
                after={"number": entry.number, "total_debit": str(entry.total_debit)},
                ip=ctx.ip, request_id=ctx.request_id)
    if entry.po_id:
        po = (await ctx.session.execute(
            select(PurchaseOrder).where(PurchaseOrder.id == entry.po_id))).scalar_one_or_none()
        if po:
            await p2p.touch(ctx.session, ctx.tenant_id, po, actor_id=ctx.user_id, source="journal_posted")
    await ctx.session.commit()
    return {**result, "entry": accounting.entry_view(entry)}


class ReversalIn(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


@router.post("/accounting/journal-entries/{entry_id}/reverse", summary="Reverse a posted entry")
async def reverse_entry(entry_id: str, payload: ReversalIn,
                        ctx: Context = Depends(require("accounting:reverse"))):
    entry = await _tenant_entry(ctx, entry_id)
    try:
        reversal = await accounting.reverse_entry(ctx.session, ctx.tenant, entry, reversed_by=ctx.user_id,
                                                  reason=payload.reason)
    except accounting.AccountingError as exc:
        raise _accounting_error(exc) from exc
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="journal_entry.reversed", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="journal_entry", entity_id=entry.id,
                before={"number": entry.number}, after={"reversal": reversal.number,
                                                        "reason": payload.reason[:300]},
                ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return {"ok": True, "original": entry.number, "reversal_id": reversal.id,
            "reversal_number": reversal.number, "status": entry.status}


# ------------------------------------------------------------------ posting from documents
class AllocationIn(BaseModel):
    line_no: int = Field(ge=1)
    cost_center_code: str | None = None
    unit_number: str | None = None
    item_id: str | None = None


class PostingIn(BaseModel):
    allocations: list[AllocationIn] = Field(default_factory=list)
    entry_date: datetime | None = None


@router.post("/purchase-orders/{po_id}/accounting/accrual", status_code=201,
             summary="Draft the GR/IR accrual for a confirmed receipt")
async def draft_accrual(po_id: str, payload: PostingIn, ctx: Context = Depends(require("accounting:write"))):
    po = await _tenant_po(ctx, po_id)
    try:
        entry = await accounting.draft_receipt_accrual(
            ctx.session, ctx.tenant, po,
            allocations=[row.model_dump() for row in payload.allocations],
            created_by=ctx.user_id, entry_date=payload.entry_date,
        )
    except accounting.AccountingError as exc:
        raise _accounting_error(exc) from exc
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="journal_entry.drafted", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="purchase_order", entity_id=po.id,
                after={"number": entry.number, "kind": "receipt_accrual"}, ip=ctx.ip,
                request_id=ctx.request_id)
    await p2p.touch(ctx.session, ctx.tenant_id, po, actor_id=ctx.user_id, source="accrual_drafted")
    await ctx.session.commit()
    return accounting.entry_view(entry)


@router.post("/invoices/{invoice_id}/posting", status_code=201,
             summary="Draft the invoice posting (GR/IR clearing, VAT, variance, payable)")
async def draft_invoice_posting(invoice_id: str, payload: PostingIn,
                               ctx: Context = Depends(require("accounting:write"))):
    invoice = await _tenant_invoice(ctx, invoice_id)
    po = await _tenant_po(ctx, invoice.po_id)
    try:
        entry = await accounting.draft_invoice_posting(
            ctx.session, ctx.tenant, po, invoice,
            allocations=[row.model_dump() for row in payload.allocations],
            created_by=ctx.user_id, entry_date=payload.entry_date,
        )
    except accounting.AccountingError as exc:
        raise _accounting_error(exc) from exc
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="journal_entry.drafted", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="supplier_invoice", entity_id=invoice.id,
                after={"number": entry.number, "kind": "invoice"}, ip=ctx.ip, request_id=ctx.request_id)
    await p2p.touch(ctx.session, ctx.tenant_id, po, actor_id=ctx.user_id, source="invoice_posting_drafted")
    await ctx.session.commit()
    return accounting.entry_view(entry)


@router.post("/invoices/{invoice_id}/payment-posting", status_code=201,
             summary="Draft the AP settlement for a recorded payment")
async def draft_payment_posting(invoice_id: str, payload: PostingIn,
                               ctx: Context = Depends(require("accounting:write"))):
    invoice = await _tenant_invoice(ctx, invoice_id)
    po = await _tenant_po(ctx, invoice.po_id)
    try:
        entry = await accounting.draft_payment_posting(
            ctx.session, ctx.tenant, po, invoice, created_by=ctx.user_id, entry_date=payload.entry_date,
        )
    except accounting.AccountingError as exc:
        raise _accounting_error(exc) from exc
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="journal_entry.drafted", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="supplier_invoice", entity_id=invoice.id,
                after={"number": entry.number, "kind": "payment"}, ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return accounting.entry_view(entry)


@router.get("/purchase-orders/{po_id}/accounting", summary="Postings and cost allocation for an order")
async def po_accounting(po_id: str, ctx: Context = Depends(require("accounting:read"))):
    po = await _tenant_po(ctx, po_id)
    entries = await accounting.postings_for_po(ctx.session, ctx.tenant_id, po.id)
    names = {row.code: row.name for row in await accounting.list_accounts(
        ctx.session, ctx.tenant_id, active_only=False)}
    centres = {row.code: row for row in await accounting.list_cost_centers(
        ctx.session, ctx.tenant_id, active_only=False)}
    cost = await accounting.cost_report(ctx.session, ctx.tenant_id, group_by="cost_center", po_id=po.id)
    for row in cost["items"]:
        centre = centres.get(row["key"])
        if centre:
            row["label"], row["label_ar"] = centre.name, centre.name_ar
    return {
        "po_id": po.id, "currency": po.currency,
        "entries": [accounting.entry_view(row, account_names=names) for row in entries],
        "draft_count": sum(1 for row in entries if row.status == "draft"),
        "posted_count": sum(1 for row in entries if row.status == "posted"),
        "cost_by_center": cost,
        "accounts": [{"code": row.code, "name": row.name, "name_ar": row.name_ar, "kind": row.kind}
                     for row in await accounting.list_accounts(ctx.session, ctx.tenant_id)],
    }


# ------------------------------------------------------------------ reports
@router.get("/accounting/trial-balance", summary="Trial balance over posted entries")
async def trial_balance(ctx: Context = Depends(require("accounting:read"))):
    return await accounting.trial_balance(ctx.session, ctx.tenant_id, currency=ctx.tenant.currency)


@router.get("/accounting/cost-report", summary="Posted cost by cost centre, item, unit or supplier")
async def cost_report(ctx: Context = Depends(require("cost:read")),
                      group_by: str = Query(default="cost_center"), po_id: str | None = None):
    try:
        report = await accounting.cost_report(ctx.session, ctx.tenant_id, group_by=group_by, po_id=po_id)
    except accounting.AccountingError as exc:
        raise _accounting_error(exc) from exc
    centres = {row.code: row for row in await accounting.list_cost_centers(
        ctx.session, ctx.tenant_id, active_only=False)}
    for row in report["items"]:
        centre = centres.get(row["key"])
        if centre:
            row["label"], row["label_ar"] = centre.name, centre.name_ar
    return report


@router.get("/accounting/supplier-account/{supplier_id}", summary="Supplier sub-ledger with journal support")
async def supplier_account(supplier_id: str, ctx: Context = Depends(require("ledger:read"))):
    supplier = (await ctx.session.execute(
        select(Supplier).where(Supplier.id == supplier_id, Supplier.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not supplier:
        raise NotFound("Supplier not found.")
    account = await accounting.supplier_account(ctx.session, ctx.tenant_id, supplier_id)
    balance = await invoicing.supplier_balance(ctx.session, ctx.tenant_id, supplier_id)
    return {**account, "balance": balance}


# ------------------------------------------------------------------ auditor document package
class PackageIn(BaseModel):
    invoice_id: str | None = None
    notes: str = ""


@router.get("/purchase-orders/{po_id}/audit-package", summary="Current auditor package (if any)")
async def get_package(po_id: str, ctx: Context = Depends(require("audit_package:read"))):
    po = await _tenant_po(ctx, po_id)
    package = await audit_package.latest(ctx.session, ctx.tenant_id, po.id)
    if not package:
        return {"po_id": po.id, "package": None, "components": [], "status": "not_assembled",
                "can_post_to_accounts": False,
                "hint_en": "Assemble the package once the requester has confirmed the delivery.",
                "hint_ar": "جهّز الحزمة بعد تأكيد طالب الشراء للاستلام."}
    # One stable envelope: the UI reads ``package`` whatever the state of the evidence.
    view = audit_package.package_view(package)
    return {"po_id": po.id, "package": view, "status": view["status"],
            "can_post_to_accounts": view["can_post_to_accounts"],
            "components": view["components"],
            "verification": await audit_package.verify_manifest(package)}


@router.post("/purchase-orders/{po_id}/audit-package", status_code=201,
             summary="Assemble and seal the auditor package")
async def build_package(po_id: str, payload: PackageIn,
                        ctx: Context = Depends(require("audit_package:read"))):
    po = await _tenant_po(ctx, po_id)
    contract = await p2p.stage_summary(ctx.session, ctx.tenant_id, po)
    data = await p2p.evidence(ctx.session, po)
    if not any(row.status == "confirmed" for row in data["receipts"]):
        raise ValidationFailed(
            "The package needs a goods receipt confirmed by the requester.",
            extra={"code": "no_confirmed_receipt"},
        )
    try:
        package = await audit_package.assemble(ctx.session, ctx.tenant, po, generated_by=ctx.user_id,
                                               invoice_id=payload.invoice_id, notes=payload.notes)
    except ValueError as exc:
        raise Conflict(str(exc)) from exc
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="audit_package.assembled", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="purchase_order", entity_id=po.id,
                after={"code": package.code, "components": len(package.components or []),
                       "manifest": package.manifest_hash},
                ip=ctx.ip, request_id=ctx.request_id)
    await p2p.touch(ctx.session, ctx.tenant_id, po, actor_id=ctx.user_id, source="audit_package_assembled")
    await ctx.session.commit()
    view = audit_package.package_view(package)
    return {**view, "stage": contract["stage"]}


class PackageReviewIn(BaseModel):
    decision: str = Field(pattern="^(accepted|queried)$")
    comment: str = ""


@router.post("/audit-packages/{package_id}/review", summary="Auditor accepts the evidence or raises a query")
async def review_package(package_id: str, payload: PackageReviewIn,
                         ctx: Context = Depends(require("audit_package:review"))):
    package = (await ctx.session.execute(
        select(AuditPackage).where(AuditPackage.id == package_id, AuditPackage.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not package:
        raise NotFound("Audit package not found.")
    result = await audit_package.review(ctx.session, ctx.tenant, package, reviewer_id=ctx.user_id,
                                        decision=payload.decision, comment=payload.comment)
    if not result.get("ok"):
        raise Conflict(result.get("error", "Cannot review this package."))
    await audit(ctx.session, tenant_id=ctx.tenant_id, action=f"audit_package.{payload.decision}",
                actor_id=ctx.user_id, actor_label=ctx.user.email, entity_type="audit_package",
                entity_id=package.id, after={"status": package.status, "comment": payload.comment[:300]},
                ip=ctx.ip, request_id=ctx.request_id)
    po = (await ctx.session.execute(
        select(PurchaseOrder).where(PurchaseOrder.id == package.po_id))).scalar_one_or_none()
    if po:
        await p2p.touch(ctx.session, ctx.tenant_id, po, actor_id=ctx.user_id, source="audit_review")
    await ctx.session.commit()
    return {**result, "package": audit_package.package_view(package)}


@router.get("/purchase-orders/{po_id}/lifecycle", summary="Eleven-stage purchase-to-pay status")
async def po_lifecycle(po_id: str, ctx: Context = Depends(require("po:read"))):
    po = await _tenant_po(ctx, po_id)
    lifecycle = await p2p.lifecycle(ctx.session, ctx.tenant_id, po, record=True, actor_id=ctx.user_id,
                                    source="read")
    await ctx.session.commit()
    return lifecycle


@router.get("/accounting/coverage", summary="Is the tenant's accounting set up?")
async def coverage(ctx: Context = Depends(require("accounting:read"))):
    """Answers the question the UI needs first: are there accounts and cost centres to post to?"""
    accounts = await accounting.list_accounts(ctx.session, ctx.tenant_id, active_only=False)
    centres = await accounting.list_cost_centers(ctx.session, ctx.tenant_id, active_only=False)
    entries = (await ctx.session.execute(
        select(func.count()).select_from(JournalEntry).where(JournalEntry.tenant_id == ctx.tenant_id)
    )).scalar_one()
    control_codes = {accounting.ACCOUNT_INVENTORY, accounting.ACCOUNT_GRIR, accounting.ACCOUNT_AP,
                     accounting.ACCOUNT_VAT_INPUT, accounting.ACCOUNT_BANK}
    missing = sorted(control_codes - {row.code for row in accounts})
    return {
        "accounts": len(accounts), "cost_centers": len(centres), "journal_entries": int(entries),
        "missing_control_accounts": missing,
        "ready": not missing and bool(centres),
        "currency": ctx.tenant.currency,
    }
