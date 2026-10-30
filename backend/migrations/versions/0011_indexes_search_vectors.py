"""indexes, full-text search and vectors

Revision ID: 0011_indexes_search_vectors
Revises: 0010_rls_policies_and_grants
Create Date: 2026-10-30 00:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0011_indexes_search_vectors'
down_revision: str | None = '0010_rls_policies_and_grants'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    # Portable performance indexes (NFR-PERF-1): keyset pagination + hot lookups.
    op.create_index("ix_rfq_tenant_status_created", "rfq", ["tenant_id", "status", "created_at"])
    op.create_index("ix_pr_tenant_status_created", "purchase_requisition", ["tenant_id", "status", "created_at"])
    op.create_index("ix_offer_rfq_tco", "offer", ["rfq_id", "tco_total"])
    op.create_index("ix_offer_supplier_created", "offer", ["supplier_id", "created_at"])
    op.create_index("ix_price_history_item_observed", "price_history", ["tenant_id", "item_key", "observed_at"])
    op.create_index("ix_channel_message_rfq_created", "channel_message", ["rfq_id", "created_at"])
    op.create_index("ix_audit_tenant_created", "audit_log", ["tenant_id", "created_at"])
    op.create_index("ix_usage_tenant_kind_occurred", "usage_event", ["tenant_id", "kind", "occurred_at"])
    op.create_index("ix_notification_user_unread", "notification", ["user_id", "read_at"])
    if bind.dialect.name == "postgresql":
        # Trigram search for item/supplier fuzzy matching (FR-IN-6, NFR-PERF-1).
        op.execute("CREATE INDEX IF NOT EXISTS ix_supplier_name_trgm ON supplier USING gin (name gin_trgm_ops)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_supplier_name_ar_trgm ON supplier USING gin (name_ar gin_trgm_ops)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_item_name_trgm ON item USING gin (name gin_trgm_ops)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_rfq_title_trgm ON rfq USING gin (title gin_trgm_ops)")
        # Semantic search columns (pgvector). JSON payloads stay portable for SQLite dev.
        op.execute("ALTER TABLE supplier ADD COLUMN IF NOT EXISTS embedding_vec vector(1536)")
        op.execute("ALTER TABLE item ADD COLUMN IF NOT EXISTS embedding_vec vector(1536)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_supplier_embedding_hnsw ON supplier "
                   "USING hnsw (embedding_vec vector_cosine_ops)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_item_embedding_hnsw ON item "
                   "USING hnsw (embedding_vec vector_cosine_ops)")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_item_embedding_hnsw")
        op.execute("DROP INDEX IF EXISTS ix_supplier_embedding_hnsw")
        op.execute("ALTER TABLE item DROP COLUMN IF EXISTS embedding_vec")
        op.execute("ALTER TABLE supplier DROP COLUMN IF EXISTS embedding_vec")
        for name in ("ix_rfq_title_trgm", "ix_item_name_trgm", "ix_supplier_name_ar_trgm", "ix_supplier_name_trgm"):
            op.execute(f"DROP INDEX IF EXISTS {name}")
    for name, table in [
        ("ix_notification_user_unread", "notification"),
        ("ix_usage_tenant_kind_occurred", "usage_event"),
        ("ix_audit_tenant_created", "audit_log"),
        ("ix_channel_message_rfq_created", "channel_message"),
        ("ix_price_history_item_observed", "price_history"),
        ("ix_offer_supplier_created", "offer"),
        ("ix_offer_rfq_tco", "offer"),
        ("ix_pr_tenant_status_created", "purchase_requisition"),
        ("ix_rfq_tenant_status_created", "rfq"),
    ]:
        op.drop_index(name, table_name=table)

