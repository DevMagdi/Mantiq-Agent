"""Deterministic item normalisation: AR/EN text -> UNSPSC-style code + UoM conversion (FR-IN-4)."""
from __future__ import annotations

import pytest

from app.services.catalog_data import TAXONOMY
from app.services.taxonomy import Classifier, normalize_text, tokens

CLASSIFIER = Classifier(TAXONOMY)


@pytest.mark.parametrize(
    "description,expected",
    [
        ("Rebar 12mm B420D deformed steel bars", "30102400"),
        ("حديد تسليح 16 مم", "30102400"),
        ("Stretch film 500mm 23 micron", "24112000"),
        ("شكاير بلاستيك 50 كجم", "24121700"),
        ("Gear oil EP 80W-90 20L", "12180000"),
        ("زيت هيدروليك 68", "12180000"),
        ("Bearing SKF 6205-2RS", "31171500"),
        ("رولمان بلي 6305", "31171500"),
        ("MCB 32A 3-pole Schneider", "39121600"),
        ("كابل نحاس 3x2.5", "39112000"),
        ("Grease lithium EP2", "12180000"),
    ],
)
def test_classifier_maps_descriptions(description: str, expected: str) -> None:
    (code, confidence), _candidates = CLASSIFIER.best(description)
    assert code == expected, f"{description!r} -> {code} ({confidence})"
    assert confidence >= 0.75


def test_unknown_description_is_low_confidence_and_needs_review() -> None:
    (_code, confidence), _candidates = CLASSIFIER.best("Unobtainium quantum widget model ZX-9000")
    assert confidence < 0.75
    candidates = CLASSIFIER.classify("Unobtainium quantum widget model ZX-9000")
    assert len(candidates) <= 3


def test_normalize_text_strips_arabic_diacritics_and_alef_variants() -> None:
    assert normalize_text("أَحْمَال") == normalize_text("احمال")
    assert normalize_text("  Steel   Bars ") == "steel bars"


def test_tokens_are_deduplicated() -> None:
    assert tokens("steel steel bars") == {"steel", "bars"}
