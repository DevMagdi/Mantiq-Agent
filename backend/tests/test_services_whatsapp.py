"""WhatsApp as a first-class channel: send, receive, extract, receipts, consent, media (1.2).

Every test goes through the real service path (`messaging.send_message` /
`extraction.ingest_inbound`); only the provider is the deterministic fake.
"""
from __future__ import annotations

import hashlib

from sqlalchemy import select

from app.adapters.base import registry
from app.adapters.whatsapp import FakeWhatsAppProvider
from app.db.models.entities import (
    RFQ,
    Attachment,
    ChannelEvent,
    ChannelMessage,
    Offer,
    OptInRegistry,
    RFQSupplier,
    Supplier,
    Tenant,
)
from app.db.session import SessionLocal
from app.services import whatsapp_channel
from app.services.messaging import apply_delivery_status, set_opt_in
from app.services.suppliers import upsert_contact


def _fake() -> FakeWhatsAppProvider:
    provider = registry.resolve("whatsapp")
    assert isinstance(provider, FakeWhatsAppProvider), type(provider)
    return provider


def _phone() -> str:
    """A unique number per scene: inbound matching resolves a phone to exactly one supplier."""
    import uuid

    return f"+2010{uuid.uuid4().int % 10**6:06d}"


async def _scene(session, *, phone: str = ""):
    """A fresh supplier and RFQ per test: inbound matching must have exactly one candidate."""
    import uuid

    phone = phone or _phone()
    suffix = uuid.uuid4().hex[:6]
    tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
    rfq = RFQ(tenant_id=tenant.id, code=f"RFQ-WA-{suffix}", title=f"WhatsApp RFQ {suffix}",
              status="sent", currency="EGP", payment_terms="Net 30")
    supplier = Supplier(tenant_id=tenant.id, name=f"WhatsApp Supplier {suffix}",
                        name_ar=f"مورد واتساب {suffix}", status="approved",
                        preferred_language="ar", preferred_channel="whatsapp")
    session.add_all([rfq, supplier])
    await session.flush()
    await upsert_contact(session, tenant, supplier, name="WhatsApp Contact",
                         email=f"wa.{suffix}@example.com", phone=phone)
    invitation = RFQSupplier(tenant_id=tenant.id, rfq_id=rfq.id, supplier_id=supplier.id,
                             status="invited", channel="whatsapp")
    session.add(invitation)
    await set_opt_in(session, tenant.id, supplier.id, "whatsapp", "opted_in", source="test")
    await session.flush()
    _fake().clear()
    return tenant, rfq, supplier, invitation, phone


async def test_text_message_records_the_message_the_event_and_the_status():
    async with SessionLocal() as session:
        tenant, rfq, supplier, invitation, phone = await _scene(session)
        result = await whatsapp_channel.send_rich(session, tenant, rfq=rfq, supplier=supplier,
                                                  body="Hello, please quote RFQ 2026", kind="text")
        assert result["ok"] is True, result
        message = (await session.execute(
            select(ChannelMessage).where(ChannelMessage.id == result["message_id"])
        )).scalar_one()
        assert message.channel == "whatsapp" and message.direction == "outbound"
        assert message.delivery_status == "sent" and message.media_type == "text"
        assert message.provider_message_id

        event = (await session.execute(
            select(ChannelEvent).where(ChannelEvent.message_id == message.id,
                                       ChannelEvent.kind == "sent")
        )).scalars().first()
        assert event is not None and event.channel == "whatsapp"
        assert invitation.comm_status == "sent"
        assert _fake().last_outbound()["type"] == "text"
        await session.rollback()


async def test_an_approved_template_is_used_when_one_exists():
    async with SessionLocal() as session:
        tenant, rfq, supplier, _invitation, phone = await _scene(session)
        result = await whatsapp_channel.send_rich(session, tenant, rfq=rfq, supplier=supplier,
                                                  template_code="quote_request", locale="ar",
                                                  kind="template")
        assert result["ok"] is True, result
        sent = _fake().last_outbound()
        assert sent["type"] == "template"
        assert sent["template"] == "quote_request_ar"
        message = (await session.execute(
            select(ChannelMessage).where(ChannelMessage.id == result["message_id"])
        )).scalar_one()
        assert message.template_code == "quote_request"
        assert message.locale == "ar"
        await session.rollback()


