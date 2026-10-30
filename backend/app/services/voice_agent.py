"""Outbound AI voice agent: an approved-data answer engine wrapped around the call pipeline.

The safety model is the same as everywhere else in the product — the model proposes, the policy
engine disposes — applied turn by turn:

* the call always opens with the AI **disclosure**, and recording only exists with **consent**
  (`voice.start_call` enforces hours, opt-in, blacklist and the country policy before dialling);
* every supplier question is classified and answered from **approved data only** (the RFQ as written,
  published payment terms, the delivery location, the deadline). Competitor information, any
  commitment, and any specification change are **blocked structurally** — there is no branch that
  can produce those sentences, and the refusal is recorded on the turn;
* the transcript, every turn and every decision are persisted, and the commercial fields flow into
  the normal extraction + human verification queue. Nothing the AI says can accept an offer, award,
  sign or pay.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import policy_engine
from app.core.audit import audit
from app.db.models.entities import RFQ, RFQLine, Supplier, Tenant, VoiceCall, VoiceCallTurn
from app.services import numbers
from app.services.features import is_enabled
from app.services.voice import start_call

log = logging.getLogger("procureai.voice_agent")

#: Topics the agent may answer from approved data.
APPROVED_TOPICS = {
    "price", "validity", "lead_time", "payment_terms", "specification", "delivery", "rfq", "general",
    "greeting", "consent",
}
#: Topics that are refused, with the policy code the refusal is recorded under.
BLOCKED_TOPICS = {
    "competitor": policy_engine.COMPETITOR_DISCLOSURE,
    "commitment": policy_engine.ACCEPT_INTENT,
    "spec_change": policy_engine.SPEC_CHANGE,
    "payment_execution": policy_engine.SIGN_INTENT,
}

TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "competitor": ("competitor", "other supplier", "their price", "cheaper offer", "beat the price",
                   "منافس", "منافسين", "سعر غيري", "عرض تاني", "أرخص", "عروض تانية"),
    "commitment": ("award", "confirm the order", "we accept", "guarantee", "sign", "commit",
                   "approve", "you have the deal", "رسّي", "ترسية", "موافقة", "اعتماد", "نوقّع",
                   "نعتمد", "اتفقنا", "الطلب لك"),
    "spec_change": ("change the specification", "change the spec", "substitute", "instead of",
                    "modify the spec", "different specification", "تغيير المواصفات", "تغيير",
                    "بديل", "نغير", "نغيّر", "بدل", "مواصفات مختلفة"),
    "payment_execution": ("transfer the money", "pay now", "payment today", "swift now",
                          "تحويل الآن", "ادفع", "الدفع فوراً"),
    "payment_terms": ("payment", "net 30", "net 60", "terms of payment", "دفع", "سداد", "آجل"),
    "validity": ("validity", "valid until", "how long is the offer", "صلاحية", "ساري"),
    "lead_time": ("lead time", "delivery time", "when can you deliver", "مدة التوريد", "التسليم",
                  "متى التسليم", "المدة"),
    "price": ("price", "unit price", "cost", "how much", "سعر", "كام", "بكم"),
    "specification": ("specification", "spec", "grade", "diameter", "standard", "مواصفات", "قياس",
                      "مقاس", "معيار"),
    "delivery": ("delivery location", "where do we deliver", "عنوان التسليم", "مكان التسليم"),
    "greeting": ("hello", "good morning", "السلام", "صباح", "أهلاً", "اهلا"),
    "consent": ("yes you can record", "i agree", "موافق", "أيوة", "ايوه", "اتفضل"),
}


def classify_question(text: str) -> str:
    """Deterministic topic classification. Cheap, auditable and testable — no LLM in the loop."""
    lowered = (text or "").lower()
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return topic
    return "general"


def _money(value: object) -> str:
    return str(numbers.money(value))


def approved_knowledge(rfq: RFQ, lines: list[RFQLine]) -> dict:
    """Everything the agent is allowed to say. Nothing here is a competitor fact or a commitment."""
    return {
        "rfq_code": rfq.code,
        "title": rfq.title,
        "currency": rfq.currency,
        "deadline": rfq.deadline.strftime("%Y-%m-%d") if rfq.deadline else "",
        "payment_terms": rfq.payment_terms,
        "incoterm": rfq.incoterm,
        "delivery_location": rfq.delivery_location or "",
        "lines": [
            {"line_no": line.line_no, "description": line.description,
             "quantity": str(line.quantity), "uom": line.uom,
             "target_price": _money(line.target_price) if line.target_price else ""}
            for line in lines
        ],
    }


def answer_question(topic: str, knowledge: dict, *, lang: str = "ar") -> dict:
    """The policy-checked answer for one supplier question.

    A blocked topic returns a refusal sentence, never the information asked for. The refusal is what a
    human hears on the call, so it is phrased as a polite decline rather than an error.
    """
    if topic in BLOCKED_TOPICS:
        block_code = BLOCKED_TOPICS[topic]
        refusal_en = {
            "competitor": "I cannot discuss other suppliers or their pricing. I can only talk about "
                          "this request.",
            "commitment": "I cannot make any commitment, accept an offer or approve a purchase. A "
                          "buyer will review your quotation and reply in writing.",
            "spec_change": "I cannot change the specification. Please quote exactly what the request "
                           "describes, or state your deviation clearly for the buyer to review.",
            "payment_execution": "I cannot arrange or confirm any payment. Payments are handled by the "
                                 "finance team outside this call.",
        }[topic]
        refusal_ar = {
            "competitor": "لا أستطيع مناقشة موردين آخرين أو أسعارهم. أتحدث فقط عن هذا الطلب.",
            "commitment": "لا أستطيع تقديم أي التزام أو قبول عرض أو اعتماد شراء. سيراجع المشتري عرضكم "
                          "ويرد عليكم كتابةً.",
            "spec_change": "لا أستطيع تغيير المواصفات. من فضلك سعّر ما هو مكتوب في الطلب، أو اذكر "
                           "الاختلاف بوضوح ليراجعه المشتري.",
            "payment_execution": "لا أستطيع ترتيب أو تأكيد أي دفعة. المدفوعات يتولاها فريق الحسابات "
                                 "خارج هذه المكالمة.",
        }[topic]
        return {"topic": topic, "allowed": False, "block_code": block_code,
                "answer_en": refusal_en, "answer_ar": refusal_ar,
                "reason": f"Blocked topic: {topic} ({block_code})"}

    lines = knowledge.get("lines") or []
    quantity = "; ".join(f"{line['quantity']} {line['uom']} {line['description']}" for line in lines)[:300]
    answers = {
        "price": (
            f"The request is for {quantity}. Pricing is yours to quote; I cannot share any other "
            "supplier's price.",
            f"الطلب هو {quantity}. السعر من تقديركم أنتم، ولا أستطيع مشاركة أسعار موردين آخرين.",
        ),
        "validity": (
            "Please state how long your offer stays valid; the buyer compares offers on that basis.",
            "من فضلك اذكر مدة صلاحية عرضكم؛ المشتري يقارن العروض على هذا الأساس.",
        ),
        "lead_time": (
            "Please state the delivery lead time in days. Delivery location is "
            f"{knowledge.get('delivery_location') or 'the buyer premises'}.",
            "من فضلك اذكر مدة التوريد بالأيام. مكان التسليم "
            f"{knowledge.get('delivery_location') or 'مقر المشتري'}.",
        ),
        "payment_terms": (
            f"The published payment terms for this request are: {knowledge.get('payment_terms')}.",
            f"شروط السداد المعلنة لهذا الطلب: {knowledge.get('payment_terms')}.",
        ),
        "specification": (
            f"The request as written: {quantity}. I can read it back, but I cannot change it.",
            f"الطلب كما هو مكتوب: {quantity}. أستطيع قراءته، لكن لا أستطيع تغييره.",
        ),
        "delivery": (
            f"Delivery is {knowledge.get('incoterm')} to "
            f"{knowledge.get('delivery_location') or 'the buyer premises'}.",
            f"التسليم {knowledge.get('incoterm')} إلى "
            f"{knowledge.get('delivery_location') or 'مقر المشتري'}.",
        ),
        "rfq": (
            f"This is request {knowledge.get('rfq_code')} — {knowledge.get('title')}. "
            f"The deadline is {knowledge.get('deadline')}.",
            f"هذا الطلب {knowledge.get('rfq_code')} — {knowledge.get('title')}. "
            f"الموعد النهائي {knowledge.get('deadline')}.",
        ),
        "greeting": ("Hello, how can I help with this request?",
                     "أهلاً بحضرتك، كيف أساعد في هذا الطلب؟"),
        "consent": ("Thank you. I will continue with the request details.",
                    "شكراً لحضرتك. سأكمل بتفاصيل الطلب."),
        "general": (
            "I can only help with this request: the items, quantities, delivery location, payment "
            "terms and deadline. A buyer will answer anything else in writing.",
            "أستطيع المساعدة في هذا الطلب فقط: الأصناف والكميات ومكان التسليم وشروط السداد والموعد "
            "النهائي. أي شيء آخر يرد عليه المشتري كتابةً.",
        ),
    }
    answer_en, answer_ar = answers.get(topic, answers["general"])
    return {"topic": topic, "allowed": True, "block_code": "", "answer_en": answer_en,
            "answer_ar": answer_ar, "reason": "Answered from approved data"}


def parse_transcript(transcript: str) -> list[dict]:
    """Split a provider transcript into speaker turns. Providers prefix lines with `[AI]`/`[Supplier]`."""
    turns: list[dict] = []
    for raw in (transcript or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        lowered = line.lower()
        speaker = "system"
        text = line
        for label, value in (("[ai]", "ai"), ("[supplier]", "supplier"), ("[system]", "system"),
                             ("ai:", "ai"), ("supplier:", "supplier")):
            if lowered.startswith(label):
                speaker = value
                text = line[len(label):].strip(" :")
                break
        turns.append({"speaker": speaker, "text": text})
    return turns


async def run_ai_call(session: AsyncSession, tenant: Tenant, *, rfq: RFQ, supplier: Supplier,
                      language: str = "ar-EG", actor_id: str | None = None) -> dict:
    """Place the call (delegating every gate to `voice.start_call`) and policy-check every turn."""
    if not is_enabled(tenant, "voice_ai_calls"):
        return {"ok": False, "error": "AI voice calls are disabled for this tenant.",
                "codes": ["BLOCK_FEATURE_OFF"]}

    result = await start_call(session, tenant, rfq=rfq, supplier=supplier, language=language,
                              requested_by=actor_id)
    if not result.get("call_id"):
        return {**result, "codes": result.get("codes", []) + (["BLOCK_CALL_NOT_PLACED"]
                                                              if not result.get("ok") else [])}

    call = (await session.execute(select(VoiceCall).where(VoiceCall.id == result["call_id"]))).scalar_one()
    lines = list((await session.execute(
        select(RFQLine).where(RFQLine.rfq_id == rfq.id).order_by(RFQLine.line_no)
    )).scalars().all())
    knowledge = approved_knowledge(rfq, lines)

    spoken: list[VoiceCallTurn] = []   # what the supplier said, one row per question
    audited: list[VoiceCallTurn] = []  # every audible turn, the agent's replies included
    blocks = 0
    for turn in parse_transcript(call.transcript):
        seq = len(audited) + 1
        if turn["speaker"] == "supplier":
            topic = classify_question(turn["text"])
            decision = answer_question(topic, knowledge, lang=language)
            allowed = decision["allowed"]
            record = VoiceCallTurn(
                tenant_id=tenant.id, call_id=call.id, seq=seq, speaker="supplier", text=turn["text"],
                text_ar=turn["text"] if language.startswith("ar") else "",
                topic=topic, decision="allow" if allowed else "block",
                block_code=decision["block_code"], reason=decision["reason"],
            )
            spoken.append(record)
            audited.append(record)
            session.add(record)
            # The agent's own reply is an audited turn too: that is the sentence a supplier heard.
            reply = VoiceCallTurn(
                tenant_id=tenant.id, call_id=call.id, seq=seq + 1, speaker="ai",
                text=decision["answer_en"], text_ar=decision["answer_ar"], topic=topic,
                decision="allow" if allowed else "block",
                block_code=decision["block_code"], reason=decision["reason"],
            )
            audited.append(reply)
            session.add(reply)
            if not allowed:
                blocks += 1
        elif turn["speaker"] == "ai":
            topic = "disclosure" if turn["text"].lower().startswith(("hello, this is an ai", "أهلاً",
                                                                     "مرحباً")) else "rfq"
            record = VoiceCallTurn(
                tenant_id=tenant.id, call_id=call.id, seq=seq, speaker="ai", text=turn["text"],
                text_ar=turn["text"] if language.startswith("ar") else "", topic=topic,
                decision="disclose" if topic == "disclosure" else "allow",
                reason="Disclosure is always spoken first" if topic == "disclosure" else "Scripted request",
            )
            audited.append(record)
            session.add(record)
    await session.flush()

    call.policy_snapshot = {
        **(call.policy_snapshot or {}),
        "ai_agent": {
            "knowledge_version": "approved-data-v1",
            "topics_answered": sorted({t.topic for t in spoken if t.decision == "allow"}),
            "topics_blocked": sorted({t.block_code for t in spoken if t.block_code}),
            "blocked_turns": blocks,
            "guard": "no competitor data, no commitment, no specification change, no payment",
        },
    }
    await session.flush()
    await audit(session, tenant_id=tenant.id, action="voice.ai_call", actor_type="agent",
                actor_id=actor_id, actor_label="voice-agent", entity_type="voice_call",
                entity_id=call.id,
                after={"status": call.status, "consent": call.consent_given,
                       "disclosure": call.disclosure_played, "blocked_turns": blocks,
                       "topics_blocked": call.policy_snapshot["ai_agent"]["topics_blocked"],
                       "extraction_id": result.get("extraction_id")})
    return {
        **result,
        "ai_agent": call.policy_snapshot["ai_agent"],
        "turns": [{
            "seq": t.seq, "speaker": t.speaker, "text": t.text, "text_ar": t.text_ar,
            "topic": t.topic, "decision": t.decision, "block_code": t.block_code, "reason": t.reason,
        } for t in audited],
        "knowledge": knowledge,
        "policy": {"allowed_topics": sorted(APPROVED_TOPICS), "blocked_topics": sorted(BLOCKED_TOPICS)},
    }


async def call_turns(session: AsyncSession, tenant_id: str, call_id: str) -> list[dict]:
    rows = list((await session.execute(
        select(VoiceCallTurn).where(VoiceCallTurn.tenant_id == tenant_id, VoiceCallTurn.call_id == call_id)
        .order_by(VoiceCallTurn.seq, VoiceCallTurn.created_at)
    )).scalars().all())
    return [{
        "id": row.id, "seq": row.seq, "speaker": row.speaker, "text": row.text, "text_ar": row.text_ar,
        "topic": row.topic, "decision": row.decision, "block_code": row.block_code, "reason": row.reason,
    } for row in rows]


async def call_view(session: AsyncSession, tenant: Tenant, call: VoiceCall) -> dict:
    supplier = (await session.execute(
        select(Supplier).where(Supplier.id == call.supplier_id)
    )).scalar_one_or_none()
    rfq = (await session.execute(select(RFQ).where(RFQ.id == call.rfq_id))).scalar_one_or_none()
    return {
        "id": call.id,
        "rfq_id": call.rfq_id,
        "rfq_code": rfq.code if rfq else "",
        "supplier_id": call.supplier_id,
        "supplier_name": supplier.name if supplier else "",
        "supplier_name_ar": (supplier.name_ar if supplier else "") or "",
        "status": call.status,
        "provider": call.provider,
        "country": call.country,
        "language": call.language,
        "disclosure_played": call.disclosure_played,
        "consent_given": call.consent_given,
        "recording": bool(call.recording_uri),
        "duration_seconds": call.duration_seconds,
        "transcript": call.transcript,
        "summary": call.summary,
        "extracted_offer_id": call.extracted_offer_id,
        "policy_snapshot": call.policy_snapshot,
        "turns": await call_turns(session, call.tenant_id, call.id),
        "started_at": call.started_at.isoformat() if call.started_at else None,
        "ended_at": call.ended_at.isoformat() if call.ended_at else None,
        "created_by": call.created_by,
        "created_at": call.created_at.isoformat() if call.created_at else None,
        "answered_at": datetime.now(UTC).isoformat(),
    }
