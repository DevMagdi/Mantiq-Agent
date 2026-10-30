"""extensions and base helpers

Revision ID: 0001_extensions_and_base
Revises: None
Create Date: 2026-10-30 00:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0001_extensions_and_base'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Extensions required by the SRS (PostgreSQL). SQLite is supported for dev/test only.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for ext in ("pgcrypto", "citext", "pg_trgm", "vector"):
            op.execute(f"CREATE EXTENSION IF NOT EXISTS {ext}")
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

