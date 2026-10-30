"""Internal voice/text assistant: answers questions inside the user's own permissions.

Two hard rules shape the design:

1. **RBAC first, data second.** The intent is resolved before any query runs, and the intent declares
   the permission it needs. A user without `ledger:read` asking about a balance is told the capability
   is not available to them — the answer never contains the number it was asked for.
2. **Read-only, always.** The assistant can look things up and it can *draft* a purchase request
   (through the normal intake service, which leaves the requisition in `draft` and never submits it).
   There is no intent that approves, awards, signs, pays, posts or changes a specification: those
   words are classified into a `forbidden` intent that explains where the human must go instead.

Every turn is persisted (`assistant_session` + `assistant_turn`) and audited, including the blocked
ones, because "what did the assistant tell the warehouse on Tuesday" is an audit question.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.db.models.entities import (
    RFQ,
    AssistantSession,
    AssistantTurn,
    Offer,
    PurchaseOrder,
    PurchaseRequisition,
    Supplier,
    Tenant,
    User,
)
from app.services import comparison, inbox, invoicing, numbers, p2p
from app.services.features import is_enabled
from app.services.intake import intake_free_text

DOC_CODE = re.compile(r"\b(PR|RFQ|PO)-\d{4}-\d{5}\b", re.IGNORECASE)

#: intent -> permission required (None = available to any signed-in user).
INTENT_PERMISSIONS: dict[str, str | None] = {
    "help": None,
    "document_status": "doc:read",
    "my_requests": "pr:read",
    "offer_summary": "offer:read",
    "recommendation": "comparison:read",
    "supplier_balance": "ledger:read",
    "supplier_communications": "inbox:read",
    "pr_create": "pr:create",
    "forbidden": None,
}

FORBIDDEN_PATTERNS = (
    "approve", "approval", "award", "sign", "accept the offer", "pay", "post the journal",
    "اعتمد", "اعتماد", "رسّي", "ترسية", "وقّع", "توقيع", "اقبل", "ادفع", "سدد", "رحّل القيد",
)
INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "my_requests": ("my requests", "my requisitions", "طلباتي", "طلبات شرائي"),
    "offer_summary": ("offers", "quotations", "quotes", "العروض", "عروض"),
    "recommendation": ("recommend", "best offer", "which supplier", "ترشح", "أفضل عرض", "انسب مورد",
                       "أنسب مورد"),
    "supplier_balance": ("balance", "outstanding", "owed", "رصيد", "مستحق", "مديونية"),
    "supplier_communications": ("conversation", "messages", "communication", "whatsapp", "محادثات",
                                "رسائل", "تواصل"),
    "pr_create": ("create a request", "new request", "need to buy", "أحتاج", "محتاج", "طلب شراء جديد",
                  "أنشئ طلب"),
    "document_status": ("status", "where is", "track", "حالة", "فين", "أين", "ايه حالة"),
}


#: Intents that make sense *about* a document code ("which supplier do you recommend for RFQ-…").
_DOC_SCOPED_INTENTS = ("recommendation", "offer_summary")


def classify(text: str) -> str:
    lowered = (text or "").lower()
    if any(pattern in lowered for pattern in FORBIDDEN_PATTERNS):
        return "forbidden"
    if DOC_CODE.search(text or ""):
        # A code alone means "where is it", but "recommend for RFQ-…" or "offers for RFQ-…" is a
        # question about the document, not a tracking question.
        for intent in _DOC_SCOPED_INTENTS:
            if any(keyword in lowered for keyword in INTENT_KEYWORDS[intent]):
                return intent
        return "document_status"
    for intent, keywords in INTENT_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return intent
    return "help"


def _cite(kind: str, entity_id: str, code: str, label: str, link: str) -> dict:
    return {"type": kind, "id": entity_id, "code": code, "label": label, "link": link}


def _ownership_ok(user: User, *, requested_by: str | None = None, buyer_id: str | None = None) -> bool:
    """A requester sees their own documents; buyers see what they own; managers see all."""
    roles = set(user.roles or [])
    if roles & {"admin", "procurement_manager", "auditor", "finance_approver", "controller"}:
        return True
    if requested_by and requested_by == user.id:
        return True
    return bool(buyer_id and buyer_id == user.id)


async def _document_status(session: AsyncSession, tenant: Tenant, user: User, code: str,
                           permissions: set[str]) -> dict:
    prefix = code.split("-")[0].upper()
    if prefix == "PR":
        row = (await session.execute(
            select(PurchaseRequisition).where(PurchaseRequisition.tenant_id == tenant.id,
                                              PurchaseRequisition.code == code.upper())
        )).scalar_one_or_none()
        if row is None or not _ownership_ok(user, requested_by=row.requested_by):
            return {"blocked_code": "ASSISTANT_NOT_FOUND", "answer_en": "I cannot find that request.",
                    "answer_ar": "لا أستطيع العثور على هذا الطلب.", "citations": []}
        return {
            "answer_en": f"{row.code} is {row.status.replace('_', ' ')}: {row.title}.",
            "answer_ar": f"{row.code} حالته {row.status}: {row.title}.",
            "data": {"code": row.code, "status": row.status, "title": row.title,
                     "total_estimate": str(numbers.money(row.total_estimate)), "currency": row.currency},
            "citations": [_cite("purchase_requisition", row.id, row.code, row.title,
                                f"/requests?focus={row.id}")],
        }
    if prefix == "RFQ":
        row = (await session.execute(
            select(RFQ).where(RFQ.tenant_id == tenant.id, RFQ.code == code.upper())
        )).scalar_one_or_none()
        if row is None or not _ownership_ok(user, buyer_id=row.buyer_id):
            return {"blocked_code": "ASSISTANT_NOT_FOUND", "answer_en": "I cannot find that RFQ.",
                    "answer_ar": "لا أستطيع العثور على طلب عرض السعر.", "citations": []}
        offers = len((await session.execute(
            select(Offer).where(Offer.rfq_id == row.id)
        )).scalars().all())
        return {
            "answer_en": f"{row.code} is {row.status}; {offers} offer(s) received, deadline "
                         f"{row.deadline.strftime('%Y-%m-%d') if row.deadline else 'not set'}.",
            "answer_ar": f"{row.code} حالته {row.status}؛ تم استلام {offers} عرض، الموعد النهائي "
                         f"{row.deadline.strftime('%Y-%m-%d') if row.deadline else 'غير محدد'}.",
            "data": {"code": row.code, "status": row.status, "offers": offers},
            "citations": [_cite("rfq", row.id, row.code, row.title, f"/rfqs/{row.id}")],
        }
    row = (await session.execute(
        select(PurchaseOrder).where(PurchaseOrder.tenant_id == tenant.id,
                                    PurchaseOrder.code == code.upper())
    )).scalar_one_or_none()
    # A purchase order has no buyer column of its own: the RFQ carries the buyer, the PO carries who
    # created it. Ownership therefore reads the creator.
    if row is None or not _ownership_ok(user, buyer_id=row.created_by):
        return {"blocked_code": "ASSISTANT_NOT_FOUND", "answer_en": "I cannot find that order.",
                "answer_ar": "لا أستطيع العثور على أمر الشراء.", "citations": []}
    lifecycle = await p2p.lifecycle(session, tenant.id, row)
    return {
        "answer_en": f"{row.code} is at stage {lifecycle['current_label_en']}. Next: "
                     f"{lifecycle['next_action_en']}",
        "answer_ar": f"{row.code} في مرحلة {lifecycle['current_label_ar']}. التالي: "
                     f"{lifecycle['next_action_ar']}",
        "data": {"code": row.code, "status": row.status, "stage": lifecycle["current"],
                 "stage_label": lifecycle["current_label_en"],
                 "next_action": lifecycle["next_action_en"]},
        "citations": [_cite("purchase_order", row.id, row.code, row.code,
                            f"/purchase-orders/{row.id}")],
    }


async def _my_requests(session: AsyncSession, tenant: Tenant, user: User) -> dict:
    rows = list((await session.execute(
        select(PurchaseRequisition).where(PurchaseRequisition.tenant_id == tenant.id,
                                          PurchaseRequisition.requested_by == user.id)
        .order_by(PurchaseRequisition.created_at.desc()).limit(5)
    )).scalars().all())
    if not rows:
        return {"answer_en": "You have no purchase requests yet.",
                "answer_ar": "لا توجد طلبات شراء باسمك بعد.", "citations": [], "data": {"items": []}}
    summary = "; ".join(f"{row.code} ({row.status})" for row in rows)
    return {
        "answer_en": f"Your latest requests: {summary}.",
        "answer_ar": f"أحدث طلباتك: {summary}.",
        "data": {"items": [{"code": r.code, "status": r.status, "title": r.title} for r in rows]},
        "citations": [_cite("purchase_requisition", r.id, r.code, r.title, f"/requests?focus={r.id}")
                      for r in rows],
    }


async def _offer_summary(session: AsyncSession, tenant: Tenant, text: str) -> dict:
    match = DOC_CODE.search(text.upper())
    query = select(RFQ).where(RFQ.tenant_id == tenant.id)
    if match and match.group(0).upper().startswith("RFQ"):
        query = query.where(RFQ.code == match.group(0).upper())
    else:
        query = query.where(RFQ.status.in_(["sent", "replied", "negotiating", "closed"]))
    rfq = (await session.execute(query.order_by(RFQ.created_at.desc()))).scalars().first()
    if rfq is None:
        return {"answer_en": "There is no open RFQ to summarise.",
                "answer_ar": "لا يوجد طلب عرض سعر مفتوح للتلخيص.", "citations": [], "data": {}}
    offers = list((await session.execute(
        select(Offer).where(Offer.rfq_id == rfq.id).order_by(Offer.tco_total)
    )).scalars().all())
    if not offers:
        return {"answer_en": f"{rfq.code} has no offers yet.",
                "answer_ar": f"{rfq.code} ليس به عروض بعد.",
                "citations": [_cite("rfq", rfq.id, rfq.code, rfq.title, f"/rfqs/{rfq.id}")],
                "data": {"rfq_code": rfq.code, "offers": 0}}
    best = offers[0]
    supplier = (await session.execute(
        select(Supplier).where(Supplier.id == best.supplier_id)
    )).scalar_one_or_none()
    return {
        "answer_en": f"{rfq.code} has {len(offers)} offer(s). The lowest total is "
                     f"{numbers.money(best.tco_total)} {rfq.currency} from "
                     f"{supplier.name if supplier else best.supplier_id}.",
        "answer_ar": f"{rfq.code} به {len(offers)} عرض. أقل إجمالي هو "
                     f"{numbers.money(best.tco_total)} {rfq.currency} من "
                     f"{supplier.name if supplier else best.supplier_id}.",
        "data": {"rfq_code": rfq.code, "offers": len(offers),
                 "lowest_total": str(numbers.money(best.tco_total)),
                 "supplier": supplier.name if supplier else ""},
        "citations": [_cite("rfq", rfq.id, rfq.code, rfq.title, f"/rfqs/{rfq.id}")],
    }


async def _recommendation(session: AsyncSession, tenant: Tenant, text: str) -> dict:
    match = DOC_CODE.search(text.upper())
    query = select(RFQ).where(RFQ.tenant_id == tenant.id)
    if match and match.group(0).upper().startswith("RFQ"):
        query = query.where(RFQ.code == match.group(0).upper())
    rfq = (await session.execute(query.order_by(RFQ.created_at.desc()))).scalars().first()
    if rfq is None:
        return {"answer_en": "There is no RFQ to compare.",
                "answer_ar": "لا يوجد طلب للمقارنة.", "citations": [], "data": {}}
    matrix = await comparison.build_matrix(session, tenant, rfq)
    ranked = sorted(matrix.get("columns", []), key=lambda col: col.get("rank") or 999)
    if not ranked:
        return {"answer_en": f"{rfq.code} has nothing to compare yet.",
                "answer_ar": f"{rfq.code} لا يوجد ما يُقارن بعد.",
                "citations": [_cite("rfq", rfq.id, rfq.code, rfq.title, f"/rfqs/{rfq.id}")],
                "data": {"rfq_code": rfq.code}}
    top = ranked[0]
    return {
        "answer_en": f"For {rfq.code}, the weighted comparison ranks {top.get('supplier_name')} first. "
                     "A recommendation is a proposal: a human approves the award.",
        "answer_ar": f"لـ {rfq.code}، المقارنة المرجحة تضع {top.get('supplier_name')} أولاً. "
                     "التوصية اقتراح فقط: الإنسان هو من يعتمد الترسية.",
        "data": {"rfq_code": rfq.code, "top": top.get("supplier_name"),
                 "score": top.get("score"), "rank": top.get("rank")},
        "citations": [_cite("rfq", rfq.id, rfq.code, rfq.title, f"/rfqs/{rfq.id}")],
    }


async def _supplier_balance(session: AsyncSession, tenant: Tenant, text: str) -> dict:
    suppliers = list((await session.execute(
        select(Supplier).where(Supplier.tenant_id == tenant.id)
    )).scalars().all())
    lowered = (text or "").lower()
    matched = next((s for s in suppliers
                    if s.name.lower() in lowered or (s.name_ar and s.name_ar in text)), None)
    if matched is None:
        rows = await invoicing.supplier_balances(session, tenant)
        if not rows:
            return {"answer_en": "No supplier has an open balance.",
                    "answer_ar": "لا يوجد رصيد مفتوح لأي مورد.", "citations": [], "data": {"items": []}}
        top = rows[0]
        return {
            "answer_en": f"The largest open balance is {top['outstanding']} {top['currency']} with "
                         f"{top['supplier_name']}, of which {top['overdue']} is overdue.",
            "answer_ar": f"أكبر رصيد مفتوح هو {top['outstanding']} {top['currency']} لدى "
                         f"{top['supplier_name']}، منه {top['overdue']} متأخر.",
            "data": {"supplier": top["supplier_name"], "outstanding": top["outstanding"],
                     "overdue": top["overdue"], "currency": top["currency"]},
            "citations": [_cite("supplier", top["supplier_id"], top["supplier_name"], "Supplier balance",
                                "/accounting/reports?tab=balances")],
        }
    balance = await invoicing.supplier_balance(session, tenant.id, matched.id)
    return {
        "answer_en": f"{matched.name}: outstanding {balance['outstanding']} {balance['currency']}, "
                     f"overdue {balance['overdue']}, {balance['open_invoices']} open invoice(s).",
        "answer_ar": f"{matched.name}: المستحق {balance['outstanding']} {balance['currency']}، "
                     f"المتأخر {balance['overdue']}، {balance['open_invoices']} فاتورة مفتوحة.",
        "data": {"supplier": matched.name, **{k: balance[k] for k in
                                              ("outstanding", "overdue", "open_invoices", "currency")}},
        "citations": [_cite("supplier", matched.id, matched.name, "Supplier balance",
                            "/accounting/reports?tab=balances")],
    }


async def _supplier_communications(session: AsyncSession, tenant: Tenant, text: str) -> dict:
    suppliers = list((await session.execute(
        select(Supplier).where(Supplier.tenant_id == tenant.id)
    )).scalars().all())
    lowered = (text or "").lower()
    matched = next((s for s in suppliers
                    if s.name.lower() in lowered or (s.name_ar and s.name_ar in text)), None)
    data = await inbox.conversations(session, tenant, supplier_id=matched.id if matched else "",
                                     limit=5)
    if not data["items"]:
        return {"answer_en": "No supplier conversation matches that.",
                "answer_ar": "لا توجد محادثة مطابقة.", "citations": [], "data": {"items": []}}
    first = data["items"][0]
    return {
        "answer_en": f"Latest conversation with {first['supplier_name']} on "
                     f"{first['last_message']['channel']}: {first['last_message']['body'][:180]}",
        "answer_ar": f"آخر محادثة مع {first['supplier_name']} على "
                     f"{first['last_message']['channel_label_ar']}: "
                     f"{first['last_message']['body'][:180]}",
        "data": {"thread_id": first["thread_id"], "supplier": first["supplier_name"],
                 "channel": first["last_message"]["channel"], "awaiting_reply": first["awaiting_reply"]},
        "citations": [_cite("thread", first["thread_id"], first["rfq_code"], first["supplier_name"],
                            f"/inbox?thread={first['thread_id']}")],
    }


async def _create_request(session: AsyncSession, tenant: Tenant, user: User, text: str) -> dict:
    """Draft a requisition through the normal intake path. It is never submitted for the user."""
    spoken = re.sub(r"^(create a request|new request|طلب شراء جديد|أنشئ طلب)\s*[:\-]?\s*", "",
                    text.strip(), flags=re.IGNORECASE)
    pr = await intake_free_text(session, tenant, text=spoken or text, source="voice",
                                requested_by=user.id, title=None)
    await audit(session, tenant_id=tenant.id, action="assistant.drafted_request", actor_id=user.id,
                actor_label=user.email, entity_type="purchase_requisition", entity_id=pr.id,
                after={"code": pr.code, "source": "voice", "submitted": False})
    clarifying = pr.status in {"draft", "clarifying"}
    status_en = ("It is waiting for your clarification and is not submitted"
                 if pr.status == "clarifying" else "It is a draft and is not submitted")
    status_ar = ("الطلب في انتظار توضيحك ولم يُقدَّم" if pr.status == "clarifying"
                 else "الطلب مسودة ولم يُقدَّم")
    return {
        "answer_en": f"I drafted request {pr.code} from what you said. {status_en}: open it, check the "
                     "items, and submit it yourself.",
        "answer_ar": f"أنشأت مسودة طلب {pr.code} مما قلته. {status_ar}: افتحه وراجع الأصناف ثم قدّمه بنفسك.",
        "data": {"code": pr.code, "status": pr.status, "submitted": False, "draft": clarifying},
        "citations": [_cite("purchase_requisition", pr.id, pr.code, pr.title,
                            f"/requests?focus={pr.id}")],
        "requires_confirmation": False,
    }


HELP = {
    "answer_en": "I can check the status of a request, RFQ or purchase order, list your requests, "
                 "summarise offers or balances, and draft a purchase request. I cannot approve, award, "
                 "sign, pay or change a specification.",
    "answer_ar": "أستطيع متابعة حالة طلب أو عرض سعر أو أمر شراء، وعرض طلباتك، وتلخيص العروض أو "
                 "الأرصدة، وإنشاء مسودة طلب شراء. لا أستطيع الاعتماد أو الترسية أو التوقيع أو الدفع "
                 "أو تغيير المواصفات.",
}


async def ask(session: AsyncSession, tenant: Tenant, user: User, permissions: set[str], *, text: str,
              channel: str = "text", locale: str = "ar", session_id: str | None = None,
              ip: str = "", request_id: str = "") -> dict:
    if not is_enabled(tenant, "voice_assistant"):
        return {"ok": False, "error": "The assistant is disabled for this tenant.",
                "codes": ["BLOCK_FEATURE_OFF"]}

    intent = classify(text)
    required = INTENT_PERMISSIONS.get(intent)
    assistant_session = None
    if session_id:
        assistant_session = (await session.execute(
            select(AssistantSession).where(AssistantSession.id == session_id,
                                           AssistantSession.tenant_id == tenant.id,
                                           AssistantSession.user_id == user.id)
        )).scalar_one_or_none()
    if assistant_session is None:
        assistant_session = AssistantSession(
            tenant_id=tenant.id, user_id=user.id, channel=channel, locale=locale,
            permission_snapshot=sorted(permissions), role_snapshot=list(user.roles or []),
        )
        session.add(assistant_session)
        await session.flush()
    assistant_session.channel = channel

    seq = len((await session.execute(
        select(AssistantTurn).where(AssistantTurn.session_id == assistant_session.id)
    )).scalars().all()) + 1
    session.add(AssistantTurn(tenant_id=tenant.id, session_id=assistant_session.id, seq=seq,
                              role="user", text=text[:4000], intent=intent))

    payload: dict
    blocked_code = ""
    if intent == "forbidden":
        blocked_code = "ASSISTANT_FORBIDDEN_ACTION"
        payload = {
            "answer_en": "I cannot do that. Approving, awarding, signing, paying and posting are human "
                         "actions performed in the relevant screen by someone with the right "
                         "permission. I can show you where.",
            "answer_ar": "لا أستطيع فعل ذلك. الاعتماد والترسية والتوقيع والدفع والترحيل أفعال بشرية "
                         "تُنفذ في الشاشة المخصصة بواسطة من يملك الصلاحية. أستطيع أن أدلّك على المكان.",
            "data": {}, "citations": [],
        }
    elif intent == "document_status":
        code = DOC_CODE.search(text.upper()).group(0)  # type: ignore[union-attr]
        needs = {"PR": "pr:read", "RFQ": "rfq:read", "PO": "po:read"}[code.split("-")[0].upper()]
        if needs not in permissions:
            blocked_code = "ASSISTANT_PERMISSION"
            payload = {"answer_en": "That document type is outside your permissions.",
                       "answer_ar": "هذا النوع من المستندات خارج صلاحياتك.", "data": {}, "citations": []}
        else:
            payload = await _document_status(session, tenant, user, code, permissions)
            blocked_code = payload.get("blocked_code", "")
    elif required and required not in permissions:
        blocked_code = "ASSISTANT_PERMISSION"
        payload = {
            "answer_en": "Your role does not include access to that information.",
            "answer_ar": "دورك لا يتضمن الوصول إلى هذه المعلومات.",
            "data": {}, "citations": [],
        }
    elif intent == "my_requests":
        payload = await _my_requests(session, tenant, user)
    elif intent == "offer_summary":
        payload = await _offer_summary(session, tenant, text)
    elif intent == "recommendation":
        payload = await _recommendation(session, tenant, text)
    elif intent == "supplier_balance":
        payload = await _supplier_balance(session, tenant, text)
    elif intent == "supplier_communications":
        payload = await _supplier_communications(session, tenant, text)
    elif intent == "pr_create":
        payload = await _create_request(session, tenant, user, text)
    else:
        payload = {**HELP, "data": {}, "citations": []}

    answer_en = payload.get("answer_en", "")
    answer_ar = payload.get("answer_ar", "")
    session.add(AssistantTurn(
        tenant_id=tenant.id, session_id=assistant_session.id, seq=seq + 1, role="assistant",
        text=answer_en, text_ar=answer_ar, intent=intent, answer=payload.get("data", {}),
        citations=payload.get("citations", []), blocked_code=blocked_code,
    ))
    await session.flush()
    await audit(session, tenant_id=tenant.id, action="assistant.asked",
                actor_type="user", actor_id=user.id, actor_label=user.email,
                entity_type="assistant_session", entity_id=assistant_session.id,
                after={"intent": intent, "channel": channel, "blocked_code": blocked_code,
                       "citations": len(payload.get("citations", []))}, ip=ip, request_id=request_id)
    return {
        "ok": True,
        "session_id": assistant_session.id,
        "intent": intent,
        "channel": channel,
        "locale": locale,
        "answer_en": answer_en,
        "answer_ar": answer_ar,
        "data": payload.get("data", {}),
        "citations": payload.get("citations", []),
        "blocked_code": blocked_code,
        "can_act": False,
        "requires": required,
        "asked_at": datetime.now(UTC).isoformat(),
    }


async def session_view(session: AsyncSession, tenant_id: str, assistant_session: AssistantSession) -> dict:
    turns = list((await session.execute(
        select(AssistantTurn).where(AssistantTurn.session_id == assistant_session.id)
        .order_by(AssistantTurn.seq)
    )).scalars().all())
    return {
        "session_id": assistant_session.id,
        "channel": assistant_session.channel,
        "locale": assistant_session.locale,
        "status": assistant_session.status,
        "user_id": assistant_session.user_id,
        "roles": assistant_session.role_snapshot,
        "permission_snapshot": assistant_session.permission_snapshot,
        "started_at": assistant_session.created_at.isoformat() if assistant_session.created_at else None,
        "turns": [{
            "seq": turn.seq, "role": turn.role, "text": turn.text, "text_ar": turn.text_ar,
            "intent": turn.intent, "answer": turn.answer, "citations": turn.citations,
            "blocked_code": turn.blocked_code,
            "at": turn.created_at.isoformat() if turn.created_at else None,
        } for turn in turns],
    }


async def recent_sessions(session: AsyncSession, tenant_id: str, user_id: str, limit: int = 10) -> list[dict]:
    rows = list((await session.execute(
        select(AssistantSession).where(AssistantSession.tenant_id == tenant_id,
                                       AssistantSession.user_id == user_id)
        .order_by(AssistantSession.created_at.desc()).limit(limit)
    )).scalars().all())
    out: list[dict] = []
    for row in rows:
        first = (await session.execute(
            select(AssistantTurn).where(AssistantTurn.session_id == row.id, AssistantTurn.role == "user")
            .order_by(AssistantTurn.seq)
        )).scalars().first()
        out.append({
            "session_id": row.id, "channel": row.channel, "locale": row.locale,
            "opened_at": row.created_at.isoformat() if row.created_at else None,
            "first_question": first.text if first else "",
        })
    return out


__all__ = ["ask", "classify", "session_view", "recent_sessions", "INTENT_PERMISSIONS"]
