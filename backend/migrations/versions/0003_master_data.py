"""master data

Revision ID: 0003_master_data
Revises: 0002_tenancy_identity
Create Date: 2026-10-30 00:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import Text
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0003_master_data'
down_revision: str | None = '0002_tenancy_identity'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('plant',
    sa.Column('code', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('name_ar', sa.String(length=160), nullable=False),
    sa.Column('country', sa.String(length=2), nullable=False),
    sa.Column('city', sa.String(length=80), nullable=False),
    sa.Column('address', sa.String(length=255), nullable=False),
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
    op.create_index('ix_plant_tenant_id', 'plant', ['tenant_id'], unique=False)
    op.create_table('taxonomy_node',
    sa.Column('code', sa.String(length=20), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('name_ar', sa.String(length=200), nullable=False),
    sa.Column('parent_code', sa.String(length=20), nullable=True),
    sa.Column('level', sa.Integer(), nullable=False),
    sa.Column('keywords', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('default_uom', sa.String(length=16), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    op.create_table('category',
    sa.Column('code', sa.String(length=60), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('name_ar', sa.String(length=120), nullable=False),
    sa.Column('taxonomy_code', sa.String(length=20), nullable=True),
    sa.Column('default_weights', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_category_tenant_id', 'category', ['tenant_id'], unique=False)
    op.create_table('item',
    sa.Column('sku', sa.String(length=80), nullable=False),
    sa.Column('name', sa.String(length=240), nullable=False),
    sa.Column('name_ar', sa.String(length=240), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('category_code', sa.String(length=60), nullable=True),
    sa.Column('taxonomy_code', sa.String(length=20), nullable=True),
    sa.Column('uom', sa.String(length=16), nullable=False),
    sa.Column('attributes', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('embedding', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=True),
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
    op.create_index('ix_item_tenant_id', 'item', ['tenant_id'], unique=False)
    op.create_table('unit_conversion',
    sa.Column('from_unit', sa.String(length=16), nullable=False),
    sa.Column('to_unit', sa.String(length=16), nullable=False),
    sa.Column('factor', sa.Numeric(precision=24, scale=10), nullable=False),
    sa.Column('dimension', sa.String(length=24), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('from_unit', 'to_unit', name='uq_unit_conversion')
    )
    op.create_table('supplier',
    sa.Column('name', sa.String(length=240), nullable=False),
    sa.Column('name_ar', sa.String(length=240), nullable=False),
    sa.Column('legal_name', sa.String(length=240), nullable=False),
    sa.Column('country', sa.String(length=2), nullable=False),
    sa.Column('city', sa.String(length=120), nullable=False),
    sa.Column('address', sa.String(length=255), nullable=False),
    sa.Column('website', sa.String(length=255), nullable=False),
    sa.Column('domain', sa.String(length=160), nullable=True),
    sa.Column('vat_id', sa.String(length=60), nullable=True),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('source_url', sa.String(length=500), nullable=False),
    sa.Column('confidence', sa.Numeric(precision=5, scale=4), nullable=False),
    sa.Column('languages', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('categories', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('rating_price', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('rating_lead_time', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('rating_quality', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('rating_responsiveness', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('overall_score', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('sanctions_status', sa.String(length=24), nullable=False),
    sa.Column('sanctions_checked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('embedding', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=True),
    sa.Column('flags', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('pending_profile', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_supplier_domain', 'supplier', ['domain'], unique=False)
    op.create_index('ix_supplier_name', 'supplier', ['name'], unique=False)
    op.create_index('ix_supplier_tenant_id', 'supplier', ['tenant_id'], unique=False)
    op.create_table('supplier_contact',
    sa.Column('supplier_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('role', sa.String(length=80), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('phone', sa.String(length=64), nullable=False),
    sa.Column('key_id', sa.String(length=40), nullable=False),
    sa.Column('language', sa.String(length=8), nullable=False),
    sa.Column('is_primary', sa.Boolean(), nullable=False),
    sa.Column('email_opt_in', sa.Boolean(), nullable=False),
    sa.Column('whatsapp_opt_in', sa.Boolean(), nullable=False),
    sa.Column('pending_update', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_supplier_contact_supplier_id', 'supplier_contact', ['supplier_id'], unique=False)
    op.create_index('ix_supplier_contact_tenant_id', 'supplier_contact', ['tenant_id'], unique=False)
    op.create_table('supplier_certification',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('supplier_id', sa.String(length=36), nullable=False),
    sa.Column('code', sa.String(length=60), nullable=False),
    sa.Column('issuer', sa.String(length=120), nullable=False),
    sa.Column('issued_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('evidence_url', sa.String(length=500), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_supplier_certification_supplier_id', 'supplier_certification', ['supplier_id'], unique=False)
    op.create_index('ix_supplier_certification_tenant_id', 'supplier_certification', ['tenant_id'], unique=False)
    op.create_table('supplier_flag',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('supplier_id', sa.String(length=36), nullable=False),
    sa.Column('flag', sa.String(length=24), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_supplier_flag_supplier_id', 'supplier_flag', ['supplier_id'], unique=False)
    op.create_index('ix_supplier_flag_tenant_id', 'supplier_flag', ['tenant_id'], unique=False)
    op.create_table('supplier_rating_history',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('supplier_id', sa.String(length=36), nullable=False),
    sa.Column('price', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('lead_time', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('quality', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('responsiveness', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('overall', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('source', sa.String(length=60), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_supplier_rating_history_supplier_id', 'supplier_rating_history', ['supplier_id'], unique=False)
    op.create_index('ix_supplier_rating_history_tenant_id', 'supplier_rating_history', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_supplier_rating_history_tenant_id', table_name='supplier_rating_history')
    op.drop_index('ix_supplier_rating_history_supplier_id', table_name='supplier_rating_history')
    op.drop_table('supplier_rating_history')
    op.drop_index('ix_supplier_flag_tenant_id', table_name='supplier_flag')
    op.drop_index('ix_supplier_flag_supplier_id', table_name='supplier_flag')
    op.drop_table('supplier_flag')
    op.drop_index('ix_supplier_certification_tenant_id', table_name='supplier_certification')
    op.drop_index('ix_supplier_certification_supplier_id', table_name='supplier_certification')
    op.drop_table('supplier_certification')
    op.drop_index('ix_supplier_contact_tenant_id', table_name='supplier_contact')
    op.drop_index('ix_supplier_contact_supplier_id', table_name='supplier_contact')
    op.drop_table('supplier_contact')
    op.drop_index('ix_supplier_tenant_id', table_name='supplier')
    op.drop_index('ix_supplier_name', table_name='supplier')
    op.drop_index('ix_supplier_domain', table_name='supplier')
    op.drop_table('supplier')
    op.drop_table('unit_conversion')
    op.drop_index('ix_item_tenant_id', table_name='item')
    op.drop_table('item')
    op.drop_index('ix_category_tenant_id', table_name='category')
    op.drop_table('category')
    op.drop_table('taxonomy_node')
    op.drop_index('ix_plant_tenant_id', table_name='plant')
    op.drop_table('plant')

