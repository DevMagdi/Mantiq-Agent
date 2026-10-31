"""tenant-managed integrations: enable flag and the last probe error

Revision ID: 0017_tenant_integrations
Revises: 0016_channels_voice
Create Date: 2026-10-31 03:10:00

Phase 5 closed with the Integrations screen still decorative: `integration_config` rows were written
but nothing read them, and there was no way to switch a provider off. Two columns make the screen
real:

* `enabled` — turning an integration off must be a safe, reversible action that hands traffic back to
  the platform adapter, so the flag has to be stored next to the credentials;
* `last_error` — the screen has to show *why* the last probe failed, which is not the same thing as
  the row's status.

Existing rows are enabled: they were saved by an operator who expected them to be in use.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_tenant_integrations"
down_revision: str | None = "0016_channels_voice"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("integration_config",
                  sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("integration_config",
                  sa.Column("last_error", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("integration_config", "last_error")
    op.drop_column("integration_config", "enabled")
