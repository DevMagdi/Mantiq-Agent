"""Item normalization against the UNSPSC-based taxonomy (FR-IN-6).

Deterministic candidate retrieval (Arabic-aware normalization + trigram similarity +
keyword votes) with a learned-alias store. The LLM only re-ranks; the buyer confirms.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

from app.services import numbers

ARABIC_DIACRITICS = re.compile(r"[\u064B-\u0652\u0670\u0640]")
ARABIC_ALEFS = re.compile("[أإآٱ]")
ARABIC_TA = "ة"
ARABIC_YA = "ى"

SYNONYMS = {
    "حديد التسليح": "rebar", "حديد تسليح": "rebar", "سيخ": "rebar", "اسياخ": "rebar", "أسياخ": "rebar",
    "بلي": "bearing", "رولمان": "bearing", "رولمان بلي": "bearing",
    "زيت": "oil", "زيوت": "oil", "شحم": "grease", "شحوم": "grease",
    "كابل": "cable", "كيبل": "cable", "اسلاك": "wire", "أسلاك": "wire",
    "لمبة": "lamp", "لمبات": "lamp", "كشاف": "lamp",
    "شيكارة": "bag", "شكاير": "bag", "كيس": "bag", "اكياس": "bag",
    "استرتش": "stretch film", "شرنك": "shrink film", "كرتون": "carton",
    "أسمنت": "cement", "اسمنت": "cement",
    "قاطع": "breaker", "كونتاكتور": "contactor", "محبس": "valve", "فلانشة": "flange",
    "مضخة": "pump", "طلمبة": "pump", "بكرة": "pulley", "سير": "belt",
    "قفاز": "gloves", "خوذة": "helmet", "صودا": "caustic soda",
}


def normalize_text(text: str) -> str:
    if not text:
        return ""
    value = unicodedata.normalize("NFKC", numbers.normalize_digits(str(text))).lower()
    value = ARABIC_DIACRITICS.sub("", value)
    value = ARABIC_ALEFS.sub("ا", value)
    value = value.replace(ARABIC_TA, "ه").replace(ARABIC_YA, "ي")
    value = re.sub(r"[^\w\u0600-\u06FF\s./-]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def tokens(text: str) -> set[str]:
    return {t for t in normalize_text(text).split() if len(t) > 1}


def trigrams(text: str) -> set[str]:
    value = f"  {normalize_text(text)} "
    return {value[i:i + 3] for i in range(len(value) - 2)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class Candidate:
    taxonomy_code: str
    name: str
    name_ar: str
    confidence: float
    reason: str


class Classifier:
    """Keyword + trigram classifier over a taxonomy list (seeded reference data)."""

    def __init__(self, taxonomy: list[dict], learned_aliases: dict[str, str] | None = None):
        self.taxonomy = taxonomy
        self.by_code = {node["code"]: node for node in taxonomy}
        self.learned_aliases = {normalize_text(k): v for k, v in (learned_aliases or {}).items()}

    def classify(self, description: str, limit: int = 3) -> list[Candidate]:
        normalized = normalize_text(description)
        if not normalized:
            return []
        expanded = normalized
        for alias, canonical in SYNONYMS.items():
            if normalize_text(alias) in expanded:
                expanded = f"{expanded} {canonical}"

        if expanded in self.learned_aliases:
            code = self.learned_aliases[expanded]
            node = self.by_code.get(code)
            if node:
                return [Candidate(code, node["name"], node["name_ar"], 0.98, "learned alias")]

        desc_tokens = tokens(expanded)
        desc_grams = trigrams(expanded)
        results: list[Candidate] = []
        for node in self.taxonomy:
            keywords = [normalize_text(k) for k in node.get("keywords", [])] + [normalize_text(node["name"])]
            score = 0.0
            reason = ""
            for keyword in keywords:
                if not keyword:
                    continue
                if keyword in expanded:
                    score = max(score, 0.92 if len(keyword) > 4 else 0.85)
                    reason = f"keyword match: {keyword}"
                    continue
                kw_tokens = tokens(keyword)
                if kw_tokens and kw_tokens <= desc_tokens:
                    score = max(score, 0.88)
                    reason = f"token match: {keyword}"
                    continue
                sim = jaccard(trigrams(keyword), desc_grams)
                if sim > 0.34:
                    candidate_score = 0.5 + sim * 0.4
                    if candidate_score > score:
                        score = candidate_score
                        reason = f"similarity {sim:.2f} vs {keyword}"
            if score >= 0.45:
                results.append(Candidate(node["code"], node["name"], node["name_ar"], round(score, 3), reason))
        # Prefer the most specific node when scores tie (a child beats its parent).
        results.sort(
            key=lambda cand: (cand.confidence, self.by_code.get(cand.taxonomy_code, {}).get("level", 0)),
            reverse=True,
        )

        # De-duplicate parent/child collisions in favour of the more specific node.
        seen: set[str] = set()
        unique: list[Candidate] = []
        for cand in results:
            node = self.by_code.get(cand.taxonomy_code, {})
            parent = node.get("parent_code")
            names = {normalize_text(c.name) for c in unique}
            if parent and normalize_text(node["name"]) in names:
                continue
            if cand.taxonomy_code in seen:
                continue
            seen.add(cand.taxonomy_code)
            unique.append(cand)
        return unique[:limit]

    def best(self, description: str) -> tuple[tuple[str | None, float], list[Candidate]]:
        cands = self.classify(description)
        if not cands:
            return (None, 0.0), []
        return (cands[0].taxonomy_code, cands[0].confidence), cands


def category_for(taxonomy_code: str | None, categories: list[dict]) -> str | None:
    if not taxonomy_code:
        return None
    for cat in categories:
        if cat["taxonomy_code"] == taxonomy_code:
            return cat["code"]
    prefix = taxonomy_code[:2]
    for cat in categories:
        if (cat.get("taxonomy_code") or "")[:2] == prefix:
            return cat["code"]
    return None


def normalize_line(description: str, quantity, uom: str | None, target_price=None, *,
                   classifier: Classifier, categories: list[dict]) -> dict:
    """Return a normalized PR line with confidence and conversion hints."""
    (code, confidence), candidates = classifier.best(description)
    unit = numbers.normalize_uom(uom)
    node = classifier.by_code.get(code) if code else None
    default_uom = node.get("default_uom") if node else None
    qty = numbers.parse_decimal(quantity) or Decimal("1")
    conversion_note = ""
    if default_uom and default_uom != unit:
        converted = numbers.convert_quantity(qty, unit, default_uom)
        if converted is not None:
            conversion_note = f"{qty} {unit} = {converted} {default_uom}"
    return {
        "description": description.strip(),
        "quantity": qty,
        "uom": unit,
        "taxonomy_code": code,
        "taxonomy_name": node["name"] if node else "",
        "category_code": category_for(code, categories),
        "target_price": numbers.parse_decimal(target_price),
        "confidence": confidence,
        "candidates": [c.__dict__ for c in candidates],
        "needs_review": confidence < 0.75,
        "conversion_note": conversion_note,
    }
