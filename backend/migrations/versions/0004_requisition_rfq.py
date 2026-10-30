"""requisition and RFQ

Revision ID: 0004_requisition_rfq
Revises: 0003_master_data
Create Date: 2026-10-30 00:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import Text
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0004_requisition_rfq'
down_revision: str | None = '0003_master_data'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('purchase_requisition',
    sa.Column('code', sa.String(length=40), nullable=False),
    sa.Column('title', sa.String(length=240), nullable=False),
    sa.Column('source', sa.String(length=24), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('plant_id', sa.String(length=36), nullable=True),
    sa.Column('requested_by', sa.String(length=36), nullable=True),
    sa.Column('needed_by', sa.DateTime(timezone=True), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('total_estimate', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('raw_input', sa.Text(), nullable=False),
    sa.Column('confidence', sa.Numeric(precision=5, scale=4), nullable=False),
    sa.Column('clarification_resolved', sa.Boolean(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_purchase_requisition_code', 'purchase_requisition', ['code'], unique=False)
    op.create_index('ix_purchase_requisition_tenant_id', 'purchase_requisition', ['tenant_id'], unique=False)
    op.create_table('pr_line',
    sa.Column('pr_id', sa.String(length=36), nullable=False),
    sa.Column('line_no', sa.Integer(), nullable=False),
    sa.Column('description', sa.String(length=400), nullable=False),
    sa.Column('description_ar', sa.String(length=400), nullable=False),
    sa.Column('quantity', sa.Numeric(precision=18, scale=6), nullable=False),
    sa.Column('uom', sa.String(length=16), nullable=False),
    sa.Column('taxonomy_code', sa.String(length=20), nullable=True),
    sa.Column('category_code', sa.String(length=60), nullable=True),
    sa.Column('target_price', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('needed_by', sa.DateTime(timezone=True), nullable=True),
    sa.Column('attributes', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('normalization_confidence', sa.Numeric(precision=5, scale=4), nullable=False),
    sa.Column('quoted_text', sa.Text(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_pr_line_pr_id', 'pr_line', ['pr_id'], unique=False)
    op.create_index('ix_pr_line_tenant_id', 'pr_line', ['tenant_id'], unique=False)
    op.create_table('rfq',
    sa.Column('code', sa.String(length=40), nullable=False),
    sa.Column('title', sa.String(length=240), nullable=False),
    sa.Column('pr_id', sa.String(length=36), nullable=True),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('incoterm', sa.String(length=12), nullable=False),
    sa.Column('payment_terms', sa.String(length=120), nullable=False),
    sa.Column('delivery_location', sa.String(length=160), nullable=False),
    sa.Column('deadline', sa.DateTime(timezone=True), nullable=True),
    sa.Column('validity_date', sa.DateTime(timezone=True), nullable=True),
    sa.Column('buyer_id', sa.String(length=36), nullable=True),
    sa.Column('mode', sa.String(length=20), nullable=False),
    sa.Column('template_id', sa.String(length=36), nullable=True),
    sa.Column('scoring_weights', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('stage_history', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_rfq_code', 'rfq', ['code'], unique=False)
    op.create_index('ix_rfq_pr_id', 'rfq', ['pr_id'], unique=False)
    op.create_index('ix_rfq_status', 'rfq', ['status'], unique=False)
    op.create_index('ix_rfq_tenant_id', 'rfq', ['tenant_id'], unique=False)
    op.create_table('rfq_line',
    sa.Column('rfq_id', sa.String(length=36), nullable=False),
    sa.Column('line_no', sa.Integer(), nullable=False),
    sa.Column('description', sa.String(length=400), nullable=False),
    sa.Column('quantity', sa.Numeric(precision=18, scale=6), nullable=False),
    sa.Column('uom', sa.String(length=16), nullable=False),
    sa.Column('target_price', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('taxonomy_code', sa.String(length=20), nullable=True),
    sa.Column('specs', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('pr_line_id', sa.String(length=36), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_rfq_line_rfq_id', 'rfq_line', ['rfq_id'], unique=False)
    op.create_index('ix_rfq_line_taxonomy_code', 'rfq_line', ['taxonomy_code'], unique=False)
    op.create_index('ix_rfq_line_tenant_id', 'rfq_line', ['tenant_id'], unique=False)
    op.create_table('rfq_supplier',
    sa.Column('rfq_id', sa.String(length=36), nullable=False),
    sa.Column('supplier_id', sa.String(length=36), nullable=False),
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reminder_count', sa.Integer(), nullable=False),
    sa.Column('last_reminder_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('token', sa.String(length=120), nullable=False),
    sa.Column('responded_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('rfq_id', 'supplier_id', name='uq_rfq_supplier')
    )
    op.create_index('ix_rfq_supplier_rfq_id', 'rfq_supplier', ['rfq_id'], unique=False)
    op.create_index('ix_rfq_supplier_supplier_id', 'rfq_supplier', ['supplier_id'], unique=False)
    op.create_index('ix_rfq_supplier_tenant_id', 'rfq_supplier', ['tenant_id'], unique=False)
    op.create_index('ix_rfq_supplier_token', 'rfq_supplier', ['token'], unique=False)
    op.create_table('message_template',
    sa.Column('code', sa.String(length=80), nullable=False),
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('locale', sa.String(length=8), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('subject', sa.String(length=300), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('whatsapp_template_name', sa.String(length=120), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_message_template_tenant_id', 'message_template', ['tenant_id'], unique=False)
    op.create_table('web_form_token',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('token', sa.String(length=120), nullable=False),
    sa.Column('rfq_id', sa.String(length=36), nullable=False),
    sa.Column('supplier_id', sa.String(length=36), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token')
    )
    op.create_index('ix_web_form_token_rfq_id', 'web_form_token', ['rfq_id'], unique=False)
    op.create_index('ix_web_form_token_supplier_id', 'web_form_token', ['supplier_id'], unique=False)
    op.create_index('ix_web_form_token_tenant_id', 'web_form_token', ['tenant_id'], unique=False)
    op.create_table('clarification_question',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('pr_id', sa.String(length=36), nullable=False),
    sa.Column('pr_line_id', sa.String(length=36), nullable=True),
    sa.Column('field', sa.String(length=60), nullable=False),
    sa.Column('question', sa.String(length=400), nullable=False),
    sa.Column('question_ar', sa.String(length=400), nullable=False),
    sa.Column('answer', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('asked_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('answered_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('answered_by', sa.String(length=36), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_clarification_question_pr_id', 'clarification_question', ['pr_id'], unique=False)
    op.create_index('ix_clarification_question_tenant_id', 'clarification_question', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_clarification_question_tenant_id', table_name='clarification_question')
    op.drop_index('ix_clarification_question_pr_id', table_name='clarification_question')
    op.drop_table('clarification_question')
    op.drop_index('ix_web_form_token_tenant_id', table_name='web_form_token')
    op.drop_index('ix_web_form_token_supplier_id', table_name='web_form_token')
    op.drop_index('ix_web_form_token_rfq_id', table_name='web_form_token')
    op.drop_table('web_form_token')
    op.drop_index('ix_message_template_tenant_id', table_name='message_template')
    op.drop_table('message_template')
    op.drop_index('ix_rfq_supplier_token', table_name='rfq_supplier')
    op.drop_index('ix_rfq_supplier_tenant_id', table_name='rfq_supplier')
    op.drop_index('ix_rfq_supplier_supplier_id', table_name='rfq_supplier')
    op.drop_index('ix_rfq_supplier_rfq_id', table_name='rfq_supplier')
    op.drop_table('rfq_supplier')
    op.drop_index('ix_rfq_line_tenant_id', table_name='rfq_line')
    op.drop_index('ix_rfq_line_taxonomy_code', table_name='rfq_line')
    op.drop_index('ix_rfq_line_rfq_id', table_name='rfq_line')
    op.drop_table('rfq_line')
    op.drop_index('ix_rfq_tenant_id', table_name='rfq')
    op.drop_index('ix_rfq_status', table_name='rfq')
    op.drop_index('ix_rfq_pr_id', table_name='rfq')
    op.drop_index('ix_rfq_code', table_name='rfq')
    op.drop_table('rfq')
    op.drop_index('ix_pr_line_tenant_id', table_name='pr_line')
    op.drop_index('ix_pr_line_pr_id', table_name='pr_line')
    op.drop_table('pr_line')
    op.drop_index('ix_purchase_requisition_tenant_id', table_name='purchase_requisition')
    op.drop_index('ix_purchase_requisition_code', table_name='purchase_requisition')
    op.drop_table('purchase_requisition')

