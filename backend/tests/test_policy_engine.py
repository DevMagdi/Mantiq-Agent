"""The deterministic guard: 'LLM proposes, policy engine disposes' (FR-NEG-4, FR-NEG-5, NFR-SAFE-1).

No negotiation message can leave the platform without passing these rules, and none of the
rules depend on model output.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.agents import policy_engine as pe

POLICY = pe.PolicySpec(
    target_price=Decimal("20000"), walkaway_price=Decimal("22500"), max_rounds=3,
    allowed_concessions=frozenset({"payment_terms", "lead_time"}), mode="semi_auto",
    value_threshold=Decimal("250000"), currency="EGP",
)
CTX = pe.PolicyContext(round_no=1, rfq_total_value=Decimal("100000"), supplier_status="approved",
                       sanctions_status="clear", opt_in=True, channel_available=True,
                       now=datetime(2026, 10, 29, 11, 0, tzinfo=UTC), timezone="Africa/Cairo",
                       working_hours=(8, 20), tenant_max_mode="auto", send_requested=True)
DRAFT = pe.Draft(body="Can you improve to 20,500 EGP?", proposed_price=Decimal("20500"))


def test_clean_counter_offer_is_allowed() -> None:
    decision = pe.evaluate_outgoing(DRAFT, POLICY, CTX)
    assert decision.allowed is True
    assert decision.codes == []


@pytest.mark.parametrize(
    "intent,code",
    [
        ("accept", pe.ACCEPT_INTENT),
        ("sign", pe.SIGN_INTENT),
        ("change_spec", pe.SPEC_CHANGE),
        ("pay", pe.SIGN_INTENT),
        ("change_specification", pe.SPEC_CHANGE),
    ],
)
def test_forbidden_intents_are_always_blocked(intent: str, code: str) -> None:
    decision = pe.evaluate_outgoing(pe.Draft(intent=intent, proposed_price=Decimal("19000")), POLICY, CTX)
    assert decision.allowed is False
    assert code in decision.codes


def test_blacklisted_and_sanctioned_suppliers_are_blocked() -> None:
    blacklisted = pe.evaluate_outgoing(DRAFT, POLICY, pe.PolicyContext(supplier_status="blacklisted"))
    assert blacklisted.allowed is False and pe.BLACKLISTED in blacklisted.codes
    sanctioned = pe.evaluate_outgoing(DRAFT, POLICY, pe.PolicyContext(sanctions_status="flagged"))
    assert sanctioned.allowed is False and pe.SANCTIONED in sanctioned.codes


def test_price_above_walkaway_is_blocked() -> None:
    decision = pe.evaluate_outgoing(pe.Draft(proposed_price=Decimal("23000")), POLICY, CTX)
    assert decision.allowed is False
    assert pe.ABOVE_WALKAWAY in decision.codes


def test_round_limit_is_enforced() -> None:
    decision = pe.evaluate_outgoing(DRAFT, POLICY, pe.PolicyContext(round_no=4))
    assert decision.allowed is False
    assert pe.ROUND_LIMIT in decision.codes


def test_concession_outside_policy_is_blocked() -> None:
    decision = pe.evaluate_outgoing(pe.Draft(proposed_price=Decimal("20500"), concessions=["volume_discount"]),
                                    POLICY, CTX)
    assert decision.allowed is False
    assert pe.CONCESSION_NOT_ALLOWED in decision.codes


def test_competitor_disclosure_is_blocked() -> None:
    decision = pe.evaluate_outgoing(pe.Draft(proposed_price=Decimal("20500"),
                                             mentions_competitor_data=True,
                                             competitor_references=["Ezz Steel offered 19,000"]), POLICY, CTX)
    assert decision.allowed is False
    assert pe.COMPETITOR_DISCLOSURE in decision.codes


def test_whatsapp_without_opt_in_is_blocked() -> None:
    decision = pe.evaluate_outgoing(pe.Draft(proposed_price=Decimal("20500"), channel="whatsapp"),
                                    POLICY, pe.PolicyContext(opt_in=False))
    assert decision.allowed is False
    assert pe.NO_OPT_IN in decision.codes


def test_outside_working_hours_is_blocked() -> None:
    night = pe.PolicyContext(now=datetime(2026, 10, 29, 2, 0, tzinfo=UTC), timezone="Africa/Cairo",
                             working_hours=(8, 20), send_requested=True)
    decision = pe.evaluate_outgoing(DRAFT, POLICY, night)
    assert decision.allowed is False
    assert pe.OUTSIDE_HOURS in decision.codes


def test_draft_only_mode_never_sends() -> None:
    policy = pe.PolicySpec(target_price=Decimal("20000"), walkaway_price=Decimal("22500"), mode="draft_only")
    decision = pe.evaluate_outgoing(DRAFT, policy, CTX)
    assert decision.action != "send"


def test_auto_mode_respects_value_threshold() -> None:
    policy = pe.PolicySpec(target_price=Decimal("20000"), walkaway_price=Decimal("22500"), mode="auto",
                           value_threshold=Decimal("50000"))
    big = pe.PolicyContext(rfq_total_value=Decimal("90000"), tenant_max_mode="auto", send_requested=True)
    decision = pe.evaluate_outgoing(pe.Draft(proposed_price=Decimal("20500")), policy, big)
    assert decision.allowed is False
    assert pe.AUTO_THRESHOLD in decision.codes


def test_dispatch_gate_blocks_blacklist_and_daily_cap() -> None:
    blocked = pe.check_dispatch(policy=POLICY, sent_today=0, daily_cap=3, channel="email", opt_in=True,
                                blacklisted=True, sanctioned=False,
                                now=datetime(2026, 10, 29, 11, 0, tzinfo=UTC))
    assert blocked.allowed is False and pe.BLACKLISTED in blocked.codes
    capped = pe.check_dispatch(policy=POLICY, sent_today=3, daily_cap=3, channel="email", opt_in=True,
                               blacklisted=False, sanctioned=False,
                               now=datetime(2026, 10, 29, 11, 0, tzinfo=UTC))
    assert capped.allowed is False
    ok = pe.check_dispatch(policy=POLICY, sent_today=0, daily_cap=3, channel="email", opt_in=True,
                           blacklisted=False, sanctioned=False,
                           now=datetime(2026, 10, 29, 11, 0, tzinfo=UTC))
    assert ok.allowed is True


def test_drafting_stays_allowed_outside_hours_but_sending_does_not() -> None:
    night = pe.PolicyContext(now=datetime(2026, 10, 29, 2, 0, tzinfo=UTC), timezone="Africa/Cairo",
                             working_hours=(8, 20), send_requested=False)
    decision = pe.evaluate_outgoing(DRAFT, POLICY, night)
    assert decision.allowed is True
    assert decision.action == "draft_ok"


def test_a_full_day_window_is_open_even_in_the_last_hour() -> None:
    """Regression: an "all day" window of 0..23 still closed at 23:00 local, so the suite failed
    only when CI happened to run after 23:00 Cairo. 24 must mean end-of-day."""
    from datetime import datetime

    late = pe.PolicyContext(now=datetime(2026, 10, 29, 23, 45, tzinfo=UTC), timezone="Africa/Cairo",
                            working_hours=(0, 24), send_requested=True)
    assert pe.evaluate_outgoing(DRAFT, POLICY, late).allowed is True

    # A real window is still enforced for the same clock.
    night = pe.PolicyContext(now=datetime(2026, 10, 29, 23, 45, tzinfo=UTC), timezone="Africa/Cairo",
                             working_hours=(8, 20), send_requested=True)
    decision = pe.evaluate_outgoing(DRAFT, POLICY, night)
    assert decision.allowed is False
    assert pe.OUTSIDE_HOURS in decision.codes
