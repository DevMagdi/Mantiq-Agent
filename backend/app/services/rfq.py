"""RFQ lifecycle: generation from PR, templated multi-channel dispatch, reminders, status board."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import registry
from app.db.models.entities import (
    RFQ,
    MessageTemplate,
    PRLine,
    PurchaseRequisition,
    RFQLine,
    RFQSupplier,
    Supplier,
    Tenant,
    User,
)
from app.services import numbers
from app.services.common import meter, next_code, notify, quota_check
from app.services.messaging import (
    create_web_form_link,
    ensure_thread,
    opt_in_status,
    quantity_summary,
    render_template,
    send_message,
)
from app.services.suppliers import primary_contact

log = logging.getLogger("procureai.rfq")

STAGES = ["draft", "sent", "replied", "negotiating", "closed"]
TERMINAL = {"closed", "cancelled", "expired"}


def append_stage(rfq: RFQ, stage: str, actor: str = "system") -> None:
    history = list(rfq.stage_history or [])
    if history and history[-1].get("stage") == stage:
        return
    history.append({"stage": stage, "at": datetime.now(UTC).isoformat(), "actor": actor})
    rfq.stage_history = history
    rfq.status = stage


async def create_rfq_from_pr(
    session: AsyncSession,
    tenant: Tenant,
    pr: PurchaseRequisition,
    *,
    supplier_ids: list[str],
    buyer_id: str | None = None,
    deadline_days: int = 10,
    incoterm: str = "DAP",
    payment_terms: str = "Net 30",
    mode: str = "draft_only",
    channel: str = "email",
    title: str | None = None,
    scoring_weights: dict | None = None,
    deadline: datetime | None = None,
) -> RFQ:
    pr_lines = list((await session.execute(
        select(PRLine).where(PRLine.pr_id == pr.id).order_by(PRLine.line_no)
    )).scalars().all())
    rfq = RFQ(
        tenant_id=tenant.id,
        code=await next_code(session, RFQ, tenant.id, "RFQ"),
        title=title or pr.title,
        pr_id=pr.id,
        status="draft",
        currency=pr.currency,
        incoterm=incoterm,
        payment_terms=payment_terms,
        delivery_location=_delivery_location(pr),
        deadline=deadline or datetime.now(UTC) + timedelta(days=deadline_days),
        buyer_id=buyer_id,
        mode=mode,
        scoring_weights=scoring_weights or {},
        stage_history=[{"stage": "draft", "at": datetime.now(UTC).isoformat(), "actor": buyer_id or "system"}],
    )
    session.add(rfq)
    await session.flush()

    for line in pr_lines:
        session.add(RFQLine(
            tenant_id=tenant.id, rfq_id=rfq.id, line_no=line.line_no, description=line.description,
            quantity=line.quantity, uom=line.uom, target_price=line.target_price,
            # The taxonomy code drives category pricing and the comparison grouping downstream.
            taxonomy_code=line.taxonomy_code, specs=line.attributes or {}, pr_line_id=line.id,
        ))
    for supplier_id in supplier_ids:
        session.add(RFQSupplier(tenant_id=tenant.id, rfq_id=rfq.id, supplier_id=supplier_id,
                                channel=channel, status="invited"))
    if pr.status != "rfq_created":
        pr.status = "rfq_created"
    await session.flush()
    return rfq


def _delivery_location(pr: PurchaseRequisition) -> str:
    for line in (pr.notes or "").splitlines():
        if line.startswith("delivery_location:"):
            return line.split(":", 1)[1].strip()
    return ""


async def pick_template(session: AsyncSession, tenant: Tenant, code: str, channel: str, locale: str) -> MessageTemplate | None:
    for candidate_locale in ([locale, "ar", "en"] if locale != "en" else ["en", "ar"]):
        row = (await session.execute(
            select(MessageTemplate).where(MessageTemplate.tenant_id == tenant.id,
                                          MessageTemplate.code == code,
                                          MessageTemplate.channel == channel,
                                          MessageTemplate.locale == candidate_locale,
                                          MessageTemplate.is_active.is_(True))
            .order_by(MessageTemplate.version.desc())
        )).scalars().first()
        if row:
            return row
    return None


async def dispatch_rfq(session: AsyncSession, tenant: Tenant, rfq: RFQ, *,
                       channels: dict[str, str] | None = None,
                       actor_id: str | None = None) -> dict:
    """Send invitations. Per-supplier channel override via `channels` {supplier_id: channel}."""
    quota = await quota_check(session, tenant.id, "rfq")
    lines = list((await session.execute(
        select(RFQLine).where(RFQLine.rfq_id == rfq.id).order_by(RFQLine.line_no)
    )).scalars().all())
    invitations = list((await session.execute(
        select(RFQSupplier).where(RFQSupplier.rfq_id == rfq.id)
    )).scalars().all())
    buyer = (await session.execute(select(User).where(User.id == rfq.buyer_id))).scalar_one_or_none() if rfq.buyer_id else None
    results: list[dict] = []

    for invitation in invitations:
        supplier = (await session.execute(select(Supplier).where(Supplier.id == invitation.supplier_id))).scalar_one()
        channel = (channels or {}).get(supplier.id, invitation.channel or "email")
        template = await pick_template(session, tenant, "rfq_invitation", channel if channel != "web_form" else "email", "ar")
        contact = await primary_contact(session, tenant, supplier.id)
        link = await create_web_form_link(session, tenant, rfq, supplier.id)
        context = {
            "rfq_code": rfq.code,
            "title": rfq.title,
            "supplier_name": supplier.name,
            "supplier_name_ar": supplier.name_ar or supplier.name,
            "tenant_name": tenant.name,
            "tenant_name_ar": tenant.name,
            "buyer_name": (buyer.full_name if buyer else "Procurement team"),
            "quantity_summary": quantity_summary(lines),
            "deadline": rfq.deadline.strftime("%Y-%m-%d") if rfq.deadline else "",
            "incoterm": rfq.incoterm,
            "delivery_location": rfq.delivery_location or "Buyer premises",
            "payment_terms": rfq.payment_terms,
            "web_form_url": link,
            "locale_note": "يمكنكم الرد بالعربية أو الإنجليزية.",
        }
        subject = render_template(template.subject if template else f"RFQ {rfq.code} - {rfq.title}", context)
        body_ar = render_template(template.body if template else f"نطلب عرض سعر للطلب {rfq.code} عبر {link}", context)
        body_en = _english_body(context, rfq)

        if channel == "email":
            outcome = await send_message(session, tenant, rfq=rfq, supplier=supplier, subject=subject,
                                         body=f"{body_ar}\n\n---\n{body_en}", body_ar=body_ar, channel="email",
                                         author_type="user", author_id=actor_id)
        elif channel == "whatsapp":
            opted_in = await opt_in_status(session, tenant.id, supplier.id, "whatsapp")
            template_row = template
            if template_row and template_row.whatsapp_template_name and opted_in:
                provider = registry.resolve("whatsapp")
                _email, phone = ("", "")
                if contact:
                    from app.services.suppliers import contact_pii

                    _email, phone = await contact_pii(session, tenant, contact)
                thread = await ensure_thread(session, tenant, rfq, supplier, "whatsapp", subject)
                send_result = await provider.send_template(
                    to=phone, template=template_row.whatsapp_template_name,
                    variables=[supplier.name, rfq.title, rfq.deadline.strftime("%Y-%m-%d") if rfq.deadline else "",
                               link],
                )
                from app.db.models.entities import ChannelMessage

                session.add(ChannelMessage(
                    tenant_id=tenant.id, thread_id=thread.id, rfq_id=rfq.id, supplier_id=supplier.id,
                    direction="outbound", channel="whatsapp", body=body_ar, subject=subject,
                    status="sent" if send_result.ok else "failed", author_type="user", author_id=actor_id,
                    sent_at=datetime.now(UTC), provider_message_id=send_result.provider_message_id,
                    extra={"template": template_row.whatsapp_template_name},
                ))
                outcome = {"ok": send_result.ok, "error": send_result.error, "policy": {"action": "send"}}
                if send_result.ok:
                    invitation.sent_at = datetime.now(UTC)
                    await meter(session, tenant_id=tenant.id, kind="message", ref_type="channel_message")
                await session.flush()
            else:
                outcome = {"ok": False, "error": "WhatsApp requires an approved template and opt-in.",
                           "codes": ["BLOCK_NO_OPT_IN"], "policy": {"action": "block"}}
        else:  # web_form / manual phone script
            outcome = await send_message(session, tenant, rfq=rfq, supplier=supplier, subject=subject,
                                         body=link, body_ar=body_ar, channel=channel, author_type="user",
                                         author_id=actor_id)
            if channel == "web_form" and outcome.get("ok"):
                invitation.sent_at = invitation.sent_at or datetime.now(UTC)
        results.append({"supplier_id": supplier.id, "supplier": supplier.name, "channel": channel, **outcome})

    if any(r.get("ok") for r in results):
        rfq.sent_at = rfq.sent_at or datetime.now(UTC)
        append_stage(rfq, "sent", actor_id or "system")
        await meter(session, tenant_id=tenant.id, kind="rfq", ref_type="rfq", ref_id=rfq.id)
    await notify(
        session, tenant_id=tenant.id, user_id=rfq.buyer_id, kind="rfq_sent",
        title=f"RFQ {rfq.code} sent to {sum(1 for r in results if r.get('ok'))} suppliers",
        title_ar=f"تم إرسال طلب عرض السعر {rfq.code} إلى {sum(1 for r in results if r.get('ok'))} موردين",
        body="Responses will be parsed automatically as they arrive.", link=f"/rfqs/{rfq.id}",
    )
    await session.flush()
    return {"rfq_id": rfq.id, "results": results, "quota_warning": quota.get("warning", ""),
            "sent": sum(1 for r in results if r.get("ok"))}


def _english_body(context: dict, rfq: RFQ) -> str:
    return (
        f"Dear {context['supplier_name']},\n\n"
        f"{context['tenant_name']} invites you to quote for {rfq.title} ({rfq.code}).\n"
        f"Quantity: {context['quantity_summary']}\nDeadline: {context['deadline']}\n"
        f"Delivery: {rfq.incoterm} - {context['delivery_location']}\nPayment terms: {rfq.payment_terms}\n\n"
        f"Submit your offer here (no account needed): {context['web_form_url']}\n\n"
        f"Best regards,\n{context['buyer_name']}\n{context['tenant_name']}"
    )


async def send_reminders(session: AsyncSession, tenant: Tenant, *, now: datetime | None = None) -> dict:
    """Reminder cadence for non-responding suppliers; auto-close at the deadline (FR-RFQ-4)."""
    now = now or datetime.now(UTC)
    cutoff = now + timedelta(days=2)
    sent = 0
    closed = 0
    rfqs = list((await session.execute(
        select(RFQ).where(RFQ.tenant_id == tenant.id, RFQ.status.in_(["sent", "replied"]))
    )).scalars().all())
    for rfq in rfqs:
        if rfq.deadline and now > rfq.deadline:
            append_stage(rfq, "expired", "scheduler")
            await notify(session, tenant_id=tenant.id, user_id=rfq.buyer_id, kind="rfq_expired",
                         title=f"RFQ {rfq.code} closed at deadline",
                         title_ar=f"تم إغلاق طلب عرض السعر {rfq.code} عند الموعد النهائي",
                         link=f"/rfqs/{rfq.id}", severity="warning")
            closed += 1
            continue
        if not rfq.deadline or rfq.deadline > cutoff:
            continue
        invitations = list((await session.execute(
            select(RFQSupplier).where(RFQSupplier.rfq_id == rfq.id,
                                      RFQSupplier.status.in_(["invited", "sent", "reminded"]))
        )).scalars().all())
        from app.services import channels as channel_service

        rfq_lines = list((await session.execute(
            select(RFQLine).where(RFQLine.rfq_id == rfq.id).order_by(RFQLine.line_no)
        )).scalars().all())
        for invitation in invitations:
            if invitation.reminder_count >= 2:
                continue
            last = invitation.last_reminder_at or invitation.sent_at
            if last and (now - last) < timedelta(days=2):
                continue
            supplier = (await session.execute(select(Supplier).where(Supplier.id == invitation.supplier_id))).scalar_one()
            # A reminder follows the channel the supplier actually answers on — WhatsApp first.
            resolved = await channel_service.resolve(session, tenant, supplier, invitation=invitation,
                                                     rfq=rfq)
            channel = resolved["channel"] if resolved["available"] else "email"
            template = await pick_template(session, tenant, "reminder",
                                           channel if channel != "web_form" else "email",
                                           supplier.preferred_language or "ar")
            link = await create_web_form_link(session, tenant, rfq, supplier.id)
            context = {"supplier_name": supplier.name, "supplier_name_ar": supplier.name_ar or supplier.name,
                       "rfq_code": rfq.code, "title": rfq.title,
                       "quantity_summary": quantity_summary(rfq_lines),
                       "deadline": rfq.deadline.strftime("%Y-%m-%d") if rfq.deadline else "",
                       "web_form_url": link, "tenant_name": tenant.name,
                       "payment_terms": rfq.payment_terms,
                       "delivery_location": rfq.delivery_location or "Buyer premises",
                       "incoterm": rfq.incoterm}
            subject = render_template(template.subject if template else f"Reminder: RFQ {rfq.code}", context)
            body = render_template(template.body if template else f"Reminder for {rfq.code}: {link}", context)
            outcome = await send_message(session, tenant, rfq=rfq, supplier=supplier, subject=subject,
                                         body=body, body_ar=body, channel=channel, author_type="system",
                                         template_code="reminder" if channel == "whatsapp" else "",
                                         locale=supplier.preferred_language or "ar")
            if outcome.get("ok"):
                invitation.reminder_count += 1
                invitation.last_reminder_at = now
                invitation.status = "reminded"
                invitation.channel = channel
                invitation.comm_status = "sent"
                sent += 1
    await session.flush()
    return {"reminders_sent": sent, "rfqs_closed": closed}


async def close_rfq(session: AsyncSession, rfq: RFQ, actor: str, reason: str = "awarded") -> RFQ:
    rfq.closed_at = datetime.now(UTC)
    rfq.notes = f"{rfq.notes or ''}\nclosed:{reason}:{actor}".strip()
    append_stage(rfq, "closed", actor)
    await session.flush()
    return rfq


async def mark_negotiating(session: AsyncSession, rfq: RFQ, actor: str = "agent") -> None:
    if rfq.status not in TERMINAL:
        append_stage(rfq, "negotiating", actor)
        await session.flush()


async def rfq_overview(session: AsyncSession, tenant: Tenant) -> dict:
    """Board columns with counts and SLA/deadline chips for the RFQ kanban."""
    rows = list((await session.execute(select(RFQ).where(RFQ.tenant_id == tenant.id))).scalars().all())
    columns = {stage: [] for stage in STAGES + ["cancelled", "expired"]}
    for rfq in rows:
        columns.setdefault(rfq.status, []).append({
            "id": rfq.id, "code": rfq.code, "title": rfq.title, "status": rfq.status,
            "deadline": rfq.deadline.isoformat() if rfq.deadline else None,
            "buyer_id": rfq.buyer_id, "mode": rfq.mode,
            "days_left": numbers.days_between(datetime.now(UTC), rfq.deadline) if rfq.deadline else None,
        })
    return {"columns": {k: v for k, v in columns.items() if v or k in STAGES},
            "counts": {k: len(v) for k, v in columns.items()}}
