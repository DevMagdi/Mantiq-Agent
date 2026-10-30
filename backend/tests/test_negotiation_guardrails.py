"""Sixty-case negotiation guardrail suite (FR-NEG-4/5, AC-NEG-3, NFR-SAFE-1).

Each case is generated from independent parameters, and the expected outcome is computed by a
separate reference implementation of the policy rules. The engine must agree on every case —
including the ones where it is allowed to proceed.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.agents import policy_engine as pe

TARGET = Decimal("20000")
WALKWAY = Decimal("22000")
THRESHOLD = Decimal("250000")


@dataclass
class Scenario:
    name: str
    mode: str
    send: bool
    price: Decimal
    round_no: int
    concessions: list[str]
    allowed_concessions: list[str]
    opt_in: bool
    channel: str
    status: str
    sanctions: str
    value: Decimal
    competitor: bool


def _reference_blocks(case: Scenario) -> set[str]:
    """Independent oracle: the same rules written as plain conditions."""
    blocks: set[str] = set()
    if case.price <= 0:
        blocks.add(pe.INVALID_PRICE)
    if case.price > WALKWAY:
        blocks.add(pe.ABOVE_WALKAWAY)
    if case.round_no > 3:
        blocks.add(pe.ROUND_LIMIT)
    if case.status == "blacklisted":
        blocks.add(pe.BLACKLISTED)
    if case.sanctions != "clear":
        blocks.add(pe.SANCTIONED)
    if not case.opt_in and case.channel in {"whatsapp", "voice"}:
        blocks.add(pe.NO_OPT_IN)
    for concession in case.concessions:
        if concession not in case.allowed_concessions and "nothing" not in case.allowed_concessions:
            blocks.add(pe.CONCESSION_NOT_ALLOWED)
    if case.competitor:
        blocks.add(pe.COMPETITOR_DISCLOSURE)
    if case.send and case.mode == "auto" and case.value > THRESHOLD:
        blocks.add(pe.AUTO_THRESHOLD)
    return blocks


def _scenarios() -> list[Scenario]:
    prices = [Decimal("19000"), Decimal("21000"), Decimal("23500"), Decimal("0")]
    cases: list[Scenario] = []
    grid = itertools.product(
        ["draft_only", "semi_auto", "auto"],  # mode
        [True, False],                        # send requested
        prices,                               # proposed price
        [1, 4],                               # negotiation round
        [["payment_terms"], ["volume_discount"], []],  # concessions
        [True, False],                        # opt-in recorded
        ["email", "whatsapp"],                # channel
        ["approved", "blacklisted"],          # supplier status
        ["clear", "flagged"],                 # sanctions
    )
    for index, (mode, send, price, round_no, concessions, opt_in, channel, status, sanctions) in \
            enumerate(itertools.islice(grid, 60)):
        cases.append(Scenario(
            name=f"case-{index:02d}",
            mode=mode, send=send, price=price, round_no=round_no,
            concessions=list(concessions), allowed_concessions=["payment_terms", "lead_time"],
            opt_in=opt_in, channel=channel, status=status, sanctions=sanctions,
            value=Decimal("300000") if index % 2 else Decimal("100000"),
            competitor=index % 7 == 0,
        ))
    return cases


SCENARIOS = _scenarios()


def _run(case: Scenario) -> pe.Decision:
    policy = pe.PolicySpec(target_price=TARGET, walkaway_price=WALKWAY, max_rounds=3,
                           allowed_concessions=frozenset(case.allowed_concessions),
                           mode=case.mode, value_threshold=THRESHOLD, currency="EGP")
    draft = pe.Draft(body="counter", proposed_price=case.price, concessions=case.concessions,
                     channel=case.channel, mentions_competitor_data=case.competitor)
    ctx = pe.PolicyContext(round_no=case.round_no, rfq_total_value=case.value,
                           supplier_status=case.status, sanctions_status=case.sanctions,
                           opt_in=case.opt_in, channel_available=True, send_requested=case.send,
                           now=datetime(2026, 10, 29, 11, 0, tzinfo=UTC), timezone="Africa/Cairo",
                           working_hours=(8, 20), tenant_max_mode="auto")
    return pe.evaluate_outgoing(draft, policy, ctx)


def test_suite_size() -> None:
    assert len(SCENARIOS) == 60


@pytest.mark.parametrize("case", SCENARIOS, ids=[c.name for c in SCENARIOS])
def test_engine_matches_reference_rules(case: Scenario) -> None:
    decision = _run(case)
    expected = _reference_blocks(case)
    assert set(decision.codes) == expected, (
        f"{case.name}: expected {sorted(expected)} got {sorted(decision.codes)} ({case})"
    )
    assert decision.allowed is (not expected)


@pytest.mark.parametrize("case", SCENARIOS, ids=[c.name for c in SCENARIOS])
def test_no_message_is_sent_without_human_when_not_auto(case: Scenario) -> None:
    decision = _run(case)
    if decision.action == "send":
        assert case.mode == "auto" and not decision.codes
        assert case.value <= THRESHOLD


def test_every_case_has_a_defensible_reason_when_blocked() -> None:
    for case in SCENARIOS:
        decision = _run(case)
        if decision.codes:
            assert decision.reasons, case.name
            assert decision.allowed is False
