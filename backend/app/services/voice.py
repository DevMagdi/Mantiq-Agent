"""AI voice calling with mandatory disclosure, consent and country call policy (FR-RFQ-5)."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import ExtractionPayload, registry
from app.agents.policy_engine import check_dispatch
from app.db.models.entities import RFQ, CountryCallPolicy, RFQSupplier, Supplier, Tenant, VoiceCall
from app.services.common import meter, notify
from app.services.extraction import extract_offer
from app.services.messaging import opt_in_status
from app.services.suppliers import contact_pii, primary_contact

log = logging.getLogger("procureai.voice")


async def country_policy(session: AsyncSession, country: str) -> CountryCallPolicy | None:
    return (await session.execute(
        select(CountryCallPolicy).where(CountryCallPolicy.country == country.upper(),
                                        CountryCallPolicy.is_active.is_(True))
    )).scalar_one_or_none()


def call_allowed_now(policy: CountryCallPolicy, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    hour = now.hour
    return policy.allowed_hours_start <= hour < policy.allowed_hours_end


async def start_call(session: AsyncSession, tenant: Tenant, *, rfq: RFQ, supplier: Supplier,
                     language: str = "ar-EG", requested_by: str | None = None) -> dict:
    """Place an AI call. Disclosure is always spoken first; recording only with consent."""
    policy = await country_policy(session, supplier.country)
    if policy is None:
        return {"ok": False, "error": "No country call policy configured for this destination."}
    if not call_allowed_now(policy):
        return {"ok": False, "error": (
            f"Outside allowed calling hours ({policy.allowed_hours_start}:00-{policy.allowed_hours_end}:00) "
            f"for {supplier.country}."
        )}
    opted_in = await opt_in_status(session, tenant.id, supplier.id, "voice")
    decision = check_dispatch(policy=None, sent_today=0, daily_cap=999, channel="voice", opt_in=opted_in,
                              blacklisted=(supplier.status or "") == "blacklisted",
                              sanctioned=(supplier.sanctions_status or "clear") != "clear",
                              working_hours=(policy.allowed_hours_start, policy.allowed_hours_end),
                              now=datetime.now(UTC))
    if not decision.allowed:
        return {"ok": False, "error": "; ".join(decision.reasons), "codes": decision.codes}

    contact = await primary_contact(session, tenant, supplier.id)
    _email, phone = await contact_pii(session, tenant, contact) if contact else ("", "")
    if not phone:
        return {"ok": False, "error": "No phone number on file for this supplier."}

    from app.db.models.entities import RFQLine

    lines = list((await session.execute(select(RFQLine).where(RFQLine.rfq_id == rfq.id))).scalars().all())
    script = (
        f"Good day, I am calling on behalf of {tenant.name} about RFQ {rfq.code}. "
        f"We need: {'; '.join(f'{line.quantity} {line.uom} {line.description}' for line in lines)[:400]}. "
        "Could you share your unit price, lead time and offer validity?"
    )
    disclosure = policy.disclosure_text_ar if language.startswith("ar") else policy.disclosure_text_en

    call = VoiceCall(
        tenant_id=tenant.id, rfq_id=rfq.id, supplier_id=supplier.id,
        provider=getattr(registry.resolve("telephony"), "name", "fake"), status="dialing",
        country=supplier.country, language=language,
        policy_snapshot={
            "allowed_hours": [policy.allowed_hours_start, policy.allowed_hours_end],
            "recording_consent_mode": policy.recording_consent_mode,
            "disclosure": disclosure, "inbound_opt_in": opted_in,
        },
        started_at=datetime.now(UTC), created_by=requested_by,
    )
    session.add(call)
    await session.flush()

    provider = registry.resolve("telephony")
    result = await provider.place_call(to=phone, script=script, disclosure=disclosure,
                                       country=supplier.country, language=language,
                                       consent_required=policy.recording_consent_mode == "explicit")
    call.provider_call_id = result.provider_call_id
    call.status = result.status
    call.disclosure_played = result.disclosure_played
    call.consent_given = result.consent_given
    call.transcript = result.transcript
    call.recording_uri = result.recording_uri if result.consent_given else ""
    call.duration_seconds = result.duration_seconds
    call.summary = result.summary
    call.ended_at = datetime.now(UTC) if result.status in {"completed", "no_answer", "failed"} else None
    await session.flush()
    await meter(session, tenant_id=tenant.id, kind="voice_minute", quantity=max(1, result.duration_seconds / 60),
                ref_type="voice_call", ref_id=call.id)

    extraction_id = None
    if result.transcript and result.consent_given:
        extraction, offer = await extract_offer(session, tenant, rfq=rfq, supplier=supplier,
                                                payload=ExtractionPayload(text=result.transcript, source="voice"))
        extraction_id = extraction.id
        if offer:
            call.extracted_offer_id = offer.id
    invitation = (await session.execute(
        select(RFQSupplier).where(RFQSupplier.rfq_id == rfq.id, RFQSupplier.supplier_id == supplier.id)
    )).scalar_one_or_none()
    if invitation and result.status == "completed":
        invitation.status = "responded"
        invitation.responded_at = datetime.now(UTC)
    await notify(session, tenant_id=tenant.id, user_id=rfq.buyer_id, kind="voice_call_completed",
                 title=f"AI call to {supplier.name}: {result.status}",
                 title_ar=f"مكالمة آلية مع {supplier.name}: {result.status}",
                 body=result.summary[:300], link=f"/rfqs/{rfq.id}?tab=voice")
    await session.flush()
    return {
        "ok": result.status == "completed", "call_id": call.id, "status": result.status,
        "consent_given": call.consent_given, "disclosure_played": call.disclosure_played,
        "transcript": call.transcript, "duration_seconds": call.duration_seconds,
        "extraction_id": extraction_id, "recording": bool(call.recording_uri),
    }


async def manual_script(session: AsyncSession, tenant: Tenant, rfq: RFQ, supplier: Supplier) -> dict:
    """Switch-to-manual: a prepared script a human buyer can read (no automation)."""
    from app.db.models.entities import RFQLine

    lines = list((await session.execute(select(RFQLine).where(RFQLine.rfq_id == rfq.id))).scalars().all())
    policy = await country_policy(session, supplier.country)
    summary = "; ".join(f"{line.quantity} {line.uom} {line.description}" for line in lines)
    return {
        "rfq_code": rfq.code,
        "supplier": supplier.name,
        "script_ar": (
            f"السلام عليكم، معك {tenant.name} بخصوص طلب عرض السعر {rfq.code}. "
            f"المطلوب: {summary}. ممكن نعرف سعر الوحدة ومدة التوريد وصلاحية العرض؟"
        ),
        "script_en": (
            f"Hello, this is {tenant.name} regarding RFQ {rfq.code}. We need: {summary}. "
            "Could you confirm your unit price, lead time and offer validity?"
        ),
        "disclosure_required": False,
        "note": "Human-initiated calls do not require AI disclosure, but recording still requires consent.",
        "recording_rules": policy.recording_consent_mode if policy else "explicit",
        "deadline": rfq.deadline.isoformat() if rfq.deadline else None,
    }


async def escalate_consent_refusal(session: AsyncSession, tenant: Tenant, call: VoiceCall) -> None:
    """If consent is refused the recording is discarded and only notes are kept."""
    call.recording_uri = ""
    call.consent_given = False
    call.summary = (call.summary or "") + " [consent refused: recording discarded]"
    await session.flush()
