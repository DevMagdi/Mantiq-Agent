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
    ChannelMessage,
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
) -> dict:
    """Send one message, applying consent/blacklist/cap gates. Never raises on provider failure."""
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
        result = await provider.send_text(to=phone, body=body)
        provider_id, error = result.provider_message_id, result.error
    elif channel == "web_form":
        link = await create_web_form_link(session, tenant, rfq, supplier.id)
        provider_id = link
    else:
        provider_id = "manual"

    message.status = "sent" if not error else "failed"
    message.sent_at = datetime.now(UTC)
    message.provider_message_id = provider_id
    thread.last_message_at = datetime.now(UTC)
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
