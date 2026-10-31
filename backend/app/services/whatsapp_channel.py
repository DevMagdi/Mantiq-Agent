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
from app.db.models.entities import (
    RFQ,
    ClarificationQuestion,
    IntegrationConfig,
    RFQSupplier,
    Supplier,
    Tenant,
    User,
)
from app.services import channels
from app.services import integrations as integration_adapters
from app.services import phone as phone_numbers
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


def phone_number_id(payload: dict[str, Any]) -> str:
    """The Meta `phone_number_id` the webhook was delivered to, or "".

    Meta puts it in `entry[].changes[].value.metadata.phone_number_id` — the same id the Cloud API
    adapter already sends from (`CloudWhatsAppProvider.phone_id`). Twilio's form payload has no such
    field, which is exactly how the two are told apart.
    """
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            metadata = value.get("metadata", {}) or {}
            candidate = str(metadata.get("phone_number_id") or "").strip()
            if candidate:
                return candidate
    return ""


async def tenant_for_phone_number_id(session: AsyncSession, number_id: str) -> Tenant | None:
    """The tenant that registered this WhatsApp business number on the Integrations screen.

    The mapping lives where the operator already put it: `integration_config(kind="whatsapp")`
    with `config.phone_number_id`. Disabled rows are skipped — a disabled integration must not be
    able to receive traffic either.
    """
    if not number_id:
        return None
    rows = list((await session.execute(
        select(IntegrationConfig).where(IntegrationConfig.kind == "whatsapp")
    )).scalars().all())
    for row in rows:
        config = row.config or {}
        if str(config.get("phone_number_id") or "").strip() != number_id:
            continue
        if not row.enabled:
            continue
        tenant = (await session.execute(
            select(Tenant).where(Tenant.id == row.tenant_id)
        )).scalar_one_or_none()
        if tenant is not None:
            return tenant
    return None


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


async def user_by_phone(session: AsyncSession, tenant: Tenant, number: str) -> User | None:
    """The active internal user whose profile phone is this WhatsApp number, if any."""
    if not phone_numbers.normalise(number):
        return None
    users = list((await session.execute(
        select(User).where(User.tenant_id == tenant.id, User.is_active.is_(True),
                           User.phone != "")
    )).scalars().all())
    for user in users:
        if phone_numbers.same_number(user.phone, number):
            return user
    return None


async def supplier_has_open_invitation(session: AsyncSession, tenant: Tenant,
                                       supplier_id: str) -> bool:
    """True when this supplier is currently invited on an RFQ that is still collecting offers.

    That is the test for "this message is a quote reply, not a new request": a supplier can be both a
    supplier and a colleague, and the open invitation is what decides which path the message takes.
    """
    rows = list((await session.execute(
        select(RFQSupplier).where(RFQSupplier.tenant_id == tenant.id,
                                  RFQSupplier.supplier_id == supplier_id)
    )).scalars().all())
    for invitation in rows:
        rfq = (await session.execute(
            select(RFQ).where(RFQ.id == invitation.rfq_id, RFQ.tenant_id == tenant.id)
        )).scalar_one_or_none()
        if rfq is not None and rfq.status not in {"closed", "cancelled", "awarded", "expired"}:
            return True
    return False


async def _reply(session: AsyncSession, tenant: Tenant, to: str, body: str, subject: str) -> None:
    """Send one plain WhatsApp reply to an internal user (no supplier consent rules apply)."""
    provider = await integration_adapters.adapter_for(session, tenant, "whatsapp")
    result = await provider.send_text(to=phone_numbers.normalise(to) or to, body=body)
    await record_channel_event(
        session, tenant_id=tenant.id, kind="sent" if result.ok else "failed", channel="whatsapp",
        actor_type="system",
        detail={"to_user": True, "subject": subject, "provider_id": result.provider_message_id,
                "error": result.error[:200]},
    )


