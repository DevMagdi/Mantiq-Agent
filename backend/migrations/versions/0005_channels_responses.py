"""channels and responses

Revision ID: 0005_channels_responses
Revises: 0004_requisition_rfq
Create Date: 2026-10-30 00:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import Text
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0005_channels_responses'
down_revision: str | None = '0004_requisition_rfq'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('message_thread',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('rfq_id', sa.String(length=36), nullable=False),
    sa.Column('supplier_id', sa.String(length=36), nullable=False),
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('subject', sa.String(length=300), nullable=False),
    sa.Column('external_thread_id', sa.String(length=200), nullable=False),
    sa.Column('last_message_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_message_thread_rfq_id', 'message_thread', ['rfq_id'], unique=False)
    op.create_index('ix_message_thread_supplier_id', 'message_thread', ['supplier_id'], unique=False)
    op.create_index('ix_message_thread_tenant_id', 'message_thread', ['tenant_id'], unique=False)
    op.create_table('channel_message',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('thread_id', sa.String(length=36), nullable=False),
    sa.Column('rfq_id', sa.String(length=36), nullable=False),
    sa.Column('supplier_id', sa.String(length=36), nullable=False),
    sa.Column('direction', sa.String(length=10), nullable=False),
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('body_ar', sa.Text(), nullable=False),
    sa.Column('subject', sa.String(length=300), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('author_type', sa.String(length=12), nullable=False),
    sa.Column('author_id', sa.String(length=36), nullable=True),
    sa.Column('agent_run_id', sa.String(length=36), nullable=True),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('provider_message_id', sa.String(length=200), nullable=False),
    sa.Column('extra', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_channel_message_rfq_id', 'channel_message', ['rfq_id'], unique=False)
    op.create_index('ix_channel_message_supplier_id', 'channel_message', ['supplier_id'], unique=False)
    op.create_index('ix_channel_message_tenant_id', 'channel_message', ['tenant_id'], unique=False)
    op.create_index('ix_channel_message_thread_id', 'channel_message', ['thread_id'], unique=False)
    op.create_table('attachment',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('filename', sa.String(length=255), nullable=False),
    sa.Column('mime', sa.String(length=120), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('storage_key', sa.String(length=300), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('extracted_text', sa.Text(), nullable=False),
    sa.Column('message_id', sa.String(length=36), nullable=True),
    sa.Column('rfq_id', sa.String(length=36), nullable=True),
    sa.Column('uploaded_by', sa.String(length=36), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_attachment_message_id', 'attachment', ['message_id'], unique=False)
    op.create_index('ix_attachment_rfq_id', 'attachment', ['rfq_id'], unique=False)
    op.create_index('ix_attachment_tenant_id', 'attachment', ['tenant_id'], unique=False)
    op.create_table('extraction_result',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('rfq_id', sa.String(length=36), nullable=False),
    sa.Column('supplier_id', sa.String(length=36), nullable=False),
    sa.Column('message_id', sa.String(length=36), nullable=True),
    sa.Column('attachment_id', sa.String(length=36), nullable=True),
    sa.Column('offer_id', sa.String(length=36), nullable=True),
    sa.Column('source_kind', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('confidence', sa.Numeric(precision=5, scale=4), nullable=False),
    sa.Column('raw_text', sa.Text(), nullable=False),
    sa.Column('model', sa.String(length=80), nullable=False),
    sa.Column('parser_version', sa.String(length=20), nullable=False),
    sa.Column('needs_review', sa.Boolean(), nullable=False),
    sa.Column('issues', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_extraction_result_rfq_id', 'extraction_result', ['rfq_id'], unique=False)
    op.create_index('ix_extraction_result_supplier_id', 'extraction_result', ['supplier_id'], unique=False)
    op.create_index('ix_extraction_result_tenant_id', 'extraction_result', ['tenant_id'], unique=False)
    op.create_table('extracted_field',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('extraction_id', sa.String(length=36), nullable=False),
    sa.Column('field_name', sa.String(length=80), nullable=False),
    sa.Column('label', sa.String(length=120), nullable=False),
    sa.Column('value', sa.String(length=400), nullable=False),
    sa.Column('value_numeric', sa.Numeric(precision=24, scale=6), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=True),
    sa.Column('confidence', sa.Numeric(precision=5, scale=4), nullable=False),
    sa.Column('source_span', sa.Text(), nullable=False),
    sa.Column('source_offset', sa.Integer(), nullable=False),
    sa.Column('page', sa.Integer(), nullable=False),
    sa.Column('verified_by', sa.String(length=36), nullable=True),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('is_low_confidence', sa.Boolean(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_extracted_field_extraction_id', 'extracted_field', ['extraction_id'], unique=False)
    op.create_index('ix_extracted_field_tenant_id', 'extracted_field', ['tenant_id'], unique=False)
    op.create_table('voice_call',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('rfq_id', sa.String(length=36), nullable=False),
    sa.Column('supplier_id', sa.String(length=36), nullable=False),
    sa.Column('provider', sa.String(length=20), nullable=False),
    sa.Column('provider_call_id', sa.String(length=120), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('country', sa.String(length=2), nullable=False),
    sa.Column('disclosure_played', sa.Boolean(), nullable=False),
    sa.Column('consent_given', sa.Boolean(), nullable=False),
    sa.Column('recording_uri', sa.String(length=400), nullable=False),
    sa.Column('transcript', sa.Text(), nullable=False),
    sa.Column('language', sa.String(length=12), nullable=False),
    sa.Column('duration_seconds', sa.Integer(), nullable=False),
    sa.Column('extracted_offer_id', sa.String(length=36), nullable=True),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('policy_snapshot', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.String(length=36), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_voice_call_rfq_id', 'voice_call', ['rfq_id'], unique=False)
    op.create_index('ix_voice_call_supplier_id', 'voice_call', ['supplier_id'], unique=False)
    op.create_index('ix_voice_call_tenant_id', 'voice_call', ['tenant_id'], unique=False)
    op.create_table('opt_in_registry',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('supplier_id', sa.String(length=36), nullable=False),
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('source', sa.String(length=80), nullable=False),
    sa.Column('evidence', sa.Text(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'supplier_id', 'channel', name='uq_optin')
    )
    op.create_index('ix_opt_in_registry_supplier_id', 'opt_in_registry', ['supplier_id'], unique=False)
    op.create_index('ix_opt_in_registry_tenant_id', 'opt_in_registry', ['tenant_id'], unique=False)
    op.create_table('supplier_portal_token',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('supplier_id', sa.String(length=36), nullable=False),
    sa.Column('token', sa.String(length=120), nullable=False),
    sa.Column('purpose', sa.String(length=24), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('submitted', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token')
    )
    op.create_index('ix_supplier_portal_token_supplier_id', 'supplier_portal_token', ['supplier_id'], unique=False)
    op.create_index('ix_supplier_portal_token_tenant_id', 'supplier_portal_token', ['tenant_id'], unique=False)
    op.create_table('country_call_policy',
    sa.Column('country', sa.String(length=2), nullable=False),
    sa.Column('allowed_hours_start', sa.Integer(), nullable=False),
    sa.Column('allowed_hours_end', sa.Integer(), nullable=False),
    sa.Column('recording_consent_mode', sa.String(length=20), nullable=False),
    sa.Column('disclosure_text_en', sa.Text(), nullable=False),
    sa.Column('disclosure_text_ar', sa.Text(), nullable=False),
    sa.Column('max_call_seconds', sa.Integer(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('country')
    )


def downgrade() -> None:
    op.drop_table('country_call_policy')
    op.drop_index('ix_supplier_portal_token_tenant_id', table_name='supplier_portal_token')
    op.drop_index('ix_supplier_portal_token_supplier_id', table_name='supplier_portal_token')
    op.drop_table('supplier_portal_token')
    op.drop_index('ix_opt_in_registry_tenant_id', table_name='opt_in_registry')
    op.drop_index('ix_opt_in_registry_supplier_id', table_name='opt_in_registry')
    op.drop_table('opt_in_registry')
    op.drop_index('ix_voice_call_tenant_id', table_name='voice_call')
    op.drop_index('ix_voice_call_supplier_id', table_name='voice_call')
    op.drop_index('ix_voice_call_rfq_id', table_name='voice_call')
    op.drop_table('voice_call')
    op.drop_index('ix_extracted_field_tenant_id', table_name='extracted_field')
    op.drop_index('ix_extracted_field_extraction_id', table_name='extracted_field')
    op.drop_table('extracted_field')
    op.drop_index('ix_extraction_result_tenant_id', table_name='extraction_result')
    op.drop_index('ix_extraction_result_supplier_id', table_name='extraction_result')
    op.drop_index('ix_extraction_result_rfq_id', table_name='extraction_result')
    op.drop_table('extraction_result')
    op.drop_index('ix_attachment_tenant_id', table_name='attachment')
    op.drop_index('ix_attachment_rfq_id', table_name='attachment')
    op.drop_index('ix_attachment_message_id', table_name='attachment')
    op.drop_table('attachment')
    op.drop_index('ix_channel_message_thread_id', table_name='channel_message')
    op.drop_index('ix_channel_message_tenant_id', table_name='channel_message')
    op.drop_index('ix_channel_message_supplier_id', table_name='channel_message')
    op.drop_index('ix_channel_message_rfq_id', table_name='channel_message')
    op.drop_table('channel_message')
    op.drop_index('ix_message_thread_tenant_id', table_name='message_thread')
    op.drop_index('ix_message_thread_supplier_id', table_name='message_thread')
    op.drop_index('ix_message_thread_rfq_id', table_name='message_thread')
    op.drop_table('message_thread')

