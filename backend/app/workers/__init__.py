"""Background workers: Celery application and scheduled procurement jobs."""
from app.workers.celery_app import celery_app

__all__ = ["celery_app"]
