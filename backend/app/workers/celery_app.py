"""Celery application and beat schedule.

The broker is Redis (``REDIS_URL``). Tasks are deliberately thin: each one opens a session,
calls a service function and commits. That keeps every scheduled behaviour testable without a
broker (``task_always_eager``) and keeps business rules out of the worker layer.
"""
from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "procureai",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_time_limit=600,
    task_soft_time_limit=540,
    result_expires=86400,
    beat_schedule={
        # Reminder cadence and auto-close of expired RFQs.
        "rfq-reminders": {"task": "procureai.rfq_reminders", "schedule": crontab(minute=0, hour="*/2")},
        # Approval SLA escalation (SLA hours are stored per approval step).
        "approval-escalations": {"task": "procureai.escalate_approvals",
                                 "schedule": crontab(minute="*/30")},
        # Daily retention purge + due erasure requests.
        "retention-purge": {"task": "procureai.retention_purge", "schedule": crontab(minute=30, hour=2)},
        # Daily FX snapshot feeding landed-cost maths.
        "fx-refresh": {"task": "procureai.fx_refresh", "schedule": crontab(minute=10, hour=3)},
        # Discovery jobs requested but not started (agent tool follow-up).
        "discovery-sweep": {"task": "procureai.discovery_sweep", "schedule": crontab(minute="*/20")},
    },
)
