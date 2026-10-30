"""Outbound AI voice agent: disclosure, consent, approved-data answers and hard blocks (2.1).

The safety claim is tested from the outside: whatever the supplier asks, the agent's reply either
comes from the approved RFQ data or is a refusal recorded with a policy code. There is no branch that
reveals a competitor price, promises an award, or changes a specification.
"""
from __future__ import annotations

from sqlalchemy import select

from app.db.models.entities import (
    RFQ,
    CountryCallPolicy,
    RFQSupplier,
    Supplier,
    Tenant,
    VoiceCall,
    VoiceCallTurn,
)
from app.db.session import SessionLocal
from app.services import voice_agent
from app.services.messaging import set_opt_in
from app.services.suppliers import primary_contact, upsert_contact

COUNTRY = "ZY"  # a test-only destination so the demo policies never interfere
# The fake provider is deterministic on the number: `sum(digits) % 7 == 0` means "no answer", and
# `% 4` selects the scripted hard question (0 none, 1 competitor, 2 commitment, 3 spec change).
NO_ANSWER_PHONE = "+201000000001"
CLEAN_PHONE = "+201000000002"
COMPETITOR_PHONE = "+201000000003"
COMMITMENT_PHONE = "+201000000004"
SPEC_CHANGE_PHONE = "+201000000005"


async def _scene(session, *, phone: str = CLEAN_PHONE):
    tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
    rfq = (await session.execute(
        select(RFQ).where(RFQ.tenant_id == tenant.id).order_by(RFQ.created_at.desc())
    )).scalars().first()
    supplier = (await session.execute(
        select(Supplier).where(Supplier.tenant_id == tenant.id)
    )).scalars().first()
    supplier.country = COUNTRY
    policy = (await session.execute(
        select(CountryCallPolicy).where(CountryCallPolicy.country == COUNTRY)
    )).scalar_one_or_none()
    if policy is None:
        policy = CountryCallPolicy(
            country=COUNTRY, allowed_hours_start=0, allowed_hours_end=24,
            recording_consent_mode="explicit",
            disclosure_text_en="Hello, this is an AI assistant calling on behalf of the buyer.",
            disclosure_text_ar="مرحباً، أنا مساعد ذكاء اصطناعي أتصل نيابة عن المشتري.",
        )
        session.add(policy)
    else:
        policy.allowed_hours_start, policy.allowed_hours_end = 0, 24
    contact = await primary_contact(session, tenant, supplier.id)
    if contact is None:
        await upsert_contact(session, tenant, supplier, name="Voice Contact",
                             email=f"voice.{supplier.id[:6]}@example.com", phone=phone)
    else:
        from app.core.crypto import encrypt_text

        contact.phone, contact.key_id = await encrypt_text(session, tenant, phone)
    await set_opt_in(session, tenant.id, supplier.id, "voice", "opted_in", source="test")
    invitation = (await session.execute(
        select(RFQSupplier).where(RFQSupplier.rfq_id == rfq.id, RFQSupplier.supplier_id == supplier.id)
    )).scalar_one_or_none()
    if invitation is None:
        invitation = RFQSupplier(tenant_id=tenant.id, rfq_id=rfq.id, supplier_id=supplier.id,
                                 status="sent", channel="voice")
        session.add(invitation)
    await session.flush()
    return tenant, rfq, supplier


async def test_the_call_discloses_consents_records_turns_and_extracts_an_offer():
    async with SessionLocal() as session:
        tenant, rfq, supplier = await _scene(session)
        result = await voice_agent.run_ai_call(session, tenant, rfq=rfq, supplier=supplier,
                                               language="ar-EG", actor_id="u1")
        assert result["ok"] is True, result
        assert result["disclosure_played"] is True
        assert result["consent_given"] is True
        assert result["recording"] is True
        assert result["extraction_id"], "a consented transcript must enter the verification queue"
        assert result["ai_agent"]["blocked_turns"] == 0

        turns = list((await session.execute(
            select(VoiceCallTurn).where(VoiceCallTurn.call_id == result["call_id"])
            .order_by(VoiceCallTurn.seq)
        )).scalars().all())
        assert turns, "every turn must be persisted"
        assert any(turn.decision == "disclose" for turn in turns)
        assert any(turn.speaker == "supplier" for turn in turns)
        # The supplier's own quotation sentence is classified and stored with its topic.
        assert any(turn.topic in {"price", "validity", "lead_time"} for turn in turns)
        await session.rollback()


async def test_a_competitor_question_is_refused_and_the_disclosure_code_is_recorded():
    async with SessionLocal() as session:
        tenant, rfq, supplier = await _scene(session, phone=COMPETITOR_PHONE)
        result = await voice_agent.run_ai_call(session, tenant, rfq=rfq, supplier=supplier)
        assert result["ai_agent"]["blocked_turns"] >= 1
        assert "BLOCK_COMPETITOR_DISCLOSURE" in result["ai_agent"]["topics_blocked"]

        blocked = [turn for turn in result["turns"] if turn["decision"] == "block"]
        assert blocked and blocked[0]["topic"] == "competitor"
        refusal = next(turn for turn in result["turns"]
                       if turn["speaker"] == "ai" and turn["decision"] == "block")
        assert "cannot discuss other suppliers" in refusal["text"]
        for price in ("19,000", "18,500", "20,000"):
            assert price not in refusal["text"]
        await session.rollback()


