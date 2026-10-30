"""post-PO lifecycle: goods receipts, supplier invoices and the supplier ledger

Revision ID: 0013_post_po_lifecycle
Revises: 0012_bootstrap_reference_data
Create Date: 2026-10-30 00:00:00

Delivery and settlement happen after the award, so these tables are written only by a named
human action (warehouse, requester, finance). The same revision grants the new permissions and
adds the ``warehouse`` role that operates the receiving desk.
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

revision: str = "0013_post_po_lifecycle"
down_revision: str | None = "0012_bootstrap_reference_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_COLUMN = sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), "postgresql")

# Permissions introduced by this revision, per role. `admin`/`superadmin` hold every permission.
NEW_PERMISSIONS = {
    "admin": [
        "po:read", "po:update", "receipt:create", "receipt:confirm", "invoice:create",
        "invoice:approve", "payment:record", "ledger:read",
    ],
    "procurement_manager": [
        "po:read", "po:update", "receipt:create", "receipt:confirm", "invoice:create",
        "invoice:approve", "ledger:read",
    ],
    "buyer": ["po:read", "invoice:create", "ledger:read"],
    "requester": ["po:read", "receipt:confirm"],
    "finance_approver": [
        "po:read", "supplier:read", "invoice:create", "invoice:approve", "payment:record", "ledger:read",
    ],
}

WAREHOUSE_ROLE = (
    "warehouse",
    "Warehouse / Stores",
    "أمين المخزن",
    ["po:read", "receipt:create", "supplier:read", "comment:create", "report:read", "settings:read"],
)

role_table = sa.table("role", sa.column("id", sa.String), sa.column("code", sa.String),
                      sa.column("name", sa.String), sa.column("name_ar", sa.String),
                      sa.column("description", sa.String), sa.column("permissions", sa.JSON),
                      sa.column("is_system", sa.Boolean), sa.column("created_at", sa.DateTime(timezone=True)),
                      sa.column("updated_at", sa.DateTime(timezone=True)))
perm_table = sa.table("role_permission", sa.column("id", sa.String), sa.column("role_code", sa.String),
                      sa.column("permission", sa.String), sa.column("created_at", sa.DateTime(timezone=True)),
                      sa.column("updated_at", sa.DateTime(timezone=True)))


def _permissions(raw) -> list[str]:  # noqa: ANN001
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:  # pragma: no cover - defensive
            return []
    return list(raw)


def upgrade() -> None:
    op.add_column("purchase_order", sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "goods_receipt",
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("po_id", sa.String(length=36), nullable=False),
        sa.Column("supplier_id", sa.String(length=36), nullable=False),
        sa.Column("delivery_note_ref", sa.String(length=80), nullable=False),
        sa.Column("carrier", sa.String(length=120), nullable=False),
        sa.Column("warehouse", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("received_by", sa.String(length=36), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lines", JSON_COLUMN, nullable=False),
        sa.Column("discrepancy", JSON_COLUMN, nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("confirmed_by", sa.String(length=36), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmation_checks", JSON_COLUMN, nullable=False),
        sa.Column("confirmation_comment", sa.Text(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_goods_receipt_code", "goods_receipt", ["code"], unique=False)
    op.create_index("ix_goods_receipt_po_id", "goods_receipt", ["po_id"], unique=False)
    op.create_index("ix_goods_receipt_po_received", "goods_receipt", ["po_id", "received_at"], unique=False)
    op.create_index("ix_goods_receipt_supplier_id", "goods_receipt", ["supplier_id"], unique=False)
    op.create_index("ix_goods_receipt_tenant_id", "goods_receipt", ["tenant_id"], unique=False)

    op.create_table(
        "supplier_invoice",
        sa.Column("po_id", sa.String(length=36), nullable=False),
        sa.Column("supplier_id", sa.String(length=36), nullable=False),
        sa.Column("number", sa.String(length=60), nullable=False),
        sa.Column("invoice_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("subtotal", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("tax_amount", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("total", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("lines", JSON_COLUMN, nullable=False),
        sa.Column("match_result", JSON_COLUMN, nullable=False),
        sa.Column("payment_suggestion", JSON_COLUMN, nullable=False),
        sa.Column("approved_by", sa.String(length=36), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_amount", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "number", name="uq_supplier_invoice_number"),
    )
    op.create_index("ix_supplier_invoice_number", "supplier_invoice", ["number"], unique=False)
    op.create_index("ix_supplier_invoice_po_id", "supplier_invoice", ["po_id"], unique=False)
    op.create_index("ix_supplier_invoice_po_status", "supplier_invoice", ["po_id", "status"], unique=False)
    op.create_index("ix_supplier_invoice_supplier_id", "supplier_invoice", ["supplier_id"], unique=False)
    op.create_index("ix_supplier_invoice_tenant_id", "supplier_invoice", ["tenant_id"], unique=False)

    op.create_table(
        "supplier_ledger_entry",
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("supplier_id", sa.String(length=36), nullable=False),
        sa.Column("po_id", sa.String(length=36), nullable=True),
        sa.Column("invoice_id", sa.String(length=36), nullable=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("entry_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("method", sa.String(length=40), nullable=False),
        sa.Column("reference", sa.String(length=120), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ledger_supplier_entry", "supplier_ledger_entry",
                    ["tenant_id", "supplier_id", "entry_date"], unique=False)
    op.create_index("ix_supplier_ledger_entry_invoice_id", "supplier_ledger_entry", ["invoice_id"], unique=False)
    op.create_index("ix_supplier_ledger_entry_po_id", "supplier_ledger_entry", ["po_id"], unique=False)
    op.create_index("ix_supplier_ledger_entry_supplier_id", "supplier_ledger_entry", ["supplier_id"], unique=False)
    op.create_index("ix_supplier_ledger_entry_tenant_id", "supplier_ledger_entry", ["tenant_id"], unique=False)

    # ---------------------------------------------------------------- RBAC: new permissions + role
    NOW = datetime.now(UTC)
    bind = op.get_bind()
    existing_roles = {row[0] for row in bind.execute(sa.text("SELECT code FROM role")).fetchall()}

    new_perms: list[dict] = []
    for role_code, permissions in NEW_PERMISSIONS.items():
        if role_code not in existing_roles:
            continue
        current = _permissions(bind.execute(
            sa.text("SELECT permissions FROM role WHERE code = :code"), {"code": role_code}
        ).scalar_one())
        merged = list(dict.fromkeys([*current, *permissions]))
        bind.execute(sa.text("UPDATE role SET permissions = :perms, updated_at = :now WHERE code = :code"),
                     {"perms": json.dumps(merged), "now": NOW, "code": role_code})
        already = {row[0] for row in bind.execute(
            sa.text("SELECT permission FROM role_permission WHERE role_code = :code"), {"code": role_code}
        ).fetchall()}
        new_perms.extend(
            {"id": str(uuid.uuid4()), "role_code": role_code, "permission": permission,
             "created_at": NOW, "updated_at": NOW}
            for permission in permissions if permission not in already
        )

    code, name, name_ar, permissions = WAREHOUSE_ROLE
    if code not in existing_roles:
        op.bulk_insert(role_table, [{
            "id": str(uuid.uuid4()), "code": code, "name": name, "name_ar": name_ar,
            "description": "Records physical goods receipts at the warehouse (no pricing access).",
            "permissions": permissions, "is_system": True, "created_at": NOW, "updated_at": NOW,
        }])
        new_perms.extend(
            {"id": str(uuid.uuid4()), "role_code": code, "permission": permission,
             "created_at": NOW, "updated_at": NOW}
            for permission in permissions
        )
    if new_perms:
        op.bulk_insert(perm_table, new_perms)


def downgrade() -> None:
    bind = op.get_bind()
    added = [permission for permissions in NEW_PERMISSIONS.values() for permission in permissions]
    added.extend(WAREHOUSE_ROLE[3])
    for permission in sorted(set(added)):
        bind.execute(sa.text("DELETE FROM role_permission WHERE permission = :permission"),
                     {"permission": permission})
    bind.execute(sa.text("DELETE FROM role_permission WHERE role_code = :code"), {"code": WAREHOUSE_ROLE[0]})
    bind.execute(sa.text("DELETE FROM role WHERE code = :code"), {"code": WAREHOUSE_ROLE[0]})
    for role_code, permissions in NEW_PERMISSIONS.items():
        current = _permissions(bind.execute(
            sa.text("SELECT permissions FROM role WHERE code = :code"), {"code": role_code}
        ).scalar_one_or_none())
        if not current:
            continue
        kept = [permission for permission in current if permission not in permissions]
        bind.execute(sa.text("UPDATE role SET permissions = :perms WHERE code = :code"),
                     {"perms": json.dumps(kept), "code": role_code})

    op.drop_index("ix_supplier_ledger_entry_tenant_id", table_name="supplier_ledger_entry")
    op.drop_index("ix_supplier_ledger_entry_supplier_id", table_name="supplier_ledger_entry")
    op.drop_index("ix_supplier_ledger_entry_po_id", table_name="supplier_ledger_entry")
    op.drop_index("ix_supplier_ledger_entry_invoice_id", table_name="supplier_ledger_entry")
    op.drop_index("ix_ledger_supplier_entry", table_name="supplier_ledger_entry")
    op.drop_table("supplier_ledger_entry")

    op.drop_index("ix_supplier_invoice_tenant_id", table_name="supplier_invoice")
    op.drop_index("ix_supplier_invoice_supplier_id", table_name="supplier_invoice")
    op.drop_index("ix_supplier_invoice_po_status", table_name="supplier_invoice")
    op.drop_index("ix_supplier_invoice_po_id", table_name="supplier_invoice")
    op.drop_index("ix_supplier_invoice_number", table_name="supplier_invoice")
    op.drop_table("supplier_invoice")

    op.drop_index("ix_goods_receipt_tenant_id", table_name="goods_receipt")
    op.drop_index("ix_goods_receipt_supplier_id", table_name="goods_receipt")
    op.drop_index("ix_goods_receipt_po_received", table_name="goods_receipt")
    op.drop_index("ix_goods_receipt_po_id", table_name="goods_receipt")
    op.drop_index("ix_goods_receipt_code", table_name="goods_receipt")
    op.drop_table("goods_receipt")

    op.drop_column("purchase_order", "closed_at")
