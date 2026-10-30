"""approval and purchase orders

Revision ID: 0007_approval_po
Revises: 0006_negotiation_comparison
Create Date: 2026-10-30 00:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import Text
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0007_approval_po'
down_revision: str | None = '0006_negotiation_comparison'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('approval_chain',
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('category_code', sa.String(length=60), nullable=True),
    sa.Column('min_amount', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('max_amount', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_approval_chain_tenant_id', 'approval_chain', ['tenant_id'], unique=False)
    op.create_table('approval_step',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('chain_id', sa.String(length=36), nullable=False),
    sa.Column('step_no', sa.Integer(), nullable=False),
    sa.Column('role_code', sa.String(length=40), nullable=False),
    sa.Column('threshold_amount', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('sla_hours', sa.Integer(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_approval_step_chain_id', 'approval_step', ['chain_id'], unique=False)
    op.create_index('ix_approval_step_tenant_id', 'approval_step', ['tenant_id'], unique=False)
    op.create_table('approval',
    sa.Column('rfq_id', sa.String(length=36), nullable=False),
    sa.Column('offer_id', sa.String(length=36), nullable=True),
    sa.Column('chain_id', sa.String(length=36), nullable=True),
    sa.Column('step_no', sa.Integer(), nullable=False),
    sa.Column('approver_role', sa.String(length=40), nullable=False),
    sa.Column('approver_id', sa.String(length=36), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('decision', sa.String(length=20), nullable=True),
    sa.Column('comment', sa.Text(), nullable=False),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('delegated_to', sa.String(length=36), nullable=True),
    sa.Column('split_award', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_approval_rfq_id', 'approval', ['rfq_id'], unique=False)
    op.create_index('ix_approval_tenant_id', 'approval', ['tenant_id'], unique=False)
    op.create_table('purchase_order',
    sa.Column('code', sa.String(length=40), nullable=False),
    sa.Column('rfq_id', sa.String(length=36), nullable=False),
    sa.Column('supplier_id', sa.String(length=36), nullable=False),
    sa.Column('offer_id', sa.String(length=36), nullable=True),
    sa.Column('amount', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('issued_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('lines', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('document_html', sa.Text(), nullable=False),
    sa.Column('erp_status', sa.String(length=24), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_purchase_order_code', 'purchase_order', ['code'], unique=False)
    op.create_index('ix_purchase_order_rfq_id', 'purchase_order', ['rfq_id'], unique=False)
    op.create_index('ix_purchase_order_supplier_id', 'purchase_order', ['supplier_id'], unique=False)
    op.create_index('ix_purchase_order_tenant_id', 'purchase_order', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_purchase_order_tenant_id', table_name='purchase_order')
    op.drop_index('ix_purchase_order_supplier_id', table_name='purchase_order')
    op.drop_index('ix_purchase_order_rfq_id', table_name='purchase_order')
    op.drop_index('ix_purchase_order_code', table_name='purchase_order')
    op.drop_table('purchase_order')
    op.drop_index('ix_approval_tenant_id', table_name='approval')
    op.drop_index('ix_approval_rfq_id', table_name='approval')
    op.drop_table('approval')
    op.drop_index('ix_approval_step_tenant_id', table_name='approval_step')
    op.drop_index('ix_approval_step_chain_id', table_name='approval_step')
    op.drop_table('approval_step')
    op.drop_index('ix_approval_chain_tenant_id', table_name='approval_chain')
    op.drop_table('approval_chain')

