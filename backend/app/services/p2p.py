"""The purchase-to-pay stage machine (phase 2, requirement 1).

Eleven stages, derived from documents rather than from a status field somebody can set:

    requested -> in_procurement -> rfq_sent -> offers_received -> under_approval
    -> po_issued -> pending_receipt -> received_by_warehouse -> confirmed_by_requester
    -> ready_for_audit -> posted_to_accounts

Deriving the stage from the evidence means the rail on the order page can never disagree with the
documents. ``record_stage`` additionally writes the first time each stage was reached into
``p2p_stage_event`` (one row per stage, append-only, with actor and evidence), so the transition
history survives later changes to the documents and is what the audit package and the timeline read.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.entities import (
    RFQ,
    Approval,
    AuditPackage,
    GoodsReceipt,
    JournalEntry,
    Offer,
    P2PStageEvent,
    PurchaseOrder,
    PurchaseRequisition,
    SupplierInvoice,
)

log = logging.getLogger("procureai.p2p")

STAGES: tuple[tuple[str, str, str], ...] = (
    ("requested", "Requested", "تم الطلب"),
    ("in_procurement", "In procurement", "قيد المعالجة بالمشتريات"),
    ("rfq_sent", "RFQ sent", "أُرسل طلب عرض السعر"),
    ("offers_received", "Offers received", "تم استلام العروض"),
    ("under_approval", "Under approval", "قيد الاعتماد"),
    ("po_issued", "PO issued", "صدر أمر الشراء"),
    ("pending_receipt", "Pending receipt", "بانتظار الاستلام"),
    ("received_by_warehouse", "Received by warehouse", "استُلم بالمخزن"),
    ("confirmed_by_requester", "Confirmed by requester", "أكده طالب الشراء"),
    ("ready_for_audit", "Ready for audit", "جاهز للمراجعة"),
    ("posted_to_accounts", "Posted to accounts", "مُرحّل للحسابات"),
)

#: The stages that only exist because there is an accounting module behind them. With
#: `ACCOUNTING_ENABLED` off they are removed from the rail entirely, so a pilot order's lifecycle ends
#: at "confirmed by requester" and never promises a review nobody can perform. The rows already
#: written to `p2p_stage_event` are kept: the transition history stays append-only.
ACCOUNTING_STAGES: tuple[str, ...] = ("ready_for_audit", "posted_to_accounts")

STAGE_INDEX = {code: index for index, (code, _, _) in enumerate(STAGES)}
STAGE_LABELS = {code: (en, ar) for code, en, ar in STAGES}

# The stage the flow is currently waiting on, phrased for the UI ("what do I do next?").
NEXT_ACTION = {
    "requested": ("Route the request to procurement", "حوّل الطلب إلى فريق المشتريات"),
    "in_procurement": ("Issue the RFQ to suppliers", "أرسل طلب عرض السعر للموردين"),
    "rfq_sent": ("Collect supplier offers", "اجمع عروض الموردين"),
    "offers_received": ("Compare offers and submit for approval", "قارن العروض وقدّمها للاعتماد"),
    "under_approval": ("Approve the award", "اعتمد الترسية"),
    "po_issued": ("Dispatch the purchase order", "أرسل أمر الشراء للمورد"),
    "pending_receipt": ("Record the goods receipt at the warehouse", "سجّل الاستلام بالمخزن"),
    "received_by_warehouse": ("Confirm the delivery as the requester", "أكد الاستلام كطالب شراء"),
    "confirmed_by_requester": ("Assemble the document package for the auditor", "جهّز حزمة المستندات للمراجع"),
    "ready_for_audit": ("Review the package and post the invoice to the accounts",
                        "راجع الحزمة ورحّل الفاتورة للحسابات"),
    "posted_to_accounts": ("Audit the posting and settle the supplier", "راجع الترحيل وسوّ المورد"),
}


def stages() -> tuple[tuple[str, str, str], ...]:
    """The stages this deployment shows: all eleven, or nine when accounting is switched off."""
    if settings.accounting_enabled:
        return STAGES
    return tuple(row for row in STAGES if row[0] not in ACCOUNTING_STAGES)


def stage_index(code: str) -> int:
    return {row[0]: index for index, row in enumerate(stages())}.get(code, -1)


async def evidence(session: AsyncSession, po: PurchaseOrder) -> dict:
    """Everything the stage derivation needs, in one round of queries."""
    rfq = (await session.execute(select(RFQ).where(RFQ.id == po.rfq_id))).scalar_one_or_none()
    pr = None
    if rfq and rfq.pr_id:
        pr = (await session.execute(
            select(PurchaseRequisition).where(PurchaseRequisition.id == rfq.pr_id))).scalar_one_or_none()
    offers = list((await session.execute(
        select(Offer).where(Offer.rfq_id == po.rfq_id))).scalars().all())
    approvals = list((await session.execute(
        select(Approval).where(Approval.rfq_id == po.rfq_id).order_by(Approval.step_no))).scalars().all())
    receipts = list((await session.execute(
        select(GoodsReceipt).where(GoodsReceipt.po_id == po.id).order_by(GoodsReceipt.created_at)
    )).scalars().all())
    invoices = list((await session.execute(
        select(SupplierInvoice).where(SupplierInvoice.po_id == po.id).order_by(SupplierInvoice.created_at)
    )).scalars().all())
    package = None
    posted = None
    if settings.accounting_enabled:
        package = (await session.execute(
            select(AuditPackage).where(AuditPackage.po_id == po.id)
            .order_by(AuditPackage.created_at.desc())
        )).scalars().first()
        posted = (await session.execute(
            select(JournalEntry.id).where(JournalEntry.po_id == po.id,
                                          JournalEntry.source_type == "invoice",
                                          JournalEntry.status == "posted").limit(1)
        )).scalar_one_or_none()
    return {"pr": pr, "rfq": rfq, "offers": offers, "approvals": approvals, "receipts": receipts,
            "invoices": invoices, "package": package, "posted_invoice_entry": posted}


def derive_stage(data: dict, po: PurchaseOrder | None = None) -> str:
    """The furthest stage whose evidence actually exists."""
    reached = "requested"
    if data["pr"] is not None:
        pr = data["pr"]
        if pr.status in {"in_procurement", "rfq_created"} or pr.assigned_to:
            reached = "in_procurement"
    if data["rfq"] is not None:
        reached = "in_procurement" if reached == "requested" else reached
        if data["rfq"].sent_at:
            reached = "rfq_sent"
        if data["offers"]:
            reached = "offers_received"
    if data["approvals"]:
        pending = any(row.status in {"pending", "queued", "revision_requested"} for row in data["approvals"])
        all_approved = all(row.status == "approved" for row in data["approvals"]) and data["approvals"]
        if pending and not all_approved:
            reached = "under_approval"
    if po is not None:
        reached = "po_issued"
        if po.status in {"in_transit", "partially_received", "received", "confirmed", "closed"}:
            # Dispatched by the buyer: the flow is now waiting on the warehouse.
            reached = "pending_receipt"
        live = [row for row in data["receipts"] if row.status in {"received", "confirmed"}]
        confirmed = [row for row in live if row.status == "confirmed"]
        if live:
            reached = "received_by_warehouse"
        if confirmed:
            reached = "confirmed_by_requester"
        if data["package"] is not None and "ready_for_audit" in {row[0] for row in stages()}:
            reached = "ready_for_audit"
        if data["posted_invoice_entry"] and "posted_to_accounts" in {row[0] for row in stages()}:
            reached = "posted_to_accounts"
    return reached


async def lifecycle(session: AsyncSession, tenant_id: str, po: PurchaseOrder, *,
                    record: bool = False, actor_id: str | None = None, source: str = "") -> dict:
    """The eleven stages with done/current/pending, the recorded history, and the next action."""
    data = await evidence(session, po)
    current = derive_stage(data, po)
    if record:
        await record_stage(session, tenant_id, po, current, actor_id=actor_id, source=source)
    events = {row.stage: row for row in (await session.execute(
        select(P2PStageEvent).where(P2PStageEvent.tenant_id == tenant_id, P2PStageEvent.po_id == po.id)
    )).scalars().all()}
    visible = stages()
    current_index = stage_index(current)
    if current_index < 0:
        # A stage recorded by an earlier deployment that the rail no longer shows: clamp to the last
        # visible one so the summary never claims a step nobody can see.
        current = visible[-1][0]
        current_index = len(visible) - 1
    rows = []
    for code, label_en, label_ar in visible:
        index = stage_index(code)
        event = events.get(code)
        rows.append({
            "code": code, "label_en": label_en, "label_ar": label_ar,
            "order": index,
            "state": "done" if index < current_index else ("current" if index == current_index else "pending"),
            "reached_at": event.reached_at if event else None,
            "actor_id": event.actor_id if event else None,
        })
    action_en, action_ar = NEXT_ACTION.get(current, ("", ""))
    return {
        "po_id": po.id, "current": current,
        "current_label_en": STAGE_LABELS[current][0], "current_label_ar": STAGE_LABELS[current][1],
        "next_action_en": action_en, "next_action_ar": action_ar,
        "stages": rows,
        "history": [{"stage": row.stage, "reached_at": row.reached_at, "actor_id": row.actor_id,
                     "source": row.source, "evidence": row.evidence or {}} for row in
                    sorted(events.values(), key=lambda row: row.reached_at)],
    }


async def record_stage(session: AsyncSession, tenant_id: str, po: PurchaseOrder, stage: str, *,
                       actor_id: str | None = None, source: str = "",
                       payload: dict | None = None) -> None:
    """Write every stage up to ``stage`` once. Idempotent; never rewrites what already happened."""
    if stage not in STAGE_INDEX:
        return
    existing = {row.stage for row in (await session.execute(
        select(P2PStageEvent).where(P2PStageEvent.tenant_id == tenant_id, P2PStageEvent.po_id == po.id)
    )).scalars().all()}
    for code, _, _ in stages()[: stage_index(stage) + 1]:
        if code in existing:
            continue
        session.add(P2PStageEvent(
            tenant_id=tenant_id, po_id=po.id, rfq_id=po.rfq_id, stage=code,
            reached_at=datetime.now(UTC), actor_id=actor_id, source=source or "derived",
            evidence=payload or {},
        ))
    await session.flush()


async def touch(session: AsyncSession, tenant_id: str, po: PurchaseOrder, *, actor_id: str | None = None,
                source: str = "") -> str:
    """Recompute and persist the current stage after a mutation; returns the stage code."""
    data = await evidence(session, po)
    stage = derive_stage(data, po)
    await record_stage(session, tenant_id, po, stage, actor_id=actor_id, source=source)
    return stage


async def stage_summary(session: AsyncSession, tenant_id: str, po: PurchaseOrder) -> dict:
    """Compact form used in list payloads and notifications."""
    data = await evidence(session, po)
    stage = derive_stage(data, po)
    return {"stage": stage, "label_en": STAGE_LABELS[stage][0], "label_ar": STAGE_LABELS[stage][1],
            "ready_for_audit": settings.accounting_enabled and data["package"] is not None,
            "posted": bool(settings.accounting_enabled and data["posted_invoice_entry"])}
