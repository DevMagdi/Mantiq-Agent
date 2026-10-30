"""Celery task contracts (NFR-OPS-2).

The broker is not available in this environment, so every task is exercised in eager mode against
the real database. That still proves the part that matters: the task opens a session, calls the
service, commits, and returns a JSON-serialisable summary.
"""
from __future__ import annotations

from app.workers import tasks
from app.workers.celery_app import celery_app


def test_beat_schedule_wires_every_operational_job():
    schedule = celery_app.conf.beat_schedule
    assert {"rfq-reminders", "approval-escalations", "retention-purge", "fx-refresh",
            "discovery-sweep"} <= set(schedule)
    registered = {name for name in celery_app.tasks if name.startswith("procureai.")}
    assert registered == {entry["task"] for entry in schedule.values()}, \
        "every scheduled entry must point at a registered task"


def test_tasks_are_byte_string_serialisable_and_named():
    for task in (tasks.rfq_reminders, tasks.escalate_approvals, tasks.retention_purge,
                 tasks.fx_refresh, tasks.discovery_sweep):
        assert task.name.startswith("procureai.")
        assert task.serializer == "json"


def test_rfq_reminders_runs_and_reports_counts():
    summary = tasks.rfq_reminders.apply().get()
    assert set(summary) == {"tenants", "reminders", "closed"}
    assert summary["tenants"] >= 1
    assert summary["reminders"] >= 0


def test_approval_escalation_runs():
    summary = tasks.escalate_approvals.apply().get()
    assert summary["escalated"] >= 0


def test_retention_purge_runs():
    summary = tasks.retention_purge.apply().get()
    assert set(summary) == {"notifications_purged", "erasure_records"}


def test_fx_refresh_returns_a_quote_per_supported_currency():
    summary = tasks.fx_refresh.apply().get()
    assert summary["provider"] in {"static", "fx"}
    assert summary["quotes"]["EGP"]
    assert float(summary["quotes"]["EGP"]) > 0


def test_discovery_sweep_marks_stale_jobs_failed():
    import asyncio
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.db.models.entities import DiscoveryJob, Tenant
    from app.db.session import SessionLocal

    async def _seed() -> str:
        async with SessionLocal() as session:
            tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
            job = DiscoveryJob(tenant_id=tenant.id, query="stale sweep probe", country="EG",
                               provider="fake", status="running", max_results=5,
                               created_at=datetime.now(UTC) - timedelta(hours=2))
            session.add(job)
            await session.commit()
            return job.id

    job_id = asyncio.run(_seed())
    summary = tasks.discovery_sweep.apply().get()
    assert summary["marked_failed"] >= 1

    async def _read() -> str:
        async with SessionLocal() as session:
            job = (await session.execute(select(DiscoveryJob).where(DiscoveryJob.id == job_id))).scalar_one()
            return job.status

    assert asyncio.run(_read()) == "failed"
