"""billing, usage and administration

Revision ID: 0009_billing_usage_admin
Revises: 0008_agent_audit_notify
Create Date: 2026-10-30 00:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import Text
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0009_billing_usage_admin'
down_revision: str | None = '0008_agent_audit_notify'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('plan',
    sa.Column('code', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('price_monthly', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('limits', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('features', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    op.create_table('subscription',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('plan_code', sa.String(length=40), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('seats', sa.Integer(), nullable=False),
    sa.Column('period_start', sa.DateTime(timezone=True), nullable=True),
    sa.Column('period_end', sa.DateTime(timezone=True), nullable=True),
    sa.Column('trial_ends_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('provider_ref', sa.String(length=120), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_subscription_tenant_id', 'subscription', ['tenant_id'], unique=False)
    op.create_table('usage_event',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('quantity', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('ref_type', sa.String(length=40), nullable=True),
    sa.Column('ref_id', sa.String(length=36), nullable=True),
    sa.Column('cost_usd', sa.Numeric(precision=12, scale=6), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_usage_event_kind', 'usage_event', ['kind'], unique=False)
    op.create_index('ix_usage_event_tenant_id', 'usage_event', ['tenant_id'], unique=False)
    op.create_table('invoice',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('number', sa.String(length=40), nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('issued_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('lines', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_invoice_tenant_id', 'invoice', ['tenant_id'], unique=False)
    op.create_table('llm_provider_config',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('provider', sa.String(length=40), nullable=False),
    sa.Column('model', sa.String(length=80), nullable=False),
    sa.Column('base_url', sa.String(length=300), nullable=False),
    sa.Column('api_key_enc', sa.Text(), nullable=False),
    sa.Column('key_id', sa.String(length=40), nullable=False),
    sa.Column('is_default', sa.Boolean(), nullable=False),
    sa.Column('monthly_token_budget', sa.Integer(), nullable=False),
    sa.Column('tokens_used', sa.Integer(), nullable=False),
    sa.Column('is_self_hosted', sa.Boolean(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_llm_provider_config_tenant_id', 'llm_provider_config', ['tenant_id'], unique=False)
    op.create_table('integration_config',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('config', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('secrets_enc', sa.Text(), nullable=False),
    sa.Column('key_id', sa.String(length=40), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('last_tested_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_integration_config_tenant_id', 'integration_config', ['tenant_id'], unique=False)
    op.create_table('idempotency_key',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('key', sa.String(length=120), nullable=False),
    sa.Column('endpoint', sa.String(length=160), nullable=False),
    sa.Column('request_hash', sa.String(length=64), nullable=False),
    sa.Column('status_code', sa.Integer(), nullable=False),
    sa.Column('response', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'key', 'endpoint', name='uq_idem')
    )
    op.create_index('ix_idempotency_key_tenant_id', 'idempotency_key', ['tenant_id'], unique=False)
    op.create_table('outbox_event',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('topic', sa.String(length=80), nullable=False),
    sa.Column('payload', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('available_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_outbox_event_tenant_id', 'outbox_event', ['tenant_id'], unique=False)
    op.create_index('ix_outbox_event_topic', 'outbox_event', ['topic'], unique=False)
    op.create_table('retention_policy',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('entity', sa.String(length=60), nullable=False),
    sa.Column('retain_days', sa.Integer(), nullable=False),
    sa.Column('notes', sa.String(length=255), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_retention_policy_tenant_id', 'retention_policy', ['tenant_id'], unique=False)
    op.create_table('data_erasure_request',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('subject_email', sa.String(length=255), nullable=False),
    sa.Column('subject_type', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('requested_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_data_erasure_request_tenant_id', 'data_erasure_request', ['tenant_id'], unique=False)
    op.create_table('discovery_job',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('query', sa.String(length=300), nullable=False),
    sa.Column('category_code', sa.String(length=60), nullable=True),
    sa.Column('country', sa.String(length=2), nullable=False),
    sa.Column('provider', sa.String(length=40), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('max_results', sa.Integer(), nullable=False),
    sa.Column('found_count', sa.Integer(), nullable=False),
    sa.Column('error', sa.String(length=400), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('requested_by', sa.String(length=36), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_discovery_job_tenant_id', 'discovery_job', ['tenant_id'], unique=False)
    op.create_table('discovery_candidate',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('job_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=240), nullable=False),
    sa.Column('name_ar', sa.String(length=240), nullable=False),
    sa.Column('website', sa.String(length=300), nullable=False),
    sa.Column('domain', sa.String(length=160), nullable=False),
    sa.Column('country', sa.String(length=2), nullable=False),
    sa.Column('city', sa.String(length=120), nullable=False),
    sa.Column('contact_email', sa.String(length=255), nullable=False),
    sa.Column('contact_phone', sa.String(length=64), nullable=False),
    sa.Column('source_url', sa.String(length=500), nullable=False),
    sa.Column('confidence', sa.Numeric(precision=5, scale=4), nullable=False),
    sa.Column('cluster_key', sa.String(length=160), nullable=False),
    sa.Column('dedup_status', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('supplier_id', sa.String(length=36), nullable=True),
    sa.Column('evidence', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_discovery_candidate_job_id', 'discovery_candidate', ['job_id'], unique=False)
    op.create_index('ix_discovery_candidate_tenant_id', 'discovery_candidate', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_discovery_candidate_tenant_id', table_name='discovery_candidate')
    op.drop_index('ix_discovery_candidate_job_id', table_name='discovery_candidate')
    op.drop_table('discovery_candidate')
    op.drop_index('ix_discovery_job_tenant_id', table_name='discovery_job')
    op.drop_table('discovery_job')
    op.drop_index('ix_data_erasure_request_tenant_id', table_name='data_erasure_request')
    op.drop_table('data_erasure_request')
    op.drop_index('ix_retention_policy_tenant_id', table_name='retention_policy')
    op.drop_table('retention_policy')
    op.drop_index('ix_outbox_event_topic', table_name='outbox_event')
    op.drop_index('ix_outbox_event_tenant_id', table_name='outbox_event')
    op.drop_table('outbox_event')
    op.drop_index('ix_idempotency_key_tenant_id', table_name='idempotency_key')
    op.drop_table('idempotency_key')
    op.drop_index('ix_integration_config_tenant_id', table_name='integration_config')
    op.drop_table('integration_config')
    op.drop_index('ix_llm_provider_config_tenant_id', table_name='llm_provider_config')
    op.drop_table('llm_provider_config')
    op.drop_index('ix_invoice_tenant_id', table_name='invoice')
    op.drop_table('invoice')
    op.drop_index('ix_usage_event_tenant_id', table_name='usage_event')
    op.drop_index('ix_usage_event_kind', table_name='usage_event')
    op.drop_table('usage_event')
    op.drop_index('ix_subscription_tenant_id', table_name='subscription')
    op.drop_table('subscription')
    op.drop_table('plan')

