"""Channel selection, preference resolution and communication status (phase 3).

WhatsApp is the primary supplier channel in Egypt and Saudi Arabia, so it is the first candidate in
every resolution — but a channel is only chosen when it is *reachable* (an address is on file) and
*consented* (opt-in for that channel). The resolver is the single place that answers "how do we
contact this supplier for this RFQ", which keeps dispatch, reminders, negotiations and the inbox from
drifting apart.

Precedence, highest first:

1. an **RFQ override** on the invitation (`channel_override`) — a buyer may force a channel per RFQ;
2. the supplier's **preferred channel**;
3. the supplier's **secondary channels**, in the order stored;
4. the default order (WhatsApp → voice → email → web form) — a documented fallback, recorded as such.

Every resolved value carries `source`, so the UI can say *why* a channel was chosen and an auditor can
see when a fallback overrode a stated preference.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.entities import (
    RFQ,
    ChannelEvent,
    ChannelMessage,
    MessageThread,
    RFQSupplier,
    Supplier,
    SupplierContact,
    Tenant,
)
from app.services import numbers
from app.services.features import is_enabled
from app.services.messaging import opt_in_status
from app.services.suppliers import contact_pii, primary_contact

CHANNELS: tuple[str, ...] = ("whatsapp", "voice", "email", "web_form")
DEFAULT_ORDER: tuple[str, ...] = ("whatsapp", "voice", "email", "web_form")
ALIASES = {"phone": "voice", "call": "voice", "wa": "whatsapp", "form": "web_form"}

CHANNEL_LABELS: dict[str, tuple[str, str]] = {
    "whatsapp": ("WhatsApp", "واتساب"),
    "voice": ("Voice call", "مكالمة صوتية"),
    "email": ("Email", "بريد إلكتروني"),
    "web_form": ("Secure web form", "نموذج ويب آمن"),
}

CHANNEL_SOURCES: dict[str, tuple[str, str]] = {
    "rfq_override": ("RFQ override", "تجاوز على مستوى الطلب"),
    "supplier_preference": ("Supplier preference", "تفضيل المورد"),
    "secondary_preference": ("Secondary preference", "تفضيل ثانوي"),
    "fallback": ("Automatic fallback", "تحويل تلقائي"),
    "manual_switch": ("Switched by the buyer", "تحويل بواسطة المشتري"),
    "unavailable": ("No reachable channel", "لا توجد قناة متاحة"),
}

COMM_STATUSES: dict[str, tuple[str, str]] = {
    "pending": ("Not sent", "لم يُرسل"),
    "sent": ("Sent", "أُرسل"),
    "delivered": ("Delivered", "تم التسليم"),
    "read": ("Read", "تمت القراءة"),
    "replied": ("Replied", "تم الرد"),
    "failed": ("Failed", "فشل"),
    "no_response": ("No response", "لا يوجد رد"),
}


def normalise(channel: str | None) -> str:
    value = (channel or "").strip().lower()
    value = ALIASES.get(value, value)
    return value if value in CHANNELS else "email"


def label(channel: str) -> dict[str, str]:
    en, ar = CHANNEL_LABELS.get(normalise(channel), (channel, channel))
    return {"channel": normalise(channel), "label": en, "label_ar": ar}


def require_feature(tenant: Tenant, channel: str) -> bool:
    """A channel may be globally or per-tenant disabled; resolution then skips it."""
    if channel == "whatsapp":
        return is_enabled(tenant, "whatsapp")
    return True


async def supplier_preferences(supplier: Supplier) -> dict:
    secondary = [normalise(c) for c in (supplier.secondary_channels or [])]
    return {
        "supplier_id": supplier.id,
        "preferred_channel": normalise(supplier.preferred_channel),
        "secondary_channels": [c for c in secondary if c != normalise(supplier.preferred_channel)],
        "preferred_language": supplier.preferred_language or "ar",
    }


async def set_supplier_preferences(session: AsyncSession, tenant: Tenant, supplier: Supplier, *,
                                   preferred_channel: str | None = None,
                                   secondary_channels: list[str] | None = None,
                                   preferred_language: str | None = None) -> dict:
    if preferred_channel:
        supplier.preferred_channel = normalise(preferred_channel)
    if secondary_channels is not None:
        seen: list[str] = []
        for channel in secondary_channels:
            candidate = normalise(channel)
            if candidate != normalise(supplier.preferred_channel) and candidate not in seen:
                seen.append(candidate)
        supplier.secondary_channels = seen
    if preferred_language:
        supplier.preferred_language = preferred_language
    await session.flush()
    return await supplier_preferences(supplier)


async def availability(session: AsyncSession, tenant: Tenant, supplier: Supplier,
                       channel: str) -> dict:
    """Is this channel usable *now* for this supplier, and if not, why not."""
    channel = normalise(channel)
    contact: SupplierContact | None = await primary_contact(session, tenant, supplier.id)
    email, phone = await contact_pii(session, tenant, contact) if contact else ("", "")
    opted_in = await opt_in_status(session, tenant.id, supplier.id, channel)
    blacklisted = (supplier.status or "") == "blacklisted"
    reasons: list[str] = []
    if not require_feature(tenant, channel):
        reasons.append("disabled_by_tenant")
    if blacklisted:
        reasons.append("supplier_blacklisted")
    if channel == "whatsapp":
        if not phone:
            reasons.append("missing_phone")
        if not opted_in:
            reasons.append("no_opt_in")
    elif channel == "voice":
        if not phone:
            reasons.append("missing_phone")
        if not opted_in:
            reasons.append("no_opt_in")
    elif channel == "email":
        if not email:
            reasons.append("missing_email")
        if not opted_in:
            reasons.append("no_opt_in")
    return {
        "channel": channel, **label(channel), "available": not reasons, "opted_in": opted_in,
        "has_address": bool(phone if channel in {"whatsapp", "voice"} else True),
        "reasons": reasons,
        "address_hint": _mask(phone if channel in {"whatsapp", "voice"} else email),
    }


def _mask(value: str) -> str:
    """Never echo a full phone number or address into a UI payload."""
    if not value:
        return ""
    if "@" in value:
        name, _, domain = value.partition("@")
        return f"{name[:2]}***@{domain}"
    digits = "".join(ch for ch in value if ch.isdigit())
    return f"{'*' * max(0, len(digits) - 4)}{digits[-4:]}" if digits else "***"


async def resolve(session: AsyncSession, tenant: Tenant, supplier: Supplier, *,
                  invitation: RFQSupplier | None = None,
                  rfq: RFQ | None = None) -> dict:
    """Resolve the channel for one supplier, honouring override → preference → fallback."""
    prefs = await supplier_preferences(supplier)
    candidates: list[str] = []
    if invitation and invitation.channel_override:
        candidates.append(normalise(invitation.channel_override))
    candidates.append(prefs["preferred_channel"])
    candidates.extend(prefs["secondary_channels"])
    candidates.extend(c for c in DEFAULT_ORDER if c not in candidates)

    checked: dict[str, dict] = {}
    for channel in candidates:
        if channel not in checked:
            checked[channel] = await availability(session, tenant, supplier, channel)

    # An explicit override is honoured even when the channel is not currently reachable: the buyer
    # asked for it, so the screen must show the block instead of silently emailing someone else.
    if invitation is not None and invitation.channel_override:
        override = normalise(invitation.channel_override)
        return _resolved(supplier, checked[override], "rfq_override", checked, rfq, invitation)

    chosen: dict | None = None
    source = "unavailable"
    for channel in candidates:
        if checked[channel]["available"]:
            chosen = checked[channel]
            if channel == prefs["preferred_channel"]:
                source = "supplier_preference"
            elif channel in prefs["secondary_channels"]:
                source = "secondary_preference"
            else:
                source = "fallback"
            break
    if chosen is None:
        chosen = checked[normalise(prefs["preferred_channel"])]
    return _resolved(supplier, chosen, source, checked, rfq, invitation)


def _resolved(supplier: Supplier, chosen: dict, source: str, checked: dict[str, dict],
              rfq: RFQ | None, invitation: RFQSupplier | None) -> dict:
    return {
        "supplier_id": supplier.id,
        "supplier_name": supplier.name,
        "supplier_name_ar": supplier.name_ar or supplier.name,
        "channel": chosen["channel"],
        "channel_label": chosen["label"],
        "channel_label_ar": chosen["label_ar"],
        "source": source,
        "source_label": CHANNEL_SOURCES[source][0],
        "source_label_ar": CHANNEL_SOURCES[source][1],
        "available": chosen["available"],
        "opted_in": chosen["opted_in"],
        "reasons": chosen["reasons"],
        "candidates": list(checked.values()),
        "rfq_id": rfq.id if rfq else None,
        "invitation_id": invitation.id if invitation else None,
    }


async def set_rfq_channel(session: AsyncSession, tenant: Tenant, rfq: RFQ, invitation: RFQSupplier, *,
                          channel: str | None, actor_id: str | None, reason: str = "") -> dict:
    """Override (or clear) the channel for one supplier on one RFQ. Audited as a channel event."""
    previous = invitation.channel_override or invitation.channel
    if channel is None:
        invitation.channel_override = None
        invitation.channel_source = "supplier_preference"
    else:
        normalised = normalise(channel)
        supplier = (await session.execute(
            select(Supplier).where(Supplier.id == invitation.supplier_id)
        )).scalar_one()
        state = await availability(session, tenant, supplier, normalised)
        if not state["available"] and not reason.strip():
            return {"ok": False, "error": "channel_unavailable", "reasons": state["reasons"],
                    "channel": normalised}
        invitation.channel_override = normalised
        invitation.channel_source = "rfq_override"
    invitation.channel = invitation.channel_override or invitation.channel
    session.add(ChannelEvent(
        tenant_id=tenant.id, rfq_id=rfq.id, supplier_id=invitation.supplier_id, channel=invitation.channel,
        kind="channel_override", actor_type="user", actor_id=actor_id,
        detail={"from": previous, "to": invitation.channel_override, "reason": reason[:300],
                "source": invitation.channel_source},
    ))
    await session.flush()
    supplier = (await session.execute(
        select(Supplier).where(Supplier.id == invitation.supplier_id)
    )).scalar_one()
    return {"ok": True, **(await resolve(session, tenant, supplier, invitation=invitation, rfq=rfq))}


async def switch_channel(session: AsyncSession, tenant: Tenant, rfq: RFQ, supplier: Supplier,
                         invitation: RFQSupplier | None, *, channel: str, actor_id: str | None,
                         reason: str = "") -> dict:
    """Move the live conversation to another channel; every earlier message keeps its own channel."""
    normalised = normalise(channel)
    state = await availability(session, tenant, supplier, normalised)
    if not state["available"]:
        return {"ok": False, "error": "channel_unavailable", "reasons": state["reasons"],
                "channel": normalised}
    thread = (await session.execute(
        select(MessageThread).where(MessageThread.tenant_id == tenant.id, MessageThread.rfq_id == rfq.id,
                                    MessageThread.supplier_id == supplier.id)
    )).scalars().first()
    # Without a live thread the honest "from" is the channel the invitation was sent on.
    previous = thread.channel if thread else (invitation.channel if invitation else "")
    if thread:
        thread.channel = normalised
    if invitation:
        invitation.channel = normalised
        invitation.channel_override = invitation.channel_override or normalised
        invitation.channel_source = "manual_switch"
    session.add(ChannelEvent(
        tenant_id=tenant.id, rfq_id=rfq.id, supplier_id=supplier.id,
        thread_id=thread.id if thread else None, channel=normalised, kind="channel_switch",
        actor_type="user", actor_id=actor_id,
        detail={"from": previous, "to": normalised, "reason": reason[:300]},
    ))
    await session.flush()
    return {"ok": True, "from": previous, "to": normalised, "thread_id": thread.id if thread else None,
            **label(normalised)}


def comm_status_for(invitation: RFQSupplier, messages: list[ChannelMessage] | None,
                    *, now: datetime | None = None) -> str:
    """Sent → delivered → read → replied, with `no_response` once the deadline passes."""
    outbound = [m for m in (messages or []) if m.direction == "outbound"]
    inbound = [m for m in (messages or []) if m.direction == "inbound"]
    if inbound or invitation.reply_count:
        return "replied"
    if any(m.read_at or m.delivery_status == "read" for m in outbound):
        return "read"
    if any(m.delivered_at or m.delivery_status == "delivered" for m in outbound):
        return "delivered"
    if outbound and all(m.delivery_status == "failed" for m in outbound):
        return "failed"
    if outbound or invitation.sent_at:
        return "sent"
    return "pending"


async def refresh_comm_status(session: AsyncSession, invitation: RFQSupplier,
                              *, now: datetime | None = None) -> str:
    messages = list((await session.execute(
        select(ChannelMessage).where(ChannelMessage.supplier_id == invitation.supplier_id,
                                     ChannelMessage.rfq_id == invitation.rfq_id)
    )).scalars().all())
    status = comm_status_for(invitation, messages)
    invitation.comm_status = status
    await session.flush()
    return status


async def rfq_channels(session: AsyncSession, tenant: Tenant, rfq: RFQ) -> dict:
    """Everything the RFQ screen needs: one row per invited supplier with its resolved channel."""
    invitations = list((await session.execute(
        select(RFQSupplier).where(RFQSupplier.rfq_id == rfq.id)
    )).scalars().all())
    items: list[dict] = []
    for invitation in invitations:
        supplier = (await session.execute(
            select(Supplier).where(Supplier.id == invitation.supplier_id)
        )).scalar_one_or_none()
        if supplier is None:
            continue
        resolved = await resolve(session, tenant, supplier, invitation=invitation, rfq=rfq)
        status = await refresh_comm_status(session, invitation)
        items.append({
            **resolved,
            "invitation_status": invitation.status,
            "comm_status": status,
            "comm_status_label": COMM_STATUSES[status][0],
            "comm_status_label_ar": COMM_STATUSES[status][1],
            "sent_at": invitation.sent_at.isoformat() if invitation.sent_at else None,
            "delivered_at": invitation.delivered_at.isoformat() if invitation.delivered_at else None,
            "read_at": invitation.read_at.isoformat() if invitation.read_at else None,
            "responded_at": invitation.responded_at.isoformat() if invitation.responded_at else None,
            "reply_count": invitation.reply_count,
            "override": invitation.channel_override,
            "channel_source": invitation.channel_source,
        })
    summary = dict.fromkeys(COMM_STATUSES, 0)
    for item in items:
        summary[item["comm_status"]] = summary.get(item["comm_status"], 0) + 1
    return {
        "rfq_id": rfq.id,
        "items": items,
        "summary": summary,
        "mixed_channels": len({i["channel"] for i in items}) > 1,
        "channels_in_use": sorted({i["channel"] for i in items}),
        "available_channels": [
            {**label(channel), "enabled": require_feature(tenant, channel)}
            for channel in CHANNELS
        ],
    }


async def apply_preferences_to_invitations(session: AsyncSession, tenant: Tenant, rfq: RFQ) -> int:
    """Seed each invitation's channel from the supplier preference (no override is written)."""
    invitations = list((await session.execute(
        select(RFQSupplier).where(RFQSupplier.rfq_id == rfq.id)
    )).scalars().all())
    changed = 0
    for invitation in invitations:
        supplier = (await session.execute(
            select(Supplier).where(Supplier.id == invitation.supplier_id)
        )).scalar_one_or_none()
        if supplier is None:
            continue
        resolved = await resolve(session, tenant, supplier, invitation=invitation, rfq=rfq)
        if invitation.channel != resolved["channel"]:
            invitation.channel = resolved["channel"]
            invitation.channel_source = resolved["source"]
            changed += 1
    await session.flush()
    return changed


def money_or_zero(value: object) -> str:
    return str(numbers.money(value))
