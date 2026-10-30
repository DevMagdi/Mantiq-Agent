"""drop the per-user theme and numerals preference

Revision ID: 0014_drop_theme_preference
Revises: 0013_post_po_lifecycle
Create Date: 2026-10-30 00:00:00

The product ships exactly one theme (Mantiq navy with a gold accent), so a stored light/dark
preference no longer means anything and a numerals toggle never shipped. Removing the columns keeps
the user payload honest instead of returning settings the UI cannot honour.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_drop_theme_preference"
down_revision: str | None = "0013_post_po_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("app_user") as batch:
        batch.drop_column("theme")
        batch.drop_column("numerals")


def downgrade() -> None:
    with op.batch_alter_table("app_user") as batch:
        batch.add_column(sa.Column("theme", sa.String(length=12), nullable=False, server_default="dark"))
        batch.add_column(sa.Column("numerals", sa.String(length=12), nullable=False, server_default="western"))
