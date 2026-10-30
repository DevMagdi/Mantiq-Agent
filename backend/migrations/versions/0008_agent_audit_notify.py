"""agents, audit and notifications

Revision ID: 0008_agent_audit_notify
Revises: 0007_approval_po
Create Date: 2026-10-30 00:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import Text
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0008_agent_audit_notify'
down_revision: str | None = '0007_approval_po'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('agent_run',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('ref_type', sa.String(length=40), nullable=True),
    sa.Column('ref_id', sa.String(length=36), nullable=True),
    sa.Column('model', sa.String(length=80), nullable=False),
    sa.Column('input_summary', sa.Text(), nullable=False),
    sa.Column('output_summary', sa.Text(), nullable=False),
    sa.Column('reasoning', sa.Text(), nullable=False),
    sa.Column('policy_decision', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('tokens_in', sa.Integer(), nullable=False),
    sa.Column('tokens_out', sa.Integer(), nullable=False),
    sa.Column('cost_usd', sa.Numeric(precision=12, scale=6), nullable=False),
    sa.Column('latency_ms', sa.Integer(), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_agent_run_kind', 'agent_run', ['kind'], unique=False)
    op.create_index('ix_agent_run_ref_id', 'agent_run', ['ref_id'], unique=False)
    op.create_index('ix_agent_run_tenant_id', 'agent_run', ['tenant_id'], unique=False)
    op.create_table('agent_step',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=False),
    sa.Column('step_no', sa.Integer(), nullable=False),
    sa.Column('node', sa.String(length=60), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('input', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('output', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('reasoning', sa.Text(), nullable=False),
    sa.Column('latency_ms', sa.Integer(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_agent_step_run_id', 'agent_step', ['run_id'], unique=False)
    op.create_index('ix_agent_step_tenant_id', 'agent_step', ['tenant_id'], unique=False)
    op.create_table('tool_call',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=False),
    sa.Column('tool_name', sa.String(length=60), nullable=False),
    sa.Column('args', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('result', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('error', sa.String(length=400), nullable=False),
    sa.Column('idempotency_key', sa.String(length=120), nullable=False),
    sa.Column('latency_ms', sa.Integer(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_tool_call_run_id', 'tool_call', ['run_id'], unique=False)
    op.create_index('ix_tool_call_tenant_id', 'tool_call', ['tenant_id'], unique=False)
    op.create_table('audit_log',
    sa.Column('tenant_id', sa.String(length=36), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('actor_type', sa.String(length=16), nullable=False),
    sa.Column('actor_id', sa.String(length=36), nullable=True),
    sa.Column('actor_label', sa.String(length=160), nullable=False),
    sa.Column('action', sa.String(length=80), nullable=False),
    sa.Column('entity_type', sa.String(length=60), nullable=False),
    sa.Column('entity_id', sa.String(length=36), nullable=True),
    sa.Column('before', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=True),
    sa.Column('after', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=True),
    sa.Column('ip', sa.String(length=64), nullable=False),
    sa.Column('request_id', sa.String(length=64), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_audit_log_action', 'audit_log', ['action'], unique=False)
    op.create_index('ix_audit_log_tenant_id', 'audit_log', ['tenant_id'], unique=False)
    op.create_table('notification',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=True),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('title', sa.String(length=240), nullable=False),
    sa.Column('title_ar', sa.String(length=240), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('link', sa.String(length=300), nullable=False),
    sa.Column('severity', sa.String(length=20), nullable=False),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_notification_tenant_id', 'notification', ['tenant_id'], unique=False)
    op.create_index('ix_notification_user_id', 'notification', ['user_id'], unique=False)
    op.create_table('comment',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('rfq_id', sa.String(length=36), nullable=True),
    sa.Column('pr_id', sa.String(length=36), nullable=True),
    sa.Column('author_id', sa.String(length=36), nullable=True),
    sa.Column('author_name', sa.String(length=160), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('mentions', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_comment_pr_id', 'comment', ['pr_id'], unique=False)
    op.create_index('ix_comment_rfq_id', 'comment', ['rfq_id'], unique=False)
    op.create_index('ix_comment_tenant_id', 'comment', ['tenant_id'], unique=False)
    op.create_table('saved_view',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('page', sa.String(length=60), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('config', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('is_shared', sa.Boolean(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_saved_view_tenant_id', 'saved_view', ['tenant_id'], unique=False)
    op.create_index('ix_saved_view_user_id', 'saved_view', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_saved_view_user_id', table_name='saved_view')
    op.drop_index('ix_saved_view_tenant_id', table_name='saved_view')
    op.drop_table('saved_view')
    op.drop_index('ix_comment_tenant_id', table_name='comment')
    op.drop_index('ix_comment_rfq_id', table_name='comment')
    op.drop_index('ix_comment_pr_id', table_name='comment')
    op.drop_table('comment')
    op.drop_index('ix_notification_user_id', table_name='notification')
    op.drop_index('ix_notification_tenant_id', table_name='notification')
    op.drop_table('notification')
    op.drop_index('ix_audit_log_tenant_id', table_name='audit_log')
    op.drop_index('ix_audit_log_action', table_name='audit_log')
    op.drop_table('audit_log')
    op.drop_index('ix_tool_call_tenant_id', table_name='tool_call')
    op.drop_index('ix_tool_call_run_id', table_name='tool_call')
    op.drop_table('tool_call')
    op.drop_index('ix_agent_step_tenant_id', table_name='agent_step')
    op.drop_index('ix_agent_step_run_id', table_name='agent_step')
    op.drop_table('agent_step')
    op.drop_index('ix_agent_run_tenant_id', table_name='agent_run')
    op.drop_index('ix_agent_run_ref_id', table_name='agent_run')
    op.drop_index('ix_agent_run_kind', table_name='agent_run')
    op.drop_table('agent_run')

