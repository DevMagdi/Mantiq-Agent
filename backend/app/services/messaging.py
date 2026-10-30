"""Outbound/inbound messaging across email, WhatsApp and the signed web form (FR-RFQ-3)."""
from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import registry
from app.agents.policy_engine import check_dispatch
from app.core.config import settings
from app.core.security import sign_payload
from app.db.models.entities import (
    RFQ,
    ChannelEvent,
    ChannelMessage,
    MessageTemplate,
    MessageThread,
    OptInRegistry,
    OutboxEvent,
    RFQSupplier,
    Supplier,
    SupplierContact,
    Tenant,
    WebFormToken,
)
from app.services import numbers
from app.services.common import meter, notify
from app.services.suppliers import contact_pii, primary_contact

log = logging.getLogger("procureai.messaging")

DAILY_CAP = 400

#: Keywords that stop a channel. Arabic and English, because suppliers answer in both.
OPT_OUT_KEYWORDS = {"stop", "unsubscribe", "opt out", "optout", "cancel", "إلغاء", "الغاء", "ايقاف",
                    "إيقاف", "لا ترسل", "توقف"}
OPT_IN_KEYWORDS = {"start", "subscribe", "ابدأ", "اشترك", "تفعيل"}


async def record_channel_event(session: AsyncSession, *, tenant_id: str, kind: str, channel: str = "",
                               rfq_id: str | None = None, supplier_id: str | None = None,
                               thread_id: str | None = None, message_id: str | None = None,
                               actor_type: str = "system", actor_id: str | None = None,
                               detail: dict | None = None) -> ChannelEvent:
    """Append one cross-channel event. The log is the searchable spine of the unified inbox."""
    event = ChannelEvent(tenant_id=tenant_id, rfq_id=rfq_id, supplier_id=supplier_id, thread_id=thread_id,
                         message_id=message_id, channel=channel, kind=kind, actor_type=actor_type,
                         actor_id=actor_id, detail=detail or {})
    session.add(event)
    await session.flush()
    return event


def template_variables(body: str) -> list[str]:
    """The `{{name}}` placeholders a template expects — shown in the template library."""
    found: list[str] = []
    cursor = 0
    while True:
        start = body.find("{{", cursor)
        if start == -1:
            break
        end = body.find("}}", start)
        if end == -1:
            break
        name = body[start + 2:end].strip()
        if name and name not in found:
            found.append(name)
        cursor = end + 2
    return found


async def pick_template(session: AsyncSession, tenant: Tenant, code: str, channel: str,
                        locale: str = "ar") -> MessageTemplate | None:
    """Newest active template for (code, channel, locale), falling back to Arabic then any locale."""
    rows = list((await session.execute(
        select(MessageTemplate).where(MessageTemplate.tenant_id == tenant.id,
                                      MessageTemplate.code == code,
                                      MessageTemplate.channel == channel,
                                      MessageTemplate.is_active.is_(True))
        .order_by(MessageTemplate.version.desc())
    )).scalars().all())
    for row in rows:
        if row.locale == locale:
            return row
    for row in rows:
        if row.locale == "ar":
            return row
    return rows[0] if rows else None


def detect_opt_keyword(text: str) -> str:
    """`opt_out` / `opt_in` / `""` from a supplier message. Whole-word, case-insensitive."""
    squashed = " ".join((text or "").strip().lower().split())
    if not squashed:
        return ""
    for keyword in OPT_OUT_KEYWORDS:
        if squashed == keyword or squashed.startswith(keyword + " ") or f" {keyword}" in squashed:
            return "opt_out"
    for keyword in OPT_IN_KEYWORDS:
        if squashed == keyword or squashed.startswith(keyword + " "):
            return "opt_in"
    return ""


async def ensure_thread(session: AsyncSession, tenant: Tenant, rfq: RFQ, supplier: Supplier,
                        channel: str, subject: str = "") -> MessageThread:
    thread = (await session.execute(
        select(MessageThread).where(MessageThread.tenant_id == tenant.id, MessageThread.rfq_id == rfq.id,
                                    MessageThread.supplier_id == supplier.id)
    )).scalars().first()
    if thread:
        if channel and channel != thread.channel:
            thread.channel = channel
        return thread
    thread = MessageThread(tenant_id=tenant.id, rfq_id=rfq.id, supplier_id=supplier.id, channel=channel,
                           subject=subject, last_message_at=datetime.now(UTC))
    session.add(thread)
    await session.flush()
    return thread