async def test_a_structured_quote_request_is_an_interactive_message():
    async with SessionLocal() as session:
        tenant, rfq, supplier, _invitation, phone = await _scene(session)
        result = await whatsapp_channel.send_rich(session, tenant, rfq=rfq, supplier=supplier,
                                                  body="Unit price, lead time, validity?",
                                                  quote_request=True)
        assert result["ok"] is True, result
        sent = _fake().last_outbound()
        assert sent["type"] == "interactive"
        assert sent["options"] == ["Unit price", "Lead time", "Validity"]
        assert sent["flow"].startswith("quote:")
        message = (await session.execute(
            select(ChannelMessage).where(ChannelMessage.id == result["message_id"])
        )).scalar_one()
        assert message.media_type == "interactive"
        await session.rollback()


async def test_a_document_can_be_sent_on_whatsapp():
    async with SessionLocal() as session:
        tenant, rfq, supplier, _invitation, phone = await _scene(session)
        payload = b"%PDF-1.4 quotation"
        result = await whatsapp_channel.send_rich(
            session, tenant, rfq=rfq, supplier=supplier, body="The specification",
            kind="document", attachments=[{"filename": "spec.pdf", "data": payload,
                                           "mime": "application/pdf"}],
        )
        assert result["ok"] is True, result
        sent = _fake().last_outbound()
        assert sent["type"] == "document" and sent["filename"] == "spec.pdf"
        assert sent["size"] == len(payload)
        await session.rollback()


async def test_an_inbound_reply_becomes_a_verifiable_offer_and_marks_the_invitation_replied():
    async with SessionLocal() as session:
        tenant, rfq, supplier, invitation, phone = await _scene(session)
        body = (f"بخصوص {rfq.code}: سعر الطن 21000 جنيه، التسليم خلال 21 يوم، صلاحية العرض 14 يوم، "
                "الدفع Net 30")
        inbound = _fake().queue_inbound(sender=phone, body=body)
        result = await whatsapp_channel.ingest_inbound_whatsapp(session, tenant, inbound)

        assert result["matched"] is True, result
        assert result["extractions"], "the reply must enter the verification queue"
        offers = list((await session.execute(
            select(Offer).where(Offer.supplier_id == supplier.id)
        )).scalars().all())
        assert offers, "an extracted offer must exist for the buyer to verify"
        assert invitation.reply_count >= 1
        assert invitation.comm_status == "replied"

        message = (await session.execute(
            select(ChannelMessage).where(ChannelMessage.provider_message_id == inbound.external_id)
        )).scalars().first()
        assert message is not None and message.direction == "inbound" and message.channel == "whatsapp"
        events = list((await session.execute(
            select(ChannelEvent).where(ChannelEvent.message_id == message.id)
        )).scalars().all())
        assert any(event.kind == "received" for event in events)
        await session.rollback()


async def test_replying_on_whatsapp_records_consent_even_if_none_was_recorded_before():
    async with SessionLocal() as session:
        tenant, rfq, supplier, _invitation, phone = await _scene(session)
        await set_opt_in(session, tenant.id, supplier.id, "whatsapp", "opted_out", source="test")
        inbound = _fake().queue_inbound(sender=phone, body=f"{rfq.code} unit price 20500")
        await whatsapp_channel.ingest_inbound_whatsapp(session, tenant, inbound)
        opt_in = (await session.execute(
            select(OptInRegistry).where(OptInRegistry.supplier_id == supplier.id,
                                        OptInRegistry.channel == "whatsapp")
        )).scalar_one()
        assert opt_in.status == "opted_in"
        assert opt_in.source == "inbound_reply"
        await session.rollback()


async def test_a_stop_keyword_opts_the_supplier_out_and_stops_the_pipeline():
    async with SessionLocal() as session:
        tenant, rfq, supplier, _invitation, phone = await _scene(session)
        inbound = _fake().queue_inbound(sender=phone, body="STOP")
        result = await whatsapp_channel.ingest_inbound_whatsapp(session, tenant, inbound)
        assert result["matched"] is False and result["reason"] == "opted_out"

        opt_in = (await session.execute(
            select(OptInRegistry).where(OptInRegistry.supplier_id == supplier.id,
                                        OptInRegistry.channel == "whatsapp")
        )).scalar_one()
        assert opt_in.status == "opted_out"
        assert opt_in.source == "supplier_message"
        event = (await session.execute(
            select(ChannelEvent).where(ChannelEvent.kind == "opt_out")
        )).scalars().first()
        assert event is not None
        await session.rollback()


