"""negotiation and comparison

Revision ID: 0006_negotiation_comparison
Revises: 0005_channels_responses
Create Date: 2026-10-30 00:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import Text
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0006_negotiation_comparison'
down_revision: str | None = '0005_channels_responses'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('fx_rate',
    sa.Column('tenant_id', sa.String(length=36), nullable=True),
    sa.Column('base', sa.String(length=3), nullable=False),
    sa.Column('quote', sa.String(length=3), nullable=False),
    sa.Column('rate', sa.Numeric(precision=24, scale=10), nullable=False),
    sa.Column('as_of', sa.DateTime(timezone=True), nullable=False),
    sa.Column('source', sa.String(length=40), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_fx_rate_tenant_id', 'fx_rate', ['tenant_id'], unique=False)
    op.create_table('landed_cost_component',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('offer_id', sa.String(length=36), nullable=True),
    sa.Column('rfq_id', sa.String(length=36), nullable=True),
    sa.Column('kind', sa.String(length=24), nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('notes', sa.String(length=200), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_landed_cost_component_offer_id', 'landed_cost_component', ['offer_id'], unique=False)
    op.create_index('ix_landed_cost_component_rfq_id', 'landed_cost_component', ['rfq_id'], unique=False)
    op.create_index('ix_landed_cost_component_tenant_id', 'landed_cost_component', ['tenant_id'], unique=False)
    op.create_table('offer',
    sa.Column('rfq_id', sa.String(length=36), nullable=False),
    sa.Column('supplier_id', sa.String(length=36), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('incoterm', sa.String(length=12), nullable=False),
    sa.Column('payment_terms', sa.String(length=120), nullable=False),
    sa.Column('lead_time_days', sa.Integer(), nullable=True),
    sa.Column('validity_date', sa.DateTime(timezone=True), nullable=True),
    sa.Column('moq', sa.Numeric(precision=18, scale=6), nullable=True),
    sa.Column('subtotal', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('fx_rate', sa.Numeric(precision=24, scale=10), nullable=False),
    sa.Column('landed_cost_total', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('tco_total', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('total_base', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('scores', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('weighted_score', sa.Numeric(precision=6, scale=3), nullable=False),
    sa.Column('confidence', sa.Numeric(precision=5, scale=4), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('is_low_confidence', sa.Boolean(), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('extraction_id', sa.String(length=36), nullable=True),
    sa.Column('round_no', sa.Integer(), nullable=False),
    sa.Column('parent_offer_id', sa.String(length=36), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_offer_rfq_id', 'offer', ['rfq_id'], unique=False)
    op.create_index('ix_offer_supplier_id', 'offer', ['supplier_id'], unique=False)
    op.create_index('ix_offer_tenant_id', 'offer', ['tenant_id'], unique=False)
    op.create_table('offer_line',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('offer_id', sa.String(length=36), nullable=False),
    sa.Column('rfq_line_id', sa.String(length=36), nullable=True),
    sa.Column('description', sa.String(length=400), nullable=False),
    sa.Column('quantity', sa.Numeric(precision=18, scale=6), nullable=False),
    sa.Column('uom', sa.String(length=16), nullable=False),
    sa.Column('unit_price', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('line_total', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('lead_time_days', sa.Integer(), nullable=True),
    sa.Column('moq', sa.Numeric(precision=18, scale=6), nullable=True),
    sa.Column('confidence', sa.Numeric(precision=5, scale=4), nullable=False),
    sa.Column('notes', sa.String(length=400), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_offer_line_offer_id', 'offer_line', ['offer_id'], unique=False)
    op.create_index('ix_offer_line_tenant_id', 'offer_line', ['tenant_id'], unique=False)
    op.create_table('price_history',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('item_key', sa.String(length=200), nullable=False),
    sa.Column('supplier_id', sa.String(length=36), nullable=True),
    sa.Column('rfq_id', sa.String(length=36), nullable=True),
    sa.Column('unit_price', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('unit_price_base', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_price_history_item_key', 'price_history', ['item_key'], unique=False)
    op.create_index('ix_price_history_supplier_id', 'price_history', ['supplier_id'], unique=False)
    op.create_index('ix_price_history_tenant_id', 'price_history', ['tenant_id'], unique=False)
    op.create_table('benchmark_price',
    sa.Column('tenant_id', sa.String(length=36), nullable=True),
    sa.Column('item_key', sa.String(length=200), nullable=False),
    sa.Column('period', sa.String(length=20), nullable=False),
    sa.Column('p25', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('p50', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('p75', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('sample_size', sa.Integer(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_benchmark_price_item_key', 'benchmark_price', ['item_key'], unique=False)
    op.create_index('ix_benchmark_price_tenant_id', 'benchmark_price', ['tenant_id'], unique=False)
    op.create_table('negotiation_policy',
    sa.Column('rfq_id', sa.String(length=36), nullable=True),
    sa.Column('supplier_id', sa.String(length=36), nullable=True),
    sa.Column('category_code', sa.String(length=60), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('target_price', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('walkaway_price', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('max_rounds', sa.Integer(), nullable=False),
    sa.Column('allowed_concessions', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('tone', sa.String(length=20), nullable=False),
    sa.Column('mode', sa.String(length=20), nullable=False),
    sa.Column('value_threshold', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_negotiation_policy_rfq_id', 'negotiation_policy', ['rfq_id'], unique=False)
    op.create_index('ix_negotiation_policy_supplier_id', 'negotiation_policy', ['supplier_id'], unique=False)
    op.create_index('ix_negotiation_policy_tenant_id', 'negotiation_policy', ['tenant_id'], unique=False)
    op.create_table('negotiation_round',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('rfq_id', sa.String(length=36), nullable=False),
    sa.Column('supplier_id', sa.String(length=36), nullable=False),
    sa.Column('round_no', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_negotiation_round_rfq_id', 'negotiation_round', ['rfq_id'], unique=False)
    op.create_index('ix_negotiation_round_supplier_id', 'negotiation_round', ['supplier_id'], unique=False)
    op.create_index('ix_negotiation_round_tenant_id', 'negotiation_round', ['tenant_id'], unique=False)
    op.create_table('negotiation_message',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('rfq_id', sa.String(length=36), nullable=False),
    sa.Column('supplier_id', sa.String(length=36), nullable=False),
    sa.Column('round_no', sa.Integer(), nullable=False),
    sa.Column('direction', sa.String(length=10), nullable=False),
    sa.Column('actor_type', sa.String(length=12), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('body_ar', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('proposed_price', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('policy_decision', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('agent_reasoning', sa.Text(), nullable=False),
    sa.Column('agent_run_id', sa.String(length=36), nullable=True),
    sa.Column('offer_id', sa.String(length=36), nullable=True),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_negotiation_message_rfq_id', 'negotiation_message', ['rfq_id'], unique=False)
    op.create_index('ix_negotiation_message_supplier_id', 'negotiation_message', ['supplier_id'], unique=False)
    op.create_index('ix_negotiation_message_tenant_id', 'negotiation_message', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_negotiation_message_tenant_id', table_name='negotiation_message')
    op.drop_index('ix_negotiation_message_supplier_id', table_name='negotiation_message')
    op.drop_index('ix_negotiation_message_rfq_id', table_name='negotiation_message')
    op.drop_table('negotiation_message')
    op.drop_index('ix_negotiation_round_tenant_id', table_name='negotiation_round')
    op.drop_index('ix_negotiation_round_supplier_id', table_name='negotiation_round')
    op.drop_index('ix_negotiation_round_rfq_id', table_name='negotiation_round')
    op.drop_table('negotiation_round')
    op.drop_index('ix_negotiation_policy_tenant_id', table_name='negotiation_policy')
    op.drop_index('ix_negotiation_policy_supplier_id', table_name='negotiation_policy')
    op.drop_index('ix_negotiation_policy_rfq_id', table_name='negotiation_policy')
    op.drop_table('negotiation_policy')
    op.drop_index('ix_benchmark_price_tenant_id', table_name='benchmark_price')
    op.drop_index('ix_benchmark_price_item_key', table_name='benchmark_price')
    op.drop_table('benchmark_price')
    op.drop_index('ix_price_history_tenant_id', table_name='price_history')
    op.drop_index('ix_price_history_supplier_id', table_name='price_history')
    op.drop_index('ix_price_history_item_key', table_name='price_history')
    op.drop_table('price_history')
    op.drop_index('ix_offer_line_tenant_id', table_name='offer_line')
    op.drop_index('ix_offer_line_offer_id', table_name='offer_line')
    op.drop_table('offer_line')
    op.drop_index('ix_offer_tenant_id', table_name='offer')
    op.drop_index('ix_offer_supplier_id', table_name='offer')
    op.drop_index('ix_offer_rfq_id', table_name='offer')
    op.drop_table('offer')
    op.drop_index('ix_landed_cost_component_tenant_id', table_name='landed_cost_component')
    op.drop_index('ix_landed_cost_component_rfq_id', table_name='landed_cost_component')
    op.drop_index('ix_landed_cost_component_offer_id', table_name='landed_cost_component')
    op.drop_table('landed_cost_component')
    op.drop_index('ix_fx_rate_tenant_id', table_name='fx_rate')
    op.drop_table('fx_rate')

