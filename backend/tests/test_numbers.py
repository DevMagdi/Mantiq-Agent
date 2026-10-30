"""Decimal-only money math, Arabic digit parsing, FX and TCO (FR-EX-2, FR-CMP-3, NFR-DATA-1)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.services import numbers


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("21,500.50", "21500.50"),
        ("٢١٬٥٠٠٫٥", "21500.5"),
        ("2500 kg", "2500"),
        ("EGP 1,234.5/ton", "1234.5"),
        ("٢٥٠٠ كجم", "2500"),
        ("", None),
        ("no digits here", None),
        ("1.234.567", "1234567"),
        ("21.500,50", "21500.50"),
        ("٣٧٪ خصم", "37"),
    ],
)
def test_parse_decimal(raw: str, expected: str | None) -> None:
    value = numbers.parse_decimal(raw)
    assert (None if expected is None else Decimal(expected)) == value


@pytest.mark.parametrize(
    "text,currency",
    [
        ("السعر ٢١٥٠٠ جنيه", "EGP"),
        ("USD 1,200", "USD"),
        ("price in SAR", "SAR"),
        ("€ 900", "EUR"),
        ("سعر 5000 درهم", "AED"),
        ("no currency", "EGP"),
    ],
)
def test_detect_currency(text: str, currency: str) -> None:
    assert numbers.detect_currency(text) == currency


def test_unit_conversion_and_guards() -> None:
    assert numbers.convert_quantity(Decimal("1"), "t", "kg") == Decimal("1000")
    assert numbers.convert_quantity(Decimal("500"), "kg", "t") == Decimal("0.5")
    assert numbers.convert_quantity(Decimal("1"), "t", "usd") is None
    assert numbers.convert_price(Decimal("21500"), "t", "kg") == Decimal("21.5")


def test_fx_conversion_rounds_half_up() -> None:
    assert numbers.fx_convert(Decimal("100"), Decimal("48.5")) == Decimal("4850.0000")
    assert numbers.fx_convert(Decimal("1"), Decimal("1")) == Decimal("1.0000")


def test_tco_includes_landed_cost_and_cost_of_capital() -> None:
    result = numbers.compute_tco(
        Decimal("100000"), fx_rate=Decimal("1"),
        landed_components=[("freight", Decimal("3000")), ("duty", Decimal("5000"))],
        lead_time_days=30,
    )
    assert result["subtotal"] == Decimal("100000.0000")
    assert result["landed_cost_base"] == Decimal("8000.0000")
    # 18% annual cost of capital for 30 days on 100,000 = 1479.45
    assert result["cost_of_capital"] == Decimal("1479.4521")
    assert result["tco_total"] == Decimal("109479.4521")


def test_weighted_scores_normalise_price_and_lead_time() -> None:
    offers = [
        {"id": "a", "tco_total": Decimal("100000"), "lead_time_days": 30},
        {"id": "b", "tco_total": Decimal("120000"), "lead_time_days": 10},
    ]
    scored = numbers.weighted_scores(offers, {"price": Decimal("0.7"), "lead_time": Decimal("0.3")})
    by_id = {row["id"]: row for row in scored}
    assert by_id["b"]["weighted_score"] > by_id["a"]["weighted_score"]
    assert by_id["a"]["score_breakdown"]["price"] == 100.0
    assert by_id["b"]["score_breakdown"]["price"] == 83.3
    assert by_id["b"]["score_breakdown"]["lead_time"] == 100.0


def test_savings_metrics() -> None:
    result = numbers.savings(Decimal("120000"), Decimal("96000"))
    assert result["savings"] == Decimal("24000.0000")
    assert result["savings_pct"] == 20.0
    assert numbers.savings(Decimal("0"), Decimal("10"))["savings_pct"] == 0.0


def test_money_and_display_formatting() -> None:
    assert numbers.money(Decimal("10.005")) == Decimal("10.0050")
    assert numbers.fmt_amount(Decimal("1234567.5"), "EGP", "en") == "EGP 1,234,567.50"
    assert numbers.fmt_amount(Decimal("1234567.5"), "KWD", "ar") == "1,234,567.500 KWD"
