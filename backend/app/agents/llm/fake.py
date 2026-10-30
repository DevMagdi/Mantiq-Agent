"""Deterministic, seedable FakeLLM: full product behaviour with zero external accounts.

Every handler is rule-based over the same context a real model would receive, so the
pipeline, validators and policy engine are exercised exactly as in production.
"""
from __future__ import annotations

import hashlib
import re
from decimal import Decimal
from typing import Any

from app.agents.llm.base import LLMRequest, LLMResponse
from app.services import numbers
from app.services.catalog_data import COLUMN_SYNONYMS as _CATALOG_COLUMN_SYNONYMS

MISSING_QUESTIONS = {
    "grade": ("What steel grade / material grade is required?", "إيه درجة الصلب / الخامة المطلوبة؟"),
    "standard": ("Which standard applies (e.g. ASTM A615, EN 10025)?", "إيه المواصفة المطلوبة (مثلاً ASTM A615)؟"),
    "dimensions": ("What are the exact dimensions / diameter / size?", "إيه المقاسات أو القطر المطلوب بالتفصيل؟"),
    "quantity": ("What is the required quantity and unit of measure?", "إيه الكمية المطلوبة ووحدة القياس؟"),
    "tolerance": ("What tolerance is acceptable?", "إيه نسبة التفاوت المقبولة؟"),
    "delivery_location": ("Where should the material be delivered?", "فين مكان التسليم المطلوب؟"),
    "incoterm": ("Which Incoterm should suppliers quote (DAP, FOB, EXW)?", "أي شرط تسليم نطلبه من الموردين (DAP/FOB/EXW)؟"),
    "delivery_date": ("When is the material needed?", "إمتى محتاجين الخامة؟"),
    "payment_terms": ("What payment terms do you propose?", "إيه شروط السداد المقترحة؟"),
    "brand": ("Is a specific brand or OEM required?", "فيه ماركة أو مصنع معين مطلوب؟"),
}

COLUMN_SYNONYMS = _CATALOG_COLUMN_SYNONYMS

