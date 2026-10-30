"""Negotiation service: policy versioning, drafting, revalidation and confidentiality.

The guardrail *rules* are covered exhaustively by ``test_negotiation_guardrails.py``. What
this suite adds is the service wiring: drafts are persisted, auto-send only happens inside
the policy envelope, a human send is revalidated at send time, and competitor data never
leaves the building.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from app.agents import guardrails
from app.agents.policy_engine import OUTSIDE_HOURS
from app.db.models.entities import (
    RFQ,
    NegotiationMessage,
    NegotiationPolicy,
    NegotiationRound,
    Offer,
    Supplier,
    Tenant,
)
from app.db.session import SessionLocal
from app.services import negotiation


async def _seller(session) -> tuple[Tenant, RFQ, Supplier, Offer]:
    tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
    offer = (await session.execute(
        select(Offer).where(Offer.tenant_id == tenant.id, Offer.status.in_(["verified", "selected"]))
        .order_by(Offer.total_base.desc())
    )).scalars().first()
    assert offer is not None
    rfq = (await session.execute(select(RFQ).where(RFQ.id == offer.rfq_id))).scalar_one()
    supplier = (await session.execute(select(Supplier).where(Supplier.id == offer.supplier_id))).scalar_one()
    return tenant, rfq, supplier, offer


async def _policy(session, tenant, rfq, **overrides) -> NegotiationPolicy:
    params = {
        "rfq": rfq,
        "supplier_id": None,
        "target_price": Decimal("19000"),
        "walkaway_price": Decimal("22000"),
        "max_rounds": 3,
        "allowed_concessions": ["payment_terms"],
        "tone": "professional",
        "mode": "draft_only",
        "value_threshold": Decimal("0"),
        "currency": "EGP",
        "notes": "test policy",
    }
    params.update(overrides)
    return await negotiation.upsert_policy(session, tenant, **params)


def _zone_with_local_hour(hour: int) -> str:
    """An Etc/GMT zone where the wall clock is currently ``hour``: makes time-of-day deterministic."""
    offset = hour - datetime.now(UTC).hour
    if offset > 14:
        offset -= 24
    if offset < -12:
        offset += 24
    return f"Etc/GMT{-offset:+d}".replace("+0", "") if offset else "UTC"


async def test_policy_versioning_keeps_one_active_row_and_bumps_version():
    async with SessionLocal() as session:
        tenant, rfq, _supplier, _offer = await _seller(session)
        first = await _policy(session, tenant, rfq)
        assert first.version == 1 and first.is_active is True

        second = await negotiation.upsert_policy(
            session, tenant, rfq=rfq, supplier_id=None, target_price=Decimal("18500"),
            walkaway_price=Decimal("21500"), max_rounds=2, allowed_concessions=["nothing"],
            tone="firm", mode="draft_only", value_threshold=Decimal("0"), currency="EGP")
        assert second.version == 2
        await session.flush()
        actives = list((await session.execute(
            select(NegotiationPolicy).where(NegotiationPolicy.tenant_id == tenant.id,
                                            NegotiationPolicy.rfq_id == rfq.id,
                                            NegotiationPolicy.is_active.is_(True))
        )).scalars().all())
        assert len(actives) == 1 and actives[0].id == second.id
        await session.rollback()


async def test_supplier_specific_policy_overrides_the_rfq_default():
    async with SessionLocal() as session:
        tenant, rfq, supplier, _offer = await _seller(session)
        await _policy(session, tenant, rfq, target_price=Decimal("19000"))
        specific = await _policy(session, tenant, rfq, supplier_id=supplier.id,
                                 target_price=Decimal("17000"), tone="firm")
        resolved = await negotiation.policy_for(session, tenant, rfq, supplier.id)
        assert resolved.id == specific.id
        assert resolved.target_price == Decimal("17000.00")
        default = await negotiation.policy_for(session, tenant, rfq)
        assert default.supplier_id is None
        await session.rollback()


async def test_benchmark_hint_is_anonymised_and_bucketed():
    async with SessionLocal() as session:
        tenant, rfq, supplier, offer = await _seller(session)
        others = list((await session.execute(
            select(Offer).where(Offer.rfq_id == rfq.id, Offer.id != offer.id)
        )).scalars().all())
        assert others, "the demo RFQ must carry competing offers"
        offer.total_base = Decimal("100000")
        for index, other in enumerate(others):
            other.total_base = Decimal("100000") / (1 + Decimal(index + 1) / 10)

        hint, meta = await negotiation.benchmark_hint(session, tenant.id, rfq, offer)
        assert meta["count"] == len(others)
        if hint:
            assert "%" in hint
            assert supplier.name not in hint, "the hint must never name a competitor"
            assert meta["gap_bucket"] >= 3
        await session.rollback()


async def test_draft_only_policy_keeps_the_counter_offer_as_a_draft():
    async with SessionLocal() as session:
        tenant, rfq, supplier, offer = await _seller(session)
        policy = await _policy(session, tenant, rfq)
        result = await negotiation.draft_counter_offer(
            session, tenant, rfq=rfq, supplier=supplier, offer=offer, policy=policy, round_no=1,
            send_requested=True)

        assert result["status"] == "draft"
        assert result["decision"]["allowed"] is True
        assert result["decision"]["action"] == "keep_draft"
        assert result["body_ar"] and result["body_en"]
        assert result["leak_violations"] == []
        message = (await session.execute(
            select(NegotiationMessage).where(NegotiationMessage.id == result["message_id"]))).scalar_one()
        assert message.status == "draft"
        assert message.actor_type == "agent"
        assert message.policy_decision["action"] == "keep_draft"
        assert message.sent_at is None
        await session.rollback()


async def test_auto_mode_sends_only_below_the_value_threshold():
    async with SessionLocal() as session:
        tenant, rfq, supplier, offer = await _seller(session)
        value = Decimal(str(offer.total_base or 0))
        tenant.timezone = _zone_with_local_hour(12)
        policy = await _policy(session, tenant, rfq, mode="auto",
                               value_threshold=value * 2, target_price=Decimal("19000"))

        result = await negotiation.draft_counter_offer(
            session, tenant, rfq=rfq, supplier=supplier, offer=offer, policy=policy,
            round_no=1, send_requested=True)

        assert result["status"] == "sent", result
        assert result["send_result"]["ok"] is True
        round_row = (await session.execute(
            select(NegotiationRound).where(NegotiationRound.rfq_id == rfq.id,
                                           NegotiationRound.supplier_id == supplier.id)
        )).scalars().first()
        assert round_row is not None
        await session.rollback()


async def test_autonomous_send_is_refused_outside_contact_hours():
    async with SessionLocal() as session:
        tenant, rfq, supplier, offer = await _seller(session)
        value = Decimal(str(offer.total_base or 0))
        tenant.timezone = _zone_with_local_hour(3)
        policy = await _policy(session, tenant, rfq, mode="auto",
                               value_threshold=value * 2, target_price=Decimal("19000"))
        result = await negotiation.draft_counter_offer(
            session, tenant, rfq=rfq, supplier=supplier, offer=offer, policy=policy,
            round_no=1, send_requested=True)
        assert result["status"] == "blocked"
        assert OUTSIDE_HOURS in result["decision"]["codes"]
        assert result["send_result"] is None
        await session.rollback()


async def test_auto_mode_above_the_threshold_blocks_and_notifies_the_buyer():
    async with SessionLocal() as session:
        tenant, rfq, supplier, offer = await _seller(session)
        policy = await _policy(session, tenant, rfq, mode="auto", value_threshold=Decimal("1"))
        result = await negotiation.draft_counter_offer(
            session, tenant, rfq=rfq, supplier=supplier, offer=offer, policy=policy,
            round_no=1, send_requested=True)
        assert result["status"] == "blocked"
        assert "BLOCK_AUTO_VALUE_THRESHOLD" in result["decision"]["codes"]
        message = (await session.execute(
            select(NegotiationMessage).where(NegotiationMessage.id == result["message_id"]))).scalar_one()
        assert message.status == "blocked"
        await session.rollback()


async def test_round_limit_blocks_further_drafting():
    async with SessionLocal() as session:
        tenant, rfq, supplier, offer = await _seller(session)
        policy = await _policy(session, tenant, rfq)
        result = await negotiation.draft_counter_offer(
            session, tenant, rfq=rfq, supplier=supplier, offer=offer, policy=policy, round_no=4)
        assert result["status"] == "blocked"
        assert "BLOCK_ROUND_LIMIT" in result["decision"]["codes"]
        await session.rollback()


async def test_human_send_is_revalidated_against_the_current_policy():
    async with SessionLocal() as session:
        tenant, rfq, supplier, offer = await _seller(session)
        policy = await _policy(session, tenant, rfq)
        drafted = await negotiation.draft_counter_offer(
            session, tenant, rfq=rfq, supplier=supplier, offer=offer, policy=policy, round_no=1)
        message = (await session.execute(
            select(NegotiationMessage).where(NegotiationMessage.id == drafted["message_id"]))).scalar_one()

        # The policy tightens between drafting and sending: the send must fail closed.
        await negotiation.upsert_policy(
            session, tenant, rfq=rfq, supplier_id=None, target_price=Decimal("100"),
            walkaway_price=Decimal("101"), max_rounds=3, allowed_concessions=["nothing"],
            tone="firm", mode="draft_only", value_threshold=Decimal("0"), currency="EGP")
        blocked = await negotiation.approve_and_send_draft(session, tenant, message, actor_id=None)
        assert blocked["ok"] is False
        assert "BLOCK_PRICE_ABOVE_WALKWAY" in blocked["decision"]["codes"] or blocked["decision"]["codes"]
        assert message.status == "blocked"
        await session.rollback()


async def test_timeline_reports_rounds_messages_and_price_reduction():
    async with SessionLocal() as session:
        tenant, rfq, supplier, offer = await _seller(session)
        policy = await _policy(session, tenant, rfq)
        drafted = await negotiation.draft_counter_offer(
            session, tenant, rfq=rfq, supplier=supplier, offer=offer, policy=policy, round_no=1)
        assert drafted["message_id"]

        view = await negotiation.timeline(session, tenant.id, rfq.id, supplier.id)
        assert [m["id"] for m in view["messages"]] == [drafted["message_id"]]
        assert view["messages"][0]["round"] == 1
        assert view["max_rounds"] == 3
        assert isinstance(view["rounds"], list)
        assert view["price_reduction_pct"] is None or view["price_reduction_pct"] >= 0
        assert await negotiation.next_round(session, tenant.id, rfq.id, supplier.id) == 2
        await session.rollback()


def test_competitor_confidentiality_detects_names_and_exact_prices():
    names = ["Nile Steel Co", "Delta Metals"]
    prices = [Decimal("210000"), Decimal("198500")]
    assert guardrails.competitor_references("Our price is better than Nile Steel Co", names, prices)
    assert any(v.startswith("competitor_price") for v in
               guardrails.competitor_references("They quoted 198,500 for this lot", names, prices))
    clean = "We can offer a 3% reduction with Net 45 terms."
    assert guardrails.competitor_references(clean, names, prices) == []
    assert guardrails.competitor_references("A competitor offered around 150,000", names, []) == []