async def opt_in_status(session: AsyncSession, tenant_id: str, supplier_id: str, channel: str) -> bool:
    row = (await session.execute(
        select(OptInRegistry).where(OptInRegistry.tenant_id == tenant_id,
                                    OptInRegistry.supplier_id == supplier_id,
                                    OptInRegistry.channel == channel)
    )).scalar_one_or_none()
    if row:
        return row.status == "opted_in"
    # email is implicitly opted-in for legitimate B2B RFQ contact unless explicitly unsubscribed
    return channel == "email"


async def set_opt_in(session: AsyncSession, tenant_id: str, supplier_id: str, channel: str, status: str,
                     source: str = "manual", evidence: str = "") -> OptInRegistry:
    row = (await session.execute(
        select(OptInRegistry).where(OptInRegistry.tenant_id == tenant_id,
                                    OptInRegistry.supplier_id == supplier_id,
                                    OptInRegistry.channel == channel)
    )).scalar_one_or_none()
    if row:
        row.status = status
        row.source = source
        row.evidence = evidence
    else:
        row = OptInRegistry(tenant_id=tenant_id, supplier_id=supplier_id, channel=channel, status=status,
                            source=source, evidence=evidence)
        session.add(row)
    await session.flush()
    return row


def web_form_url(token: str) -> str:
    return f"{settings.public_app_url}/form/{token}"


async def create_web_form_link(session: AsyncSession, tenant: Tenant, rfq: RFQ, supplier_id: str) -> str:
    token = f"{sign_payload(f'{rfq.id}:{supplier_id}', 'webform')[:32]}{secrets.token_urlsafe(6)}"
    session.add(WebFormToken(tenant_id=tenant.id, token=token, rfq_id=rfq.id, supplier_id=supplier_id,
                             expires_at=datetime.now(UTC) + timedelta(days=21)))
    rfq_supplier = (await session.execute(
        select(RFQSupplier).where(RFQSupplier.rfq_id == rfq.id, RFQSupplier.supplier_id == supplier_id)
    )).scalar_one_or_none()
    if rfq_supplier:
        rfq_supplier.token = token
    await session.flush()
    return web_form_url(token)