async def intake_from_user(session: AsyncSession, tenant: Tenant, user: User,
                           inbound: InboundMessage, *, text: str = "") -> dict:
    """Turn a colleague's WhatsApp message into a purchase request through the text intake pipeline.

    Same service call as `POST /intake/text`, so clarification questions, taxonomy normalisation,
    provenance and the injection scan all apply. The request is created *as* the matched user, audited
    as `intake.whatsapp`, and answered on WhatsApp with the code (or the question) so the sender is
    never left guessing.
    """
    from app.services import intake

    body = (text or inbound.body or "").strip()
    if not body:
        await _reply(session, tenant, inbound.sender,
                     "لم نتمكن من قراءة رسالتك. أرسل طلب الشراء نصًا أو بصوت.",
                     "unreadable message")
        return {"matched": False, "reason": "empty_body", "channel": "whatsapp"}

    pr = await intake.intake_free_text(session, tenant, text=body, source="whatsapp",
                                       requested_by=user.id,
                                       title=body[:120].splitlines()[0] if body else "WhatsApp request")
    await audit(session, tenant_id=tenant.id, action="intake.whatsapp", actor_type="user",
                actor_id=user.id, actor_label=user.email, entity_type="purchase_requisition",
                entity_id=pr.id,
                after={"code": pr.code, "source": "whatsapp",
                       "sender": phone_numbers.normalise(inbound.sender)[-4:].rjust(4, "*")})
    questions = list((await session.execute(
        select(ClarificationQuestion).where(ClarificationQuestion.tenant_id == tenant.id,
                                           ClarificationQuestion.pr_id == pr.id,
                                           ClarificationQuestion.status == "open")
        .order_by(ClarificationQuestion.asked_at)
    )).scalars().all())
    if questions:
        question = questions[0]
        reply = (f"تم إنشاء طلبك {pr.code}. لإكماله نحتاج: "
                 f"{question.question_ar or question.question}\n"
                 f"Request {pr.code} created. We still need: {question.question}")
    else:
        reply = (f"تم إنشاء طلبك {pr.code} وسيتابعه فريق المشتريات.\n"
                 f"Request {pr.code} created and routed to procurement.")
    await _reply(session, tenant, inbound.sender, reply, f"{pr.code} acknowledgement")
    await session.flush()
    return {"matched": False, "handled_as": "buyer_intake", "pr_id": pr.id, "pr_code": pr.code,
            "clarification": bool(questions), "user_id": user.id, "channel": "whatsapp"}


async def _transcribe(session: AsyncSession, tenant: Tenant, data: bytes, filename: str) -> str:
    """The existing STT path: the same adapter `/intake/voice` uses."""
    stt = await integration_adapters.adapter_for(session, tenant, "stt")
    transcript = await stt.transcribe(data=data, filename=filename, language="ar")
    return transcript.text


async def _download_media_text(session: AsyncSession, tenant: Tenant, inbound: InboundMessage) -> str:
    """Text of the message plus any voice note, for the buyer-intake path."""
    provider = await integration_adapters.adapter_for(session, tenant, "whatsapp")
    text = inbound.body or ""
    for attachment in inbound.attachments or []:
        if attachment.get("kind") != "audio":
            continue
        media_id = attachment.get("id", "")
        if not media_id or not hasattr(provider, "download_media"):
            continue
        data, _mime = await provider.download_media(media_id)
        if not data:
            continue
        filename = attachment.get("filename") or "voice-note.ogg"
        text = f"{text}\n{await _transcribe(session, tenant, data, filename)}"
    return text.strip()


async def ingest_inbound_whatsapp(session: AsyncSession, tenant: Tenant,
                                  inbound: InboundMessage) -> dict:
    """One inbound WhatsApp message, routed to the right pipeline.

    Three destinations, decided in this order:

    1. **an active colleague** whose profile phone is this number, *and* who is not currently invited
       on an open RFQ → a purchase request through the text intake pipeline (B3);
    2. **a supplier with an open RFQ invitation** → the extraction pipeline, exactly as before;
    3. **nobody** → an unmatched event admins can see, no reply, nothing created.

    Consent keywords are handled first for suppliers, because an opt-out must be honoured even when the
    supplier also happens to be a colleague — but the opt-out never swallows a colleague's request.
    """
    sender_supplier = await _supplier_by_wa_id(session, tenant, inbound.sender)
    if sender_supplier is not None:
        consent = await handle_consent_keyword(session, tenant, sender_supplier, "whatsapp", inbound.body)
        if consent["handled"] and consent["keyword"] == "opt_out":
            await audit(session, tenant_id=tenant.id, action="channel.opt_out", actor_type="supplier",
                        actor_label=inbound.sender, entity_type="supplier",
                        entity_id=sender_supplier.id, after={"channel": "whatsapp",
                                                             "text": inbound.body[:200]})
            return {"matched": False, "reason": "opted_out", "consent": consent}

    sender_user = await user_by_phone(session, tenant, inbound.sender)
    if sender_user is not None and (
        sender_supplier is None
        or not await supplier_has_open_invitation(session, tenant, sender_supplier.id)
    ):
        # A colleague asking for something. Media is transcribed first so a voice note works exactly
        # like `/intake/voice`, then the text (or transcript) becomes the request.
        voice_text = await _download_media_text(session, tenant, inbound)
        return await intake_from_user(session, tenant, sender_user, inbound, text=voice_text)

    attachment_payloads: list[tuple[str, bytes]] = []
    extra_text = ""
    provider = await integration_adapters.adapter_for(session, tenant, "whatsapp")
    for attachment in inbound.attachments or []:
        media_id = attachment.get("id", "")
        kind = attachment.get("kind", "document")
        if not media_id or not hasattr(provider, "download_media"):
            continue
        data, _mime = await provider.download_media(media_id)
        if not data:
            continue
        filename = attachment.get("filename") or f"{kind}-{media_id[:8]}"
        if kind == "audio":
            extra_text += f"\n{await _transcribe(session, tenant, data, filename)}"
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
