"""Shared service helpers: codes, pagination, notifications, usage metering, FX, billing."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import registry
from app.api.ws import publish
from app.core.config import settings
from app.db.base import utcnow
from app.db.models.entities import (
    FxRate,
    Notification,
    Plan,
    Subscription,
    Tenant,
    UsageEvent,
    User,
)

log = logging.getLogger("procureai.services")


# ------------------------------------------------------------------ codes
async def next_code(session: AsyncSession, model, tenant_id: str, prefix: str) -> str:  # noqa: ANN001
    year = datetime.now(UTC).year
    count = (await session.execute(
        select(func.count()).select_from(model).where(model.tenant_id == tenant_id)
    )).scalar_one()
    return f"{prefix}-{year}-{count + 1:05d}"


def paginate(page: int = 1, page_size: int = 25) -> tuple[int, int]:
    page = max(1, page)
    page_size = max(1, min(page_size, 200))
    return page_size, (page - 1) * page_size


# ------------------------------------------------------------------ notifications
async def lock_for_update(session: AsyncSession, model, row_id: str, *, tenant_id: str | None = None):
    """Re-read one row under a write lock before a read-modify-write on an aggregate.

    Every mutation that decides "may this happen?" by looking at a running total has to serialize
    on the row that owns the total. Without the lock, two requests read the same balance, both pass
    the check and both write — one payment too many, two postings of one entry, an over-receipt that
    is only visible after the fact. On PostgreSQL this is `SELECT ... FOR UPDATE`; on SQLite (test
    only) the clause is dropped, which is why the concurrency proof in `scripts/concurrency_check.py`
    runs against PostgreSQL.
    """
    query = select(model).where(model.id == row_id)
    if tenant_id and hasattr(model, "tenant_id"):
        query = query.where(model.tenant_id == tenant_id)
    statement = query.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(statement)).scalar_one_or_none()


async def notify(
    session: AsyncSession,
    *,
    tenant_id: str,
    user_id: str | None,
    kind: str,
    title: str,
    title_ar: str = "",
    body: str = "",
    link: str = "",
    severity: str = "info",
) -> Notification:
    row = Notification(
        tenant_id=tenant_id, user_id=user_id, kind=kind, title=title, title_ar=title_ar or title,
        body=body, link=link, severity=severity,
    )
    session.add(row)
    await session.flush()
    publish(tenant_id, "notification.created", {
        "id": row.id, "kind": kind, "title": title, "title_ar": row.title_ar,
        "body": body, "link": link, "severity": severity, "created_at": str(row.created_at),
    })
    # Email/WhatsApp fan-out is queued through the outbox (respects quiet hours & preferences).
    return row


async def notify_role(
    session: AsyncSession, *, tenant_id: str, role_code: str, kind: str, title: str,
    title_ar: str = "", body: str = "", link: str = "", severity: str = "info",
) -> list[Notification]:
    users = (await session.execute(
        select(User).where(User.tenant_id == tenant_id, User.is_active.is_(True))
    )).scalars().all()
    out: list[Notification] = []
    for user in users:
        if role_code in (user.roles or []) or role_code == "any":
            out.append(await notify(session, tenant_id=tenant_id, user_id=user.id, kind=kind, title=title,
                                    title_ar=title_ar, body=body, link=link, severity=severity))
    return out


# ------------------------------------------------------------------ metering
async def meter(session: AsyncSession, *, tenant_id: str, kind: str, quantity: float | Decimal = 1,
                ref_type: str | None = None, ref_id: str | None = None, cost_usd: float = 0.0) -> UsageEvent:
    event = UsageEvent(tenant_id=tenant_id, kind=kind, quantity=Decimal(str(quantity)), ref_type=ref_type,
                       ref_id=ref_id, cost_usd=Decimal(str(cost_usd)), occurred_at=utcnow())
    session.add(event)
    await session.flush()
    return event


async def usage_summary(session: AsyncSession, tenant_id: str, period_days: int = 30) -> dict:
    since = utcnow() - timedelta(days=period_days)
    rows = (await session.execute(
        select(UsageEvent.kind, func.coalesce(func.sum(UsageEvent.quantity), 0))
        .where(UsageEvent.tenant_id == tenant_id, UsageEvent.occurred_at >= since)
        .group_by(UsageEvent.kind)
    )).all()
    usage = {kind: float(total) for kind, total in rows}
    tenant = (await session.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one_or_none()
    plan = None
    if tenant:
        plan = (await session.execute(select(Plan).where(Plan.code == tenant.plan_code))).scalar_one_or_none()
    limits = (plan.limits if plan else {}) or {}
    return {
        "usage": usage,
        "limits": limits,
        "plan": tenant.plan_code if tenant else "",
        "percent": {
            key.replace("_per_month", ""): (
                round(usage.get(key.replace("_per_month", ""), 0) / limit * 100, 1) if limit else 0
            )
            for key, limit in limits.items() if isinstance(limit, (int, float)) and limit > 0
        },
    }


async def quota_check(session: AsyncSession, tenant_id: str, kind: str) -> dict:
    """Graceful quota warnings; never hard-blocks an in-flight human action."""
    summary = await usage_summary(session, tenant_id)
    limits = summary["limits"]
    key = {
        "rfq": "rfqs_per_month", "message": "messages_per_month", "llm_token": "llm_tokens_per_month",
        "voice_minute": "voice_minutes_per_month",
    }.get(kind)
    limit = limits.get(key) if key else None
    used = summary["usage"].get(kind, 0)
    if not limit:
        return {"allowed": True, "used": used, "limit": None, "warning": ""}
    remaining = limit - used
    warning = ""
    if remaining < 0:
        warning = f"Plan quota exceeded for {kind} ({used}/{limit}). Upgrade required."
    elif remaining < limit * 0.1:
        warning = f"Plan quota nearly exhausted for {kind} ({used}/{limit})."
    return {"allowed": remaining >= 0, "used": used, "limit": limit, "remaining": remaining, "warning": warning}


# ------------------------------------------------------------------ FX
RATE_CACHE: dict[tuple[str, str, str], Decimal] = {}


async def get_fx_rate(session: AsyncSession, base: str, quote: str, *, tenant_id: str | None = None,
                      as_of: datetime | None = None) -> Decimal:
    base = (base or "EGP").upper()
    quote = (quote or "EGP").upper()
    if base == quote:
        return Decimal("1")
    as_of = as_of or utcnow()
    cached = (await session.execute(
        select(FxRate).where(FxRate.base == base, FxRate.quote == quote,
                             FxRate.as_of <= as_of).order_by(FxRate.as_of.desc()).limit(1)
    )).scalar_one_or_none()
    if cached and (as_of - cached.as_of) < timedelta(days=3):
        return Decimal(cached.rate)
    provider = registry.resolve("fx")
    quote_obj = await provider.rate(base, quote)
    rate = Decimal(quote_obj.rate) if quote_obj and quote_obj.rate else Decimal("0")
    if rate > 0:
        session.add(FxRate(tenant_id=tenant_id, base=base, quote=quote, rate=rate, as_of=as_of,
                           source=quote_obj.source))
        await session.flush()
        return rate
    # Last resort: inverse pair, then static table.
    inverse = (await session.execute(
        select(FxRate).where(FxRate.base == quote, FxRate.quote == base).order_by(FxRate.as_of.desc()).limit(1)
    )).scalar_one_or_none()
    if inverse and Decimal(inverse.rate) > 0:
        return (Decimal("1") / Decimal(inverse.rate)).quantize(Decimal("0.0000000001"))
    static = await registry.resolve("fx").rate(base, quote)
    return Decimal(static.rate) if static else Decimal("1")


async def set_manual_fx(session: AsyncSession, *, tenant_id: str, base: str, quote: str,
                        rate: Decimal, user_id: str | None = None) -> FxRate:
    row = FxRate(tenant_id=tenant_id, base=base.upper(), quote=quote.upper(), rate=Decimal(rate),
                 as_of=utcnow(), source="manual_override")
    session.add(row)
    await session.flush()
    return row


# ------------------------------------------------------------------ billing
async def ensure_subscription(session: AsyncSession, tenant: Tenant) -> Subscription:
    sub = (await session.execute(
        select(Subscription).where(Subscription.tenant_id == tenant.id).order_by(Subscription.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    if sub:
        return sub
    sub = Subscription(tenant_id=tenant.id, plan_code=tenant.plan_code or "growth", status="active", seats=10,
                       period_start=utcnow(), period_end=utcnow() + timedelta(days=30))
    session.add(sub)
    await session.flush()
    return sub


async def plan_for(session: AsyncSession, tenant: Tenant) -> Plan | None:
    return (await session.execute(select(Plan).where(Plan.code == tenant.plan_code))).scalar_one_or_none()


def inbound_address(tenant: Tenant) -> str:
    domain = settings.public_app_url.split("//")[-1].split("/")[0].split(":")[0]
    return f"rfq+{tenant.slug}@{domain}"