async def test_a_commitment_request_is_refused_under_the_accept_code():
    async with SessionLocal() as session:
        tenant, rfq, supplier = await _scene(session, phone=COMMITMENT_PHONE)
        result = await voice_agent.run_ai_call(session, tenant, rfq=rfq, supplier=supplier)
        assert "BLOCK_ACCEPT_INTENT" in result["ai_agent"]["topics_blocked"]
        refusal = next(turn for turn in result["turns"]
                       if turn["speaker"] == "ai" and turn["decision"] == "block")
        assert "cannot make any commitment" in refusal["text"]
        await session.rollback()


async def test_a_specification_change_is_refused_and_never_applied():
    async with SessionLocal() as session:
        tenant, rfq, supplier = await _scene(session, phone=SPEC_CHANGE_PHONE)
        description_before = rfq.title
        result = await voice_agent.run_ai_call(session, tenant, rfq=rfq, supplier=supplier)
        assert "BLOCK_SPEC_CHANGE" in result["ai_agent"]["topics_blocked"]
        refusal = next(turn for turn in result["turns"]
                       if turn["speaker"] == "ai" and turn["decision"] == "block")
        assert "cannot change the specification" in refusal["text"]
        assert rfq.title == description_before
        await session.rollback()


async def test_approved_questions_are_answered_from_the_rfq_only():
    knowledge = {
        "rfq_code": "RFQ-2026-00007", "title": "Rebar", "currency": "EGP", "deadline": "2026-11-30",
        "payment_terms": "Net 30", "incoterm": "DAP", "delivery_location": "Sadat City",
        "lines": [{"line_no": 1, "description": "Rebar 12mm", "quantity": "50", "uom": "t",
                   "target_price": "21000"}],
    }
    price = voice_agent.answer_question("price", knowledge)
    assert price["allowed"] is True
    assert "50 t Rebar 12mm" in price["answer_en"]
    assert "cannot share any other supplier's price" in price["answer_en"]

    terms = voice_agent.answer_question("payment_terms", knowledge)
    assert "Net 30" in terms["answer_en"]

    spec = voice_agent.answer_question("specification", knowledge)
    assert spec["allowed"] is True and "Rebar 12mm" in spec["answer_en"]

    unknown = voice_agent.answer_question("general", knowledge)
    assert unknown["allowed"] is True
    assert "only help with this request" in unknown["answer_en"].lower()


async def test_the_classifier_separates_allowed_from_forbidden_in_both_languages():
    assert voice_agent.classify_question("إيه أسعار المنافسين؟") == "competitor"
    assert voice_agent.classify_question("What do other suppliers charge?") == "competitor"
    assert voice_agent.classify_question("ينفع نغيّر المواصفات؟") == "spec_change"
    assert voice_agent.classify_question("هل الطلب رسّي علينا؟") == "commitment"
    assert voice_agent.classify_question("ما هي شروط السداد؟") == "payment_terms"
    assert voice_agent.classify_question("What is the delivery lead time?") == "lead_time"


async def test_the_voice_ai_flag_blocks_the_call_before_dialling():
    async with SessionLocal() as session:
        tenant, rfq, supplier = await _scene(session)
        tenant.settings = {**(tenant.settings or {}), "features": {"voice_ai_calls": False}}
        result = await voice_agent.run_ai_call(session, tenant, rfq=rfq, supplier=supplier)
        assert result["ok"] is False
        assert result["codes"] == ["BLOCK_FEATURE_OFF"]
        assert (await session.execute(
            select(VoiceCall).where(VoiceCall.rfq_id == rfq.id, VoiceCall.supplier_id == supplier.id)
        )).scalars().first() is None
        tenant.settings = {**(tenant.settings or {}), "features": {"voice_ai_calls": True}}
        await session.rollback()


async def test_the_call_log_and_console_expose_the_policy_snapshot():
    async with SessionLocal() as session:
        tenant, rfq, supplier = await _scene(session, phone=COMPETITOR_PHONE)
        result = await voice_agent.run_ai_call(session, tenant, rfq=rfq, supplier=supplier)
        call = (await session.execute(
            select(VoiceCall).where(VoiceCall.id == result["call_id"])
        )).scalar_one()
        view = await voice_agent.call_view(session, tenant, call)
        assert view["rfq_code"] == rfq.code
        assert view["policy_snapshot"]["ai_agent"]["guard"].startswith("no competitor data")
        assert view["turns"]
        assert any(turn["decision"] == "block" for turn in view["turns"])
        await session.rollback()


async def test_a_call_that_is_not_answered_produces_no_transcript_and_no_offer():
    async with SessionLocal() as session:
        tenant, rfq, supplier = await _scene(session, phone=NO_ANSWER_PHONE)
        result = await voice_agent.run_ai_call(session, tenant, rfq=rfq, supplier=supplier)
        call = (await session.execute(
            select(VoiceCall).where(VoiceCall.id == result["call_id"])
        )).scalar_one()
        assert call.status == "no_answer"
        assert call.consent_given is False
        assert result["turns"] == []
        assert result["extraction_id"] is None
        assert result["ai_agent"]["blocked_turns"] == 0
        await session.rollback()
