"""The unified communication inbox: every channel, one conversation per supplier and RFQ.

A buyer should not have to remember whether a supplier answered on WhatsApp, called, or emailed. The
inbox groups the message rows that the individual channels write into one thread, keeps the channel of
every message visible, and lets the buyer answer on the channel the supplier actually used.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.entities import (
    RFQ,
    ChannelEvent,
    ChannelMessage,
    MessageThread,
    RFQSupplier,
    Supplier,
    Tenant,
)
from app.services import channels as channel_service
from app.services.messaging import ensure_thread, send_message

MAX_THREADS = 300


def _message_view(message: ChannelMessage, supplier_name: str = "", supplier_name_ar: str = "") -> dict:
    return {
        "id": message.id,
        "direction": message.direction,
        "channel": message.channel,
        "channel_label": channel_service.CHANNEL_LABELS.get(message.channel, (message.channel, ""))[0],
        "channel_label_ar": channel_service.CHANNEL_LABELS.get(message.channel, ("", message.channel))[1],
        "author_type": message.author_type,
        "author_id": message.author_id,
        "status": message.status,
        "delivery_status": message.delivery_status,
        "delivered_at": message.delivered_at.isoformat() if message.delivered_at else None,
        "read_at": message.read_at.isoformat() if message.read_at else None,
        "subject": message.subject,
        "body": message.body,
        "body_ar": message.body_ar,
        "media_type": message.media_type,
        "template_code": message.template_code,
        "locale": message.locale,
        "switched_from": message.switched_from,
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "supplier_name": supplier_name,
        "supplier_name_ar": supplier_name_ar,
    }


async def _thread_messages(session: AsyncSession, tenant_id: str,
                           thread_ids: list[str] | None = None) -> dict[str, list[ChannelMessage]]:
    query = select(ChannelMessage).where(ChannelMessage.tenant_id == tenant_id)
    if thread_ids is not None:
        if not thread_ids:
            return {}
        query = query.where(ChannelMessage.thread_id.in_(thread_ids))
    rows = list((await session.execute(query.order_by(ChannelMessage.created_at))).scalars().all())
    grouped: dict[str, list[ChannelMessage]] = {}
    for row in rows:
        grouped.setdefault(row.thread_id, []).append(row)
    return grouped


async def conversations(session: AsyncSession, tenant: Tenant, *, q: str = "", channel: str = "",
                        supplier_id: str = "", rfq_id: str = "", status: str = "",
                        limit: int = 50) -> dict:
    """Thread list, newest first, with the channel mix and whether the ball is in our court."""
    threads = list((await session.execute(
        select(MessageThread).where(MessageThread.tenant_id == tenant.id)
        .order_by(MessageThread.last_message_at.desc().nullslast(), MessageThread.created_at.desc())
        .limit(MAX_THREADS)
    )).scalars().all())
    grouped = await _thread_messages(session, tenant.id, [t.id for t in threads])
    supplier_ids = {t.supplier_id for t in threads}
    suppliers = {
        row.id: row for row in (await session.execute(
            select(Supplier).where(Supplier.id.in_(supplier_ids))
        )).scalars().all()
    } if supplier_ids else {}
    rfq_ids = {t.rfq_id for t in threads if t.rfq_id}
    rfqs = {
        row.id: row for row in (await session.execute(
            select(RFQ).where(RFQ.id.in_(rfq_ids))
        )).scalars().all()
    } if rfq_ids else {}
    invitations = {
        (row.rfq_id, row.supplier_id): row for row in (await session.execute(
            select(RFQSupplier).where(RFQSupplier.tenant_id == tenant.id,
                                      RFQSupplier.rfq_id.in_(rfq_ids or [""]))
        )).scalars().all()
    }

    items: list[dict] = []
    needle = q.strip().lower()
    for thread in threads:
        messages = grouped.get(thread.id, [])
        if not messages:
            continue
        supplier = suppliers.get(thread.supplier_id)
        rfq = rfqs.get(thread.rfq_id)
        if supplier_id and thread.supplier_id != supplier_id:
            continue
        if rfq_id and thread.rfq_id != rfq_id:
            continue
        if channel and not any(m.channel == channel for m in messages):
            continue
        if needle and not any(needle in (m.body or "").lower() or needle in (m.subject or "").lower()
                              for m in messages):
            continue
        last = messages[-1]
        inbound = [m for m in messages if m.direction == "inbound"]
        awaiting = bool(messages) and last.direction == "outbound"
        if status == "awaiting_reply" and not awaiting:
            continue
        if status == "replied" and not inbound:
            continue
        invitation = invitations.get((thread.rfq_id, thread.supplier_id))
        comm_status = channel_service.comm_status_for(invitation, messages) if invitation else (
            "replied" if inbound else "sent")
        if status == "no_response" and comm_status != "no_response":
            continue
        items.append({
            "thread_id": thread.id,
            "rfq_id": thread.rfq_id,
            "rfq_code": rfq.code if rfq else "",
            "rfq_title": rfq.title if rfq else "",
            "supplier_id": thread.supplier_id,
            "supplier_name": supplier.name if supplier else thread.supplier_id,
            "supplier_name_ar": (supplier.name_ar if supplier else "") or "",
            "current_channel": thread.channel,
            "channels_used": sorted({m.channel for m in messages}),
            "last_message": _message_view(last),
            "last_message_at": last.created_at.isoformat() if last.created_at else None,
            "message_count": len(messages),
            "inbound_count": len(inbound),
            "outbound_count": len(messages) - len(inbound),
            "comm_status": comm_status,
            "comm_status_label": channel_service.COMM_STATUSES.get(comm_status, (comm_status, ""))[0],
            "comm_status_label_ar": channel_service.COMM_STATUSES.get(comm_status, ("", comm_status))[1],
            "awaiting_reply": awaiting,
            "preferred_channel": channel_service.normalise(supplier.preferred_channel) if supplier else "email",
        })

    return {
        "items": items[:limit],
        "total": len(items),
        "channels": sorted({c for item in items for c in item["channels_used"]}),
        "awaiting_reply": sum(1 for item in items if item["awaiting_reply"]),
        "generated_at": datetime.now(UTC).isoformat(),
    }


async def thread_detail(session: AsyncSession, tenant: Tenant, thread: MessageThread) -> dict:
    messages = list((await session.execute(
        select(ChannelMessage).where(ChannelMessage.tenant_id == tenant.id,
                                     ChannelMessage.thread_id == thread.id)
        .order_by(ChannelMessage.created_at)
    )).scalars().all())
    supplier = (await session.execute(
        select(Supplier).where(Supplier.id == thread.supplier_id)
    )).scalar_one_or_none()
    rfq = (await session.execute(select(RFQ).where(RFQ.id == thread.rfq_id))).scalar_one_or_none()
    invitation = (await session.execute(
        select(RFQSupplier).where(RFQSupplier.rfq_id == thread.rfq_id,
                                  RFQSupplier.supplier_id == thread.supplier_id)
    )).scalar_one_or_none()
    events = list((await session.execute(
        select(ChannelEvent).where(ChannelEvent.tenant_id == tenant.id,
                                   ChannelEvent.thread_id == thread.id)
        .order_by(ChannelEvent.created_at)
    )).scalars().all())
    resolved = None
    if supplier is not None and rfq is not None:
        resolved = await channel_service.resolve(session, tenant, supplier, invitation=invitation, rfq=rfq)
    last_inbound = next((m for m in reversed(messages) if m.direction == "inbound"), None)
    return {
        "thread_id": thread.id,
        "rfq_id": thread.rfq_id,
        "rfq_code": rfq.code if rfq else "",
        "rfq_title": rfq.title if rfq else "",
        "supplier_id": thread.supplier_id,
        "supplier_name": supplier.name if supplier else "",
        "supplier_name_ar": (supplier.name_ar if supplier else "") or "",
        "current_channel": thread.channel,
        "reply_channel": last_inbound.channel if last_inbound else thread.channel,
        "channels_used": sorted({m.channel for m in messages}),
        "messages": [_message_view(m) for m in messages],
        "events": [{
            "id": event.id, "kind": event.kind, "channel": event.channel,
            "actor_type": event.actor_type, "actor_id": event.actor_id,
            "detail": event.detail, "created_at": event.created_at.isoformat() if event.created_at else None,
        } for event in events],
        "resolved_channel": resolved,
        "comm_status": channel_service.comm_status_for(invitation, messages) if invitation else "",
    }


async def reply(session: AsyncSession, tenant: Tenant, thread: MessageThread, *, body: str,
                subject: str = "", channel: str | None = None, author_id: str | None = None,
                locale: str = "ar") -> dict:
    """Answer on the channel the supplier used unless the buyer explicitly picks another one."""
    rfq = (await session.execute(select(RFQ).where(RFQ.id == thread.rfq_id))).scalar_one_or_none()
    supplier = (await session.execute(
        select(Supplier).where(Supplier.id == thread.supplier_id)
    )).scalar_one_or_none()
    if rfq is None or supplier is None:
        return {"ok": False, "error": "thread_not_found"}
    # A pair has exactly one canonical thread (`messaging.ensure_thread`). Re-resolving it here means a
    # reply always lands where the outbound history lives, even if the caller passed a stale row.
    canonical = await ensure_thread(session, tenant, rfq, supplier, thread.channel or "email",
                                    subject=thread.subject or "")
    messages = list((await session.execute(
        select(ChannelMessage).where(ChannelMessage.thread_id.in_({thread.id, canonical.id}))
        .order_by(ChannelMessage.created_at)
    )).scalars().all())
    last_inbound = next((m for m in reversed(messages) if m.direction == "inbound"), None)
    target = channel_service.normalise(channel or (last_inbound.channel if last_inbound else thread.channel))
    state = await channel_service.availability(session, tenant, supplier, target)
    if not state["available"]:
        return {"ok": False, "error": "channel_unavailable", "channel": target,
                "reasons": state["reasons"]}
    # `switched_from` reports the channel the thread was on, so the UI can say "moved from email".
    previous = thread.channel
    outcome = await send_message(
        session, tenant, rfq=rfq, supplier=supplier, subject=subject or f"RFQ {rfq.code}",
        body=body, body_ar=body, channel=target, author_type="user", author_id=author_id,
        locale=locale, switched_from=previous if previous and previous != target else "",
    )
    for row in {thread.id: thread, canonical.id: canonical}.values():
        row.channel = target
        row.last_message_at = datetime.now(UTC)
    await session.flush()
    return {**outcome, "channel": target, "thread_id": canonical.id,
            "switched_from": previous if previous != target else ""}


async def search(session: AsyncSession, tenant: Tenant, *, q: str, channel: str = "",
                 supplier_id: str = "", limit: int = 50) -> dict:
    """Cross-channel search over message bodies and subjects, with a per-channel tally."""
    needle = (q or "").strip().lower()
    query = select(ChannelMessage).where(ChannelMessage.tenant_id == tenant.id)
    if channel:
        query = query.where(ChannelMessage.channel == channel_service.normalise(channel))
    if supplier_id:
        query = query.where(ChannelMessage.supplier_id == supplier_id)
    rows = list((await session.execute(query.order_by(ChannelMessage.created_at.desc()).limit(2000)))
                .scalars().all())
    hits: list[dict] = []
    tally: dict[str, int] = {}
    for row in rows:
        text = f"{row.subject}\n{row.body}"
        if needle and needle not in text.lower():
            continue
        tally[row.channel] = tally.get(row.channel, 0) + 1
        index = text.lower().find(needle) if needle else 0
        start = max(0, index - 60)
        hits.append({
            **_message_view(row),
            "snippet": text[start:start + 200].replace("\n", " ").strip(),
        })
        if len(hits) >= limit:
            break
    return {"items": hits, "total": len(hits), "by_channel": tally, "query": q}
