"""tenancy and identity

Revision ID: 0002_tenancy_identity
Revises: 0001_extensions_and_base
Create Date: 2026-10-30 00:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import Text
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0002_tenancy_identity'
down_revision: str | None = '0001_extensions_and_base'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('tenant',
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('slug', sa.String(length=80), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('plan_code', sa.String(length=40), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('locale', sa.String(length=8), nullable=False),
    sa.Column('timezone', sa.String(length=64), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('data_region', sa.String(length=32), nullable=False),
    sa.Column('settings', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('slug')
    )
    op.create_table('app_user',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('hashed_password', sa.String(length=255), nullable=False),
    sa.Column('full_name', sa.String(length=160), nullable=False),
    sa.Column('full_name_ar', sa.String(length=160), nullable=False),
    sa.Column('job_title', sa.String(length=120), nullable=False),
    sa.Column('primary_role', sa.String(length=40), nullable=False),
    sa.Column('roles', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('is_superadmin', sa.Boolean(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('must_change_password', sa.Boolean(), nullable=False),
    sa.Column('mfa_enabled', sa.Boolean(), nullable=False),
    sa.Column('totp_secret', sa.String(length=64), nullable=False),
    sa.Column('backup_codes', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('locale', sa.String(length=8), nullable=False),
    sa.Column('theme', sa.String(length=12), nullable=False),
    sa.Column('numerals', sa.String(length=12), nullable=False),
    sa.Column('preferred_currency', sa.String(length=3), nullable=False),
    sa.Column('phone', sa.String(length=40), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('failed_logins', sa.Integer(), nullable=False),
    sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('notification_prefs', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('plant_id', sa.String(length=36), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'email', name='uq_user_tenant_email')
    )
    op.create_index('ix_app_user_email', 'app_user', ['email'], unique=False)
    op.create_index('ix_app_user_tenant_id', 'app_user', ['tenant_id'], unique=False)
    op.create_table('role',
    sa.Column('code', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('name_ar', sa.String(length=80), nullable=False),
    sa.Column('description', sa.String(length=255), nullable=False),
    sa.Column('permissions', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('is_system', sa.Boolean(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    op.create_table('role_permission',
    sa.Column('role_code', sa.String(length=40), nullable=False),
    sa.Column('permission', sa.String(length=80), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('role_code', 'permission', name='uq_role_permission')
    )
    op.create_index('ix_role_permission_role_code', 'role_permission', ['role_code'], unique=False)
    op.create_table('user_role_assignment',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('role_code', sa.String(length=40), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_user_role_assignment_tenant_id', 'user_role_assignment', ['tenant_id'], unique=False)
    op.create_index('ix_user_role_assignment_user_id', 'user_role_assignment', ['user_id'], unique=False)
    op.create_table('refresh_session',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('jti', sa.String(length=80), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('user_agent', sa.String(length=255), nullable=False),
    sa.Column('ip', sa.String(length=64), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('jti')
    )
    op.create_index('ix_refresh_session_tenant_id', 'refresh_session', ['tenant_id'], unique=False)
    op.create_index('ix_refresh_session_user_id', 'refresh_session', ['user_id'], unique=False)
    op.create_table('api_key',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('prefix', sa.String(length=16), nullable=False),
    sa.Column('hashed_key', sa.String(length=128), nullable=False),
    sa.Column('scopes', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=True),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_api_key_prefix', 'api_key', ['prefix'], unique=False)
    op.create_index('ix_api_key_tenant_id', 'api_key', ['tenant_id'], unique=False)
    op.create_table('approval_limit',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('role_code', sa.String(length=40), nullable=False),
    sa.Column('category_code', sa.String(length=60), nullable=True),
    sa.Column('max_amount', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_approval_limit_tenant_id', 'approval_limit', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_approval_limit_tenant_id', table_name='approval_limit')
    op.drop_table('approval_limit')
    op.drop_index('ix_api_key_tenant_id', table_name='api_key')
    op.drop_index('ix_api_key_prefix', table_name='api_key')
    op.drop_table('api_key')
    op.drop_index('ix_refresh_session_user_id', table_name='refresh_session')
    op.drop_index('ix_refresh_session_tenant_id', table_name='refresh_session')
    op.drop_table('refresh_session')
    op.drop_index('ix_user_role_assignment_user_id', table_name='user_role_assignment')
    op.drop_index('ix_user_role_assignment_tenant_id', table_name='user_role_assignment')
    op.drop_table('user_role_assignment')
    op.drop_index('ix_role_permission_role_code', table_name='role_permission')
    op.drop_table('role_permission')
    op.drop_table('role')
    op.drop_index('ix_app_user_tenant_id', table_name='app_user')
    op.drop_index('ix_app_user_email', table_name='app_user')
    op.drop_table('app_user')
    op.drop_table('tenant')

