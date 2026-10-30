"""Scheduled procurement jobs.

Every task is idempotent and safe to re-run: they query for work that is due, act, and return a
small JSON-serialisable summary that shows up in the Celery result backend and in the logs.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select

from app.db.models.entities import DiscoveryJob, Tenant
from app.db.session import SessionLocal
from app.workers.celery_app import celery_app

log = logging.getLogger("procureai.workers")


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _tenants(session) -> list[Tenant]:
    return list((await session.execute(select(Tenant).where(Tenant.status == "active"))).scalars().all())


@celery_app.task(name="procureai.rfq_reminders")
def rfq_reminders() -> dict[str, Any]:
    """Send the reminder cadence for every tenant and auto-close expired RFQs."""

    async def _inner() -> dict[str, Any]:
        from app.services.rfq import send_reminders

        totals = {"tenants": 0, "reminders": 0, "closed": 0}
        async with SessionLocal() as session:
            for tenant in await _tenants(session):
                result = await send_reminders(session, tenant)
                totals["tenants"] += 1
                totals["reminders"] += int(result.get("reminders_sent", 0))
                totals["closed"] += int(result.get("rfqs_closed", 0))
            await session.commit()
        return totals

    summary = _run(_inner())
    log.info("rfq_reminders %s", summary)
    return summary


@celery_app.task(name="procureai.escalate_approvals")
def escalate_approvals() -> dict[str, Any]:
    """Escalate approvals whose SLA has expired and reschedule them."""

    async def _inner() -> dict[str, Any]:
        from app.services.approval import escalate_overdue

        escalated = 0
        async with SessionLocal() as session:
            for tenant in await _tenants(session):
                escalated += await escalate_overdue(session, tenant)
            await session.commit()
        return {"escalated": escalated}

    summary = _run(_inner())
    log.info("escalate_approvals %s", summary)
    return summary


@celery_app.task(name="procureai.retention_purge")
def retention_purge() -> dict[str, Any]:
    """Apply retention windows and execute due erasure requests."""

    async def _inner() -> dict[str, Any]:
        from app.services.billing_helpers import purge_expired

        totals = {"notifications_purged": 0, "erasure_records": 0}
        async with SessionLocal() as session:
            for tenant in await _tenants(session):
                result = await purge_expired(session, tenant)
                totals["notifications_purged"] += int(result.get("notifications_purged", 0))
                totals["erasure_records"] += int(result.get("erasure_records", 0))
            await session.commit()
        return totals

    summary = _run(_inner())
    log.info("retention_purge %s", summary)
    return summary


@celery_app.task(name="procureai.fx_refresh")
def fx_refresh() -> dict[str, Any]:
    """Pull the FX table from the configured provider (static in sandbox mode)."""

    async def _inner() -> dict[str, Any]:
        from app.adapters.base import registry

        provider = registry.resolve("fx")
        quotes: dict[str, str] = {}
        for quote in ("EGP", "SAR", "AED", "EUR", "GBP"):
            rate = await provider.rate("USD", quote)
            if rate:
                quotes[quote] = str(rate.rate)
        return {"provider": getattr(provider, "name", "unknown"), "quotes": quotes}

    summary = _run(_inner())
    log.info("fx_refresh %s", summary)
    return summary


@celery_app.task(name="procureai.discovery_sweep")
def discovery_sweep() -> dict[str, Any]:
    """Restart discovery jobs left running by a crashed worker."""

    async def _inner() -> dict[str, Any]:
        from datetime import UTC, datetime, timedelta

        stale_before = datetime.now(UTC) - timedelta(minutes=30)
        restarted = 0
        async with SessionLocal() as session:
            rows = list((await session.execute(
                select(DiscoveryJob).where(DiscoveryJob.status == "running",
                                           DiscoveryJob.created_at < stale_before)
            )).scalars().all())
            for job in rows:
                job.status = "failed"
                job.error = job.error or "worker restart: job exceeded 30 minutes"
                job.finished_at = datetime.now(UTC)
                restarted += 1
            await session.commit()
        return {"marked_failed": restarted}

    summary = _run(_inner())
    log.info("discovery_sweep %s", summary)
    return summary
