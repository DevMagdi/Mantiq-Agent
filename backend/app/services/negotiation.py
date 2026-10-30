"""Negotiation agent service: policy CRUD, drafting, drafting-mode enforcement, timeline.

The agent can only recommend/propose; every outbound message is validated by the pure
policy engine and scanned for competitor leakage before it can leave the platform.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.guardrails import competitor_references, scan_untrusted
from app.agents.llm import llm_for
from app.agents.llm.base import LLMMessage, LLMRequest
from app.agents.policy_engine import Draft, PolicyContext, PolicySpec, evaluate_outgoing
from app.db.models.entities import (
    RFQ,
    AgentRun,
    NegotiationMessage,
    NegotiationPolicy,
    NegotiationRound,
    Offer,
    RFQSupplier,
    Supplier,
    Tenant,
)
from app.services import numbers
from app.services.common import notify
from app.services.messaging import send_message

log = logging.getLogger("procureai.negotiation")


async def policy_for(session: AsyncSession, tenant: Tenant, rfq: RFQ,
                     supplier_id: str | None = None) -> NegotiationPolicy | None:
    query = select(NegotiationPolicy).where(NegotiationPolicy.tenant_id == tenant.id,
                                            NegotiationPolicy.is_active.is_(True))
    if supplier_id:
        row = (await session.execute(
            query.where(NegotiationPolicy.rfq_id == rfq.id, NegotiationPolicy.supplier_id == supplier_id)
            .order_by(NegotiationPolicy.version.desc())
        )).scalars().first()
        if row:
            return row
    row = (await session.execute(
        query.where(NegotiationPolicy.rfq_id == rfq.id, NegotiationPolicy.supplier_id.is_(None))
        .order_by(NegotiationPolicy.version.desc())
    )).scalars().first()
    return row


async def upsert_policy(session: AsyncSession, tenant: Tenant, *, rfq: RFQ, supplier_id: str | None,
                        target_price: Decimal, walkaway_price: Decimal, max_rounds: int,
                        allowed_concessions: list[str], tone: str, mode: str,
                        value_threshold: Decimal, currency: str, notes: str = "",
                        created_by: str | None = None) -> NegotiationPolicy:
    existing = (await session.execute(
        select(NegotiationPolicy).where(NegotiationPolicy.tenant_id == tenant.id,
                                        NegotiationPolicy.rfq_id == rfq.id,
                                        NegotiationPolicy.supplier_id == supplier_id,
                                        NegotiationPolicy.is_active.is_(True))
    )).scalars().first()
    version = 1
    if existing:
        existing.is_active = False
        version = (existing.version or 1) + 1
    policy = NegotiationPolicy(
        tenant_id=tenant.id, rfq_id=rfq.id, supplier_id=supplier_id, currency=currency,
        target_price=numbers.money(target_price), walkaway_price=numbers.money(walkaway_price),
        max_rounds=max_rounds, allowed_concessions=allowed_concessions, tone=tone, mode=mode,
        value_threshold=numbers.money(value_threshold), version=version, is_active=True,
        notes=notes, created_by=created_by,
    )
    session.add(policy)
    await session.flush()
    return policy


async def benchmark_hint(session: AsyncSession, tenant_id: str, rfq: RFQ, offer: Offer) -> tuple[str, dict]:
    """Anonymised aggregate only — never a competitor's identity or exact price."""
    others = list((await session.execute(
        select(Offer).where(Offer.tenant_id == tenant_id, Offer.rfq_id == rfq.id, Offer.id != offer.id,
                            Offer.status != "rejected")
    )).scalars().all())
    prices = sorted(float(o.total_base) for o in others if o.total_base)
    if not prices:
        return "", {"count": 0}
    median = prices[len(prices) // 2]
    current = float(offer.total_base or 0)
    if not current:
        return "", {"count": len(prices)}
    gap_pct = (current - median) / median * 100 if median else 0
    if gap_pct > 4:
        bucket = max(3, int(round(gap_pct)))
        hint = f"لدينا مؤشرات من السوق تشير إلى إمكانية خفض السعر بنحو {bucket}% تقريباً."
        return hint, {"count": len(prices), "gap_bucket": bucket}
    return "", {"count": len(prices)}


async def draft_counter_offer(session: AsyncSession, tenant: Tenant, *, rfq: RFQ, supplier: Supplier,
                              offer: Offer, policy: NegotiationPolicy, round_no: int,
                              concession: str | None = None, actor_id: str | None = None,
                              send_requested: bool = False) -> dict:
    """Draft (and maybe send) a counter-offer, fully audited with the policy decision."""
    spec = PolicySpec.from_row(policy)
    hint, hint_meta = await benchmark_hint(session, tenant.id, rfq, offer)
    llm = await llm_for(session, tenant)
    run = AgentRun(
        tenant_id=tenant.id, kind="negotiation_draft", status="running", ref_type="rfq", ref_id=rfq.id,
        model=f"{llm.name}:{llm.model}", created_by=actor_id, started_at=datetime.now(UTC),
    )
    session.add(run)
    await session.flush()

    current_unit = await _current_unit_price(session, offer)
    response = await llm.complete(LLMRequest(
        task="negotiation_draft",
        messages=[
            LLMMessage("system", (
                "You draft B2B counter-offers. Rules: never accept an offer, never sign, never change "
                "specifications, never reveal competitor names or exact competitor prices, never exceed "
                "the walk-away price. Output JSON with body_ar, body_en, proposed_price."
            )),
            LLMMessage("user", (
                f"RFQ {rfq.code} | round {round_no}/{spec.max_rounds} | tone {spec.tone} | "
                f"currency {spec.currency} | current unit price {current_unit} | target {spec.target_price} | "
                f"walk-away {spec.walkaway_price} | market hint: {hint or 'none'} | "
                f"allowed concessions: {sorted(spec.allowed_concessions)}"
            )),
        ],
        context={
            "current_price": str(current_unit), "target_price": str(spec.target_price),
            "walkaway_price": str(spec.walkaway_price), "currency": spec.currency, "round_no": round_no,
            "tone": spec.tone, "concession": concession or "", "benchmark_hint": hint,
        },
    ))
    data = response.data or {}
    proposed = numbers.parse_decimal(data.get("proposed_price")) or spec.target_price
    body_ar = str(data.get("body_ar") or "")
    body_en = str(data.get("body_en") or "")
    combined = f"{body_ar}\n{body_en}"

    # Confidentiality check: compare against actual competitor identity and prices.
    competitors = list((await session.execute(
        select(Supplier).join(Offer, Offer.supplier_id == Supplier.id)
        .where(Offer.rfq_id == rfq.id, Offer.tenant_id == tenant.id, Supplier.id != supplier.id)
    )).scalars().all())
    competitor_prices = [Decimal(str(o.total_base or 0)) for o in (await session.execute(
        select(Offer).where(Offer.rfq_id == rfq.id, Offer.supplier_id != supplier.id)
    )).scalars().all()]
    leaks = competitor_references(combined, [c.name for c in competitors], competitor_prices)

    invitation = (await session.execute(
        select(RFQSupplier).where(RFQSupplier.rfq_id == rfq.id, RFQSupplier.supplier_id == supplier.id)
    )).scalar_one_or_none()
    from app.services.messaging import opt_in_status

    opted_in = await opt_in_status(session, tenant.id, supplier.id, invitation.channel if invitation else "email")
    draft = Draft(body=body_en, body_ar=body_ar, proposed_price=proposed,
                  concessions=[concession] if concession else [], intent="counter_offer",
                  channel=invitation.channel if invitation else "email",
                  mentions_competitor_data=bool(leaks), competitor_references=leaks)
    ctx = PolicyContext(
        round_no=round_no, rfq_total_value=Decimal(str(offer.total_base or 0)),
        supplier_status=supplier.status, sanctions_status=supplier.sanctions_status, opt_in=opted_in,
        channel_available=True, timezone=tenant.timezone, send_requested=send_requested,
    )
    decision = evaluate_outgoing(draft, spec, ctx)
    injection = scan_untrusted(body_ar + body_en)

    message = NegotiationMessage(
        tenant_id=tenant.id, rfq_id=rfq.id, supplier_id=supplier.id, round_no=round_no,
        direction="outbound", actor_type="agent", body=body_en, body_ar=body_ar, status="draft",
        channel=draft.channel, proposed_price=numbers.money(proposed),
        policy_decision=decision.as_dict(), agent_reasoning=(response.text or "")[:4000],
        agent_run_id=run.id, offer_id=offer.id,
    )
    session.add(message)
    run.policy_decision = decision.as_dict()
    run.reasoning = (response.text or "")[:4000]
    run.input_summary = f"RFQ {rfq.code}, supplier {supplier.name}, round {round_no}"
    run.output_summary = body_en[:500]
    run.tokens_in = response.tokens_in
    run.tokens_out = response.tokens_out
    run.cost_usd = Decimal(str(response.cost_usd))
    run.latency_ms = response.latency_ms
    run.status = "completed" if decision.allowed else "blocked"
    run.finished_at = datetime.now(UTC)
    await session.flush()

    send_result = None
    if decision.allowed and decision.action == "send" and not injection.flagged:
        send_result = await send_message(session, tenant, rfq=rfq, supplier=supplier,
                                         subject=f"Counter-offer - {rfq.code}", body=body_en,
                                         body_ar=body_ar, channel=draft.channel, author_type="agent",
                                         author_id=actor_id, agent_run_id=run.id,
                                         policy_decision=decision.as_dict())
        if send_result.get("ok"):
            message.status = "sent"
            message.sent_at = datetime.now(UTC)
            round_row = (await session.execute(
                select(NegotiationRound).where(NegotiationRound.rfq_id == rfq.id,
                                               NegotiationRound.supplier_id == supplier.id,
                                               NegotiationRound.round_no == round_no)
            )).scalar_one_or_none()
            if not round_row:
                session.add(NegotiationRound(tenant_id=tenant.id, rfq_id=rfq.id, supplier_id=supplier.id,
                                             round_no=round_no, status="open", started_at=datetime.now(UTC)))
    elif decision.allowed and decision.action == "keep_draft":
        message.status = "draft"
    else:
        message.status = "blocked"
        await notify(session, tenant_id=tenant.id, user_id=rfq.buyer_id, kind="policy_block",
                     title=f"Negotiation draft blocked for {supplier.name}",
                     title_ar=f"تم حظر مسودة تفاوض للمورد {supplier.name}",
                     body="; ".join(decision.reasons)[:300], link=f"/rfqs/{rfq.id}", severity="warning")
    await session.flush()
    return {
        "message_id": message.id, "status": message.status, "decision": decision.as_dict(),
        "proposed_price": str(proposed), "body_ar": body_ar, "body_en": body_en,
        "leak_violations": leaks, "injection": injection.codes, "run_id": run.id,
        "send_result": send_result, "market_hint_used": hint_meta,
    }


async def _current_unit_price(session: AsyncSession, offer: Offer) -> Decimal:
    from app.db.models.entities import OfferLine

    line = (await session.execute(
        select(OfferLine).where(OfferLine.offer_id == offer.id).order_by(OfferLine.unit_price.asc()).limit(1)
    )).scalars().first()
    return Decimal(str(line.unit_price)) if line else Decimal(str(offer.total_base or 0))


async def approve_and_send_draft(session: AsyncSession, tenant: Tenant, message: NegotiationMessage,
                                 actor_id: str | None) -> dict:
    """Human sends a draft: revalidated against policy at send time (not at draft time)."""
    policy = await policy_for(session, tenant, (await session.execute(
        select(RFQ).where(RFQ.id == message.rfq_id)
    )).scalar_one(), message.supplier_id)
    if not policy:
        return {"ok": False, "error": "No active negotiation policy for this RFQ."}
    supplier = (await session.execute(select(Supplier).where(Supplier.id == message.supplier_id))).scalar_one()
    rfq = (await session.execute(select(RFQ).where(RFQ.id == message.rfq_id))).scalar_one()
    spec = PolicySpec.from_row(policy)
    draft = Draft(body=message.body, body_ar=message.body_ar, proposed_price=message.proposed_price,
                  concessions=[], intent="counter_offer", channel=message.channel)
    ctx = PolicyContext(round_no=message.round_no, rfq_total_value=Decimal(str(message.proposed_price or 0)),
                        supplier_status=supplier.status, sanctions_status=supplier.sanctions_status,
                        timezone=tenant.timezone, send_requested=True)
    decision = evaluate_outgoing(draft, spec, ctx)
    if not decision.allowed:
        message.policy_decision = decision.as_dict()
        message.status = "blocked"
        await session.flush()
        return {"ok": False, "decision": decision.as_dict()}
    result = await send_message(session, tenant, rfq=rfq, supplier=supplier,
                                subject=f"Counter-offer - {rfq.code}", body=message.body,
                                body_ar=message.body_ar, channel=message.channel, author_type="user",
                                author_id=actor_id, policy_decision=decision.as_dict())
    message.status = "sent" if result.get("ok") else "failed"
    message.sent_at = datetime.now(UTC) if result.get("ok") else None
    message.policy_decision = decision.as_dict()
    await session.flush()
    return {"ok": bool(result.get("ok")), "decision": decision.as_dict(), "send": result}


async def timeline(session: AsyncSession, tenant_id: str, rfq_id: str, supplier_id: str) -> dict:
    messages = list((await session.execute(
        select(NegotiationMessage).where(NegotiationMessage.tenant_id == tenant_id,
                                         NegotiationMessage.rfq_id == rfq_id,
                                         NegotiationMessage.supplier_id == supplier_id)
        .order_by(NegotiationMessage.created_at)
    )).scalars().all())
    rounds = list((await session.execute(
        select(NegotiationRound).where(NegotiationRound.rfq_id == rfq_id,
                                       NegotiationRound.supplier_id == supplier_id)
        .order_by(NegotiationRound.round_no)
    )).scalars().all())
    offers = list((await session.execute(
        select(Offer).where(Offer.rfq_id == rfq_id, Offer.supplier_id == supplier_id)
        .order_by(Offer.created_at)
    )).scalars().all())
    decline = None
    if len(offers) >= 2:
        first, last = float(offers[0].total_base or 0), float(offers[-1].total_base or 0)
        if first:
            decline = round((first - last) / first * 100, 2)
    return {
        "messages": [{
            "id": m.id, "round": m.round_no, "direction": m.direction, "status": m.status,
            "body": m.body, "body_ar": m.body_ar, "actor_type": m.actor_type, "channel": m.channel,
            "proposed_price": str(m.proposed_price) if m.proposed_price is not None else None,
            "policy": m.policy_decision, "reasoning": m.agent_reasoning,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        } for m in messages],
        "rounds": [{"round_no": r.round_no, "status": r.status,
                    "started_at": r.started_at.isoformat() if r.started_at else None} for r in rounds],
        "offers": [{"id": o.id, "total_base": str(o.total_base), "round": o.round_no,
                    "created_at": o.created_at.isoformat() if o.created_at else None} for o in offers],
        "price_reduction_pct": decline,
        "max_rounds": (await _max_rounds(session, tenant_id, rfq_id)),
    }


async def _max_rounds(session: AsyncSession, tenant_id: str, rfq_id: str) -> int:
    policy = (await session.execute(
        select(NegotiationPolicy).where(NegotiationPolicy.tenant_id == tenant_id,
                                        NegotiationPolicy.rfq_id == rfq_id,
                                        NegotiationPolicy.is_active.is_(True))
    )).scalars().first()
    return int(policy.max_rounds) if policy else 3


async def next_round(session: AsyncSession, tenant_id: str, rfq_id: str, supplier_id: str) -> int:
    count = (await session.execute(
        select(func.count()).select_from(NegotiationMessage)
        .where(NegotiationMessage.rfq_id == rfq_id, NegotiationMessage.supplier_id == supplier_id,
               NegotiationMessage.direction == "outbound")
    )).scalar_one()
    return int(count) + 1