async def send_message(
    session: AsyncSession,
    tenant: Tenant,
    *,
    rfq: RFQ,
    supplier: Supplier,
    subject: str,
    body: str,
    body_ar: str = "",
    channel: str = "email",
    author_type: str = "user",
    author_id: str | None = None,
    agent_run_id: str | None = None,
    policy_decision: dict | None = None,
    status_if_blocked: str = "draft",
    attachments: list[dict] | None = None,
    template_code: str = "",
    locale: str = "ar",
    media_type: str = "text",
    switched_from: str = "",
    interactive: dict | None = None,
) -> dict:
    """Send one message, applying consent/blacklist/cap gates. Never raises on provider failure.

    `attachments` are `{filename, data, mime}` dicts (WhatsApp documents); `template_code` selects an
    approved Meta template when the provider supports templates. The rich variants degrade to plain
    text when the provider or the tenant flag does not allow them, and the message row records which
    form was actually used so the inbox never has to guess.
    """
    contact: SupplierContact | None = await primary_contact(session, tenant, supplier.id)
    email, phone = await contact_pii(session, tenant, contact) if contact else ("", "")
    opted_in = await opt_in_status(session, tenant.id, supplier.id, channel)
    blacklisted = (supplier.status or "") == "blacklisted"
    sanctioned = (supplier.sanctions_status or "clear") != "clear"

    sent_today = len((await session.execute(
        select(ChannelMessage).where(ChannelMessage.tenant_id == tenant.id,
                                    ChannelMessage.direction == "outbound",
                                    ChannelMessage.created_at >= datetime.now(UTC) - timedelta(days=1))
    )).scalars().all())
    available = (
        (channel == "email" and bool(email))
        or (channel == "whatsapp" and bool(phone))
        or channel == "web_form"
        or channel == "phone"
    )
    decision = check_dispatch(
        policy=None, sent_today=sent_today, daily_cap=DAILY_CAP, channel=channel, opt_in=opted_in,
        blacklisted=blacklisted, sanctioned=sanctioned, timezone=tenant.timezone,
        working_hours=(0, 24) if channel == "web_form"
        else (settings.contact_hours_start, settings.contact_hours_end),
    )
    if not available:
        decision.codes.append("BLOCK_CHANNEL_UNAVAILABLE")
        decision.reasons.append(f"No reachable {channel} address on file for this supplier.")
        decision.allowed = False
        decision.action = "block"

    thread = await ensure_thread(session, tenant, rfq, supplier, channel, subject)
    message = ChannelMessage(
        tenant_id=tenant.id, thread_id=thread.id, rfq_id=rfq.id, supplier_id=supplier.id,
        direction="outbound", channel=channel, body=body, body_ar=body_ar, subject=subject,
        status="draft", author_type=author_type, author_id=author_id, agent_run_id=agent_run_id,
        extra={"policy": policy_decision or decision.as_dict()},
        template_code=template_code, locale=locale, media_type=media_type, switched_from=switched_from,
    )
    session.add(message)
    await session.flush()

    if not decision.allowed:
        message.status = status_if_blocked
        await session.flush()
        return {"ok": False, "message_id": message.id, "error": "; ".join(decision.reasons),
                "codes": decision.codes, "policy": decision.as_dict()}

    provider_id = ""
    error = ""
    if channel == "email":
        provider = registry.resolve("email")
        result = await provider.send(to=email, subject=subject, body=body, html=_html(body),
                                     reply_to=settings.smtp_from)
        provider_id, error = result.provider_message_id, result.error
    elif channel == "whatsapp":
        provider = registry.resolve("whatsapp")
        if interactive:
            result = await provider.send_interactive(
                to=phone, body=interactive.get("body", body),
                options=interactive.get("options", []), flow=interactive.get("flow", ""),
            )
            message.media_type = "interactive"
        elif attachments:
            result = await provider.send_document(
                to=phone, filename=attachments[0]["filename"], data=attachments[0]["data"],
                caption=body, mime=attachments[0].get("mime", ""),
            )
            message.media_type = "document"
        elif template_code and getattr(provider, "supports_templates", True):
            template_row = await pick_template(session, tenant, template_code, "whatsapp", locale)
            if template_row and template_row.whatsapp_template_name:
                result = await provider.send_template(
                    to=phone, template=template_row.whatsapp_template_name,
                    variables=[supplier.name, body[:300]],
                )
                message.template_code = template_code
            else:
                result = await provider.send_text(to=phone, body=body)
        else:
            result = await provider.send_text(to=phone, body=body)
        provider_id, error = result.provider_message_id, result.error
    elif channel == "web_form":
        link = await create_web_form_link(session, tenant, rfq, supplier.id)
        provider_id = link
    else:
        provider_id = "manual"

    message.status = "sent" if not error else "failed"
    message.delivery_status = "sent" if not error else "failed"
    message.sent_at = datetime.now(UTC)
    message.provider_message_id = provider_id
    thread.last_message_at = datetime.now(UTC)
    await record_channel_event(
        session, tenant_id=tenant.id, kind="sent" if not error else "failed", channel=channel,
        rfq_id=rfq.id, supplier_id=supplier.id, thread_id=thread.id, message_id=message.id,
        actor_type=author_type, actor_id=author_id,
        detail={"media_type": message.media_type, "template_code": message.template_code,
                "locale": message.locale, "provider_id": provider_id, "error": error[:200]},
    )
    invitation = (await session.execute(
        select(RFQSupplier).where(RFQSupplier.rfq_id == rfq.id, RFQSupplier.supplier_id == supplier.id)
    )).scalar_one_or_none()
    if invitation is not None and not error and invitation.comm_status in {"pending", ""}:
        invitation.comm_status = "sent"
    if channel == "email" and not error:
        rfq_supplier = (await session.execute(
            select(RFQSupplier).where(RFQSupplier.rfq_id == rfq.id, RFQSupplier.supplier_id == supplier.id)
        )).scalar_one_or_none()
        if rfq_supplier:
            rfq_supplier.sent_at = rfq_supplier.sent_at or datetime.now(UTC)
            rfq_supplier.channel = channel
    session.add(OutboxEvent(tenant_id=tenant.id, topic="message.sent",
                            payload={"message_id": message.id, "channel": channel, "supplier_id": supplier.id},
                            status="published", published_at=datetime.now(UTC)))
    await meter(session, tenant_id=tenant.id, kind="message", ref_type="channel_message", ref_id=message.id)
    await session.flush()
    if error:
        log.warning("message provider error: %s", error)
        await notify(session, tenant_id=tenant.id, user_id=None, kind="message_failed",
                     title=f"Message to {supplier.name} failed", title_ar=f"فشل إرسال رسالة إلى {supplier.name}",
                     body=error[:300], link=f"/rfqs/{rfq.id}", severity="warning")
    return {"ok": not error, "message_id": message.id, "provider_id": provider_id, "error": error,
            "policy": decision.as_dict()}


