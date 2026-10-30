"""Async engine/session with PostgreSQL tenant RLS context."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import Base

_is_sqlite = settings.database_url.startswith("sqlite")
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
)

if _is_sqlite:
    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def session_scope(tenant_id: str | None = None) -> AsyncIterator[AsyncSession]:
    """Open a session, applying the tenant context for Postgres RLS."""
    async with SessionLocal() as session:
        if tenant_id and not _is_sqlite and settings.tenant_rls_enabled:
            await session.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id})
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency; tenant context is bound in `get_current_user`."""
    async with SessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


@event.listens_for(Session, "after_begin")
def _apply_tenant_context(session, transaction, connection):  # noqa: ANN001
    """Re-assert the tenant GUC on every transaction so RLS survives commits."""
    if _is_sqlite or not settings.tenant_rls_enabled:
        return
    tenant_id = session.info.get("tenant_id")
    if tenant_id:
        connection.exec_driver_sql("SELECT set_config('app.tenant_id', %(tid)s, true)", {"tid": tenant_id})


async def create_schema_dev_only() -> None:
    """Dev/test convenience only. Production schema is Alembic (see migrations/)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
