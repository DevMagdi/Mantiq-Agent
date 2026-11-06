"""extensions and base helpers

Revision ID: 0001_extensions_and_base
Revises: None
Create Date: 2026-10-30 00:00:00
"""
from __future__ import annotations

import logging
import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0001_extensions_and_base'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.env")

#: Extensions the SRS demands (PostgreSQL). The vector extension carries the embedding columns;
#: pg_trgm powers the fuzzy-search GIN indexes; pgcrypto and citext are required by policy even
#: though no column uses them today. A deployment missing any of them fails the migration, unless an
#: operator explicitly signs off on the reduced guarantees — see docs/RUNBOOK.md.
REQUIRED_EXTENSIONS = ("pgcrypto", "citext", "pg_trgm", "vector")


def missing_extensions(bind) -> list[str]:
    rows = bind.execute(sa.text("SELECT name FROM pg_available_extensions")).fetchall()
    available = {row[0] for row in rows}
    return [ext for ext in REQUIRED_EXTENSIONS if ext not in available]


def allow_missing() -> bool:
    return os.environ.get("MIGRATION_ALLOW_MISSING_EXTENSIONS", "").strip().lower() in {"1", "true", "yes"}


def upgrade() -> None:
    # Extensions required by the SRS (PostgreSQL). SQLite is supported for dev/test only.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        missing = missing_extensions(bind)
        if missing and not allow_missing():
            raise RuntimeError(
                f"PostgreSQL is missing required extensions: {', '.join(missing)}. "
                "Install postgresql-contrib and pgvector, or set MIGRATION_ALLOW_MISSING_EXTENSIONS=1 "
                "to explicitly accept the reduced guarantees (no pg_trgm fuzzy indexes; pgcrypto/citext "
                "remain unused today)."
            )
        rows = bind.execute(sa.text("SELECT name FROM pg_available_extensions")).fetchall()
        available = {row[0] for row in rows}
        for ext in REQUIRED_EXTENSIONS:
            if ext in available:
                op.execute(f"CREATE EXTENSION IF NOT EXISTS {ext}")
        if missing:
            log.warning("MIGRATION_ALLOW_MISSING_EXTENSIONS=1: skipping unavailable extensions: %s",
                        ", ".join(missing))
    if bind.dialect.name == "postgresql":
        op.execute(
            """
            CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
            BEGIN
                NEW.updated_at = now();
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """
        )
        op.execute(
            """
            CREATE OR REPLACE FUNCTION reject_audit_mutation() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'audit_log is append-only';
            END;
            $$ LANGUAGE plpgsql;
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS reject_audit_mutation()")
        op.execute("DROP FUNCTION IF EXISTS set_updated_at()")