def _html(body: str) -> str:
    escaped = (body or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    paragraphs = "".join(f"<p>{line}</p>" for line in escaped.split("\n\n") if line.strip())
    return (
        "<div style=\"font-family:'IBM Plex Sans Arabic',Arial,sans-serif;color:#16283f;line-height:1.65\">"
        f"{paragraphs}<hr style=\"border:none;border-top:1px solid #d9dce2\"/>"
        "<p style=\"color:#7a8494;font-size:12px\">MANTIQ · PROCUREMENT INTELLIGENCE</p></div>"
    )


def render_template(template_body: str, context: dict) -> str:
    """Simple, explicit {{variable}} substitution (no expression evaluation)."""
    rendered = template_body or ""
    for key, value in context.items():
        rendered = rendered.replace("{{" + key + "}}", str(value if value is not None else ""))
    return rendered


def quantity_summary(lines: list) -> str:
    parts = []
    for line in lines:
        qty = numbers.parse_decimal(getattr(line, "quantity", 0)) or 0
        parts.append(f"{qty.normalize()} {getattr(line, 'uom', '')} {getattr(line, 'description', '')[:60]}")
    return "; ".join(parts)[:400]


# ------------------------------------------------------------------ delivery & consent lifecycle
DELIVERY_RANK = {"pending": 0, "sent": 1, "delivered": 2, "read": 3, "failed": 0}


async def apply_delivery_status(session: AsyncSession, *, tenant_id: str, provider_message_id: str,
                                status: str, at: datetime | None = None) -> dict:
    """Apply a provider delivery/read receipt to the message, the invitation and the event log.

    Providers deliver these out of order (a `read` may arrive after a late `delivered`), so the rank
    guards against moving a message backwards. Unknown ids are acknowledged and ignored: a webhook
    must never fail because of a message this tenant did not send.
    """
    status = (status or "").strip().lower()
    if status not in DELIVERY_RANK:
        return {"updated": False, "reason": "unknown_status"}
    message = (await session.execute(
        select(ChannelMessage).where(ChannelMessage.tenant_id == tenant_id,
                                     ChannelMessage.provider_message_id == provider_message_id)
    )).scalars().first()
    if message is None:
        return {"updated": False, "reason": "unknown_message"}
    at = at or datetime.now(UTC)
    if DELIVERY_RANK[status] < DELIVERY_RANK.get(message.delivery_status, 0):
        return {"updated": False, "reason": "stale_status", "message_id": message.id}
    message.delivery_status = status
    if status == "delivered" and message.delivered_at is None:
        message.delivered_at = at
    if status == "read" and message.read_at is None:
        message.read_at = at
        message.delivered_at = message.delivered_at or at
    invitation = (await session.execute(
        select(RFQSupplier).where(RFQSupplier.rfq_id == message.rfq_id,
                                  RFQSupplier.supplier_id == message.supplier_id)
    )).scalar_one_or_none()
    if invitation is not None:
        if status == "delivered" and invitation.delivered_at is None:
            invitation.delivered_at = at
        if status == "read" and invitation.read_at is None:
            invitation.read_at = at
        if status in {"delivered", "read"} and invitation.comm_status in {"pending", "sent", ""}:
            invitation.comm_status = status
    await record_channel_event(
        session, tenant_id=tenant_id, kind=status, channel=message.channel, rfq_id=message.rfq_id,
        supplier_id=message.supplier_id, thread_id=message.thread_id, message_id=message.id,
        detail={"provider_message_id": provider_message_id, "at": at.isoformat()},
    )
    await session.flush()
    return {"updated": True, "message_id": message.id, "status": status}


async def handle_consent_keyword(session: AsyncSession, tenant: Tenant, supplier: Supplier,
                                 channel: str, text: str) -> dict:
    """Honour STOP / START in the supplier's own words, on any channel."""
    keyword = detect_opt_keyword(text)
    if not keyword:
        return {"handled": False}
    status = "opted_out" if keyword == "opt_out" else "opted_in"
    await set_opt_in(session, tenant.id, supplier.id, channel, status, source="supplier_message",
                     evidence=text[:200])
    await record_channel_event(
        session, tenant_id=tenant.id, kind=keyword, channel=channel, supplier_id=supplier.id,
        actor_type="supplier", detail={"text": text[:200], "status": status},
    )
    await notify(session, tenant_id=tenant.id, user_id=None, kind=f"consent_{keyword}",
                 title=f"{supplier.name} {keyword.replace('_', ' ')} on {channel}",
                 title_ar=(f"{supplier.name} ألغى الاشتراك على {channel}" if keyword == "opt_out"
                           else f"{supplier.name} فعّل الاشتراك على {channel}"),
                 body=text[:300], link="/inbox", severity="warning" if keyword == "opt_out" else "info")
    return {"handled": True, "keyword": keyword, "status": status, "channel": channel}
