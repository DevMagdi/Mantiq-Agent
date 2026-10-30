"""AI voice calling: disclosure, consent, recording and country call windows (FR-RFQ-5).

The safety promise is narrow and testable: the AI says it is an AI before anyone speaks,
the recording only exists if consent was given, and no call is placed outside the
destination's permitted hours or without an explicit voice opt-in.
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import select

from app.db.models.entities import (
    RFQ,
    CountryCallPolicy,
    RFQSupplier,
    Supplier,
    Tenant,
    UsageEvent,
    VoiceCall,
)
from app.db.session import SessionLocal
from app.services import voice
from app.services.messaging import set_opt_in
from app.services.suppliers import contact_pii, primary_contact, upsert_contact

#: A test-only destination so these cases never depend on the demo seed's policies.
COUNTRY = "ZZ"


async def _tenant(session) -> Tenant:
    return (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()


async def _open_rfq(session, tenant) -> RFQ:
    return (await session.execute(
        select(RFQ).where(RFQ.tenant_id == tenant.id, RFQ.status.in_(["sent", "open", "replied"]))
        .order_by(RFQ.created_at.desc())
    )).scalars().first() or (await session.execute(
        select(RFQ).where(RFQ.tenant_id == tenant.id).order_by(RFQ.created_at.desc())
    )).scalars().first()


async def _scene(session, *, start: int = 0, end: int = 24, phone: str = "+201000001111",
                 opt_in: bool = True) -> tuple[Tenant, RFQ, Supplier]:
    tenant = await _tenant(session)
    rfq = await _open_rfq(session, tenant)
    supplier = (await session.execute(
        select(Supplier).where(Supplier.tenant_id == tenant.id)
    )).scalars().first()
    supplier.country = COUNTRY
    policy = (await session.execute(
        select(CountryCallPolicy).where(CountryCallPolicy.country == COUNTRY)
    )).scalar_one_or_none()
    if policy is None:
        policy = CountryCallPolicy(country=COUNTRY, allowed_hours_start=start, allowed_hours_end=end,
                                   recording_consent_mode="explicit",
                                   disclosure_text_en="Hello, this is an AI assistant calling on behalf of the buyer.",
                                   disclosure_text_ar="مرحباً، أنا مساعد آلي أتصل نيابة عن المشتري.")
        session.add(policy)
    else:
        policy.allowed_hours_start, policy.allowed_hours_end = start, end
    contact = await primary_contact(session, tenant, supplier.id)
    if contact is None:
        await upsert_contact(session, tenant, supplier, name="Voice Contact",
                             email=f"voice.{supplier.id[:6]}@example.com", phone=phone)
    else:
        from app.core.crypto import encrypt_text

        contact.phone, contact.key_id = await encrypt_text(session, tenant, phone)
        contact.email_opt_in = True
    await set_opt_in(session, tenant.id, supplier.id, "voice",
                     "opted_in" if opt_in else "opted_out", source="test")
    invitation = (await session.execute(
        select(RFQSupplier).where(RFQSupplier.rfq_id == rfq.id, RFQSupplier.supplier_id == supplier.id)
    )).scalar_one_or_none()
    if invitation is None:
        session.add(RFQSupplier(tenant_id=tenant.id, rfq_id=rfq.id, supplier_id=supplier.id,
                                status="sent", channel="voice"))
    await session.flush()
    return tenant, rfq, supplier


def test_call_window_respects_the_country_policy():
    policy = SimpleNamespace(allowed_hours_start=9, allowed_hours_end=18)
    assert voice.call_allowed_now(policy, datetime(2026, 5, 4, 9, 0, tzinfo=UTC)) is True
    assert voice.call_allowed_now(policy, datetime(2026, 5, 4, 17, 59, tzinfo=UTC)) is True
    assert voice.call_allowed_now(policy, datetime(2026, 5, 4, 18, 0, tzinfo=UTC)) is False
    assert voice.call_allowed_now(policy, datetime(2026, 5, 4, 8, 59, tzinfo=UTC)) is False
    assert voice.call_allowed_now(policy, datetime(2026, 5, 4, 3, 0, tzinfo=UTC)) is False


async def test_ai_call_discloses_itself_records_with_consent_and_extracts_an_offer():
    async with SessionLocal() as session:
        tenant, rfq, supplier = await _scene(session)
        result = await voice.start_call(session, tenant, rfq=rfq, supplier=supplier, language="ar-EG")

        assert result["ok"] is True, result
        assert result["disclosure_played"] is True, "the AI must identify itself before speaking"
        assert result["consent_given"] is True
        assert result["recording"] is True
        assert result["extraction_id"], "a consented transcript becomes a verifiable offer"
        assert "[AI]" in result["transcript"]

        call = (await session.execute(select(VoiceCall).where(VoiceCall.id == result["call_id"]))).scalar_one()
        policy_snapshot = call.policy_snapshot
        assert policy_snapshot["disclosure"]
        assert policy_snapshot["recording_consent_mode"] == "explicit"
        assert policy_snapshot["inbound_opt_in"] is True
        assert call.ended_at is not None

        meter = (await session.execute(
            select(UsageEvent).where(UsageEvent.kind == "voice_minute", UsageEvent.ref_id == call.id)
        )).scalars().first()
        assert meter is not None and meter.quantity > 0, "voice minutes must be metered for billing"
        await session.rollback()


async def test_recording_is_dropped_when_consent_is_refused():
    async with SessionLocal() as session:
        tenant, rfq, supplier = await _scene(session)
        result = await voice.start_call(session, tenant, rfq=rfq, supplier=supplier)
        call = (await session.execute(select(VoiceCall).where(VoiceCall.id == result["call_id"]))).scalar_one()
        assert call.recording_uri

        await voice.escalate_consent_refusal(session, tenant, call)
        assert call.recording_uri == ""
        assert call.consent_given is False
        assert "consent refused" in call.summary
        await session.rollback()


async def test_call_without_voice_opt_in_is_blocked():
    async with SessionLocal() as session:
        tenant, rfq, supplier = await _scene(session, opt_in=False)
        result = await voice.start_call(session, tenant, rfq=rfq, supplier=supplier)
        assert result["ok"] is False
        assert "opt" in result["error"].lower() or "NO_OPT_IN" in result.get("codes", [])
        await session.rollback()


async def test_call_outside_the_window_is_not_placed():
    async with SessionLocal() as session:
        tenant, rfq, supplier = await _scene(session, start=0, end=0)
        result = await voice.start_call(session, tenant, rfq=rfq, supplier=supplier)
        assert result["ok"] is False
        assert "allowed calling hours" in result["error"]
        assert (await session.execute(select(VoiceCall).where(VoiceCall.rfq_id == rfq.id))).scalars().first() is None
        await session.rollback()


async def test_call_to_a_country_without_a_policy_is_refused():
    async with SessionLocal() as session:
        tenant, rfq, supplier = await _scene(session)
        supplier.country = "QQ"
        result = await voice.start_call(session, tenant, rfq=rfq, supplier=supplier)
        assert result["ok"] is False
        assert "No country call policy" in result["error"]
        await session.rollback()


async def test_call_without_a_phone_number_is_refused():
    async with SessionLocal() as session:
        tenant, rfq, supplier = await _scene(session)
        contact = await primary_contact(session, tenant, supplier.id)
        from app.core.crypto import encrypt_text

        contact.phone, contact.key_id = await encrypt_text(session, tenant, "")
        await session.flush()
        result = await voice.start_call(session, tenant, rfq=rfq, supplier=supplier)
        assert result["ok"] is False
        assert "phone" in result["error"].lower()
        await session.rollback()


async def test_no_answer_is_recorded_without_a_recording_or_offer():
    async with SessionLocal() as session:
        tenant, rfq, supplier = await _scene(session)
        # The fake provider's deterministic "no answer" branch keys off the number's checksum.
        no_answer_phone = next(candidate for candidate in
                               (f"+20100000{n:04d}" for n in range(10000))
                               if sum(ord(c) for c in candidate) % 7 == 0)
        contact = await primary_contact(session, tenant, supplier.id)
        from app.core.crypto import encrypt_text

        contact.phone, contact.key_id = await encrypt_text(session, tenant, no_answer_phone)
        await session.flush()

        result = await voice.start_call(session, tenant, rfq=rfq, supplier=supplier)
        assert result["status"] == "no_answer"
        assert result["ok"] is False
        assert result["recording"] is False
        assert result["extraction_id"] is None
        await session.rollback()


async def test_manual_script_never_claims_to_be_automated():
    async with SessionLocal() as session:
        tenant, rfq, supplier = await _scene(session)
        script = await voice.manual_script(session, tenant, rfq, supplier)
        assert script["rfq_code"] == rfq.code
        assert script["disclosure_required"] is False
        assert script["recording_rules"] == "explicit"
        assert "consent" in script["note"].lower()
        assert script["script_ar"] and script["script_en"]
        await session.rollback()


async def test_supplier_phone_is_never_stored_in_plaintext():
    async with SessionLocal() as session:
        tenant, _rfq, supplier = await _scene(session)
        contact = await primary_contact(session, tenant, supplier.id)
        assert "+201000001111" not in (contact.phone or ""), "supplier PII must stay encrypted at rest"
        decrypted_email, decrypted_phone = await contact_pii(session, tenant, contact)
        assert decrypted_phone.startswith("+20")
        await session.rollback()
