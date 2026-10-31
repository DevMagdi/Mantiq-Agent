"""WhatsApp as a first-class supplier channel: rich outbound, inbound webhook, statuses, media.

The pipeline is deliberately the *same* pipeline as every other channel: a message is created by
`messaging.send_message` (so consent, blacklist, caps and the policy engine all apply), inbound replies
go through `extraction.ingest_inbound` (so every reply becomes a verifiable offer), and delivery
receipts update the one message row the inbox reads. Nothing here can approve, award or pay.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import InboundMessage
from app.core.audit import audit
from app.db.models.entities import RFQ, RFQSupplier, Supplier, Tenant
from app.services import channels
from app.services import integrations as integration_adapters
from app.services.extraction import ingest_inbound
from app.services.features import is_enabled
from app.services.messaging import (
    apply_delivery_status,
    handle_consent_keyword,
    pick_template,
    record_channel_event,
    render_template,
    send_message,
    set_opt_in,
)
from app.services.rfq import quantity_summary
from app.services.suppliers import contact_pii

log = logging.getLogger("procureai.whatsapp")


def _quote_variables(tenant: Tenant, rfq: RFQ, supplier: Supplier, lines: list) -> dict[str, str]:
    return {
        "supplier_name": supplier.name,
        "supplier_name_ar": supplier.name_ar or supplier.name,
        "tenant_name": tenant.name,
        "rfq_code": rfq.code,
        "title": rfq.title,
        "quantity_summary": quantity_summary(lines),
        "deadline": rfq.deadline.strftime("%Y-%m-%d") if rfq.deadline else "",
        "payment_terms": rfq.payment_terms,
        "delivery_location": rfq.delivery_location or "Buyer premises",
        "incoterm": rfq.incoterm,
    }


async def send_rich(
    session: AsyncSession, tenant: Tenant, *,
    rfq: RFQ,
    supplier: Supplier,
    kind: str = "text",
    body: str = "",
    body_ar: str = "",
    template_code: str = "",
    locale: str = "ar",
    attachments: list[dict] | None = None,
    author_type: str = "user",
    author_id: str | None = None,
    quote_request: bool = False,
) -> dict:
    """Send one WhatsApp message in the richest form the provider and tenant allow.

    `kind` is `text | template | document | interactive`. A quote request is an interactive message
    (WhatsApp Flow when configured) that asks for exactly three numbers — unit price, lead time and
    validity — because that is what a supplier can answer from a phone in one reply.
    """
    if not is_enabled(tenant, "whatsapp"):
        return {"ok": False, "error": "WhatsApp is disabled for this tenant.", "codes": ["BLOCK_FEATURE_OFF"]}
    if attachments and not is_enabled(tenant, "whatsapp_rich_messages"):
        return {"ok": False, "error": "Rich WhatsApp messages are disabled for this tenant.",
                "codes": ["BLOCK_FEATURE_OFF"]}

    supplier_prefs = await channels.supplier_preferences(supplier)
    locale = locale or supplier_prefs["preferred_language"] or "ar"
    if not body and template_code:
        template = await pick_template(session, tenant, template_code, "whatsapp", locale)
        if template:
            from app.db.models.entities import RFQLine

            rfq_lines = list((await session.execute(
                select(RFQLine).where(RFQLine.rfq_id == rfq.id).order_by(RFQLine.line_no)
            )).scalars().all())
            context = _quote_variables(tenant, rfq, supplier, rfq_lines)
            body = render_template(template.body, context)
            body_ar = render_template(template.body, context) if locale == "ar" else body_ar
    if not body and quote_request:
        # A structured quote request carries its own bilingual prompt: the three numbers the supplier
        # must reply with, in the language they were contacted in.
        body = ("نحتاج عرض سعركم: سعر الوحدة، مدة التوريد، وصلاحية العرض. "
                "يمكنكم الرد على هذه الرسالة مباشرة."
                if locale == "ar" else
                "We need your quotation: unit price, lead time and offer validity. "
                "You can reply directly to this message.")
    if not body:
        return {"ok": False, "error": "A WhatsApp message needs a body or a template.",
                "codes": ["EMPTY_MESSAGE"]}

    interactive = None
    if quote_request:
        interactive = {
            "body": body,
            "options": ["Unit price", "Lead time", "Validity"],
            "flow": f"quote:{rfq.id}:{supplier.id}",
        }

    outcome = await send_message(
        session, tenant, rfq=rfq, supplier=supplier, subject=f"RFQ {rfq.code}",
        body=body, body_ar=body_ar or body, channel="whatsapp", author_type=author_type,
        author_id=author_id, attachments=attachments, template_code=template_code, locale=locale,
        media_type="interactive" if interactive else ("document" if attachments else "text"),
        interactive=interactive,
    )
    return {**outcome, "kind": "interactive" if interactive else ("document" if attachments else kind)}


async def handle_webhook(session: AsyncSession, tenant: Tenant, payload: dict[str, Any], *,
                         actor_label: str = "whatsapp:webhook") -> dict:
    """Process one WhatsApp webhook delivery: receipts, consent, messages (with media).

    Order matters. Receipts first (they are cheap and idempotent), then consent keywords, then the
    message pipeline — stopping an opted-out supplier's text from ever reaching an RFQ.
    """
    provider = await integration_adapters.adapter_for(session, tenant, "whatsapp")
    statuses = provider.parse_statuses(payload) if hasattr(provider, "parse_statuses") else []
    receipts = []
    for status in statuses:
        receipts.append(await apply_delivery_status(
            session, tenant_id=tenant.id, provider_message_id=status["provider_message_id"],
            status=status["status"],
        ))
    for event in (provider.parse_consent_events(payload) if hasattr(provider, "parse_consent_events") else []):
        supplier = await _supplier_by_wa_id(session, tenant, event["wa_id"])
        if supplier is not None:
            await set_opt_in(session, tenant.id, supplier.id, "whatsapp", "opted_in",
                             source="whatsapp_opt_in_event", evidence=event.get("type", ""))
            await record_channel_event(session, tenant_id=tenant.id, kind="opt_in", channel="whatsapp",
                                       supplier_id=supplier.id, actor_type="supplier",
                                       detail={"source": "whatsapp_opt_in_event"})

    results: list[dict] = []
    for inbound in provider.parse_webhook(payload):
        results.append(await ingest_inbound_whatsapp(session, tenant, inbound))
    await session.flush()
    return {"statuses": receipts, "messages": results, "processed": len(results)}


async def _supplier_by_wa_id(session: AsyncSession, tenant: Tenant, wa_id: str) -> Supplier | None:
    if not wa_id:
        return None
    normalized = "".join(ch for ch in wa_id if ch.isdigit())[-9:]
    if not normalized:
        return None
    from app.db.models.entities import SupplierContact

    contacts = list((await session.execute(
        select(SupplierContact).where(SupplierContact.tenant_id == tenant.id)
    )).scalars().all())
    for contact in contacts:
        _email, phone = await contact_pii(session, tenant, contact)
        digits = "".join(ch for ch in (phone or "") if ch.isdigit())
        if digits and (digits.endswith(normalized) or normalized.endswith(digits[-9:])):
            return (await session.execute(
                select(Supplier).where(Supplier.id == contact.supplier_id)
            )).scalar_one_or_none()
    return None


async def ingest_inbound_whatsapp(session: AsyncSession, tenant: Tenant,
                                  inbound: InboundMessage) -> dict:
    """One inbound WhatsApp message: consent keywords, media download, then the shared pipeline."""
    sender_supplier = await _supplier_by_wa_id(session, tenant, inbound.sender)
    if sender_supplier is not None:
        consent = await handle_consent_keyword(session, tenant, sender_supplier, "whatsapp", inbound.body)
        if consent["handled"] and consent["keyword"] == "opt_out":
            await audit(session, tenant_id=tenant.id, action="channel.opt_out", actor_type="supplier",
                        actor_label=inbound.sender, entity_type="supplier",
                        entity_id=sender_supplier.id, after={"channel": "whatsapp",
                                                             "text": inbound.body[:200]})
            return {"matched": False, "reason": "opted_out", "consent": consent}

    attachment_payloads: list[tuple[str, bytes]] = []
    extra_text = ""
    provider = await integration_adapters.adapter_for(session, tenant, "whatsapp")
    for attachment in inbound.attachments or []:
        media_id = attachment.get("id", "")
        kind = attachment.get("kind", "document")
        if not media_id or not hasattr(provider, "download_media"):
            continue
        data, mime = await provider.download_media(media_id)
        if not data:
            continue
        filename = attachment.get("filename") or f"{kind}-{media_id[:8]}"
        if kind == "audio":
            stt = await integration_adapters.adapter_for(session, tenant, "stt")
            transcript = await stt.transcribe(data=data, filename=filename, language="ar")
            extra_text += f"\n{transcript.text}"
            attachment_payloads.append((f"{filename}.txt", transcript.text.encode("utf-8")))
        else:
            attachment_payloads.append((filename, data))
    if extra_text:
        inbound.body = f"{inbound.body}{extra_text}"[:20000]

    result = await ingest_inbound(session, tenant, inbound, attachment_payloads=attachment_payloads)
    if result.get("matched"):
        invitation = (await session.execute(
            select(RFQSupplier).where(RFQSupplier.rfq_id == result["rfq_id"],
                                      RFQSupplier.supplier_id == result["supplier_id"])
        )).scalar_one_or_none()
        if invitation is not None:
            invitation.reply_count += 1
            invitation.responded_at = invitation.responded_at or datetime.now(UTC)
            invitation.comm_status = "replied"
            invitation.status = "responded"
        # A message on WhatsApp proves the number works; email replies prove nothing about WhatsApp.
        await set_opt_in(session, tenant.id, result["supplier_id"], "whatsapp", "opted_in",
                         source="inbound_reply", evidence="supplier replied on WhatsApp")
    else:
        await record_channel_event(
            session, tenant_id=tenant.id, kind="received_unmatched", channel="whatsapp",
            supplier_id=sender_supplier.id if sender_supplier else None,
            detail={"reason": result.get("reason", ""), "body": inbound.body[:200]},
        )
    await session.flush()
    return {**result, "channel": "whatsapp"}