async def test_delivery_and_read_receipts_move_forward_but_never_backwards():
    async with SessionLocal() as session:
        tenant, rfq, supplier, invitation, phone = await _scene(session)
        result = await whatsapp_channel.send_rich(session, tenant, rfq=rfq, supplier=supplier,
                                                  body="Reminder")
        message = (await session.execute(
            select(ChannelMessage).where(ChannelMessage.id == result["message_id"])
        )).scalar_one()

        read = await apply_delivery_status(session, tenant_id=tenant.id,
                                           provider_message_id=message.provider_message_id,
                                           status="read")
        assert read["updated"] is True
        assert message.delivery_status == "read" and message.read_at is not None
        assert invitation.read_at is not None

        stale = await apply_delivery_status(session, tenant_id=tenant.id,
                                           provider_message_id=message.provider_message_id,
                                           status="delivered")
        assert stale["updated"] is False and stale["reason"] == "stale_status"
        assert message.delivery_status == "read"

        unknown = await apply_delivery_status(session, tenant_id=tenant.id,
                                              provider_message_id="does-not-exist", status="delivered")
        assert unknown["updated"] is False and unknown["reason"] == "unknown_message"
        await session.rollback()


async def test_a_webhook_delivery_processes_receipts_and_messages_in_one_call():
    async with SessionLocal() as session:
        tenant, rfq, supplier, _invitation, phone = await _scene(session)
        sent = await whatsapp_channel.send_rich(session, tenant, rfq=rfq, supplier=supplier,
                                                body="Please quote")
        message = (await session.execute(
            select(ChannelMessage).where(ChannelMessage.id == sent["message_id"])
        )).scalar_one()

        digits = phone.replace("+", "").replace(" ", "")
        payload = {
            "entry": [{"changes": [{"value": {
                "statuses": [{"id": message.provider_message_id, "status": "delivered",
                              "recipient_id": digits, "timestamp": "1760000000"}],
                "messages": [{"id": "wamid.inbound-1", "from": digits, "type": "text",
                              "timestamp": "1760000001",
                              "text": {"body": f"{rfq.code} price 20900, lead time 20 days"}}],
                "contacts": [{"wa_id": digits, "profile": {"name": "Ahmed"}}],
            }}]}]
        }
        handled = await whatsapp_channel.handle_webhook(session, tenant, payload)
        assert handled["processed"] == 1
        assert handled["statuses"][0]["status"] == "delivered"
        assert handled["messages"][0]["matched"] is True
        await session.rollback()


async def test_an_inbound_document_is_stored_and_extracted():
    async with SessionLocal() as session:
        tenant, rfq, supplier, _invitation, phone = await _scene(session)
        text = (f"Quotation for {rfq.code}\nUnit price: 20750\nLead time: 18 days\n"
                "Validity: 30 days\nPayment terms: Net 45")
        inbound = _fake().queue_media(sender=phone, filename="quote.txt",
                                      data=text.encode("utf-8"), caption="")
        result = await whatsapp_channel.ingest_inbound_whatsapp(session, tenant, inbound)
        assert result["matched"] is True
        assert result["extractions"]
        attachment = (await session.execute(
            select(Attachment).where(Attachment.rfq_id == rfq.id,
                                     Attachment.filename == "quote.txt")
        )).scalars().first()
        assert attachment is not None
        assert attachment.sha256 == hashlib.sha256(text.encode()).hexdigest()
        await session.rollback()


async def test_a_disabled_whatsapp_flag_blocks_the_send_before_the_provider():
    async with SessionLocal() as session:
        tenant, rfq, supplier, _invitation, phone = await _scene(session)
        tenant.settings = {**(tenant.settings or {}), "features": {"whatsapp": False}}
        result = await whatsapp_channel.send_rich(session, tenant, rfq=rfq, supplier=supplier,
                                                  body="blocked")
        assert result["ok"] is False
        assert result["codes"] == ["BLOCK_FEATURE_OFF"]
        assert _fake().outbox == []
        tenant.settings = {**(tenant.settings or {}), "features": {"whatsapp": True}}
        await session.rollback()
