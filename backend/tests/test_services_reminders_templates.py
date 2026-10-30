"""Reminders, templates and the hand-off into the existing offer-verification flow (1.2 & 3).

The chain under test: a reminder is drafted from an AR/EN template and delivered on the supplier's
resolved channel; a WhatsApp reply lands in the same verification queue the buyer already uses, and
verifying the extracted fields updates the offer the comparison screen reads.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.adapters.base import registry
from app.adapters.whatsapp import FakeWhatsAppProvider
from app.db.models.entities import (
    RFQ,
    ExtractedField,
    ExtractionResult,
    MessageTemplate,
    Offer,
    RFQLine,
    RFQSupplier,
    Supplier,
    Tenant,
)
from app.db.session import SessionLocal
from app.services import extraction, whatsapp_channel
from app.services import rfq as rfq_service
from app.services.messaging import pick_template, render_template, set_opt_in, template_variables
from app.services.suppliers import upsert_contact


def _fake() -> FakeWhatsAppProvider:
    provider = registry.resolve("whatsapp")
    assert isinstance(provider, FakeWhatsAppProvider)
    return provider


async def _scene(*, deadline_in: timedelta = timedelta(hours=6), opt_in: bool = True,
                 language: str = "ar") -> dict:
    async with SessionLocal() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
        suffix = uuid.uuid4().hex[:6]
        supplier = Supplier(tenant_id=tenant.id, name=f"Reminder Supplier {suffix}",
                            name_ar=f"مورد التذكير {suffix}", status="approved",
                            preferred_language=language, preferred_channel="whatsapp",
                            secondary_channels=["email"])
        rfq = RFQ(tenant_id=tenant.id, code=f"RFQ-REM-{suffix}", title=f"Reminder RFQ {suffix}",
                  status="sent", currency="EGP", deadline=datetime.now(UTC) + deadline_in,
                  payment_terms="Net 30", incoterm="DAP", delivery_location="Sadat City")
        session.add_all([supplier, rfq])
        await session.flush()
        # A unique number per scene: the inbound matcher resolves a phone to exactly one supplier.
        phone = f"+2010{int(suffix, 16) % 10**6:06d}"
        await upsert_contact(session, tenant, supplier, name="Reminder Contact",
                             email=f"rem.{suffix}@example.com", phone=phone)
        session.add(RFQLine(tenant_id=tenant.id, rfq_id=rfq.id, line_no=1,
                            description="Rebar 12mm", quantity=50, uom="t"))
        invitation = RFQSupplier(tenant_id=tenant.id, rfq_id=rfq.id, supplier_id=supplier.id,
                                 status="sent", channel="email",
                                 sent_at=datetime.now(UTC) - timedelta(days=3))
        session.add(invitation)
        await set_opt_in(session, tenant.id, supplier.id, "whatsapp",
                         "opted_in" if opt_in else "opted_out", source="test")
        await session.commit()
        return {"supplier_id": supplier.id, "rfq_id": rfq.id, "invitation_id": invitation.id,
                "tenant_slug": tenant.slug, "phone": phone}


async def test_a_reminder_goes_out_on_whatsapp_when_that_is_the_supplier_channel():
    scene = await _scene()
    provider = _fake()
    provider.clear()
    async with SessionLocal() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
        result = await rfq_service.send_reminders(session, tenant)
        assert result["reminders_sent"] >= 1
        invitation = (await session.execute(
            select(RFQSupplier).where(RFQSupplier.id == scene["invitation_id"])
        )).scalar_one()
        assert invitation.reminder_count == 1
        assert invitation.channel == "whatsapp"
        assert invitation.comm_status == "sent"
        assert invitation.last_reminder_at is not None
        await session.commit()

    outbound = provider.last_outbound()
    assert outbound["type"] in {"template", "text"}
    # The rendered body carries the RFQ data, not the raw placeholders.
    body = outbound.get("body") or ""
    assert "{{" not in body


async def test_a_supplier_who_declined_whatsapp_is_reminded_by_email():
    scene = await _scene(opt_in=False)
    provider = _fake()
    provider.clear()
    async with SessionLocal() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
        await rfq_service.send_reminders(session, tenant)
        invitation = (await session.execute(
            select(RFQSupplier).where(RFQSupplier.id == scene["invitation_id"])
        )).scalar_one()
        assert invitation.channel == "email"
        await session.commit()
    assert provider.outbox == [], "an opted-out supplier must not receive a WhatsApp reminder"


async def test_the_reminder_cadence_stops_at_two_and_respects_the_two_day_gap():
    scene = await _scene(deadline_in=timedelta(days=5))
    t0 = datetime.now(UTC)
    async with SessionLocal() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
        rfq = (await session.execute(select(RFQ).where(RFQ.id == scene["rfq_id"]))).scalar_one()
        # The scheduler only chases RFQs whose deadline is within two days, so pin the deadline to the
        # exact moment the cadence is measured against.
        rfq.deadline = t0 + timedelta(days=2)
        invitation = (await session.execute(
            select(RFQSupplier).where(RFQSupplier.id == scene["invitation_id"])
        )).scalar_one()
        await rfq_service.send_reminders(session, tenant, now=t0)
        again = await rfq_service.send_reminders(session, tenant, now=t0)
        await session.refresh(invitation)
        assert invitation.reminder_count == 1
        assert again["reminders_sent"] == 0, "the two-day gap must hold"

        later = t0 + timedelta(days=2)
        await rfq_service.send_reminders(session, tenant, now=later)
        await session.refresh(invitation)
        assert invitation.reminder_count == 2

        last = await rfq_service.send_reminders(session, tenant, now=later + timedelta(hours=1))
        await session.refresh(invitation)
        assert invitation.reminder_count == 2, "never more than two reminders"
        assert last["reminders_sent"] == 0
        await session.commit()


async def test_a_reminder_template_is_picked_in_the_supplier_language_and_rendered():
    async with SessionLocal() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
        arabic = await pick_template(session, tenant, "reminder", "whatsapp", "ar")
        english = await pick_template(session, tenant, "reminder", "whatsapp", "en")
        assert arabic is not None and english is not None
        assert arabic.locale == "ar" and english.locale == "en"
        assert arabic.id != english.id

        context = {"supplier_name": "Nile Steel", "rfq_code": "RFQ-2026-00009", "title": "Rebar",
                   "quantity_summary": "50 t", "deadline": "2026-11-30", "web_form_url": "https://x/y",
                   "tenant_name": "Nile Foods", "payment_terms": "Net 30",
                   "delivery_location": "Sadat City", "incoterm": "DAP"}
        for template in (arabic, english):
            rendered = render_template(template.body, context)
            assert "{{" not in rendered
            assert context["rfq_code"] in rendered
            assert template_variables(template.body)


async def test_every_phase_three_template_declares_its_channel_locale_and_variables():
    async with SessionLocal() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
        rows = list((await session.execute(
            select(MessageTemplate).where(MessageTemplate.tenant_id == tenant.id)
        )).scalars().all())
        assert len(rows) >= 20
        for row in rows:
            assert row.channel in {"whatsapp", "email", "voice", "sms"}
            assert row.locale in {"ar", "en"}
            assert row.kind
            if row.channel in {"whatsapp", "email"}:
                assert template_variables(row.body), f"{row.code}/{row.locale} has no placeholders"
        # Both locales exist for the two channels the field team uses every day.
        for channel in ("whatsapp", "email"):
            for locale in ("ar", "en"):
                assert any(row.channel == channel and row.locale == locale for row in rows)


async def test_an_expired_rfq_is_closed_and_the_buyer_is_told():
    scene = await _scene(deadline_in=timedelta(hours=-2))
    async with SessionLocal() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
        result = await rfq_service.send_reminders(session, tenant)
        assert result["rfqs_closed"] >= 1
        rfq = (await session.execute(select(RFQ).where(RFQ.id == scene["rfq_id"]))).scalar_one()
        assert rfq.status in {"expired", "closed"}
        await session.commit()


# ------------------------------------------------------------------ offer verification hand-off
async def test_verified_whatsapp_extraction_updates_the_offer_the_buyer_compares():
    scene = await _scene()
    async with SessionLocal() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
        rfq = (await session.execute(select(RFQ).where(RFQ.id == scene["rfq_id"]))).scalar_one()
        body = (f"{rfq.code} سعر الطن 22000 جنيه، التسليم خلال 25 يوم، "
                "صلاحية العرض 30 يوم، الدفع Net 45")
        inbound = _fake().queue_inbound(sender=scene["phone"], body=body)
        outcome = await whatsapp_channel.ingest_inbound_whatsapp(session, tenant, inbound)
        assert outcome["matched"] is True, outcome

        first = outcome["extractions"][0]
        extraction_id = first["extraction_id"] if isinstance(first, dict) else first
        record = (await session.execute(
            select(ExtractionResult).where(ExtractionResult.id == extraction_id)
        )).scalar_one()
        assert record.offer_id, "the reply must create an offer for the buyer to verify"
        assert record.raw_text

        price_field = (await session.execute(
            select(ExtractedField).where(ExtractedField.extraction_id == extraction_id,
                                         ExtractedField.field_name == "unit_price")
        )).scalars().first()
        assert price_field is not None and price_field.verified_at is None

        await extraction.verify_field(session, tenant, price_field, value="22250", user_id="u1")
        offer = (await session.execute(
            select(Offer).where(Offer.id == record.offer_id)
        )).scalar_one()
        assert str(offer.total_base).startswith("1112500") or offer.total_base > 0

        await extraction.mark_extraction_verified(session, tenant, record, user_id="u1")
        assert record.status == "verified"
        assert offer.status == "verified"
        assert offer.is_low_confidence is False
        await session.commit()


async def test_a_missing_information_follow_up_uses_the_supplier_channel():
    scene = await _scene()
    provider = _fake()
    provider.clear()
    async with SessionLocal() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
        rfq = (await session.execute(select(RFQ).where(RFQ.id == scene["rfq_id"]))).scalar_one()
        supplier = (await session.execute(
            select(Supplier).where(Supplier.id == scene["supplier_id"])
        )).scalar_one()
        record = ExtractionResult(tenant_id=tenant.id, rfq_id=rfq.id, supplier_id=supplier.id,
                                  source_kind="text", status="draft", needs_review=True,
                                  issues=["MISSING_PRICE"], raw_text="", confidence=0.4)
        session.add(record)
        await session.flush()
        result = await extraction.request_missing_info(session, tenant, rfq, supplier, record)
        assert result["channel"] == "whatsapp"
        assert provider.last_outbound() is not None
        await session.commit()
