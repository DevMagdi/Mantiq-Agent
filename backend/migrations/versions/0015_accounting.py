"""accounting: chart of accounts, cost centres, journal entries, audit packages and P2P stages

Revision ID: 0015_accounting
Revises: 0014_drop_theme_preference
Create Date: 2026-10-31 00:00:00

The books are the last gate of the purchase-to-pay chain, so this revision adds a controlled
double-entry ledger (draft -> posted -> reversed, never updated in place), the analytical dimensions
the cost reports need, the auditor document package, and an append-only history of the eleven P2P
stages. It also seeds a standard procurement chart of accounts and an "unallocated" cost centre for
every existing tenant, and grants the new permissions to the new controller / auditor roles.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

revision: str = "0015_accounting"
down_revision: str | None = "0014_drop_theme_preference"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_COLUMN = sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), "postgresql")

# Standard procurement chart of accounts. Codes are stable and referenced by name in the services.
CHART = [
    ("110100", "Inventory — raw materials", "المخزون - مواد خام", "asset", "debit", False),
    ("110200", "GR/IR clearing", "حساب الاستلام غير المفوتر", "asset", "debit", True),
    ("120100", "VAT input — recoverable", "ضريبة القيمة المضافة - مشتريات", "asset", "debit", False),
    ("210100", "Accounts payable — trade", "الدائنون - الموردون", "liability", "credit", True),
    ("220100", "Accrued expenses", "مصروفات مستحقة", "liability", "credit", False),
    ("100100", "Bank — operating account", "البنك - الحساب الجاري", "asset", "debit", False),
    ("510100", "Materials consumed", "تكلفة المواد المستهلكة", "expense", "debit", False),
    ("520100", "Freight and clearing", "الشحن والتخليص", "expense", "debit", False),
    ("530100", "Purchase price variance", "فرق أسعار الشراء", "expense", "debit", False),
    ("540100", "Rounding differences", "فروق التقريب", "expense", "debit", False),
    ("300100", "Retained earnings", "الأرباح المحتجزة", "equity", "credit", False),
]

UNALLOCATED = ("CC-000", "Unallocated", "غير موزّع")

TENANT_TABLES = ("gl_account", "cost_center", "journal_entry", "journal_line", "cost_allocation",
                 "audit_package", "p2p_stage_event")


def upgrade() -> None:
    op.add_column("purchase_requisition", sa.Column("department", sa.String(length=120),
                                                    nullable=False, server_default=""))
    # The backfill is done; the column keeps no server default so the model stays the only truth.
    with op.batch_alter_table("purchase_requisition") as batch:
        batch.alter_column("department", server_default=None)
    op.add_column("purchase_requisition", sa.Column("assigned_to", sa.String(length=36), nullable=True))
    op.add_column("purchase_requisition", sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_purchase_requisition_assigned_to", "purchase_requisition", ["assigned_to"])

    op.create_table(
        "gl_account",
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("name_ar", sa.String(length=160), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("normal_balance", sa.String(length=6), nullable=False),
        sa.Column("parent_code", sa.String(length=20), nullable=True),
        sa.Column("is_control", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_gl_account_tenant_code"),
    )
    op.create_index("ix_gl_account_code", "gl_account", ["code"])
    op.create_index("ix_gl_account_tenant_id", "gl_account", ["tenant_id"])

    op.create_table(
        "cost_center",
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("name_ar", sa.String(length=160), nullable=False),
        sa.Column("plant_id", sa.String(length=36), nullable=True),
        sa.Column("manager_id", sa.String(length=36), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_cost_center_tenant_code"),
    )
    op.create_index("ix_cost_center_code", "cost_center", ["code"])
    op.create_index("ix_cost_center_tenant_id", "cost_center", ["tenant_id"])

    op.create_table(
        "journal_entry",
        sa.Column("number", sa.String(length=40), nullable=False),
        sa.Column("entry_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("memo", sa.String(length=400), nullable=False),
        sa.Column("memo_ar", sa.String(length=400), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("source_type", sa.String(length=24), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=True),
        sa.Column("po_id", sa.String(length=36), nullable=True),
        sa.Column("invoice_id", sa.String(length=36), nullable=True),
        sa.Column("receipt_id", sa.String(length=36), nullable=True),
        sa.Column("supplier_id", sa.String(length=36), nullable=True),
        sa.Column("match_snapshot", JSON_COLUMN, nullable=False),
        sa.Column("total_debit", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("total_credit", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("posted_by", sa.String(length=36), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reversal_of", sa.String(length=36), nullable=True),
        sa.Column("reversed_by", sa.String(length=36), nullable=True),
        sa.Column("reversal_reason", sa.Text(), nullable=False),
        sa.Column("lines", JSON_COLUMN, nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "number", name="uq_journal_entry_number"),
    )
    for column in ("tenant_id", "number", "status", "source_type", "source_id", "po_id", "invoice_id",
                   "receipt_id", "supplier_id", "reversal_of"):
        op.create_index(f"ix_journal_entry_{column}", "journal_entry", [column])

    op.create_table(
        "journal_line",
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("entry_id", sa.String(length=36), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("account_code", sa.String(length=20), nullable=False),
        sa.Column("description", sa.String(length=400), nullable=False),
        sa.Column("debit", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("credit", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("cost_center_code", sa.String(length=20), nullable=True),
        sa.Column("unit_number", sa.String(length=40), nullable=True),
        sa.Column("item_id", sa.String(length=36), nullable=True),
        sa.Column("supplier_id", sa.String(length=36), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("tenant_id", "entry_id", "account_code", "cost_center_code", "unit_number",
                   "item_id", "supplier_id"):
        op.create_index(f"ix_journal_line_{column}", "journal_line", [column])

    op.create_table(
        "cost_allocation",
        sa.Column("journal_entry_id", sa.String(length=36), nullable=False),
        sa.Column("po_id", sa.String(length=36), nullable=True),
        sa.Column("invoice_id", sa.String(length=36), nullable=True),
        sa.Column("receipt_id", sa.String(length=36), nullable=True),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(length=400), nullable=False),
        sa.Column("cost_center_code", sa.String(length=20), nullable=False),
        sa.Column("unit_number", sa.String(length=40), nullable=True),
        sa.Column("item_id", sa.String(length=36), nullable=True),
        sa.Column("amount", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("entry_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("tenant_id", "journal_entry_id", "po_id", "invoice_id", "receipt_id",
                   "cost_center_code", "unit_number", "item_id"):
        op.create_index(f"ix_cost_allocation_{column}", "cost_allocation", [column])

    op.create_table(
        "audit_package",
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("po_id", sa.String(length=36), nullable=False),
        sa.Column("invoice_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("components", JSON_COLUMN, nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("summary", JSON_COLUMN, nullable=False),
        sa.Column("generated_by", sa.String(length=36), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewer_id", sa.String(length=36), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision", sa.String(length=16), nullable=True),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_audit_package_code"),
    )
    for column in ("tenant_id", "code", "po_id", "invoice_id"):
        op.create_index(f"ix_audit_package_{column}", "audit_package", [column])

    op.create_table(
        "p2p_stage_event",
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("po_id", sa.String(length=36), nullable=False),
        sa.Column("rfq_id", sa.String(length=36), nullable=True),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("reached_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("evidence", JSON_COLUMN, nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "po_id", "stage", name="uq_p2p_stage_event"),
    )
    for column in ("tenant_id", "po_id", "rfq_id", "stage"):
        op.create_index(f"ix_p2p_stage_event_{column}", "p2p_stage_event", [column])

    bind = op.get_bind()
    now = datetime.now(UTC)

    # Standard chart of accounts + the unallocated cost centre for every tenant that exists today.
    tenants = [row[0] for row in bind.execute(sa.text("SELECT id FROM tenant")).fetchall()]
    accounts: list[dict] = []
    centres: list[dict] = []
    for tenant_id in tenants:
        for code, name, name_ar, kind, normal, control in CHART:
            accounts.append({
                "id": str(uuid.uuid4()), "tenant_id": tenant_id, "code": code, "name": name,
                "name_ar": name_ar, "kind": kind, "normal_balance": normal, "parent_code": None,
                "is_control": control, "is_active": True, "description": "",
                "version": 1, "created_at": now, "updated_at": now,
            })
        centres.append({
            "id": str(uuid.uuid4()), "tenant_id": tenant_id, "code": UNALLOCATED[0],
            "name": UNALLOCATED[1], "name_ar": UNALLOCATED[2], "plant_id": None, "manager_id": None,
            "is_active": True, "description": "Default dimension until a real cost centre is chosen.",
            "version": 1, "created_at": now, "updated_at": now,
        })
    if accounts:
        bind.execute(sa.text(
            "INSERT INTO gl_account (id, tenant_id, code, name, name_ar, kind, normal_balance, parent_code, "
            "is_control, is_active, description, version, created_at, updated_at) VALUES "
            "(:id, :tenant_id, :code, :name, :name_ar, :kind, :normal_balance, :parent_code, "
            ":is_control, :is_active, :description, :version, :created_at, :updated_at)"
        ), accounts)
    if centres:
        bind.execute(sa.text(
            "INSERT INTO cost_center (id, tenant_id, code, name, name_ar, plant_id, manager_id, is_active, "
            "description, version, created_at, updated_at) VALUES "
            "(:id, :tenant_id, :code, :name, :name_ar, :plant_id, :manager_id, :is_active, "
            ":description, :version, :created_at, :updated_at)"
        ), centres)

    # New permissions per role. The matrix lives in app/core/rbac.py and is re-synced at every
    # bootstrap; this block only has to make the permissions exist for a database that is migrated
    # but not bootstrapped yet (and it must stay frozen, like every other migration).
    new_permissions = {
        "procurement_manager": ["accounting:read", "accounting:write", "cost:read", "cost:write",
                                "audit_package:read"],
        "buyer": ["accounting:read", "cost:read", "audit_package:read"],
        "finance_approver": ["accounting:read", "accounting:write", "cost:read", "audit_package:read"],
        "controller": ["pr:read", "rfq:read", "po:read", "supplier:read", "invoice:create",
                       "invoice:approve", "payment:record", "ledger:read", "accounting:read",
                       "accounting:write", "accounting:post", "accounting:reverse", "cost:read",
                       "cost:write", "audit_package:read", "report:read", "comment:create",
                       "audit:read", "settings:read"],
        "auditor": ["pr:read", "rfq:read", "offer:read", "comparison:read", "po:read", "supplier:read",
                    "ledger:read", "accounting:read", "cost:read", "audit_package:read",
                    "audit_package:review", "approval:read", "approval:decide", "report:read",
                    "comment:create", "audit:read", "settings:read"],
    }
    new_roles = {
        "controller": ("Financial Controller", "مدير الحسابات"),
        "auditor": ("Internal Auditor", "المراجع الداخلي"),
    }

    role_table = sa.table("role", sa.column("id", sa.String), sa.column("code", sa.String),
                          sa.column("name", sa.String),
                          sa.column("name_ar", sa.String), sa.column("description", sa.String),
                          sa.column("permissions", sa.JSON), sa.column("is_system", sa.Boolean),
                          sa.column("created_at", sa.DateTime(timezone=True)),
                          sa.column("updated_at", sa.DateTime(timezone=True)))
    perm_table = sa.table("role_permission", sa.column("id", sa.String), sa.column("role_code", sa.String),
                          sa.column("permission", sa.String),
                          sa.column("created_at", sa.DateTime(timezone=True)),
                          sa.column("updated_at", sa.DateTime(timezone=True)))
    existing_roles = {row[0] for row in bind.execute(sa.text("SELECT code FROM role")).fetchall()}
    missing_roles = [code for code in new_roles if code not in existing_roles]
    if missing_roles:
        op.bulk_insert(role_table, [{
            "id": str(uuid.uuid4()), "code": code,
            "name": new_roles[code][0], "name_ar": new_roles[code][1],
            "description": f"{new_roles[code][0]} role (phase 2 accounting).",
            "permissions": sorted(new_permissions[code]), "is_system": True,
            "created_at": now, "updated_at": now,
        } for code in missing_roles])
    permission_rows: list[dict] = []
    for role_code, permissions in new_permissions.items():
        if role_code not in existing_roles and role_code not in missing_roles:
            continue
        already = {row[0] for row in bind.execute(
            sa.text("SELECT permission FROM role_permission WHERE role_code = :code"), {"code": role_code}
        ).fetchall()}
        permission_rows.extend(
            {"id": str(uuid.uuid4()), "role_code": role_code, "permission": permission,
             "created_at": now, "updated_at": now}
            for permission in permissions if permission not in already
        )
        if role_code in existing_roles:
            current = bind.execute(sa.text("SELECT permissions FROM role WHERE code = :code"),
                                   {"code": role_code}).scalar_one()
            if isinstance(current, str):
                current = json.loads(current)
            merged = list(dict.fromkeys([*(current or []), *permissions]))
            bind.execute(sa.text("UPDATE role SET permissions = :perms, updated_at = :now WHERE code = :code"),
                         {"perms": json.dumps(merged), "now": now, "code": role_code})
    if permission_rows:
        op.bulk_insert(perm_table, permission_rows)

    chain_table = sa.table("approval_chain", sa.column("id", sa.String), sa.column("tenant_id", sa.String),
                           sa.column("name", sa.String), sa.column("category_code", sa.String),
                           sa.column("min_amount", sa.Numeric), sa.column("max_amount", sa.Numeric),
                           sa.column("currency", sa.String), sa.column("is_active", sa.Boolean),
                           sa.column("version", sa.Integer), sa.column("created_at", sa.DateTime(timezone=True)),
                           sa.column("updated_at", sa.DateTime(timezone=True)))
    step_table = sa.table("approval_step", sa.column("id", sa.String), sa.column("tenant_id", sa.String),
                          sa.column("chain_id", sa.String), sa.column("step_no", sa.Integer),
                          sa.column("role_code", sa.String), sa.column("threshold_amount", sa.Numeric),
                          sa.column("sla_hours", sa.Integer),
                          sa.column("created_at", sa.DateTime(timezone=True)),
                          sa.column("updated_at", sa.DateTime(timezone=True)))
    chains: list[dict] = []
    steps: list[dict] = []
    for tenant_id in tenants:
        exists = bind.execute(sa.text(
            "SELECT count(*) FROM approval_chain WHERE tenant_id = :t AND name = :n"
        ), {"t": tenant_id, "n": "High value with audit"}).scalar_one()
        if exists:
            continue
        chain_id = str(uuid.uuid4())
        chains.append({
            "id": chain_id, "tenant_id": tenant_id, "name": "High value with audit",
            "category_code": None, "min_amount": 1_000_000, "max_amount": None, "currency": "EGP",
            "is_active": True, "version": 1, "created_at": now, "updated_at": now,
        })
        for step_no, role_code, threshold in ((1, "procurement_manager", 0),
                                              (2, "finance_approver", 250_000),
                                              (3, "auditor", 1_000_000)):
            steps.append({
                "id": str(uuid.uuid4()), "tenant_id": tenant_id, "chain_id": chain_id,
                "step_no": step_no, "role_code": role_code, "threshold_amount": threshold,
                "sla_hours": 48, "created_at": now, "updated_at": now,
            })
    if chains:
        op.bulk_insert(chain_table, chains)
        op.bulk_insert(step_table, steps)

    # Tenant isolation on the new tables (PostgreSQL only; SQLite is the test/dev fallback).
    if bind.dialect.name == "postgresql":
        op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON gl_account, cost_center, journal_entry, "
                   "journal_line, cost_allocation, audit_package, p2p_stage_event TO procureai_app")
        # The books are append-only for the application role: no UPDATE/DELETE on postings.
        op.execute("REVOKE UPDATE, DELETE ON journal_line, cost_allocation, p2p_stage_event FROM procureai_app")
        for table in TENANT_TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
            op.execute(f"CREATE POLICY tenant_isolation_{table} ON {table} "
                       "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')) "
                       "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))")


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM approval_step WHERE role_code = 'auditor'"))
    bind.execute(sa.text("DELETE FROM approval_chain WHERE name = 'High value with audit'"))
    for code in ("controller", "auditor"):
        bind.execute(sa.text("DELETE FROM role_permission WHERE role_code = :code"), {"code": code})
        bind.execute(sa.text("DELETE FROM role WHERE code = :code"), {"code": code})
    bind.execute(sa.text("DELETE FROM role_permission WHERE permission LIKE 'accounting:%' "
                         "OR permission LIKE 'cost:%' OR permission LIKE 'audit_package:%'"))

    op.drop_index("ix_p2p_stage_event_stage", table_name="p2p_stage_event")
    op.drop_index("ix_p2p_stage_event_po", table_name="p2p_stage_event")
    op.drop_table("p2p_stage_event")
    op.drop_index("ix_audit_package_invoice_id", table_name="audit_package")
    op.drop_index("ix_audit_package_po", table_name="audit_package")
    op.drop_index("ix_audit_package_code", table_name="audit_package")
    op.drop_table("audit_package")
    op.drop_index("ix_cost_allocation_po", table_name="cost_allocation")
    op.drop_index("ix_cost_allocation_item", table_name="cost_allocation")
    op.drop_index("ix_cost_allocation_center", table_name="cost_allocation")
    op.drop_index("ix_cost_allocation_entry", table_name="cost_allocation")
    op.drop_table("cost_allocation")
    op.drop_index("ix_journal_line_item", table_name="journal_line")
    op.drop_index("ix_journal_line_cost_center", table_name="journal_line")
    op.drop_index("ix_journal_line_account", table_name="journal_line")
    op.drop_index("ix_journal_line_entry_id", table_name="journal_line")
    op.drop_table("journal_line")
    op.drop_index("ix_journal_entry_source", table_name="journal_entry")
    op.drop_index("ix_journal_entry_invoice_id", table_name="journal_entry")
    op.drop_index("ix_journal_entry_po_id", table_name="journal_entry")
    op.drop_index("ix_journal_entry_tenant_status", table_name="journal_entry")
    op.drop_index("ix_journal_entry_number", table_name="journal_entry")
    op.drop_table("journal_entry")
    op.drop_index("ix_cost_center_tenant_id", table_name="cost_center")
    op.drop_index("ix_cost_center_code", table_name="cost_center")
    op.drop_table("cost_center")
    op.drop_index("ix_gl_account_tenant_id", table_name="gl_account")
    op.drop_index("ix_gl_account_code", table_name="gl_account")
    op.drop_table("gl_account")

    op.drop_index("ix_purchase_requisition_assigned_to", table_name="purchase_requisition")
    op.drop_column("purchase_requisition", "assigned_at")
    op.drop_column("purchase_requisition", "assigned_to")
    op.drop_column("purchase_requisition", "department")