FIELD_LABELS = {
    "unit_price": ("Unit price", "سعر الوحدة"),
    "currency": ("Currency", "العملة"),
    "quantity": ("Quantity", "الكمية"),
    "uom": ("Unit of measure", "وحدة القياس"),
    "moq": ("Minimum order quantity", "أقل كمية للطلب"),
    "lead_time_days": ("Lead time (days)", "مدة التوريد (أيام)"),
    "validity_days": ("Offer validity (days)", "صلاحية العرض (أيام)"),
    "payment_terms": ("Payment terms", "شروط السداد"),
    "incoterm": ("Incoterm", "شرط التسليم"),
    "delivery_terms": ("Delivery terms", "شروط التسليم"),
    "notes": ("Notes", "ملاحظات"),
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", numbers.normalize_digits(text or "").lower()).strip()


class FakeLLM:
    """Rule-based stand-in for a hosted model (scripted, deterministic, offline)."""

    name = "fake"
    model = "fake-deterministic-1"

    def __init__(self, seed: int = 7) -> None:
        self.seed = seed

    async def complete(self, request: LLMRequest) -> LLMResponse:
        handlers = {
            "extract_offer": self._extract_offer,
            "clarify": self._clarify,
            "negotiation_draft": self._negotiation_draft,
            "recommendation": self._recommendation,
            "column_mapping": self._column_mapping,
            "item_mapping": self._item_mapping,
            "missing_info": self._missing_info,
            "summary": self._summary,
        }
        handler = handlers.get(request.task)
        prompt_chars = sum(len(m.content) for m in request.messages)
        if handler is None:
            return LLMResponse(text="OK", tokens_in=prompt_chars // 4, tokens_out=2, model=self.model,
                               provider=self.name)
        data, text = handler(request)
        return LLMResponse(
            text=text,
            data=data,
            tokens_in=max(1, prompt_chars // 4),
            tokens_out=max(1, len(text or "") // 4),
            model=self.model,
            provider=self.name,
        )

    # ------------------------------------------------------------- handlers
    def _extract_offer(self, request: LLMRequest) -> tuple[dict[str, Any], str]:
        text = request.context.get("text", "")
        haystack = numbers.normalize_digits(text)
        fields: dict[str, Any] = {}

        def add(name: str, value: Any, span: str, confidence: float) -> None:
            if value in (None, ""):
                return
            label_en, label_ar = FIELD_LABELS.get(name, (name, name))
            fields[name] = {
                "value": str(value),
                "label": label_en,
                "label_ar": label_ar,
                "confidence": confidence,
                "source_span": span.strip()[:300],
            }

        currency = numbers.detect_currency(haystack, default="")
        if currency:
            span = next((line for line in haystack.splitlines() if currency.lower() in line.lower()), currency)
            add("currency", currency, span, 0.97)

        price_patterns = [
            r"(?:unit\s*price|price\s*per\s*(?:ton|tonne|kg|piece|pcs|unit)|سعر\s*(?:الطن|الوحدة|القطعة|الكيلو))\s*[:=]?\s*([\d,\.]+)",
            r"([\d,\.]+)\s*(?:EGP|USD|SAR|AED|KWD|QAR|جنيه|ريال|درهم|دولار)\s*(?:/|per|للطن|للوحدة|للقطعة)",
            r"(?:EGP|USD|SAR|AED|KWD|QAR)\s*([\d,\.]+)\s*(?:/|per)\s*(?:ton|tonne|kg|pc|pcs|unit)",
        ]
        for pattern in price_patterns:
            match = re.search(pattern, haystack, re.IGNORECASE)
            if match:
                value = numbers.parse_decimal(match.group(1))
                if value and value > 0:
                    add("unit_price", value, match.group(0), 0.95)
                    break

        qty_match = re.search(
            r"(?:quantity|qty|الكمية)\s*[:=]?\s*([\d,\.]+)\s*([A-Za-z\u0600-\u06FF]+)?", haystack, re.IGNORECASE
        )
        if qty_match:
            add("quantity", numbers.parse_decimal(qty_match.group(1)), qty_match.group(0), 0.93)
            if qty_match.group(2):
                add("uom", numbers.normalize_uom(qty_match.group(2)), qty_match.group(0), 0.85)

        moq_match = re.search(r"(?:moq|minimum order|أقل كمية)\s*[:=]?\s*([\d,\.]+)", haystack, re.IGNORECASE)
        if moq_match:
            add("moq", numbers.parse_decimal(moq_match.group(1)), moq_match.group(0), 0.9)

        lead_match = re.search(
            r"(?:lead\s*time|delivery\s*(?:time|within)|التسليم\s*(?:خلال|في)|مدة\s*التوريد)\s*[:=]?\s*([\d,\.]+)\s*(day|days|week|weeks|أيام|يوم|أسبوع|اسبوع)",
            haystack, re.IGNORECASE,
        )
        if lead_match:
            amount = numbers.parse_decimal(lead_match.group(1)) or Decimal("0")
            unit = lead_match.group(2).lower()
            days = int(amount) if unit in {"day", "days", "أيام", "يوم"} else int(amount * 7)
            add("lead_time_days", days, lead_match.group(0), 0.9)

        validity_match = re.search(
            r"(?:validity|valid\s*for|صالح(?:ة)?\s*لمدة|صلاحية)\s*[:=]?\s*([\d,\.]+)\s*(day|days|week|weeks|يوم|أيام|أسبوع)",
            haystack, re.IGNORECASE,
        )
        if validity_match:
            amount = numbers.parse_decimal(validity_match.group(1)) or Decimal("0")
            unit = validity_match.group(2).lower()
            days = int(amount) if unit in {"day", "days", "يوم", "أيام"} else int(amount * 7)
            add("validity_days", days, validity_match.group(0), 0.85)

        payment = re.search(r"(net\s*\d+|advance|prepaid|cod|دفع\s*مقدم|الدفع\s*بعد|آجل)", haystack, re.IGNORECASE)
        if payment:
            add("payment_terms", payment.group(0), payment.group(0), 0.85)

        incoterm = re.search(r"\b(EXW|FOB|CIF|CIP|DAP|DDP|FCA|CPT)\b", haystack)
        if incoterm:
            add("incoterm", incoterm.group(1).upper(), incoterm.group(0), 0.92)

        if not fields:
            add("notes", haystack.strip()[:200], haystack.strip()[:200], 0.4)

        return {"fields": fields}, f"Extracted {len(fields)} fields from supplier reply."

    def _clarify(self, request: LLMRequest) -> tuple[dict[str, Any], str]:
        missing = request.context.get("missing_fields", [])
        questions = []
        for field in missing:
            en, ar = MISSING_QUESTIONS.get(field, (f"Please confirm {field}.", f"يرجى تأكيد {field}."))
            questions.append({"field": field, "question": en, "question_ar": ar})
        return {"questions": questions}, f"Asked {len(questions)} clarification questions."

    def _negotiation_draft(self, request: LLMRequest) -> tuple[dict[str, Any], str]:
        ctx = request.context
        current = numbers.parse_decimal(ctx.get("current_price")) or Decimal("0")
        target = numbers.parse_decimal(ctx.get("target_price")) or current
        walkaway = numbers.parse_decimal(ctx.get("walkaway_price")) or current
        currency = ctx.get("currency", "EGP")
        round_no = ctx.get("round_no", 1)
        tone = ctx.get("tone", "professional")
        concession = ctx.get("concession") or ""
        benchmark_hint = ctx.get("benchmark_hint", "")

        # Never disclose competitor specifics: only anonymised aggregates supplied by the tool.
        proposed = min(target, max(current * Decimal("0.94"), walkaway * Decimal("0.995")))
        if proposed <= 0:
            proposed = target
        pct = ((current - proposed) / current * 100) if current else Decimal("0")

        body_ar = (
            f"تحية طيبة،\n\nشكراً على عرضكم. بعد مراجعة العرض ومقارنته بمستويات السوق، "
            f"نقترح سعر {numbers.fmt_amount(proposed, currency, 'ar')} للوحدة"
            f"{' ' + concession if concession else ''}. "
            f"{benchmark_hint} نأمل تأكيد العرض بهذا السعر لإتمام الإجراءات في الجولة {round_no}.\n\n"
            "مع التقدير،"
        )
        body_en = (
            "Dear supplier,\n\nThank you for your offer. After reviewing it against market levels, "
            f"we propose a unit price of {numbers.fmt_amount(proposed, currency, 'en')}"
            f"{' with ' + concession if concession else ''}. {benchmark_hint} "
            f"We would appreciate confirmation at this price to proceed (round {round_no}).\n\nKind regards,"
        )
        return (
            {"body_ar": body_ar, "body_en": body_en, "proposed_price": str(proposed),
             "delta_pct": float(pct), "tone": tone},
            f"Drafted counter-offer at {proposed} {currency} (-{pct:.2f}%).",
        )

    def _recommendation(self, request: LLMRequest) -> tuple[dict[str, Any], str]:
        ranked = request.context.get("ranked", [])
        currency = request.context.get("currency", "EGP")
        if not ranked:
            return {"recommended": [], "justification_ar": "", "justification_en": ""}, "No offers to rank."
        top = ranked[:3]
        lines_en = []
        lines_ar = []
        for item in top:
            lines_en.append(
                f"{item.get('supplier_name')} ranks #{item.get('rank')} with a weighted score of "
                f"{item.get('weighted_score')}/100 and a TCO of "
                f"{numbers.fmt_amount(item.get('tco_total'), currency, 'en')}"
                f"{', lead time ' + str(item.get('lead_time_days')) + ' days' if item.get('lead_time_days') else ''}."
            )
            lines_ar.append(
                f"{item.get('supplier_name')} في المركز {item.get('rank')} بدرجة مرجحة "
                f"{item.get('weighted_score')}/100 وإجمالي تكلفة ملكية "
                f"{numbers.fmt_amount(item.get('tco_total'), currency, 'ar')}."
            )
        return (
            {"recommended": [item.get("offer_id") for item in top],
             "justification_en": " ".join(lines_en), "justification_ar": " ".join(lines_ar)},
            f"Recommended {len(top)} offers with traceable figures.",
        )

    def _column_mapping(self, request: LLMRequest) -> tuple[dict[str, Any], str]:
        headers = [str(h) for h in request.context.get("headers", [])]
        mapping: dict[str, str] = {}
        for header in headers:
            norm = _norm(header)
            for field, synonyms in COLUMN_SYNONYMS.items():
                if field in mapping.values():
                    continue
                if any(syn in norm or norm in syn for syn in synonyms):
                    mapping[header] = field
                    break
        return {"mapping": mapping, "confidence": 0.9 if mapping else 0.3}, f"Mapped {len(mapping)} columns."

    def _item_mapping(self, request: LLMRequest) -> tuple[dict[str, Any], str]:
        candidates = request.context.get("candidates", [])
        if not candidates:
            return {"taxonomy_code": None, "confidence": 0.0}, "No candidate taxonomy nodes."
        best = candidates[0]
        return (
            {"taxonomy_code": best.get("taxonomy_code"), "confidence": min(0.99, best.get("confidence", 0.5) + 0.05)},
            f"Selected {best.get('name')}.",
        )

    def _missing_info(self, request: LLMRequest) -> tuple[dict[str, Any], str]:
        missing = request.context.get("missing", [])
        lines_ar = [f"- {MISSING_QUESTIONS.get(m, (m, m))[1]}" for m in missing]
        lines_en = [f"- {MISSING_QUESTIONS.get(m, (m, m))[0]}" for m in missing]
        return {"missing": missing, "body_ar": "\n".join(lines_ar), "body_en": "\n".join(lines_en)}, \
            "Drafted missing-information request."

    def _summary(self, request: LLMRequest) -> tuple[dict[str, Any], str]:
        facts = request.context.get("facts", {})
        digest = hashlib.sha256(str(sorted(facts.items())).encode()).hexdigest()[:8]
        text = "Summary: " + ", ".join(f"{k}={v}" for k, v in list(facts.items())[:8]) + f" [{digest}]"
        return {"summary": text}, text


class ScriptedLLM:
    """Fixed-output provider for tests that need to simulate a bad or adversarial model."""

    name = "scripted"
    model = "scripted-1"

    def __init__(self, responses: list[dict[str, Any] | str]) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        item = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        if isinstance(item, str):
            return LLMResponse(text=item, model=self.model, provider=self.name, tokens_in=10, tokens_out=10)
        return LLMResponse(data=item, text=str(item)[:200], model=self.model, provider=self.name,
                           tokens_in=10, tokens_out=10)
