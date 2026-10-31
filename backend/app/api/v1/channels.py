"""Phase-3 API: channel selection, WhatsApp, the unified inbox, AI voice and the assistant."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.adapters.base import registry
from app.api.deps import Context, require
from app.core.audit import audit
from app.core.errors import NotFound, ValidationFailed
from app.db.models.entities import (
    RFQ,
    AssistantSession,
    ChannelEvent,
    MessageThread,
    RFQSupplier,
    Supplier,
    VoiceCall,
)
from app.services import assistant, channels, features, inbox, messaging, voice_agent, whatsapp_channel

log = logging.getLogger("procureai.api.channels")
router = APIRouter(tags=["channels"])


# ------------------------------------------------------------------ helpers
async def _supplier(ctx: Context, supplier_id: str) -> Supplier:
    row = (await ctx.session.execute(
        select(Supplier).where(Supplier.id == supplier_id, Supplier.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not row:
        raise NotFound("Supplier not found.")
    return row


async def _rfq(ctx: Context, rfq_id: str) -> RFQ:
    row = (await ctx.session.execute(
        select(RFQ).where(RFQ.id == rfq_id, RFQ.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not row:
        raise NotFound("RFQ not found.")
    return row


async def _invitation(ctx: Context, rfq_id: str, supplier_id: str) -> RFQSupplier:
    row = (await ctx.session.execute(
        select(RFQSupplier).where(RFQSupplier.rfq_id == rfq_id, RFQSupplier.supplier_id == supplier_id,
                                  RFQSupplier.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not row:
        raise NotFound("Supplier is not invited to this RFQ.")
    return row


# ------------------------------------------------------------------ capabilities & flags
@router.get("/channels/capabilities", summary="Available supplier channels and their state")
async def capabilities(ctx: Context = Depends(require("channel:read"))):
    from app.services import integrations as integration_service

    provider = await integration_service.adapter_for(ctx.session, ctx.tenant, "whatsapp")
    return {
        "channels": [
            {**channels.label(channel), "enabled": channels.require_feature(ctx.tenant, channel),
             "primary": channel == "whatsapp"}
            for channel in channels.CHANNELS
        ],
        "default_order": list(channels.DEFAULT_ORDER),
        "whatsapp": {
            "provider": getattr(provider, "name", "fake"),
            "supports_templates": bool(getattr(provider, "supports_templates", False)),
            "supports_documents": bool(getattr(provider, "supports_documents", False)),
            "supports_interactive": bool(getattr(provider, "supports_interactive", False)),
            "supports_read_receipts": bool(getattr(provider, "supports_read_receipts", False)),
        },
        "features": features.describe(ctx.tenant),
    }


@router.get("/settings/features", summary="Tenant feature flags for channels and AI voice")
async def get_features(ctx: Context = Depends(require("settings:read"))):
    return {"features": features.describe(ctx.tenant)}


class FeaturePatch(BaseModel):
    features: dict[str, bool]


@router.patch("/settings/features", summary="Turn channel / AI-voice features on or off")
async def patch_features(payload: FeaturePatch, ctx: Context = Depends(require("settings:update"))):
    before = features.describe(ctx.tenant)
    try:
        result = await features.update(ctx.session, ctx.tenant, payload.features)
    except ValueError as exc:
        raise ValidationFailed(str(exc)) from exc
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="settings.features_updated",
                actor_id=ctx.user_id, actor_label=ctx.user.email, entity_type="tenant",
                entity_id=ctx.tenant_id, before={"features": before}, after={"features": result["features"]},
                ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return result


# ------------------------------------------------------------------ supplier preferences
class ChannelPrefsIn(BaseModel):
    preferred_channel: str | None = Field(None, pattern="^(whatsapp|voice|email|web_form)$")
    secondary_channels: list[str] | None = None
    preferred_language: str | None = Field(None, pattern="^(ar|en)$")


@router.get("/suppliers/{supplier_id}/channels", summary="Channel preferences and reachability")
async def get_supplier_channels(supplier_id: str, ctx: Context = Depends(require("channel:read"))):
    supplier = await _supplier(ctx, supplier_id)
    prefs = await channels.supplier_preferences(supplier)
    states = [await channels.availability(ctx.session, ctx.tenant, supplier, channel)
              for channel in channels.CHANNELS]
    return {**prefs, "channels": states,
            "resolved": await channels.resolve(ctx.session, ctx.tenant, supplier)}


@router.patch("/suppliers/{supplier_id}/channels", summary="Set the preferred and secondary channels")
async def set_supplier_channels(supplier_id: str, payload: ChannelPrefsIn,
                                ctx: Context = Depends(require("channel:write"))):
    supplier = await _supplier(ctx, supplier_id)
    before = await channels.supplier_preferences(supplier)
    result = await channels.set_supplier_preferences(
        ctx.session, ctx.tenant, supplier, preferred_channel=payload.preferred_channel,
        secondary_channels=payload.secondary_channels, preferred_language=payload.preferred_language,
    )
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="supplier.channels_updated",
                actor_id=ctx.user_id, actor_label=ctx.user.email, entity_type="supplier",
                entity_id=supplier.id, before=before, after=result, ip=ctx.ip,
                request_id=ctx.request_id)
    await ctx.session.commit()
    return result


class OptInIn(BaseModel):
    channel: str = Field("whatsapp", pattern="^(whatsapp|voice|email)$")
    status: str = Field("opted_in", pattern="^(opted_in|opted_out)$")
    evidence: str = ""
    source: str = "manual"


@router.post("/suppliers/{supplier_id}/opt-in", summary="Record consent for one channel")
async def set_opt_in(supplier_id: str, payload: OptInIn, ctx: Context = Depends(require("channel:write"))):
    supplier = await _supplier(ctx, supplier_id)
    row = await messaging.set_opt_in(ctx.session, ctx.tenant_id, supplier.id, payload.channel,
                                     payload.status, source=payload.source, evidence=payload.evidence)
    await messaging.record_channel_event(
        ctx.session, tenant_id=ctx.tenant_id, kind=payload.status, channel=payload.channel,
        supplier_id=supplier.id, actor_type="user", actor_id=ctx.user_id,
        detail={"source": payload.source, "evidence": payload.evidence[:200]},
    )
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="channel.opt_in_recorded",
                actor_id=ctx.user_id, actor_label=ctx.user.email, entity_type="supplier",
                entity_id=supplier.id,
                after={"channel": payload.channel, "status": payload.status, "source": payload.source},
                ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return {"supplier_id": supplier.id, "channel": row.channel, "status": row.status,
            "source": row.source}


# ------------------------------------------------------------------ RFQ channels
@router.get("/rfqs/{rfq_id}/channels", summary="Resolved channel and communication status per supplier")
async def rfq_channels(rfq_id: str, ctx: Context = Depends(require("channel:read"))):
    rfq = await _rfq(ctx, rfq_id)
    return await channels.rfq_channels(ctx.session, ctx.tenant, rfq)


class ChannelIn(BaseModel):
    channel: str | None = Field(None, pattern="^(whatsapp|voice|email|web_form)$")
    reason: str = ""


@router.put("/rfqs/{rfq_id}/suppliers/{supplier_id}/channel",
            summary="Override the channel for one supplier on this RFQ (null clears it)")
async def override_channel(rfq_id: str, supplier_id: str, payload: ChannelIn,
                           ctx: Context = Depends(require("channel:write"))):
    rfq = await _rfq(ctx, rfq_id)
    invitation = await _invitation(ctx, rfq.id, supplier_id)
    before = {"channel_override": invitation.channel_override, "channel": invitation.channel}
    result = await channels.set_rfq_channel(ctx.session, ctx.tenant, rfq, invitation,
                                            channel=payload.channel, actor_id=ctx.user_id,
                                            reason=payload.reason)
    if not result.get("ok"):
        await ctx.session.rollback()
        raise ValidationFailed(result.get("error", "channel_unavailable"),
                               extra={"reasons": result.get("reasons", [])})
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="rfq.channel_override",
                actor_id=ctx.user_id, actor_label=ctx.user.email, entity_type="rfq", entity_id=rfq.id,
                before=before, after={"channel": result["channel"], "source": result["source"]},
                ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return result


@router.post("/rfqs/{rfq_id}/suppliers/{supplier_id}/switch-channel",
             summary="Move the live conversation to another channel")
async def switch_channel(rfq_id: str, supplier_id: str, payload: ChannelIn,
                         ctx: Context = Depends(require("channel:write"))):
    rfq = await _rfq(ctx, rfq_id)
    if not features.is_enabled(ctx.tenant, "channel_switching"):
        raise ValidationFailed("Channel switching is disabled for this tenant.",
                               extra={"code": "BLOCK_FEATURE_OFF"})
    supplier = await _supplier(ctx, supplier_id)
    invitation = (await ctx.session.execute(
        select(RFQSupplier).where(RFQSupplier.rfq_id == rfq.id, RFQSupplier.supplier_id == supplier.id)
    )).scalar_one_or_none()
    if not payload.channel:
        raise ValidationFailed("A target channel is required.")
    result = await channels.switch_channel(ctx.session, ctx.tenant, rfq, supplier, invitation,
                                           channel=payload.channel, actor_id=ctx.user_id,
                                           reason=payload.reason)
    if not result.get("ok"):
        await ctx.session.rollback()
        raise ValidationFailed(result.get("error", "channel_unavailable"),
                               extra={"reasons": result.get("reasons", [])})
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="rfq.channel_switched",
                actor_id=ctx.user_id, actor_label=ctx.user.email, entity_type="rfq", entity_id=rfq.id,
                after=result, ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return result


# ------------------------------------------------------------------ WhatsApp outbound
class WhatsAppIn(BaseModel):
    supplier_id: str
    body: str = ""
    template_code: str = ""
    locale: str = Field("ar", pattern="^(ar|en)$")
    kind: str = Field("text", pattern="^(text|template|document|quote_request)$")
    quote_request: bool = False


@router.post("/rfqs/{rfq_id}/whatsapp", summary="Send a rich WhatsApp message to a supplier")
async def send_whatsapp(rfq_id: str, payload: WhatsAppIn, ctx: Context = Depends(require("rfq:send"))):
    rfq = await _rfq(ctx, rfq_id)
    supplier = await _supplier(ctx, payload.supplier_id)
    result = await whatsapp_channel.send_rich(
        ctx.session, ctx.tenant, rfq=rfq, supplier=supplier, kind=payload.kind, body=payload.body,
        template_code=payload.template_code, locale=payload.locale,
        quote_request=payload.quote_request or payload.kind == "quote_request",
        author_type="user", author_id=ctx.user_id,
    )
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="whatsapp.sent",
                actor_id=ctx.user_id, actor_label=ctx.user.email, entity_type="rfq", entity_id=rfq.id,
                after={"supplier_id": supplier.id, "ok": result.get("ok"), "kind": result.get("kind"),
                       "template_code": payload.template_code}, ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return result


@router.post("/suppliers/{supplier_id}/whatsapp/document",
             summary="Send a document to a supplier over WhatsApp")
async def send_whatsapp_document(supplier_id: str, rfq_id: str = Query(...), caption: str = "",
                                 file: UploadFile = File(...),
                                 ctx: Context = Depends(require("rfq:send"))):
    rfq = await _rfq(ctx, rfq_id)
    supplier = await _supplier(ctx, supplier_id)
    data = await file.read()
    result = await whatsapp_channel.send_rich(
        ctx.session, ctx.tenant, rfq=rfq, supplier=supplier, body=caption or file.filename or "",
        kind="document", attachments=[{"filename": file.filename or "document.pdf", "data": data,
                                       "mime": file.content_type or "application/pdf"}],
        author_type="user", author_id=ctx.user_id,
    )
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="whatsapp.document_sent",
                actor_id=ctx.user_id, actor_label=ctx.user.email, entity_type="rfq", entity_id=rfq.id,
                after={"supplier_id": supplier.id, "filename": file.filename,
                       "ok": result.get("ok")}, ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return result


# ------------------------------------------------------------------ unified inbox
@router.get("/inbox", summary="Unified conversation inbox across every channel")
async def list_inbox(ctx: Context = Depends(require("inbox:read")), q: str = "", channel: str = "",
                     supplier_id: str = "", rfq_id: str = "", status: str = "",
                     limit: int = Query(50, ge=1, le=200)):
    return await inbox.conversations(ctx.session, ctx.tenant, q=q, channel=channel,
                                     supplier_id=supplier_id, rfq_id=rfq_id, status=status,
                                     limit=limit)


@router.get("/inbox/search", summary="Search every message on every channel")
async def search_inbox(ctx: Context = Depends(require("inbox:read")), q: str = Query(...),
                       channel: str = "", supplier_id: str = "", limit: int = Query(50, ge=1, le=200)):
    return await inbox.search(ctx.session, ctx.tenant, q=q, channel=channel, supplier_id=supplier_id,
                              limit=limit)


@router.get("/inbox/threads/{thread_id}", summary="One conversation across all channels")
async def thread_detail(thread_id: str, ctx: Context = Depends(require("inbox:read"))):
    thread = (await ctx.session.execute(
        select(MessageThread).where(MessageThread.id == thread_id,
                                    MessageThread.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not thread:
        raise NotFound("Conversation not found.")
    return await inbox.thread_detail(ctx.session, ctx.tenant, thread)


class ReplyIn(BaseModel):
    body: str = Field(min_length=1, max_length=8000)
    subject: str = ""
    channel: str | None = Field(None, pattern="^(whatsapp|voice|email|web_form)$")
    locale: str = Field("ar", pattern="^(ar|en)$")


@router.post("/inbox/threads/{thread_id}/reply", summary="Reply on the channel the supplier used")
async def reply(thread_id: str, payload: ReplyIn, ctx: Context = Depends(require("inbox:reply"))):
    thread = (await ctx.session.execute(
        select(MessageThread).where(MessageThread.id == thread_id,
                                    MessageThread.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not thread:
        raise NotFound("Conversation not found.")
    result = await inbox.reply(ctx.session, ctx.tenant, thread, body=payload.body,
                               subject=payload.subject, channel=payload.channel,
                               author_id=ctx.user_id, locale=payload.locale)
    if not result.get("ok"):
        await ctx.session.rollback()
        raise ValidationFailed(result.get("error", "send_failed"),
                               extra={"reasons": result.get("reasons", [])})
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="inbox.replied", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="message_thread", entity_id=thread.id,
                after={"channel": result.get("channel"), "switched_from": result.get("switched_from")},
                ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return result


@router.get("/inbox/threads/{thread_id}/events", summary="The channel event trail of one thread")
async def thread_events(thread_id: str, ctx: Context = Depends(require("inbox:read"))):
    rows = list((await ctx.session.execute(
        select(ChannelEvent).where(ChannelEvent.tenant_id == ctx.tenant_id,
                                   ChannelEvent.thread_id == thread_id)
        .order_by(ChannelEvent.created_at)
    )).scalars().all())
    return {"items": [{
        "id": row.id, "kind": row.kind, "channel": row.channel, "supplier_id": row.supplier_id,
        "actor_type": row.actor_type, "actor_id": row.actor_id, "detail": row.detail,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    } for row in rows]}


# ------------------------------------------------------------------ template library
@router.get("/templates/library", summary="Templates per channel and locale, with their variables")
async def template_library(ctx: Context = Depends(require("rfq:read"))):
    from app.db.models.entities import MessageTemplate

    rows = list((await ctx.session.execute(
        select(MessageTemplate).where(MessageTemplate.tenant_id == ctx.tenant_id,
                                      MessageTemplate.is_active.is_(True))
        .order_by(MessageTemplate.channel, MessageTemplate.code, MessageTemplate.locale)
    )).scalars().all())
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row.channel, []).append({
            "id": row.id, "code": row.code, "kind": row.kind, "channel": row.channel,
            "locale": row.locale, "name": row.name, "subject": row.subject, "body": row.body,
            "whatsapp_template_name": row.whatsapp_template_name, "version": row.version,
            "description": row.description,
            "variables": row.variables or messaging.template_variables(row.body),
        })
    return {
        "channels": [{"channel": channel, **channels.label(channel), "count": len(items)}
                     for channel, items in sorted(grouped.items())],
        "items": grouped,
        "total": len(rows),
    }


# ------------------------------------------------------------------ AI voice calls
@router.get("/voice/features", summary="Which voice features are enabled for this tenant")
async def voice_features(ctx: Context = Depends(require("rfq:read"))):
    return {
        "features": [row for row in features.describe(ctx.tenant)
                     if row["key"].startswith("voice")],
        "provider": getattr(registry.resolve("telephony"), "name", "fake"),
        "disclosure_required": True,
        "recording_requires_consent": True,
    }


class CallIn(BaseModel):
    rfq_id: str
    supplier_id: str
    language: str = Field("ar-EG", pattern="^(ar-EG|ar-SA|en)$")
    mode: str = Field("ai", pattern="^(ai|human)$")


@router.post("/voice/calls", status_code=201, summary="Place an outbound AI call (disclosure + consent)")
async def place_call(payload: CallIn, ctx: Context = Depends(require("voice:call"))):
    rfq = await _rfq(ctx, payload.rfq_id)
    supplier = await _supplier(ctx, payload.supplier_id)
    if payload.mode == "human":
        from app.services import voice as voice_service

        script = await voice_service.manual_script(ctx.session, ctx.tenant, rfq, supplier)
        await ctx.session.commit()
        return {"mode": "human", **script}
    result = await voice_agent.run_ai_call(ctx.session, ctx.tenant, rfq=rfq, supplier=supplier,
                                           language=payload.language, actor_id=ctx.user_id)
    if not result.get("ok") and result.get("error"):
        await audit(ctx.session, tenant_id=ctx.tenant_id, action="voice.call_blocked",
                    actor_id=ctx.user_id, actor_label=ctx.user.email, entity_type="rfq",
                    entity_id=rfq.id, after={"supplier_id": supplier.id,
                                             "error": result["error"]}, ip=ctx.ip,
                    request_id=ctx.request_id)
        await ctx.session.commit()
        return result
    await ctx.session.commit()
    return result


@router.get("/voice/calls", summary="Call log for an RFQ (or the whole tenant)")
async def list_calls(ctx: Context = Depends(require("voice:call")), rfq_id: str = "",
                     supplier_id: str = "", limit: int = Query(50, ge=1, le=200)):
    query = select(VoiceCall).where(VoiceCall.tenant_id == ctx.tenant_id)
    if rfq_id:
        query = query.where(VoiceCall.rfq_id == rfq_id)
    if supplier_id:
        query = query.where(VoiceCall.supplier_id == supplier_id)
    rows = list((await ctx.session.execute(
        query.order_by(VoiceCall.created_at.desc()).limit(limit)
    )).scalars().all())
    return {"items": [{
        "id": row.id, "rfq_id": row.rfq_id, "supplier_id": row.supplier_id, "status": row.status,
        "disclosure_played": row.disclosure_played, "consent_given": row.consent_given,
        "recording": bool(row.recording_uri), "duration_seconds": row.duration_seconds,
        "extracted_offer_id": row.extracted_offer_id,
        "blocked_turns": (row.policy_snapshot or {}).get("ai_agent", {}).get("blocked_turns", 0),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    } for row in rows]}


@router.get("/voice/calls/{call_id}", summary="Call console: transcript, policy turns and extraction")
async def call_detail(call_id: str, ctx: Context = Depends(require("voice:call"))):
    call = (await ctx.session.execute(
        select(VoiceCall).where(VoiceCall.id == call_id, VoiceCall.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not call:
        raise NotFound("Call not found.")
    return await voice_agent.call_view(ctx.session, ctx.tenant, call)


# ------------------------------------------------------------------ internal assistant
class AskIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    channel: str = Field("text", pattern="^(text|voice)$")
    locale: str = Field("ar", pattern="^(ar|en)$")
    session_id: str | None = None


@router.post("/assistant/ask", summary="Ask the internal assistant (read-only, RBAC-scoped)")
async def assistant_ask(payload: AskIn, ctx: Context = Depends(require("assistant:use"))):
    result = await assistant.ask(ctx.session, ctx.tenant, ctx.user, set(ctx.permissions),
                                 text=payload.text, channel=payload.channel, locale=payload.locale,
                                 session_id=payload.session_id, ip=ctx.ip,
                                 request_id=ctx.request_id)
    await ctx.session.commit()
    return result


@router.post("/assistant/transcribe", summary="Transcribe a spoken question (STT) without trusting it")
async def assistant_transcribe(file: UploadFile = File(...), ask: bool = Query(False),
                               locale: str = Query("ar", pattern="^(ar|en)$"),
                               ctx: Context = Depends(require("assistant:use"))):
    """Speech becomes text, and text is treated as untrusted input like any other intake.

    With `ask=true` the transcript is answered in the same round trip, so the voice screen can speak
    one reply instead of chaining two calls.
    """
    data = await file.read()
    stt = registry.resolve("stt")
    transcript = await stt.transcribe(data=data, filename=file.filename or "question.wav",
                                      language="ar")
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="assistant.transcribed",
                actor_id=ctx.user_id, actor_label=ctx.user.email, entity_type="assistant_session",
                after={"filename": file.filename, "chars": len(transcript.text),
                       "confidence": float(transcript.confidence)}, ip=ctx.ip,
                request_id=ctx.request_id)
    payload = {"text": transcript.text, "language": transcript.language,
               "confidence": float(transcript.confidence)}
    if ask and transcript.text.strip():
        payload["answer"] = await assistant.ask(ctx.session, ctx.tenant, ctx.user, set(ctx.permissions),
                                                text=transcript.text, channel="voice", locale=locale,
                                                ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return payload


@router.get("/assistant/sessions", summary="My recent assistant conversations")
async def assistant_sessions(ctx: Context = Depends(require("assistant:use"))):
    return {"items": await assistant.recent_sessions(ctx.session, ctx.tenant_id, ctx.user_id)}


@router.get("/assistant/sessions/{session_id}", summary="One assistant conversation, turn by turn")
async def assistant_session_detail(session_id: str, ctx: Context = Depends(require("assistant:use"))):
    row = (await ctx.session.execute(
        select(AssistantSession).where(AssistantSession.id == session_id,
                                       AssistantSession.tenant_id == ctx.tenant_id,
                                       AssistantSession.user_id == ctx.user_id)
    )).scalar_one_or_none()
    if not row:
        raise NotFound("Conversation not found.")
    return await assistant.session_view(ctx.session, ctx.tenant_id, row)
