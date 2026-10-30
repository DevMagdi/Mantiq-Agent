"""The unified inbox: one conversation per supplier+RFQ, every channel visible (1.5 & 3).

The inbox is the place where a mixed-channel conversation has to stay legible: the same buyer sees an
email, then a WhatsApp message, then a voice note on one timeline, and the reply goes out on the
channel the supplier last used.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.adapters.base import registry
from app.adapters.whatsapp import FakeWhatsAppProvider
from app.db.models.entities import (
    RFQ,
    ChannelMessage,
    MessageThread,
    RFQSupplier,
    Supplier,
    Tenant,
)
from app.db.session import SessionLocal
from app.services import inbox
from app.services.messaging import set_opt_in
from app.services.suppliers import upsert_contact


async def _scene(session, *, phone: str = "+20 100 555 1212"):
    """A dedicated supplier and RFQ so the thread is fresh and the assertions are deterministic."""
    tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
    suffix = uuid.uuid4().hex[:6]
    supplier = Supplier(tenant_id=tenant.id, name=f"Inbox Supplier {suffix}",
                        name_ar=f"مورد البريد {suffix}", status="approved", preferred_language="ar")
    rfq = RFQ(tenant_id=tenant.id, code=f"RFQ-INBOX-{suffix}", title=f"Inbox RFQ {suffix}",
              status="sent", currency="EGP")
    session.add_all([supplier, rfq])
    await session.flush()
    await upsert_contact(session, tenant, supplier, name="Inbox Contact",
                         email=f"inbox.{suffix}@example.com", phone=phone)
    invitation = RFQSupplier(tenant_id=tenant.id, rfq_id=rfq.id, supplier_id=supplier.id,
                             status="sent", channel="email")
    session.add(invitation)
    await set_opt_in(session, tenant.id, supplier.id, "whatsapp", "opted_in", source="test")
    await set_opt_in(session, tenant.id, supplier.id, "email", "opted_in", source="test")
    await session.flush()
    return tenant, rfq, supplier, invitation


async def _messages(session, tenant, rfq, supplier, *, channel: str = "email") -> MessageThread:
    """The canonical thread for the pair — the same rule `messaging.ensure_thread` uses."""
    thread = (await session.execute(
        select(MessageThread).where(MessageThread.tenant_id == tenant.id,
                                    MessageThread.rfq_id == rfq.id,
                                    MessageThread.supplier_id == supplier.id)
    )).scalars().first()
    if thread is not None:
        thread.channel = channel
        return thread
    thread = MessageThread(tenant_id=tenant.id, rfq_id=rfq.id, supplier_id=supplier.id,
                           channel=channel, subject=f"RFQ {rfq.code}",
                           last_message_at=datetime.now(UTC))
    session.add(thread)
    await session.flush()
    return thread


async def test_one_thread_shows_every_channel_the_supplier_touched():
    async with SessionLocal() as session:
        tenant, rfq, supplier, _invitation = await _scene(session)
        thread = await _messages(session, tenant, rfq, supplier)
        now = datetime.now(UTC)
        session.add_all([
            ChannelMessage(tenant_id=tenant.id, thread_id=thread.id, rfq_id=rfq.id,
                           supplier_id=supplier.id, direction="outbound", channel="email",
                           subject=f"RFQ {rfq.code}", body="Please quote the attached lines.",
                           status="sent", created_at=now - timedelta(hours=5)),
            ChannelMessage(tenant_id=tenant.id, thread_id=thread.id, rfq_id=rfq.id,
                           supplier_id=supplier.id, direction="inbound", channel="whatsapp",
                           body="السعر 21000 للطن", status="received",
                           created_at=now - timedelta(hours=1)),
        ])
        await session.flush()

        listing = await inbox.conversations(session, tenant, supplier_id=supplier.id)
        item = next(row for row in listing["items"] if row["thread_id"] == thread.id)
        assert item["channels_used"] == ["email", "whatsapp"]
        assert item["last_message"]["channel"] == "whatsapp"
        assert item["last_message"]["channel_label_ar"]
        assert item["awaiting_reply"] is False, "the supplier spoke last"
        assert item["comm_status"] == "replied"

        detail = await inbox.thread_detail(session, tenant, thread)
        assert [message["channel"] for message in detail["messages"]] == ["email", "whatsapp"]
        assert detail["reply_channel"] == "whatsapp"
        await session.rollback()


async def test_the_reply_goes_out_on_the_channel_the_supplier_used():
    async with SessionLocal() as session:
        tenant, rfq, supplier, _invitation = await _scene(session)
        supplier.preferred_channel = "email"
        thread = await _messages(session, tenant, rfq, supplier, channel="email")
        session.add(ChannelMessage(tenant_id=tenant.id, thread_id=thread.id, rfq_id=rfq.id,
                                   supplier_id=supplier.id, direction="inbound", channel="whatsapp",
                                   body="أرسل لي التأكيد على الواتساب", status="received"))
        await session.flush()

        provider = registry.resolve("whatsapp")
        assert isinstance(provider, FakeWhatsAppProvider)
        provider.clear()
        result = await inbox.reply(session, tenant, thread, body="بالتأكيد، سيصلك الآن",
                                   author_id="u1")
        assert result["ok"] is True, result
        assert result["channel"] == "whatsapp"
        assert result["switched_from"] == "email"
        assert provider.last_outbound()["type"] in {"text", "template"}
        assert thread.channel == "whatsapp"
        await session.rollback()


async def test_a_buyer_can_force_a_different_channel_and_it_is_recorded():
    async with SessionLocal() as session:
        tenant, rfq, supplier, _invitation = await _scene(session)
        thread = await _messages(session, tenant, rfq, supplier)
        session.add(ChannelMessage(tenant_id=tenant.id, thread_id=thread.id, rfq_id=rfq.id,
                                   supplier_id=supplier.id, direction="inbound", channel="email",
                                   body="Unit price 21000", status="received"))
        await session.flush()
        result = await inbox.reply(session, tenant, thread, body="A quick follow-up",
                                   channel="whatsapp", author_id="u1")
        assert result["ok"] is True, result
        assert result["channel"] == "whatsapp"
        assert result["switched_from"] == "email"
        await session.rollback()


async def test_replying_on_a_channel_without_consent_is_refused_with_the_reason():
    async with SessionLocal() as session:
        tenant, rfq, supplier, _invitation = await _scene(session)
        await set_opt_in(session, tenant.id, supplier.id, "whatsapp", "opted_out", source="test")
        thread = await _messages(session, tenant, rfq, supplier)
        result = await inbox.reply(session, tenant, thread, body="Hello", channel="whatsapp",
                                   author_id="u1")
        assert result["ok"] is False
        assert result["error"] == "channel_unavailable"
        assert "no_opt_in" in result["reasons"]
        await session.rollback()


async def test_search_finds_a_message_on_any_channel_and_counts_per_channel():
    async with SessionLocal() as session:
        tenant, rfq, supplier, _invitation = await _scene(session)
        thread = await _messages(session, tenant, rfq, supplier)
        session.add_all([
            ChannelMessage(tenant_id=tenant.id, thread_id=thread.id, rfq_id=rfq.id,
                           supplier_id=supplier.id, direction="inbound", channel="email",
                           body="Our best price for rebar is 21000 EGP per tonne.", status="received"),
            ChannelMessage(tenant_id=tenant.id, thread_id=thread.id, rfq_id=rfq.id,
                           supplier_id=supplier.id, direction="inbound", channel="whatsapp",
                           body="سعر الحديد 21000 جنيه للطن", status="received"),
        ])
        await session.flush()
        found = await inbox.search(session, tenant, q="21000")
        assert found["total"] >= 2
        assert set(found["by_channel"]) >= {"email", "whatsapp"}
        assert all("21000" in row["body"] for row in found["items"])
        await session.rollback()


async def test_an_outbound_message_without_a_reply_is_flagged_as_awaiting():
    async with SessionLocal() as session:
        tenant, rfq, supplier, _invitation = await _scene(session)
        thread = await _messages(session, tenant, rfq, supplier)
        session.add(ChannelMessage(tenant_id=tenant.id, thread_id=thread.id, rfq_id=rfq.id,
                                   supplier_id=supplier.id, direction="outbound", channel="whatsapp",
                                   body="Reminder: please quote", status="sent"))
        await session.flush()
        listing = await inbox.conversations(session, tenant, supplier_id=supplier.id)
        item = next(row for row in listing["items"] if row["thread_id"] == thread.id)
        assert item["awaiting_reply"] is True
        assert listing["awaiting_reply"] >= 1
        filtered = await inbox.conversations(session, tenant, supplier_id=supplier.id,
                                             status="awaiting_reply")
        assert all(row["awaiting_reply"] for row in filtered["items"])
        await session.rollback()


async def test_the_thread_carries_the_policy_and_channel_events_for_the_auditor():
    async with SessionLocal() as session:
        tenant, rfq, supplier, _invitation = await _scene(session)
        thread = await _messages(session, tenant, rfq, supplier)
        session.add(ChannelMessage(tenant_id=tenant.id, thread_id=thread.id, rfq_id=rfq.id,
                                   supplier_id=supplier.id, direction="outbound", channel="email",
                                   body="Sent", status="sent"))
        await session.flush()
        reply = await inbox.reply(session, tenant, thread, body="Second attempt", channel="whatsapp",
                                  author_id="u1")
        assert reply["ok"] is True, reply
        detail = await inbox.thread_detail(session, tenant, thread)
        assert detail["messages"]
        assert detail["current_channel"] == "whatsapp"
        # The supplier's preference still resolves to email: the inbox reply channel and the
        # preference are two different questions, and the API answers both.
        assert detail["reply_channel"] == "whatsapp"
        assert detail["resolved_channel"]["channel"] == "email"
        kinds = {event["kind"] for event in detail["events"]}
        assert kinds, "channel events must be visible on the thread"
        await session.rollback()
