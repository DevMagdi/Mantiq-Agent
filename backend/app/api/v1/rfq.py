"""RFQ, replies, comparison, negotiation, approval and PO endpoints (FR-RFQ..CMP)."""
from __future__ import annotations

import io
import logging
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.adapters.base import InboundMessage
from app.api.deps import Context, require
from app.core.audit import audit
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.db.models.entities import (
    RFQ,
    Approval,
    ChannelMessage,
    ExtractedField,
    ExtractionResult,
    MessageTemplate,
    NegotiationMessage,
    Offer,
    PurchaseRequisition,
    RFQLine,
    RFQSupplier,
    Supplier,
    Tenant,
    VoiceCall,
)
from app.services import comparison, negotiation, numbers, voice
from app.services.approval import decide as approval_decide
from app.services.approval import delegate as approval_delegate
from app.services.approval import escalate_overdue, request_approval
from app.services.common import notify
from app.services.extraction import (
    ingest_inbound,
    mark_extraction_verified,
    request_missing_info,
    verify_field,
)
from app.services.messaging import render_template, send_message
from app.services.rfq import (
    append_stage,
    close_rfq,
    create_rfq_from_pr,
    dispatch_rfq,
    mark_negotiating,
    pick_template,
    rfq_overview,
    send_reminders,
)

log = logging.getLogger("procureai.api.rfq")
router = APIRouter(tags=["rfq"])


