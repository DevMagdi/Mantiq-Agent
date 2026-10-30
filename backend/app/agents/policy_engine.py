"""Deterministic negotiation policy engine — "the LLM proposes, the policy engine disposes".

Pure Python, no I/O, no DB, no LLM. Every outgoing supplier-facing action must obtain an
`allow` decision from `evaluate_outgoing`. Hard blocks are structural: no negotiation tool
exists for accepting, signing, paying or changing specifications (FR-NEG-4).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.services import numbers

# ------------------------------------------------------------------ codes
ACCEPT_INTENT = "BLOCK_ACCEPT_INTENT"
SIGN_INTENT = "BLOCK_SIGN_INTENT"
SPEC_CHANGE = "BLOCK_SPEC_CHANGE"
ROUND_LIMIT = "BLOCK_ROUND_LIMIT"
ABOVE_WALKAWAY = "BLOCK_ABOVE_WALKAWAY"
INVALID_PRICE = "BLOCK_INVALID_PRICE"
CONCESSION_NOT_ALLOWED = "BLOCK_CONCESSION_NOT_ALLOWED"
COMPETITOR_DISCLOSURE = "BLOCK_COMPETITOR_DISCLOSURE"
BLACKLISTED = "BLOCK_SUPPLIER_BLACKLISTED"
SANCTIONED = "BLOCK_SANCTIONS"
NO_OPT_IN = "BLOCK_NO_OPT_IN"
CHANNEL_UNAVAILABLE = "BLOCK_CHANNEL_UNAVAILABLE"
OUTSIDE_HOURS = "BLOCK_OUTSIDE_HOURS"
AUTO_THRESHOLD = "BLOCK_AUTO_VALUE_THRESHOLD"
AUTO_DISABLED = "BLOCK_AUTO_MODE_DISABLED"
WARN_ABOVE_TARGET = "WARN_ABOVE_TARGET"
WARN_APPROVAL_REQUIRED = "WARN_APPROVAL_REQUIRED"
WARN_DRAFT_ONLY = "WARN_DRAFT_ONLY"

FORBIDDEN_INTENTS = {
    "accept": ACCEPT_INTENT,
    "accept_offer": ACCEPT_INTENT,
    "sign": SIGN_INTENT,
    "sign_contract": SIGN_INTENT,
    "pay": SIGN_INTENT,
    "change_spec": SPEC_CHANGE,
    "change_specification": SPEC_CHANGE,
}

ALLOWED_CONCESSIONS = {"volume", "payment_terms", "lead_time", "packaging", "warranty", "delivery_schedule",
                       "freight", "nothing"}
MODES = {"draft_only", "semi_auto", "auto"}


@dataclass(frozen=True)
class PolicySpec:
    target_price: Decimal
    walkaway_price: Decimal
    max_rounds: int = 3
    allowed_concessions: frozenset[str] = frozenset({"nothing"})
    mode: str = "draft_only"
    value_threshold: Decimal = Decimal("0")
    currency: str = "EGP"
    tone: str = "professional"

    @classmethod
    def from_row(cls, row) -> PolicySpec:  # noqa: ANN001 - ORM row duck-typed
        concessions = row.allowed_concessions or ["nothing"]
        return cls(
            target_price=numbers.money(row.target_price),
            walkaway_price=numbers.money(row.walkaway_price),
            max_rounds=int(row.max_rounds or 3),
            allowed_concessions=frozenset(str(c) for c in concessions),
            mode=row.mode if row.mode in MODES else "draft_only",
            value_threshold=numbers.money(row.value_threshold),
            currency=row.currency or "EGP",
            tone=row.tone or "professional",
        )


@dataclass
class Draft:
    body: str = ""
    body_ar: str = ""
    proposed_price: Decimal | None = None
    concessions: list[str] = field(default_factory=list)
    intent: str = "counter_offer"
    channel: str = "email"
    mentions_competitor_data: bool = False
    competitor_references: list[str] = field(default_factory=list)


@dataclass
class PolicyContext:
    round_no: int = 1
    rfq_total_value: Decimal = Decimal("0")
    supplier_status: str = "approved"
    sanctions_status: str = "clear"
    opt_in: bool = True
    channel_available: bool = True
    now: datetime | None = None
    timezone: str = "Africa/Cairo"
    working_hours: tuple[int, int] = (8, 20)
    tenant_max_mode: str = "auto"
    approval_limit: Decimal | None = None
    send_requested: bool = False


@dataclass
class Decision:
    allowed: bool
    action: str  # send | keep_draft | block
    codes: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    proposed_price: Decimal | None = None
    auto_value_ok: bool = True

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "action": self.action,
            "codes": self.codes,
            "reasons": self.reasons,
            "warnings": self.warnings,
            "proposed_price": str(self.proposed_price) if self.proposed_price is not None else None,
            "auto_value_ok": self.auto_value_ok,
        }


MODE_RANK = {"draft_only": 0, "semi_auto": 1, "auto": 2}


def _outside_working_hours(ctx: PolicyContext) -> bool:
    now = ctx.now or datetime.now(ZoneInfo(ctx.timezone))
    if now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo(ctx.timezone))
    local = now.astimezone(ZoneInfo(ctx.timezone))
    start, end = ctx.working_hours
    if end >= 24:
        # `end=24` is "end of day": an explicitly unrestricted window (web forms, test harnesses).
        return False
    return not (time(start, 0) <= local.time() <= time(end, 0))


def evaluate_outgoing(draft: Draft, policy: PolicySpec, ctx: PolicyContext) -> Decision:
    """Validate a draft (or an auto-send request) against the policy. Never raises."""
    blocks: list[str] = []
    reasons: list[str] = []
    warnings: list[str] = []

    def block(code: str, reason: str) -> None:
        blocks.append(code)
        reasons.append(reason)

    # 1) structural intents — the agent may only recommend and negotiate price/terms.
    intent = (draft.intent or "counter_offer").lower()
    if intent in FORBIDDEN_INTENTS:
        block(FORBIDDEN_INTENTS[intent],
              f"Agent may not perform '{intent}': only a human can accept, sign, pay or change specs.")

    # 2) eligibility of the counterparty
    if (ctx.supplier_status or "").lower() == "blacklisted":
        block(BLACKLISTED, "Supplier is blacklisted; a Procurement Manager override with reason is required.")
    if (ctx.sanctions_status or "clear").lower() not in {"clear", "not_flagged"}:
        block(SANCTIONED, "Supplier is flagged by sanctions screening.")

    # 3) channel consent and availability (WhatsApp opt-in, unsubscribe, provider availability)
    if not ctx.channel_available:
        block(CHANNEL_UNAVAILABLE, f"Channel '{draft.channel}' is not configured for this tenant.")
    if not ctx.opt_in and draft.channel in {"whatsapp", "voice"}:
        block(NO_OPT_IN, "No opt-in on record for this channel; use email or the web form instead.")

    # 4) negotiation round cap
    if ctx.round_no > policy.max_rounds:
        block(ROUND_LIMIT, f"Round {ctx.round_no} exceeds the maximum of {policy.max_rounds} rounds.")

    # 5) price envelope — walk-away is absolute, target only produces a warning.
    price = draft.proposed_price
    if price is not None:
        normalized = numbers.money(price)
        if normalized <= 0:
            block(INVALID_PRICE, "Proposed price must be greater than zero.")
        elif policy.walkaway_price > 0 and normalized > policy.walkaway_price:
            block(ABOVE_WALKAWAY,
                  f"Proposed price {normalized} exceeds the walk-away price {policy.walkaway_price}.")
        elif policy.target_price > 0 and normalized > policy.target_price:
            warnings.append(WARN_ABOVE_TARGET)
            reasons.append(f"Proposed price {normalized} is above the target {policy.target_price}.")

    # 6) concessions must be pre-authorised
    for concession in draft.concessions:
        key = str(concession).lower().strip()
        if key and key not in policy.allowed_concessions and "nothing" not in policy.allowed_concessions:
            block(CONCESSION_NOT_ALLOWED, f"Concession '{concession}' is not allowed by this policy.")

    # 7) competitor confidentiality (defence in depth on top of redaction)
    if draft.mentions_competitor_data or draft.competitor_references:
        block(COMPETITOR_DISCLOSURE, "Draft appears to reference competitor names or exact prices.")

    # 8) mode and value thresholds
    mode = policy.mode if policy.mode in MODES else "draft_only"
    if MODE_RANK.get(mode, 0) > MODE_RANK.get(ctx.tenant_max_mode, 2):
        mode = ctx.tenant_max_mode
    auto_value_ok = True
    if mode in {"semi_auto", "auto"}:
        threshold = policy.value_threshold
        value = numbers.money(ctx.rfq_total_value)
        if mode == "auto" and (threshold <= 0 or value > threshold):
            auto_value_ok = False
            if ctx.send_requested:
                block(AUTO_THRESHOLD,
                      f"Auto mode requires the RFQ value ({value}) to be at or below the auto threshold "
                      f"({threshold}); human send required.")
        if ctx.tenant_max_mode == "draft_only" and ctx.send_requested:
            block(AUTO_DISABLED, "Autonomous sending is disabled at tenant level.")

    # 9) working hours for autonomous sending only
    if ctx.send_requested and mode != "draft_only" and _outside_working_hours(ctx):
        block(OUTSIDE_HOURS, "Outside the configured supplier contact hours; the message will be queued.")

    # 10) approvals — a message is allowed but the resulting award needs approval
    if ctx.approval_limit is not None and ctx.rfq_total_value > ctx.approval_limit:
        warnings.append(WARN_APPROVAL_REQUIRED)
        reasons.append("Award value exceeds the sender's approval limit; approval chain required before PO.")

    if blocks:
        return Decision(False, "block", codes=blocks, reasons=reasons, warnings=warnings,
                        proposed_price=numbers.money(price) if price is not None else None,
                        auto_value_ok=auto_value_ok)

    if ctx.send_requested and mode == "draft_only":
        warnings.append(WARN_DRAFT_ONLY)
        return Decision(True, "keep_draft", codes=[], reasons=reasons, warnings=warnings,
                        proposed_price=numbers.money(price) if price is not None else None)

    action = "send" if ctx.send_requested else "draft_ok"
    return Decision(True, action, codes=[], reasons=reasons, warnings=warnings,
                    proposed_price=numbers.money(price) if price is not None else None)


def can_send_without_human(policy: PolicySpec, ctx: PolicyContext) -> bool:
    """True only for the bounded auto mode: below threshold, within rounds, on-policy."""
    if policy.mode != "auto":
        return False
    if ctx.rfq_total_value > policy.value_threshold:
        return False
    if ctx.round_no > policy.max_rounds:
        return False
    return ctx.supplier_status.lower() != "blacklisted" and (ctx.sanctions_status or "clear") == "clear"


def check_dispatch(
    *,
    policy: PolicySpec | None,
    sent_today: int,
    daily_cap: int,
    channel: str,
    opt_in: bool,
    blacklisted: bool,
    sanctioned: bool,
    now: datetime | None = None,
    timezone: str = "Africa/Cairo",
    working_hours: tuple[int, int] = (7, 21),
) -> Decision:
    """Gate for RFQ invitations / reminders (rate limits, consent, blocks)."""
    blocks: list[str] = []
    reasons: list[str] = []
    if blacklisted:
        blocks.append(BLACKLISTED)
        reasons.append("Supplier is blacklisted.")
    if sanctioned:
        blocks.append(SANCTIONED)
        reasons.append("Supplier is flagged by sanctions screening.")
    if channel in {"whatsapp", "voice"} and not opt_in:
        blocks.append(NO_OPT_IN)
        reasons.append("No opt-in on record for this channel.")
    if sent_today >= daily_cap:
        blocks.append("BLOCK_DAILY_CAP")
        reasons.append(f"Daily send cap reached ({sent_today}/{daily_cap}).")
    ctx = PolicyContext(now=now, timezone=timezone, working_hours=working_hours)
    if _outside_working_hours(ctx):
        blocks.append(OUTSIDE_HOURS)
        reasons.append("Outside supplier contact hours; queued for the next window.")
    if blocks:
        return Decision(False, "block", codes=blocks, reasons=reasons)
    return Decision(True, "send")
