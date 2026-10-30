"""Channel selection and preference resolution (phase 3, requirement 1.1).

The promise being tested: WhatsApp is tried first, but a channel is only chosen when it is reachable
*and* consented; an RFQ can override the supplier; a mixed-channel RFQ resolves each supplier
independently; and every choice records why it was made.
"""
from __future__ import annotations

from sqlalchemy import select

from app.db.models.entities import RFQ, ChannelEvent, ChannelMessage, RFQSupplier, Supplier, Tenant
from app.db.session import SessionLocal
from app.services import channels
from app.services.messaging import set_opt_in
from app.services.suppliers import contact_pii, primary_contact


async def _tenant(session) -> Tenant:
    return (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()


async def _rfq(session, tenant) -> RFQ:
    row = (await session.execute(
        select(RFQ).where(RFQ.tenant_id == tenant.id).order_by(RFQ.created_at.desc())
    )).scalars().first()
    assert row is not None
    return row


async def _suppliers(session, tenant, count: int = 2) -> list[Supplier]:
    return list((await session.execute(
        select(Supplier).where(Supplier.tenant_id == tenant.id).limit(count)
    )).scalars().all())


async def _invite(session, tenant, rfq, supplier, *, channel: str = "email") -> RFQSupplier:
    row = (await session.execute(
        select(RFQSupplier).where(RFQSupplier.rfq_id == rfq.id,
                                  RFQSupplier.supplier_id == supplier.id)
    )).scalar_one_or_none()
    if row is None:
        row = RFQSupplier(tenant_id=tenant.id, rfq_id=rfq.id, supplier_id=supplier.id,
                          status="invited", channel=channel)
        session.add(row)
    else:
        row.channel = channel
    await session.flush()
    return row


async def _phone(session, tenant, supplier, phone: str) -> None:
    contact = await primary_contact(session, tenant, supplier.id)
    if contact is None:
        from app.services.suppliers import upsert_contact

        await upsert_contact(session, tenant, supplier, name="Channel Contact",
                             email=f"channel.{supplier.id[:6]}@example.com", phone=phone)
        return
    from app.core.crypto import encrypt_text

    contact.phone, contact.key_id = await encrypt_text(session, tenant, phone)
    await session.flush()


async def _contact_phone(session, tenant, supplier) -> str:
    contact = await primary_contact(session, tenant, supplier.id)
    if contact is None:
        return ""
    _email, phone = await contact_pii(session, tenant, contact)
    return phone


async def test_whatsapp_wins_when_the_supplier_prefers_it_and_has_consented():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        rfq = await _rfq(session, tenant)
        supplier = (await _suppliers(session, tenant, 1))[0]
        await _phone(session, tenant, supplier, "+20 100 444 5566")
        supplier.preferred_channel = "whatsapp"
        supplier.secondary_channels = ["email"]
        await set_opt_in(session, tenant.id, supplier.id, "whatsapp", "opted_in", source="test")
        invitation = await _invite(session, tenant, rfq, supplier)

        resolved = await channels.resolve(session, tenant, supplier, invitation=invitation, rfq=rfq)
        assert resolved["channel"] == "whatsapp"
        assert resolved["source"] == "supplier_preference"
        assert resolved["available"] is True
        assert resolved["opted_in"] is True
        await session.rollback()


async def test_a_preferred_whatsapp_without_consent_falls_back_to_email_and_says_so():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        supplier = (await _suppliers(session, tenant, 1))[0]
        await _phone(session, tenant, supplier, "+20 100 777 8899")
        supplier.preferred_channel = "whatsapp"
        supplier.secondary_channels = ["email"]
        await set_opt_in(session, tenant.id, supplier.id, "whatsapp", "opted_out", source="test")

        resolved = await channels.resolve(session, tenant, supplier)
        assert resolved["channel"] == "email"
        assert resolved["source"] == "secondary_preference"
        whatsapp_state = next(c for c in resolved["candidates"] if c["channel"] == "whatsapp")
        assert whatsapp_state["available"] is False
        assert "no_opt_in" in whatsapp_state["reasons"]
        await session.rollback()


async def test_an_rfq_override_beats_the_supplier_preference():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        rfq = await _rfq(session, tenant)
        supplier = (await _suppliers(session, tenant, 1))[0]
        await _phone(session, tenant, supplier, "+20 101 222 3344")
        supplier.preferred_channel = "whatsapp"
        await set_opt_in(session, tenant.id, supplier.id, "whatsapp", "opted_in", source="test")
        await set_opt_in(session, tenant.id, supplier.id, "voice", "opted_in", source="test")
        invitation = await _invite(session, tenant, rfq, supplier)

        result = await channels.set_rfq_channel(session, tenant, rfq, invitation, channel="voice",
                                                actor_id="u1", reason="Buyer wants a call")
        assert result["ok"] is True
        assert result["channel"] == "voice"
        assert result["source"] == "rfq_override"
        events = list((await session.execute(
            select(ChannelEvent).where(ChannelEvent.rfq_id == rfq.id,
                                       ChannelEvent.kind == "channel_override")
        )).scalars().all())
        assert events and events[0].detail["reason"] == "Buyer wants a call"
        await session.rollback()


async def test_an_override_to_an_unreachable_channel_needs_a_reason():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        rfq = await _rfq(session, tenant)
        supplier = (await _suppliers(session, tenant, 1))[0]
        invitation = await _invite(session, tenant, rfq, supplier)
        await set_opt_in(session, tenant.id, supplier.id, "voice", "opted_out", source="test")

        blocked = await channels.set_rfq_channel(session, tenant, rfq, invitation, channel="voice",
                                                 actor_id="u1")
        assert blocked["ok"] is False
        assert blocked["error"] == "channel_unavailable"
        assert "no_opt_in" in blocked["reasons"]

        forced = await channels.set_rfq_channel(session, tenant, rfq, invitation, channel="voice",
                                                actor_id="u1", reason="Supplier asked for a call")
        assert forced["ok"] is True
        assert forced["channel"] == "voice"
        await session.rollback()


async def test_a_mixed_channel_rfq_resolves_each_supplier_independently():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        rfq = await _rfq(session, tenant)
        whatsapp_supplier, email_supplier = await _suppliers(session, tenant, 2)

        await _phone(session, tenant, whatsapp_supplier, "+20 100 111 2222")
        whatsapp_supplier.preferred_channel = "whatsapp"
        await set_opt_in(session, tenant.id, whatsapp_supplier.id, "whatsapp", "opted_in", source="test")
        email_supplier.preferred_channel = "email"
        await _invite(session, tenant, rfq, whatsapp_supplier)
        await _invite(session, tenant, rfq, email_supplier)

        overview = await channels.rfq_channels(session, tenant, rfq)
        resolved = {item["supplier_id"]: item for item in overview["items"]}
        assert resolved[whatsapp_supplier.id]["channel"] == "whatsapp"
        assert resolved[email_supplier.id]["channel"] == "email"
        assert overview["mixed_channels"] is True
        assert set(overview["channels_in_use"]) == {"whatsapp", "email"}
        await session.rollback()


async def test_switching_channel_keeps_every_earlier_message_on_its_own_channel():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        rfq = await _rfq(session, tenant)
        supplier = (await _suppliers(session, tenant, 1))[0]
        await _phone(session, tenant, supplier, "+20 109 333 4455")
        await set_opt_in(session, tenant.id, supplier.id, "voice", "opted_in", source="test")
        invitation = await _invite(session, tenant, rfq, supplier, channel="email")
        from app.services.messaging import ensure_thread

        thread = await ensure_thread(session, tenant, rfq, supplier, "email")
        session.add(ChannelMessage(tenant_id=tenant.id, thread_id=thread.id, rfq_id=rfq.id,
                                   supplier_id=supplier.id, direction="outbound", channel="email",
                                   body="Original email", status="sent"))
        await session.flush()

        result = await channels.switch_channel(session, tenant, rfq, supplier, invitation,
                                               channel="voice", actor_id="u1", reason="No email reply")
        assert result["ok"] is True and result["to"] == "voice"

        message = (await session.execute(
            select(ChannelMessage).where(ChannelMessage.thread_id == thread.id)
        )).scalars().first()
        assert message.channel == "email", "switching must not rewrite history"
        event = (await session.execute(
            select(ChannelEvent).where(ChannelEvent.kind == "channel_switch",
                                       ChannelEvent.rfq_id == rfq.id,
                                       ChannelEvent.supplier_id == supplier.id)
        )).scalars().first()
        assert event is not None and event.detail["from"] == "email" and event.detail["to"] == "voice"
        await session.rollback()


async def test_preferences_are_normalised_deduplicated_and_keep_the_preferred_out_of_secondary():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        supplier = (await _suppliers(session, tenant, 1))[0]
        result = await channels.set_supplier_preferences(
            session, tenant, supplier, preferred_channel="wa",
            secondary_channels=["email", "email", "whatsapp", "phone"], preferred_language="ar",
        )
        assert result["preferred_channel"] == "whatsapp"
        assert result["secondary_channels"] == ["email", "voice"]
        await session.rollback()


async def test_a_blacklisted_supplier_has_no_usable_channel_even_with_a_number():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        supplier = (await _suppliers(session, tenant, 1))[0]
        await _phone(session, tenant, supplier, "+20 102 555 6677")
        await set_opt_in(session, tenant.id, supplier.id, "whatsapp", "opted_in", source="test")
        supplier.status = "blacklisted"

        state = await channels.availability(session, tenant, supplier, "whatsapp")
        assert state["available"] is False
        assert "supplier_blacklisted" in state["reasons"]
        await session.rollback()


async def test_the_tenant_flag_removes_whatsapp_from_resolution():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        supplier = (await _suppliers(session, tenant, 1))[0]
        await _phone(session, tenant, supplier, "+20 103 666 7788")
        supplier.preferred_channel = "whatsapp"
        supplier.secondary_channels = ["email"]
        await set_opt_in(session, tenant.id, supplier.id, "whatsapp", "opted_in", source="test")
        tenant.settings = {**(tenant.settings or {}), "features": {"whatsapp": False}}

        resolved = await channels.resolve(session, tenant, supplier)
        assert resolved["channel"] == "email"
        assert resolved["source"] == "secondary_preference"
        tenant.settings = {**(tenant.settings or {}), "features": {"whatsapp": True}}
        await session.rollback()


async def test_communication_status_moves_sent_delivered_read_replied():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        rfq = await _rfq(session, tenant)
        supplier = (await _suppliers(session, tenant, 1))[0]
        invitation = await _invite(session, tenant, rfq, supplier)
        outbound = ChannelMessage(tenant_id=tenant.id, thread_id="status-thread", rfq_id=rfq.id,
                                  supplier_id=supplier.id, direction="outbound", channel="whatsapp",
                                  body="Hello", status="sent", delivery_status="sent")
        session.add(outbound)
        await session.flush()

        assert await channels.refresh_comm_status(session, invitation) == "sent"
        outbound.delivery_status = "delivered"
        assert await channels.refresh_comm_status(session, invitation) == "delivered"
        outbound.delivery_status = "read"
        assert await channels.refresh_comm_status(session, invitation) == "read"
        session.add(ChannelMessage(tenant_id=tenant.id, thread_id="status-thread", rfq_id=rfq.id,
                                   supplier_id=supplier.id, direction="inbound", channel="whatsapp",
                                   body="Our price is 21,000", status="received"))
        await session.flush()
        assert await channels.refresh_comm_status(session, invitation) == "replied"
        await session.rollback()


async def test_supplier_channel_addresses_are_masked_in_payloads():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        supplier = (await _suppliers(session, tenant, 1))[0]
        await _phone(session, tenant, supplier, "+20 100 999 8877")
        state = await channels.availability(session, tenant, supplier, "whatsapp")
        assert state["address_hint"].endswith("8877")
        assert "+20" not in state["address_hint"]
        assert await _contact_phone(session, tenant, supplier)
        await session.rollback()


async def test_an_email_unsubscribe_is_honoured_before_extraction_on_any_channel():
    """Consent is a channel-wide contract: an email STOP opts out and never becomes an offer."""
    import uuid

    from app.adapters.base import InboundMessage
    from app.db.models.entities import OptInRegistry
    from app.services import extraction
    from app.services.suppliers import upsert_contact

    suffix = uuid.uuid4().hex[:6]
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        supplier = Supplier(tenant_id=tenant.id, name=f"Consent Supplier {suffix}",
                            name_ar=f"مورد الموافقة {suffix}", status="approved",
                            preferred_channel="email")
        rfq = RFQ(tenant_id=tenant.id, code=f"RFQ-CONSENT-{suffix}", title=f"Consent RFQ {suffix}",
                  status="sent", currency="EGP", payment_terms="Net 30")
        session.add_all([supplier, rfq])
        await session.flush()
        email = f"consent.{suffix}@example.com"
        await upsert_contact(session, tenant, supplier, name="Consent Contact", email=email,
                             phone=f"+2011{uuid.uuid4().int % 10**6:06d}")
        await _invite(session, tenant, rfq, supplier)

        inbound = InboundMessage(channel="email", external_id=f"consent-{suffix}", sender=email,
                                 subject=f"{rfq.code} re: quotation", body="unsubscribe")
        result = await extraction.ingest_inbound(session, tenant, inbound)

        assert result["matched"] is False and result["reason"] == "opt_out"
        assert result["rfq_id"] == rfq.id
        opt_in = (await session.execute(
            select(OptInRegistry).where(OptInRegistry.supplier_id == supplier.id,
                                        OptInRegistry.channel == "email")
        )).scalar_one()
        assert opt_in.status == "opted_out" and opt_in.source == "supplier_message"
        assert (await session.execute(
            select(ChannelMessage).where(ChannelMessage.supplier_id == supplier.id)
        )).scalars().first() is None, "an opt-out must not be parsed as a supplier offer"
        await session.rollback()