async def _tenant_rfq(ctx: Context, rfq_id: str) -> RFQ:
    rfq = (await ctx.session.execute(
        select(RFQ).where(RFQ.id == rfq_id, RFQ.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not rfq:
        raise NotFound("RFQ not found.")
    return rfq


# ------------------------------------------------------------------ RFQ CRUD
@router.get("/rfqs", summary="List RFQs")
async def list_rfqs(ctx: Context = Depends(require("rfq:read")), status: str | None = None,
                    q: str | None = None, page: int = 1, page_size: int = 25):
    from sqlalchemy import func

    filters = [RFQ.tenant_id == ctx.tenant_id]
    if status:
        filters.append(RFQ.status == status)
    if q:
        filters.append(RFQ.title.ilike(f"%{q}%"))
    total = (await ctx.session.execute(select(func.count()).select_from(RFQ).where(*filters))).scalar_one()
    rows = list((await ctx.session.execute(
        select(RFQ).where(*filters).order_by(RFQ.created_at.desc())
        .limit(page_size).offset((page - 1) * page_size)
    )).scalars().all())
    return {"items": [{
        "id": r.id, "code": r.code, "title": r.title, "status": r.status, "mode": r.mode,
        "currency": r.currency, "deadline": r.deadline, "created_at": r.created_at,
        "sent_at": r.sent_at, "incoterm": r.incoterm,
    } for r in rows], "total": int(total), "page": page, "page_size": page_size}


@router.get("/rfqs/board", summary="Kanban board grouped by stage with deadline chips")
async def board(ctx: Context = Depends(require("rfq:read"))):
    return await rfq_overview(ctx.session, ctx.tenant)


class CreateRfqIn(BaseModel):
    pr_id: str
    supplier_ids: list[str] = Field(min_length=1)
    deadline_days: int = Field(10, ge=1, le=120)
    incoterm: str = "DAP"
    payment_terms: str = "Net 30"
    mode: str = Field("draft_only", pattern="^(draft_only|semi_auto|auto)$")
    channel: str = Field("email", pattern="^(email|whatsapp|web_form|phone)$")
    title: str | None = None
    scoring_weights: dict[str, float] | None = None


@router.post("/rfqs", status_code=201, summary="Create an RFQ from a requisition")
async def create_rfq(payload: CreateRfqIn, ctx: Context = Depends(require("rfq:create"))):
    pr = (await ctx.session.execute(
        select(PurchaseRequisition).where(PurchaseRequisition.id == payload.pr_id,
                                          PurchaseRequisition.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not pr:
        raise NotFound("Requisition not found.")
    if not pr.clarification_resolved:
        raise ValidationFailed("Resolve outstanding clarification questions first.")
    supplier_rows = list((await ctx.session.execute(
        select(Supplier).where(Supplier.id.in_(payload.supplier_ids), Supplier.tenant_id == ctx.tenant_id)
    )).scalars().all())
    if len(supplier_rows) != len(set(payload.supplier_ids)):
        raise ValidationFailed("One or more suppliers were not found in this tenant.")
    blocked = [s.name for s in supplier_rows if s.status == "blacklisted"]
    if blocked:
        raise Conflict(f"Blacklisted suppliers cannot receive an RFQ: {', '.join(blocked)}")
    rfq = await create_rfq_from_pr(ctx.session, ctx.tenant, pr, supplier_ids=payload.supplier_ids,
                                   buyer_id=ctx.user_id, deadline_days=payload.deadline_days,
                                   incoterm=payload.incoterm, payment_terms=payload.payment_terms,
                                   mode=payload.mode, channel=payload.channel, title=payload.title,
                                   scoring_weights=payload.scoring_weights)
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="rfq.created", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="rfq", entity_id=rfq.id,
                after={"code": rfq.code, "suppliers": len(supplier_rows)}, ip=ctx.ip,
                request_id=ctx.request_id)
    await ctx.session.commit()
    return {"id": rfq.id, "code": rfq.code, "status": rfq.status}


class RfqPatch(BaseModel):
    mode: str | None = Field(None, pattern="^(draft_only|semi_auto|auto)$")
    deadline: datetime | None = None
    incoterm: str | None = None
    payment_terms: str | None = None
    delivery_location: str | None = None
    title: str | None = None
    stage: str | None = Field(None, pattern="^(draft|sent|replied|negotiating|closed|cancelled|expired)$")


@router.patch("/rfqs/{rfq_id}", summary="Update RFQ settings / move stage on the board")
async def patch_rfq(rfq_id: str, payload: RfqPatch, ctx: Context = Depends(require("rfq:update"))):
    rfq = await _tenant_rfq(ctx, rfq_id)
    changes = payload.model_dump(exclude_none=True)
    stage = changes.pop("stage", None)
    for key, value in changes.items():
        setattr(rfq, key, value)
    if stage and stage != rfq.status:
        if stage == "closed":
            await close_rfq(ctx.session, rfq, ctx.user_id, "manual")
        else:
            append_stage(rfq, stage, ctx.user_id)
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="rfq.updated", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="rfq", entity_id=rfq.id,
                after={**changes, "stage": stage}, ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return {"ok": True, "status": rfq.status}


@router.get("/rfqs/{rfq_id}", summary="RFQ detail (lines, invitations, offers, messages, policy)")
async def get_rfq(rfq_id: str, ctx: Context = Depends(require("rfq:read"))):
    rfq = await _tenant_rfq(ctx, rfq_id)
    rfq_lines = list((await ctx.session.execute(
        select(RFQLine).where(RFQLine.rfq_id == rfq.id).order_by(RFQLine.line_no)
    )).scalars().all())
    invitations = list((await ctx.session.execute(
        select(RFQSupplier).where(RFQSupplier.rfq_id == rfq.id)
    )).scalars().all())
    suppliers = {s.id: s for s in (await ctx.session.execute(
        select(Supplier).where(Supplier.id.in_([i.supplier_id for i in invitations] or ["none"]))
    )).scalars().all()}
    offers = list((await ctx.session.execute(
        select(Offer).where(Offer.rfq_id == rfq.id).order_by(Offer.created_at)
    )).scalars().all())
    messages = list((await ctx.session.execute(
        select(ChannelMessage).where(ChannelMessage.rfq_id == rfq.id)
        .order_by(ChannelMessage.created_at.desc()).limit(50)
    )).scalars().all())
    policy = await negotiation.policy_for(ctx.session, ctx.tenant, rfq)
    pr = (await ctx.session.execute(
        select(PurchaseRequisition).where(PurchaseRequisition.id == rfq.pr_id)
    )).scalar_one_or_none() if rfq.pr_id else None
    return {
        "id": rfq.id, "code": rfq.code, "title": rfq.title, "status": rfq.status, "mode": rfq.mode,
        "currency": rfq.currency, "incoterm": rfq.incoterm, "payment_terms": rfq.payment_terms,
        "delivery_location": rfq.delivery_location, "deadline": rfq.deadline,
        "validity_date": rfq.validity_date, "buyer_id": rfq.buyer_id, "sent_at": rfq.sent_at,
        "stage_history": rfq.stage_history, "scoring_weights": rfq.scoring_weights,
        "notes": rfq.notes, "pr": {"id": pr.id, "code": pr.code, "title": pr.title} if pr else None,
        "lines": [{"id": line.id, "line_no": line.line_no, "description": line.description,
                   "quantity": str(line.quantity), "uom": line.uom,
                   "target_price": str(line.target_price or "")} for line in rfq_lines],
        "suppliers": [{
            "invitation_id": i.id, "supplier_id": i.supplier_id,
            "name": suppliers[i.supplier_id].name if i.supplier_id in suppliers else "",
            "name_ar": suppliers[i.supplier_id].name_ar if i.supplier_id in suppliers else "",
            "channel": i.channel, "status": i.status, "sent_at": i.sent_at,
            "reminder_count": i.reminder_count,
            "token": i.token,
        } for i in invitations],
        "offers": [{"id": o.id, "supplier_id": o.supplier_id, "total_base": str(o.total_base),
                    "tco_total": str(o.tco_total), "currency": o.currency, "status": o.status,
                    "weighted_score": float(o.weighted_score or 0), "lead_time_days": o.lead_time_days,
                    "is_low_confidence": o.is_low_confidence, "extraction_id": o.extraction_id}
                   for o in offers],
        "messages": [{"id": m.id, "direction": m.direction, "channel": m.channel, "status": m.status,
                      "body": m.body[:4000], "author_type": m.author_type, "created_at": m.created_at}
                     for m in messages],
        "policy": {
            "target_price": str(policy.target_price), "walkaway_price": str(policy.walkaway_price),
            "max_rounds": policy.max_rounds, "allowed_concessions": policy.allowed_concessions,
            "mode": policy.mode, "value_threshold": str(policy.value_threshold), "tone": policy.tone,
            "version": policy.version,
        } if policy else None,
    }


class DispatchIn(BaseModel):
    channels: dict[str, str] = Field(default_factory=dict)


@router.post("/rfqs/{rfq_id}/dispatch", summary="Send the RFQ to invited suppliers (email/WhatsApp/web link)")
async def dispatch(rfq_id: str, payload: DispatchIn | None = Body(default=None),
                   ctx: Context = Depends(require("rfq:send"))):
    rfq = await _tenant_rfq(ctx, rfq_id)
    if rfq.status in {"closed", "cancelled"}:
        raise Conflict("This RFQ is closed.")
    channels = payload.channels if payload else None
    result = await dispatch_rfq(ctx.session, ctx.tenant, rfq, channels=channels, actor_id=ctx.user_id)
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="rfq.dispatched", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="rfq", entity_id=rfq.id,
                after={"sent": result["sent"], "results": result["results"]}, ip=ctx.ip,
                request_id=ctx.request_id)
    await ctx.session.commit()
    return result


@router.get("/rfqs/{rfq_id}/preview", summary="Template preview (AR/EN) before sending")
async def preview(rfq_id: str, ctx: Context = Depends(require("rfq:read")), locale: str = "ar"):
    rfq = await _tenant_rfq(ctx, rfq_id)
    rfq_lines = list((await ctx.session.execute(
        select(RFQLine).where(RFQLine.rfq_id == rfq.id).order_by(RFQLine.line_no)
    )).scalars().all())
    template = await pick_template(ctx.session, ctx.tenant, "rfq_invitation", "email", locale)
    context = {
        "rfq_code": rfq.code, "title": rfq.title, "supplier_name": "{{supplier_name}}",
        "tenant_name": ctx.tenant.name, "buyer_name": ctx.user.full_name,
        "quantity_summary": "; ".join(f"{line.quantity} {line.uom} {line.description}" for line in rfq_lines),
        "deadline": rfq.deadline.strftime("%Y-%m-%d") if rfq.deadline else "",
        "incoterm": rfq.incoterm, "delivery_location": rfq.delivery_location or "Buyer premises",
        "payment_terms": rfq.payment_terms, "web_form_url": "{{web_form_url}}",
    }
    return {"locale": locale, "subject": render_template(template.subject if template else "", context),
            "body": render_template(template.body if template else "", context),
            "template_id": template.id if template else None}


@router.post("/rfqs/{rfq_id}/reminders", summary="Run the reminder cadence for this RFQ")
async def reminders(rfq_id: str, ctx: Context = Depends(require("rfq:send"))):
    await _tenant_rfq(ctx, rfq_id)
    result = await send_reminders(ctx.session, ctx.tenant)
    await ctx.session.commit()
    return result


@router.post("/rfqs/{rfq_id}/close", summary="Close an RFQ")
async def close(rfq_id: str, ctx: Context = Depends(require("rfq:cancel"))):
    rfq = await _tenant_rfq(ctx, rfq_id)
    await close_rfq(ctx.session, rfq, ctx.user_id, "manual")
    await ctx.session.commit()
    return {"ok": True, "status": rfq.status}


# ------------------------------------------------------------------ templates
@router.get("/templates", summary="Message templates (per channel and locale)")
async def templates(ctx: Context = Depends(require("rfq:read")), channel: str | None = None):
    query = select(MessageTemplate).where(MessageTemplate.tenant_id == ctx.tenant_id)
    if channel:
        query = query.where(MessageTemplate.channel == channel)
    rows = list((await ctx.session.execute(query.order_by(MessageTemplate.code))).scalars().all())
    return {"items": [{"id": t.id, "code": t.code, "channel": t.channel, "locale": t.locale,
                       "name": t.name, "subject": t.subject, "body": t.body,
                       "whatsapp_template_name": t.whatsapp_template_name, "version": t.version,
                       "is_active": t.is_active} for t in rows]}


class TemplateIn(BaseModel):
    code: str
    channel: str
    locale: str = "ar"
    name: str
    subject: str = ""
    body: str
    whatsapp_template_name: str = ""


@router.post("/templates", status_code=201, summary="Create or version a template")
async def create_template(payload: TemplateIn, ctx: Context = Depends(require("admin:templates"))):
    previous = (await ctx.session.execute(
        select(MessageTemplate).where(MessageTemplate.tenant_id == ctx.tenant_id,
                                      MessageTemplate.code == payload.code,
                                      MessageTemplate.channel == payload.channel,
                                      MessageTemplate.locale == payload.locale)
    )).scalars().all()
    version = max((t.version for t in previous), default=0) + 1
    for row in previous:
        row.is_active = False
    template = MessageTemplate(tenant_id=ctx.tenant_id, version=version, **payload.model_dump())
    ctx.session.add(template)
    await ctx.session.flush()
    await ctx.session.commit()
    return {"id": template.id, "version": version}


# ------------------------------------------------------------------ messages & simulated replies
@router.get("/rfqs/{rfq_id}/messages", summary="Thread timeline for an RFQ (all suppliers)")
async def rfq_messages(rfq_id: str, ctx: Context = Depends(require("rfq:read")),
                       supplier_id: str | None = None):
    await _tenant_rfq(ctx, rfq_id)
    query = select(ChannelMessage).where(ChannelMessage.rfq_id == rfq_id)
    if supplier_id:
        query = query.where(ChannelMessage.supplier_id == supplier_id)
    rows = list((await ctx.session.execute(query.order_by(ChannelMessage.created_at))).scalars().all())
    return {"items": [{
        "id": m.id, "supplier_id": m.supplier_id, "direction": m.direction, "channel": m.channel,
        "author_type": m.author_type, "status": m.status, "subject": m.subject, "body": m.body,
        "body_ar": m.body_ar, "created_at": m.created_at,
    } for m in rows]}


class MessageIn(BaseModel):
    supplier_id: str
    subject: str
    body: str
    channel: str = Field("email", pattern="^(email|whatsapp|web_form|phone)$")


@router.post("/rfqs/{rfq_id}/messages", summary="Send a manual message to a supplier")
async def post_message(rfq_id: str, payload: MessageIn, ctx: Context = Depends(require("negotiation:send"))):
    rfq = await _tenant_rfq(ctx, rfq_id)
    supplier = (await ctx.session.execute(
        select(Supplier).where(Supplier.id == payload.supplier_id, Supplier.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not supplier:
        raise NotFound("Supplier not found.")
    result = await send_message(ctx.session, ctx.tenant, rfq=rfq, supplier=supplier,
                                subject=payload.subject, body=payload.body, channel=payload.channel,
                                author_type="user", author_id=ctx.user_id)
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="message.sent", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="rfq", entity_id=rfq.id,
                after={"supplier_id": supplier.id, "ok": result.get("ok"), "channel": payload.channel},
                ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return result


def _sample_pdf(supplier_name: str, price: str, lead: int, currency: str) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(40, 800, f"{supplier_name} - Quotation")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(40, 775, "Dear Procurement team,")
    pdf.drawString(40, 755, f"Unit price: {price} {currency} per ton")
    pdf.drawString(40, 740, f"Lead time: {lead} days")
    pdf.drawString(40, 725, "Validity: 14 days")
    pdf.drawString(40, 710, "Payment terms: Net 30")
    pdf.drawString(40, 695, "Incoterm: DAP Cairo")
    pdf.drawString(40, 670, "Quantity: 500 ton rebar 12mm")
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _sample_excel(supplier_name: str, price: str, currency: str) -> bytes:
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append([f"{supplier_name} quotation", "", "", ""])
    sheet.append(["Description", "Quantity", "Unit", "Unit price"])
    sheet.append(["Rebar 12mm B420D", 500, "ton", float(price)])
    sheet.append(["", "", "Currency", currency])
    sheet.append(["", "", "Lead time", "18 days"])
    sheet.append(["", "", "Validity", "21 days"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


class SimulateReplyIn(BaseModel):
    supplier_id: str
    kind: str = Field("text", pattern="^(text|pdf|excel|whatsapp|voice_note)$")
    unit_price: str | None = None
    lead_time_days: int = 21


@router.post("/rfqs/{rfq_id}/simulate-reply", summary="Inject a supplier reply through the real parsing pipeline")
async def simulate_reply(rfq_id: str, payload: SimulateReplyIn, ctx: Context = Depends(require("offer:verify"))):
    """Uses the configured adapters (Fake by default). Identical code path to a real webhook."""
    rfq = await _tenant_rfq(ctx, rfq_id)
    supplier = (await ctx.session.execute(
        select(Supplier).where(Supplier.id == payload.supplier_id, Supplier.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not supplier:
        raise NotFound("Supplier not found.")
    base_price = Decimal(payload.unit_price) if payload.unit_price else Decimal("21500")
    attachments: list[tuple[str, bytes]] = []
    if payload.kind == "pdf":
        attachments.append(("quotation.pdf", _sample_pdf(supplier.name, str(base_price), payload.lead_time_days,
                                                         rfq.currency)))
        body = f"Please find our quotation attached.\nSupplier: {supplier.name}\nRFQ: {rfq.code}"
        channel = "email"
    elif payload.kind == "excel":
        attachments.append(("quotation.xlsx", _sample_excel(supplier.name, str(base_price), rfq.currency)))
        body = f"Attached our price list.\nRFQ: {rfq.code}"
        channel = "email"
    elif payload.kind == "whatsapp":
        body = (f"أهلاً، بخصوص طلب عرض السعر {rfq.code}: سعر الطن {base_price} {rfq.currency}، "
                f"التسليم خلال {payload.lead_time_days} يوم، العرض صالح 14 يوم، الدفع Net 30.")
        channel = "whatsapp"
    else:
        body = (f"Quotation for {rfq.code}\nUnit price: {base_price} {rfq.currency} per ton\n"
                f"Lead time: {payload.lead_time_days} days\nValidity: 14 days\nPayment terms: Net 30\n"
                "Incoterm: DAP")
        channel = "email"
    # Reply as the supplier's real contact address so the inbound matcher behaves exactly as it
    # does for a live email/webhook (no special cases for simulated traffic).
    from app.services.suppliers import contact_pii, primary_contact

    contact = await primary_contact(ctx.session, ctx.tenant, supplier.id)
    sender = ""
    if contact:
        sender, _phone = await contact_pii(ctx.session, ctx.tenant, contact)
    inbound = InboundMessage(channel=channel, external_id=f"sim-{supplier.id[:8]}-{payload.kind}",
                             sender=sender or f"sales@{supplier.domain or 'supplier.example'}",
                             subject=f"RFQ {rfq.code}", body=body)
    result = await ingest_inbound(ctx.session, ctx.tenant, inbound, attachment_payloads=attachments)
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="reply.simulated", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="rfq", entity_id=rfq.id,
                after={"supplier_id": supplier.id, "kind": payload.kind, "matched": result.get("matched")},
                ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return result


# ------------------------------------------------------------------ extractions / verification
@router.get("/extractions", summary="Verification queue (low-confidence extractions first)")
async def list_extractions(ctx: Context = Depends(require("offer:read")), status: str | None = None,
                           rfq_id: str | None = None):
    query = select(ExtractionResult).where(ExtractionResult.tenant_id == ctx.tenant_id)
    if status:
        query = query.where(ExtractionResult.status == status)
    if rfq_id:
        query = query.where(ExtractionResult.rfq_id == rfq_id)
    rows = list((await ctx.session.execute(
        query.order_by(ExtractionResult.needs_review.desc(), ExtractionResult.created_at.desc()).limit(100)
    )).scalars().all())
    out = []
    for row in rows:
        supplier = (await ctx.session.execute(select(Supplier).where(Supplier.id == row.supplier_id))).scalar_one_or_none()
        out.append({"id": row.id, "rfq_id": row.rfq_id, "supplier_id": row.supplier_id,
                    "supplier_name": supplier.name if supplier else "", "status": row.status,
                    "confidence": float(row.confidence or 0), "needs_review": row.needs_review,
                    "issues": row.issues, "source_kind": row.source_kind,
                    # The model that produced the draft is part of the audit trail: a reviewer must be
                    # able to see whether a real provider or the deterministic fallback read the offer.
                    "model": row.model, "offer_id": row.offer_id, "created_at": row.created_at})
    return {"items": out}


@router.get("/extractions/{extraction_id}", summary="Extraction detail with per-field provenance")
async def get_extraction(extraction_id: str, ctx: Context = Depends(require("offer:read"))):
    extraction = (await ctx.session.execute(
        select(ExtractionResult).where(ExtractionResult.id == extraction_id,
                                       ExtractionResult.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not extraction:
        raise NotFound("Extraction not found.")
    fields = list((await ctx.session.execute(
        select(ExtractedField).where(ExtractedField.extraction_id == extraction.id)
    )).scalars().all())
    supplier = (await ctx.session.execute(select(Supplier).where(Supplier.id == extraction.supplier_id))).scalar_one_or_none()
    offer = (await ctx.session.execute(select(Offer).where(Offer.id == extraction.offer_id))).scalar_one_or_none() if extraction.offer_id else None
    return {
        "id": extraction.id, "rfq_id": extraction.rfq_id, "supplier_id": extraction.supplier_id,
        "supplier_name": supplier.name if supplier else "", "status": extraction.status,
        "confidence": float(extraction.confidence or 0), "needs_review": extraction.needs_review,
        "issues": extraction.issues, "raw_text": extraction.raw_text, "model": extraction.model,
        "source_kind": extraction.source_kind,
        "fields": [{"id": f.id, "name": f.field_name, "label": f.label, "value": f.value,
                    "currency": f.currency, "confidence": float(f.confidence or 0),
                    "source_span": f.source_span, "page": f.page,
                    "low_confidence": f.is_low_confidence, "verified_by": f.verified_by,
                    "verified_at": f.verified_at} for f in fields],
        "offer": {"id": offer.id, "subtotal": str(offer.subtotal), "currency": offer.currency,
                  "lead_time_days": offer.lead_time_days, "status": offer.status} if offer else None,
    }


class VerifyFieldIn(BaseModel):
    value: str | None = None


@router.post("/extractions/{extraction_id}/fields/{field_id}/verify", summary="One-click field verification")
async def verify_extraction_field(extraction_id: str, field_id: str, payload: VerifyFieldIn,
                                  ctx: Context = Depends(require("offer:verify"))):
    field = (await ctx.session.execute(
        select(ExtractedField).where(ExtractedField.id == field_id,
                                     ExtractedField.extraction_id == extraction_id,
                                     ExtractedField.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not field:
        raise NotFound("Field not found.")
    await verify_field(ctx.session, ctx.tenant, field, value=payload.value, user_id=ctx.user_id)
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="extraction.field_verified", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="extracted_field", entity_id=field.id,
                after={"field": field.field_name, "value": field.value}, ip=ctx.ip,
                request_id=ctx.request_id)
    await ctx.session.commit()
    return {"ok": True, "value": field.value}


@router.post("/extractions/{extraction_id}/verify", summary="Verify the whole extraction and unlock comparison")
async def verify_extraction(extraction_id: str, ctx: Context = Depends(require("offer:verify"))):
    extraction = (await ctx.session.execute(
        select(ExtractionResult).where(ExtractionResult.id == extraction_id,
                                       ExtractionResult.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not extraction:
        raise NotFound("Extraction not found.")
    await mark_extraction_verified(ctx.session, ctx.tenant, extraction, user_id=ctx.user_id)
    rfq = (await ctx.session.execute(select(RFQ).where(RFQ.id == extraction.rfq_id))).scalar_one()
    from app.services.suppliers import rate_from_outcomes

    supplier = (await ctx.session.execute(select(Supplier).where(Supplier.id == extraction.supplier_id))).scalar_one()
    await rate_from_outcomes(ctx.session, ctx.tenant, supplier, responded=True, lead_time_days=None,
                             price_index=None)
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="extraction.verified", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="extraction_result", entity_id=extraction.id,
                ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    await notify(ctx.session, tenant_id=ctx.tenant_id, user_id=rfq.buyer_id, kind="offer_verified",
                 title=f"Offer verified for {rfq.code}", title_ar=f"تم التحقق من عرض {rfq.code}",
                 link=f"/rfqs/{rfq.id}")
    await ctx.session.commit()
    return {"ok": True, "status": extraction.status}


@router.post("/extractions/{extraction_id}/request-missing-info", summary="Ask the supplier for missing data")
async def request_missing(extraction_id: str, ctx: Context = Depends(require("negotiation:send"))):
    extraction = (await ctx.session.execute(
        select(ExtractionResult).where(ExtractionResult.id == extraction_id,
                                       ExtractionResult.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not extraction:
        raise NotFound("Extraction not found.")
    rfq = (await ctx.session.execute(select(RFQ).where(RFQ.id == extraction.rfq_id))).scalar_one()
    supplier = (await ctx.session.execute(select(Supplier).where(Supplier.id == extraction.supplier_id))).scalar_one()
    result = await request_missing_info(ctx.session, ctx.tenant, rfq, supplier, extraction)
    await ctx.session.commit()
    return result


@router.get("/inbound/{channel}", include_in_schema=False, summary="Meta webhook verification handshake")
async def inbound_verify(channel: str, request: Request):
    """Meta calls this once (GET) to verify the callback; we echo `hub.challenge` on a token match."""
    from app.core.config import settings

    params = request.query_params
    if params.get("hub.mode") == "subscribe" and \
            params.get("hub.verify_token") == settings.whatsapp_verify_token:
        return Response(content=params.get("hub.challenge", ""), media_type="text/plain")
    return Response(status_code=403, content="verification failed")


@router.post("/inbound/{channel}", summary="Inbound webhook (email/WhatsApp) with signature verification")
async def inbound(channel: str, request: Request):
    """Provider webhook. In production the signature is verified before any parsing.

    WhatsApp goes through the phase-3 channel service, which handles delivery receipts, consent
    keywords and media downloads *before* the shared extraction pipeline sees the text.
    """
    from app.adapters.base import registry
    from app.core.config import settings
    from app.db.session import SessionLocal
    from app.services import whatsapp_channel

    body = await request.body()
    provider = registry.resolve("whatsapp") if channel == "whatsapp" else (
        registry.resolve("sms") if channel == "sms" else None)
    form_payload: dict[str, str] | None = None
    # The Twilio check must follow the provider selected *for this channel*: WhatsApp can run on the
    # Meta Cloud API while SMS runs on Twilio, and vice versa. Reading WHATSAPP_PROVIDER for an SMS
    # callback would skip signature verification on a public endpoint.
    twilio_channel = ((channel == "whatsapp" and settings.whatsapp_provider == "twilio")
                      or (channel == "sms" and settings.sms_provider == "twilio"))
    if channel == "whatsapp" and settings.whatsapp_provider == "cloud_api":
        signature = request.headers.get("x-hub-signature-256", "")
        if not provider.verify_webhook(body, signature):
            return Response(status_code=401, content='{"error":"invalid signature"}')
    elif twilio_channel:
        # Twilio posts application/x-www-form-urlencoded with an X-Twilio-Signature header computed
        # over the full public URL plus the sorted POST parameters.
        from urllib.parse import parse_qs

        from app.core.config import settings as _settings

        signature = request.headers.get("x-twilio-signature", "")
        callback_url = _settings.twilio_webhook_url or str(request.url)
        if not provider.verify_webhook(body, signature, callback_url):
            return Response(status_code=401, content='{"error":"invalid signature"}')
        form_payload = {key: values[-1] for key, values in
                        parse_qs(body.decode(), keep_blank_values=True).items()}
    async with SessionLocal() as session:
        tenant = (await session.execute(
            select(Tenant).where(Tenant.slug == request.headers.get("x-tenant-slug", "nile"))
        )).scalar_one_or_none()
        if not tenant:
            raise NotFound("Tenant not found.")
        payload = form_payload if form_payload is not None else await request.json()
        if channel == "whatsapp":
            handled = await whatsapp_channel.handle_webhook(session, tenant, payload)
            await session.commit()
            return {"processed": handled["processed"], "statuses": handled["statuses"],
                    "results": handled["messages"]}
        if channel == "sms":
            from app.services.extraction import ingest_inbound as ingest_sms

            inbound_messages = provider.parse_webhook(payload)
            sms_results = [await ingest_sms(session, tenant, message) for message in inbound_messages]
            await session.commit()
            return {"processed": len(sms_results), "results": sms_results}
        messages = [InboundMessage(channel="email", external_id=payload.get("message_id", ""),
                                   sender=payload.get("from", ""), subject=payload.get("subject", ""),
                                   body=payload.get("body", ""))]
        results = [await ingest_inbound(session, tenant, message) for message in messages]
        await session.commit()
    return {"processed": len(results), "results": results}


# ------------------------------------------------------------------ comparison & recommendation
@router.get("/rfqs/{rfq_id}/comparison", summary="Offer comparison matrix with TCO and weighted scores")
async def comparison_matrix(rfq_id: str, ctx: Context = Depends(require("comparison:read"))):
    rfq = await _tenant_rfq(ctx, rfq_id)
    return await comparison.build_matrix(ctx.session, ctx.tenant, rfq)


@router.put("/rfqs/{rfq_id}/weights", summary="Set comparison weights for this RFQ")
async def set_weights(rfq_id: str, weights: dict[str, float], ctx: Context = Depends(require("rfq:update"))):
    rfq = await _tenant_rfq(ctx, rfq_id)
    if abs(sum(weights.values()) - 1.0) > 0.05:
        raise ValidationFailed("Weights must sum to 1.0 (±0.05).")
    await comparison.update_weights(ctx.session, rfq, weights)
    await ctx.session.commit()
    return {"weights": rfq.scoring_weights}


@router.post("/rfqs/{rfq_id}/recommend", summary="Top-3 recommendation with traceable justification")
async def recommend(rfq_id: str, ctx: Context = Depends(require("comparison:read"))):
    rfq = await _tenant_rfq(ctx, rfq_id)
    result = await comparison.recommend(ctx.session, ctx.tenant, rfq, actor_id=ctx.user_id)
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="comparison.recommended", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="rfq", entity_id=rfq.id,
                after={"recommended": len(result.get("recommended", []))}, ip=ctx.ip,
                request_id=ctx.request_id)
    await ctx.session.commit()
    return result


@router.get("/benchmark", summary="Price history and anonymised benchmark for an item")
async def benchmark(ctx: Context = Depends(require("offer:read")), item_key: str = Query(...)):
    return await comparison.benchmark_for(ctx.session, ctx.tenant_id, item_key)


# ------------------------------------------------------------------ negotiation
class PolicyIn(BaseModel):
    supplier_id: str | None = None
    target_price: str
    walkaway_price: str
    max_rounds: int = Field(3, ge=1, le=10)
    allowed_concessions: list[str] = Field(default_factory=lambda: ["nothing"])
    tone: str = "professional"
    mode: str = Field("draft_only", pattern="^(draft_only|semi_auto|auto)$")
    value_threshold: str = "0"
    currency: str = "EGP"
    notes: str = ""


@router.put("/rfqs/{rfq_id}/policy", summary="Create a versioned negotiation policy")
async def put_policy(rfq_id: str, payload: PolicyIn, ctx: Context = Depends(require("negotiation:policy"))):
    rfq = await _tenant_rfq(ctx, rfq_id)
    target = numbers.parse_decimal(payload.target_price)
    walkaway = numbers.parse_decimal(payload.walkaway_price)
    if not target or not walkaway:
        raise ValidationFailed("target_price and walkaway_price must be numeric.")
    if walkaway < target:
        raise ValidationFailed("walkaway_price must be greater than or equal to target_price.")
    if payload.mode in {"semi_auto", "auto"} and "negotiation:auto" not in ctx.permissions:
        raise ValidationFailed("Your role cannot enable semi-auto or auto negotiation.")
    policy = await negotiation.upsert_policy(
        ctx.session, ctx.tenant, rfq=rfq, supplier_id=payload.supplier_id, target_price=target,
        walkaway_price=walkaway, max_rounds=payload.max_rounds,
        allowed_concessions=payload.allowed_concessions, tone=payload.tone, mode=payload.mode,
        value_threshold=numbers.parse_decimal(payload.value_threshold) or Decimal("0"),
        currency=payload.currency, notes=payload.notes, created_by=ctx.user_id,
    )
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="negotiation.policy_set", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="negotiation_policy", entity_id=policy.id,
                after=payload.model_dump(), ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return {"id": policy.id, "version": policy.version, "mode": policy.mode}


@router.get("/rfqs/{rfq_id}/policy", summary="Active negotiation policy")
async def get_policy(rfq_id: str, ctx: Context = Depends(require("negotiation:read")),
                     supplier_id: str | None = None):
    rfq = await _tenant_rfq(ctx, rfq_id)
    policy = await negotiation.policy_for(ctx.session, ctx.tenant, rfq, supplier_id)
    if not policy:
        return {"policy": None}
    return {"policy": {
        "id": policy.id, "target_price": str(policy.target_price), "walkaway_price": str(policy.walkaway_price),
        "max_rounds": policy.max_rounds, "allowed_concessions": policy.allowed_concessions,
        "mode": policy.mode, "value_threshold": str(policy.value_threshold), "tone": policy.tone,
        "currency": policy.currency, "version": policy.version,
    }}


class DraftIn(BaseModel):
    supplier_id: str
    offer_id: str
    round_no: int = 1
    concession: str | None = None
    send: bool = False


@router.post("/rfqs/{rfq_id}/negotiate", summary="Draft (and optionally send) a policy-checked counter-offer")
async def negotiate(rfq_id: str, payload: DraftIn, ctx: Context = Depends(require("negotiation:draft"))):
    rfq = await _tenant_rfq(ctx, rfq_id)
    supplier = (await ctx.session.execute(
        select(Supplier).where(Supplier.id == payload.supplier_id, Supplier.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not supplier:
        raise NotFound("Supplier not found.")
    offer = (await ctx.session.execute(
        select(Offer).where(Offer.id == payload.offer_id, Offer.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not offer:
        raise NotFound("Offer not found.")
    policy = await negotiation.policy_for(ctx.session, ctx.tenant, rfq, payload.supplier_id)
    if not policy:
        raise Conflict("Define a negotiation policy before drafting counter-offers.")
    if payload.send and "negotiation:send" not in ctx.permissions:
        raise ValidationFailed("Your role cannot send negotiation messages.")
    result = await negotiation.draft_counter_offer(
        ctx.session, ctx.tenant, rfq=rfq, supplier=supplier, offer=offer, policy=policy,
        round_no=payload.round_no, concession=payload.concession, actor_id=ctx.user_id,
        send_requested=payload.send,
    )
    await mark_negotiating(ctx.session, rfq, ctx.user_id)
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="negotiation.drafted", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="negotiation_message", entity_id=result["message_id"],
                after={"decision": result["decision"], "status": result["status"],
                       "leaks": result["leak_violations"]}, ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return result


@router.post("/negotiations/{message_id}/send", summary="Send an approved draft (re-validated at send time)")
async def send_draft(message_id: str, ctx: Context = Depends(require("negotiation:send"))):
    message = (await ctx.session.execute(
        select(NegotiationMessage).where(NegotiationMessage.id == message_id,
                                         NegotiationMessage.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not message:
        raise NotFound("Negotiation message not found.")
    if message.status not in {"draft", "blocked"}:
        raise Conflict(f"Message is '{message.status}' and cannot be sent.")
    result = await negotiation.approve_and_send_draft(ctx.session, ctx.tenant, message, ctx.user_id)
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="negotiation.sent", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="negotiation_message", entity_id=message.id,
                after={"ok": result.get("ok")}, ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return result


@router.get("/rfqs/{rfq_id}/suppliers/{supplier_id}/timeline", summary="Negotiation timeline + reasoning")
async def timeline(rfq_id: str, supplier_id: str, ctx: Context = Depends(require("negotiation:read"))):
    await _tenant_rfq(ctx, rfq_id)
    return await negotiation.timeline(ctx.session, ctx.tenant_id, rfq_id, supplier_id)


# ------------------------------------------------------------------ approvals
@router.get("/approvals", summary="Approval inbox")
async def approvals(ctx: Context = Depends(require("approval:read")), status: str | None = None):
    query = select(Approval).where(Approval.tenant_id == ctx.tenant_id)
    if status:
        query = query.where(Approval.status == status)
    rows = list((await ctx.session.execute(
        query.order_by(Approval.created_at.desc()).limit(100)
    )).scalars().all())
    out = []
    for row in rows:
        rfq = (await ctx.session.execute(select(RFQ).where(RFQ.id == row.rfq_id))).scalar_one_or_none()
        chain = list((await ctx.session.execute(
            select(Approval).where(Approval.tenant_id == ctx.tenant_id, Approval.rfq_id == row.rfq_id)
            .order_by(Approval.step_no)
        )).scalars().all())
        out.append({
            "id": row.id, "rfq_id": row.rfq_id, "rfq_code": rfq.code if rfq else "",
            "rfq_title": rfq.title if rfq else "", "step_no": row.step_no,
            "status": row.status, "approver_role": row.approver_role,
            "amount": str(row.amount), "currency": row.currency, "due_at": row.due_at,
            "created_at": row.created_at, "offer_id": row.offer_id,
            "can_decide": row.status == "pending" and (
                row.approver_role in (ctx.user.roles or []) or "admin" in (ctx.user.roles or [])
                or ctx.user.is_superadmin
            ),
            "chain": [{"step_no": a.step_no, "role": a.approver_role, "status": a.status,
                       "decided_at": a.decided_at} for a in chain],
        })
    return {"items": out}


class DecisionIn(BaseModel):
    decision: str = Field(pattern="^(approve|reject|request_revision|split_award)$")
    comment: str = ""
    split: list[dict] = Field(default_factory=list)


@router.post("/approvals/{approval_id}/decide", summary="Approve / reject / request revision / split award")
async def decide(approval_id: str, payload: DecisionIn, ctx: Context = Depends(require("approval:decide"))):
    approval = (await ctx.session.execute(
        select(Approval).where(Approval.id == approval_id, Approval.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not approval:
        raise NotFound("Approval not found.")
    if approval.status == "pending" and approval.approver_role not in (ctx.user.roles or []) \
            and "admin" not in (ctx.user.roles or []) and not ctx.user.is_superadmin:
        raise ValidationFailed(f"This step requires the {approval.approver_role} role.")
    if payload.decision == "split_award" and not payload.split:
        raise ValidationFailed("Split award requires at least one {offer_id, share} entry.")
    result = await approval_decide(ctx.session, ctx.tenant, approval, decision=payload.decision,
                                   comment=payload.comment, user=ctx.user,
                                   split=payload.split if payload.decision == "split_award" else None)
    if not result.get("ok"):
        # A second decision on an already-decided step must not look like success to the UI.
        raise ValidationFailed(result.get("error") or "This approval can no longer be decided.")
    await audit(ctx.session, tenant_id=ctx.tenant_id, action=f"approval.{payload.decision}",
                actor_id=ctx.user_id, actor_label=ctx.user.email, entity_type="approval",
                entity_id=approval.id, after={"result": result, "comment": payload.comment}, ip=ctx.ip,
                request_id=ctx.request_id)
    await ctx.session.commit()
    return result


class DelegateIn(BaseModel):
    to_user_id: str
    comment: str = ""


@router.post("/approvals/{approval_id}/delegate", summary="Delegate an approval")
async def delegate(approval_id: str, payload: DelegateIn, ctx: Context = Depends(require("approval:delegate"))):
    approval = (await ctx.session.execute(
        select(Approval).where(Approval.id == approval_id, Approval.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not approval:
        raise NotFound("Approval not found.")
    await approval_delegate(ctx.session, ctx.tenant, approval, payload.to_user_id, ctx.user, payload.comment)
    await ctx.session.commit()
    return {"ok": True}


class RequestApprovalIn(BaseModel):
    offer_ids: list[str] = Field(min_length=1)
    amount: str
    split: list[dict] = Field(default_factory=list)


@router.post("/rfqs/{rfq_id}/request-approval", summary="Start the multi-level approval chain")
async def start_approval(rfq_id: str, payload: RequestApprovalIn, ctx: Context = Depends(require("comparison:read"))):
    rfq = await _tenant_rfq(ctx, rfq_id)
    amount = numbers.parse_decimal(payload.amount)
    if not amount or amount <= 0:
        raise ValidationFailed("amount must be a positive number.")
    approvals_rows = await request_approval(ctx.session, ctx.tenant, rfq, offer_ids=payload.offer_ids,
                                            amount=amount, requested_by=ctx.user_id, split=payload.split)
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="approval.requested", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="rfq", entity_id=rfq.id,
                after={"amount": str(amount), "steps": len(approvals_rows)}, ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return {"approvals": [{"id": a.id, "step": a.step_no, "role": a.approver_role} for a in approvals_rows]}


@router.post("/approvals/escalate", summary="Run SLA escalation for overdue approvals")
async def escalate(ctx: Context = Depends(require("approval:decide"))):
    count = await escalate_overdue(ctx.session, ctx.tenant)
    await ctx.session.commit()
    return {"escalated": count}


# ------------------------------------------------------------------ voice
class VoiceCallIn(BaseModel):
    supplier_id: str
    language: str = "ar-EG"


@router.post("/rfqs/{rfq_id}/voice-calls", summary="Start an AI supplier call (disclosure + consent)")
async def start_voice_call(rfq_id: str, payload: VoiceCallIn, ctx: Context = Depends(require("rfq:send"))):
    rfq = await _tenant_rfq(ctx, rfq_id)
    supplier = (await ctx.session.execute(
        select(Supplier).where(Supplier.id == payload.supplier_id, Supplier.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not supplier:
        raise NotFound("Supplier not found.")
    result = await voice.start_call(ctx.session, ctx.tenant, rfq=rfq, supplier=supplier,
                                    language=payload.language, requested_by=ctx.user_id)
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="voice.call_started", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="rfq", entity_id=rfq.id,
                after={"supplier_id": supplier.id, "status": result.get("status"),
                       "consent": result.get("consent_given")}, ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return result


@router.get("/voice-calls/{call_id}", summary="Voice call console state")
async def get_call(call_id: str, ctx: Context = Depends(require("rfq:read"))):
    call = (await ctx.session.execute(
        select(VoiceCall).where(VoiceCall.id == call_id, VoiceCall.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not call:
        raise NotFound("Call not found.")
    return {"id": call.id, "status": call.status, "disclosure_played": call.disclosure_played,
            "consent_given": call.consent_given, "transcript": call.transcript,
            "recording": bool(call.recording_uri), "duration_seconds": call.duration_seconds,
            "summary": call.summary, "policy_snapshot": call.policy_snapshot,
            "extracted_offer_id": call.extracted_offer_id}


@router.get("/rfqs/{rfq_id}/manual-script", summary="Prepared script for switching a call to manual")
async def manual_script(rfq_id: str, ctx: Context = Depends(require("rfq:read")), supplier_id: str = Query(...)):
    rfq = await _tenant_rfq(ctx, rfq_id)
    supplier = (await ctx.session.execute(
        select(Supplier).where(Supplier.id == supplier_id, Supplier.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not supplier:
        raise NotFound("Supplier not found.")
    return await voice.manual_script(ctx.session, ctx.tenant, rfq, supplier)
