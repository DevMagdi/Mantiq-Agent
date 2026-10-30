"""Programmatic Alembic upgrade used at API startup (dev convenience, prod uses the job)."""
from __future__ import annotations

import logging

from alembic import command
from alembic.config import Config

from app.core.config import BACKEND_DIR, settings

log = logging.getLogger("procureai.migrations")


def alembic_config() -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    return config


def upgrade_sync(revision: str = "head") -> None:
    command.upgrade(alembic_config(), revision)


async def run_migrations(revision: str = "head") -> None:
    """Run migrations in a worker thread (Alembic is synchronous)."""
    import asyncio

    sqlite_fallback = settings.database_url.startswith("sqlite")
    if sqlite_fallback and settings.app_env == "test":
        return
    try:
        await asyncio.to_thread(upgrade_sync, revision)
        log.info("migrations applied up to %s", revision)
    except Exception as exc:  # pragma: no cover - surfaced in logs, not fatal for liveness
        # `metadata.create_all` skips raw-SQL extras (RLS policies, extensions, triggers), so it is
        # only ever a sandbox convenience on the documented SQLite fallback.
        if settings.app_env != "prod" and (not sqlite_fallback or settings.allow_sqlite_fallback):
            log.warning("alembic upgrade failed (%s); falling back to metadata create for dev", exc)
            from app.db.session import create_schema_dev_only

            await create_schema_dev_only()
        else:
            raise
