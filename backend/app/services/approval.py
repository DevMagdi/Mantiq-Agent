"""Multi-level approval workflow, split award and PO hand-off (FR-CMP-3..5)."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.entities import (
    RFQ,
    Approval,
    ApprovalChain,
    ApprovalLimit,
    ApprovalStep,
    Offer,
    PurchaseOrder,
    Tenant,
    User,
)
from app.services import numbers
from app.services.common import notify, notify_role
from app.services.po import create_po_for_offer

log = logging.getLogger("procureai.approval")


async def resolve_chain(session: AsyncSession, tenant: Tenant, rfq: RFQ, amount: Decimal,
                        category_code: str | None = None) -> tuple[ApprovalChain | None, list[ApprovalStep]]:
    chains = list((await session.execute(
        select(ApprovalChain).where(ApprovalChain.tenant_id == tenant.id, ApprovalChain.is_active.is_(True))
        .order_by(ApprovalChain.min_amount.desc())
    )).scalars().all())
    for chain in chains:
        in_range = Decimal(str(chain.min_amount or 0)) <= amount and (
            chain.max_amount is None or amount <= Decimal(str(chain.max_amount))
        )
        category_ok = not chain.category_code or chain.category_code == category_code
        if in_range and category_ok:
            steps = list((await session.execute(
                select(ApprovalStep).where(ApprovalStep.chain_id == chain.id).order_by(ApprovalStep.step_no)
            )).scalars().all())
            if steps:
                return chain, steps
    # Fallback: derive a two-step chain from approval limits.
    limits = list((await session.execute(
        select(ApprovalLimit).where(ApprovalLimit.tenant_id == tenant.id)
    )).scalars().all())
    approver = max(limits, key=lambda limit: float(limit.max_amount or 0), default=None)
    role = approver.role_code if approver and approver.max_amount and amount <= approver.max_amount else "finance_approver"
    step = ApprovalStep(tenant_id=tenant.id, chain_id="", step_no=1, role_code=role, threshold_amount=amount,
                        sla_hours=24)
    return None, [step]


async def request_approval(session: AsyncSession, tenant: Tenant, rfq: RFQ, *, offer_ids: list[str],
                           amount: Decimal, requested_by: str | None = None,
                           split: list[dict] | None = None) -> list[Approval]:
    existing = list((await session.execute(
        select(Approval).where(Approval.tenant_id == tenant.id, Approval.rfq_id == rfq.id,
                               Approval.status.in_(["pending", "awaiting_next", "revision_requested"]))
    )).scalars().all())
    if existing:
        return existing
    category_code = None
    if offer_ids:
        from app.db.models.entities import OfferLine, RFQLine

        line = (await session.execute(
            select(OfferLine).where(OfferLine.offer_id == offer_ids[0]).limit(1)
        )).scalars().first()
        if line and line.rfq_line_id:
            rfq_line = (await session.execute(select(RFQLine).where(RFQLine.id == line.rfq_line_id))).scalar_one_or_none()
            category_code = rfq_line.specs.get("category_code") if rfq_line and rfq_line.specs else None
    chain, steps = await resolve_chain(session, tenant, rfq, amount, category_code)
    created: list[Approval] = []
    for index, step in enumerate(steps):
        approval = Approval(
            tenant_id=tenant.id, rfq_id=rfq.id, offer_id=offer_ids[0] if offer_ids else None,
            chain_id=chain.id if chain else None, step_no=step.step_no,
            approver_role=step.role_code, status="pending" if index == 0 else "queued",
            amount=numbers.money(amount), currency=rfq.currency,
            due_at=datetime.now(UTC) + timedelta(hours=step.sla_hours or 24),
            split_award=split or [],
        )
        session.add(approval)
        created.append(approval)
    await notify_role(session, tenant_id=tenant.id, role_code=steps[0].role_code, kind="approval_required",
                      title=f"Approval required: {rfq.code} ({numbers.fmt_amount(amount, rfq.currency, 'en')})",
                      title_ar=f"مطلوب اعتماد: {rfq.code} ({numbers.fmt_amount(amount, rfq.currency, 'ar')})",
                      body="An offer is ready for your decision.", link="/approvals", severity="warning")
    await session.flush()
    return created


async def decide(session: AsyncSession, tenant: Tenant, approval: Approval, *, decision: str,
                 comment: str, user: User, split: list[dict] | None = None) -> dict:
    """decision: approve | reject | request_revision | split_award."""
    if approval.status not in {"pending", "revision_requested"}:
        return {"ok": False, "error": f"Approval is '{approval.status}', not pending."}
    rfq = (await session.execute(select(RFQ).where(RFQ.id == approval.rfq_id))).scalar_one()
    approval.status = "approved" if decision == "approve" else (
        "rejected" if decision == "reject" else ("revision_requested" if decision == "request_revision" else "approved")
    )
    approval.decision = decision
    approval.comment = comment
    approval.approver_id = user.id
    approval.decided_at = datetime.now(UTC)
    if split:
        approval.split_award = split

    if decision == "reject":
        if approval.offer_id:
            offer = (await session.execute(select(Offer).where(Offer.id == approval.offer_id))).scalar_one_or_none()
            if offer:
                offer.status = "rejected"
        await notify(session, tenant_id=tenant.id, user_id=rfq.buyer_id, kind="approval_rejected",
                     title=f"{rfq.code} rejected by {user.full_name}",
                     title_ar=f"تم رفض {rfq.code} بواسطة {user.full_name}", body=comment[:300],
                     link=f"/rfqs/{rfq.id}", severity="destructive")
        await session.flush()
        return {"ok": True, "status": "rejected", "next_step": None}

    if decision == "request_revision":
        await notify(session, tenant_id=tenant.id, user_id=rfq.buyer_id, kind="approval_revision",
                     title=f"Revision requested on {rfq.code}",
                     title_ar=f"مطلوب تعديل على {rfq.code}", body=comment[:300], link=f"/rfqs/{rfq.id}")
        await session.flush()
        return {"ok": True, "status": "revision_requested", "next_step": None}

    # Advance the chain (or complete).
    remaining = list((await session.execute(
        select(Approval).where(Approval.tenant_id == tenant.id, Approval.rfq_id == rfq.id,
                               Approval.id != approval.id, Approval.status.in_(["queued", "pending"]))
        .order_by(Approval.step_no)
    )).scalars().all())
    if remaining:
        next_approval = remaining[0]
        next_approval.status = "pending"
        await notify_role(session, tenant_id=tenant.id, role_code=next_approval.approver_role,
                          kind="approval_required",
                          title=f"Approval required (step {next_approval.step_no}): {rfq.code}",
                          title_ar=f"اعتماد مطلوب (خطوة {next_approval.step_no}): {rfq.code}",
                          body="Previous level approved.", link="/approvals", severity="warning")
        await session.flush()
        return {"ok": True, "status": "approved", "next_step": next_approval.step_no}

    # Final approval -> award and PO(s)
    split_rows = split or approval.split_award or []
    purchase_orders = await finalize_award(session, tenant, rfq, approval, split_rows, user)
    await notify(session, tenant_id=tenant.id, user_id=rfq.buyer_id, kind="approval_completed",
                 title=f"{rfq.code} fully approved",
                 title_ar=f"تم اعتماد {rfq.code} بالكامل",
                 body=f"{len(purchase_orders)} purchase order(s) generated.", link=f"/rfqs/{rfq.id}")
    await session.flush()
    return {"ok": True, "status": "approved", "next_step": None, "purchase_orders": [po.id for po in purchase_orders]}


async def finalize_award(session: AsyncSession, tenant: Tenant, rfq: RFQ, approval: Approval,
                         split: list[dict], user: User) -> list[PurchaseOrder]:
    from app.services.rfq import close_rfq

    orders: list[PurchaseOrder] = []
    if split:
        for entry in split:
            offer_id = entry.get("offer_id")
            if not offer_id:
                continue
            offer = (await session.execute(select(Offer).where(Offer.id == offer_id))).scalar_one_or_none()
            if not offer:
                continue
            share = Decimal(str(entry.get("share") or 1))
            offer.status = "selected"
            orders.append(await create_po_for_offer(session, tenant, rfq, offer, created_by=user.id, share=share))
    else:
        offer_id = approval.offer_id
        if not offer_id:
            offer_id = (await session.execute(
                select(Offer.id).where(Offer.rfq_id == rfq.id).order_by(Offer.weighted_score.desc()).limit(1)
            )).scalar_one_or_none()
        if offer_id:
            offer = (await session.execute(select(Offer).where(Offer.id == offer_id))).scalar_one()
            offer.status = "selected"
            orders.append(await create_po_for_offer(session, tenant, rfq, offer, created_by=user.id))
    await close_rfq(session, rfq, actor=user.id, reason="approved")
    await session.flush()
    return orders


async def escalate_overdue(session: AsyncSession, tenant: Tenant) -> int:
    overdue = list((await session.execute(
        select(Approval).where(Approval.tenant_id == tenant.id, Approval.status == "pending",
                               Approval.due_at < datetime.now(UTC))
    )).scalars().all())
    for approval in overdue:
        await notify_role(session, tenant_id=tenant.id, role_code="procurement_manager", kind="approval_escalation",
                          title=f"Approval SLA breached (step {approval.step_no})",
                          title_ar=f"تجاوز مهلة الاعتماد (خطوة {approval.step_no})",
                          body=f"Approval {approval.id} is overdue.", link="/approvals", severity="destructive")
        approval.due_at = datetime.now(UTC) + timedelta(hours=12)
    await session.flush()
    return len(overdue)


async def delegate(session: AsyncSession, tenant: Tenant, approval: Approval, to_user_id: str,
                   user: User, comment: str = "") -> Approval:
    approval.delegated_to = to_user_id
    approval.comment = f"{approval.comment}\n[delegated by {user.full_name}] {comment}".strip()
    await notify(session, tenant_id=tenant.id, user_id=to_user_id, kind="approval_delegated",
                 title="An approval was delegated to you", title_ar="تم تفويض اعتماد لك",
                 body=comment[:200], link="/approvals")
    await session.flush()
    return approval
