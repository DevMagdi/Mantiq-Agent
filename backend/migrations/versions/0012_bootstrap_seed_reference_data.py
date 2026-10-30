"""bootstrap reference data (roles, permissions, plans, taxonomy, units, templates, call policy)

Revision ID: 0012_bootstrap_seed_reference_data
Revises: 0011_indexes_search_vectors
Create Date: 2026-10-30 00:00:00

Reference data only — no credentials, no tenant-specific rows. Re-runnable by design
(ON CONFLICT DO NOTHING via the unique keys) and fully reversible.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0012_bootstrap_seed_reference_data"
down_revision: str | None = "0011_indexes_search_vectors"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLES = {
    "superadmin": ("Platform SuperAdmin", "مدير المنصة"),
    "admin": ("Administrator", "مدير النظام"),
    "procurement_manager": ("Procurement Manager", "مدير المشتريات"),
    "buyer": ("Buyer", "مسؤول شراء"),
    "requester": ("Plant / Department Requester", "طالب من المصنع أو القسم"),
    "finance_approver": ("Finance Approver", "معتمد مالي"),
}

PERMISSIONS = {
    "superadmin": ["*"],
    "admin": ["pr:read", "pr:create", "pr:update", "pr:delete", "pr:verify", "rfq:read", "rfq:create",
              "rfq:update", "rfq:send", "rfq:cancel", "supplier:read", "supplier:create", "supplier:update",
              "supplier:import", "supplier:discovery", "supplier:approve", "offer:read", "offer:verify",
              "negotiation:read", "negotiation:policy", "negotiation:draft", "negotiation:send",
              "negotiation:auto", "comparison:read", "approval:read", "approval:decide", "approval:delegate",
              "po:create", "po:export", "report:read", "comment:create", "audit:read", "admin:users",
              "admin:roles", "admin:limits", "admin:templates", "admin:integrations", "admin:api_keys",
              "admin:billing", "admin:llm", "settings:read", "settings:update"],
    "procurement_manager": ["pr:read", "pr:create", "pr:update", "pr:verify", "rfq:read", "rfq:create",
                            "rfq:update", "rfq:send", "rfq:cancel", "supplier:read", "supplier:create",
                            "supplier:update", "supplier:import", "supplier:discovery", "supplier:approve",
                            "offer:read", "offer:verify", "negotiation:read", "negotiation:policy",
                            "negotiation:draft", "negotiation:send", "negotiation:auto", "comparison:read",
                            "approval:read", "approval:decide", "approval:delegate", "po:create", "po:export",
                            "report:read", "comment:create", "audit:read", "admin:templates", "settings:read"],
    "buyer": ["pr:read", "pr:create", "pr:update", "pr:verify", "rfq:read", "rfq:create", "rfq:update",
              "rfq:send", "supplier:read", "supplier:create", "supplier:update", "supplier:discovery",
              "offer:read", "offer:verify", "negotiation:read", "negotiation:draft", "negotiation:send",
              "comparison:read", "approval:read", "po:create", "report:read", "comment:create", "settings:read"],
    "requester": ["pr:read", "pr:create", "pr:update", "rfq:read", "supplier:read", "offer:read",
                  "comparison:read", "comment:create", "report:read", "settings:read"],
    "finance_approver": ["pr:read", "rfq:read", "offer:read", "comparison:read", "approval:read",
                         "approval:decide", "approval:delegate", "report:read", "comment:create",
                         "audit:read", "settings:read"],
}

PLANS = [
    ("starter", "Starter", 490, "USD", {"users": 5, "rfqs_per_month": 30, "messages_per_month": 500,
                                        "llm_tokens_per_month": 2000000, "voice_minutes_per_month": 0}),
    ("growth", "Growth", 1490, "USD", {"users": 25, "rfqs_per_month": 200, "messages_per_month": 5000,
                                       "llm_tokens_per_month": 15000000, "voice_minutes_per_month": 200}),
    ("enterprise", "Enterprise", 3900, "USD", {"users": 200, "rfqs_per_month": 2000,
                                               "messages_per_month": 50000,
                                               "llm_tokens_per_month": 100000000,
                                               "voice_minutes_per_month": 2000}),
]

TAXONOMY = [
    ("30000000", "Structures and Building Components", "هياكل ومكونات إنشائية", None, 1, "t"),
    ("30102400", "Rebar / Reinforcing Steel", "حديد تسليح", "30000000", 2, "t"),
    ("30102300", "Steel Sections and Beams", "قطاعات وكمرات حديد", "30000000", 2, "t"),
    ("30111600", "Cement and Mortar", "أسمنت ومونة", "30000000", 2, "t"),
    ("31000000", "Manufacturing Components and Supplies", "مكونات ومستلزمات تصنيع", None, 1, "pcs"),
    ("31171500", "Bearings", "رولمان بلي", "31000000", 2, "pcs"),
    ("31201500", "V-Belts and Pulleys", "سيور وبكرات", "31000000", 2, "pcs"),
    ("40151500", "Industrial Pumps", "مضخات صناعية", None, 2, "pcs"),
    ("24101600", "Lifting Equipment", "معدات رفع", None, 2, "pcs"),
    ("24000000", "Material Handling and Storage", "مناولة وتخزين", None, 1, "pcs"),
    ("24112000", "Packaging Materials", "مواد تعبئة وتغليف", "24000000", 2, "roll"),
    ("24121700", "Woven Bags and Sacks", "شكائر وأكياس منسوجة", "24000000", 2, "pcs"),
    ("12180000", "Lubricants and Greases", "زيوت وشحوم", None, 2, "l"),
    ("15120000", "Fuels", "وقود", None, 2, "l"),
    ("39000000", "Electrical Systems and Lighting", "أنظمة كهربائية وإضاءة", None, 1, "pcs"),
    ("39121600", "Circuit Breakers and Protection", "قواطع وحماية", "39000000", 2, "pcs"),
    ("39112000", "Cables and Wiring", "كابلات وأسلاك", "39000000", 2, "m"),
    ("39131500", "Industrial Lighting", "إضاءة صناعية", "39000000", 2, "pcs"),
    ("47000000", "Cleaning and Hygiene", "تنظيف ونظافة", None, 1, "l"),
    ("53131600", "Personal Protective Equipment", "معدات حماية شخصية", None, 2, "pcs"),
    ("12352100", "Industrial Chemicals", "كيماويات صناعية", None, 2, "kg"),
    ("27000000", "Tools and General Machinery", "عدد وآلات عامة", None, 1, "pcs"),
    ("27112100", "Hand Tools", "عدد يدوية", "27000000", 2, "pcs"),
    ("40141700", "Valves and Fittings", "محابس ووصلات", None, 2, "pcs"),
]

UNITS = [("t", "kg", "1000", "mass"), ("kg", "t", "0.001", "mass"), ("lb", "kg", "0.45359237", "mass"),
         ("kg", "lb", "2.20462262", "mass"), ("t", "lb", "2204.62262", "mass"),
         ("m", "ft", "3.280839895", "length"), ("ft", "m", "0.3048", "length"),
         ("m", "cm", "100", "length"), ("cm", "mm", "10", "length"),
         ("l", "ml", "1000", "volume"), ("m3", "l", "1000", "volume"),
         ("doz", "pcs", "12", "count"), ("pcs", "doz", "0.0833333333", "count"),
         ("bag", "pcs", "1", "count"), ("roll", "pcs", "1", "count"), ("drum", "l", "208", "volume")]

CALL_POLICIES = [
    ("EG", 9, 17, "explicit",
     "Hello, this is an AI assistant calling on behalf of the buyer to collect a quotation. This call may be recorded if you agree.",
     "أهلاً بحضرتك، أنا مساعد ذكاء اصطناعي بتكلم نيابة عن جهة الشراء لجمع عرض سعر. المكالمة قد يتم تسجيلها لو حضرتك موافق.", 480),
    ("SA", 9, 17, "explicit",
     "Hello, this is an AI assistant calling on behalf of the buyer. Do you consent to this call being recorded?",
     "مرحباً، أنا مساعد ذكاء اصطناعي أتصل نيابة عن جهة الشراء. هل توافق على تسجيل المكالمة؟", 420),
    ("AE", 9, 18, "explicit",
     "Hello, this is an AI assistant calling on behalf of the buyer. Do you consent to recording?",
     "مرحباً، أنا مساعد ذكاء اصطناعي أتصل نيابة عن جهة الشراء. هل توافق على التسجيل؟", 420),
]

role_table = sa.table("role", sa.column("id", sa.String), sa.column("code", sa.String),
                      sa.column("name", sa.String), sa.column("name_ar", sa.String),
                      sa.column("description", sa.String), sa.column("permissions", sa.JSON),
                      sa.column("is_system", sa.Boolean), sa.column("created_at", sa.DateTime(timezone=True)),
                      sa.column("updated_at", sa.DateTime(timezone=True)))
perm_table = sa.table("role_permission", sa.column("id", sa.String), sa.column("role_code", sa.String),
                      sa.column("permission", sa.String), sa.column("created_at", sa.DateTime(timezone=True)),
                      sa.column("updated_at", sa.DateTime(timezone=True)))
plan_table = sa.table("plan", sa.column("id", sa.String), sa.column("code", sa.String),
                      sa.column("name", sa.String), sa.column("price_monthly", sa.Numeric),
                      sa.column("currency", sa.String), sa.column("limits", sa.JSON),
                      sa.column("features", sa.JSON), sa.column("is_active", sa.Boolean),
                      sa.column("created_at", sa.DateTime(timezone=True)),
                      sa.column("updated_at", sa.DateTime(timezone=True)))
tax_table = sa.table("taxonomy_node", sa.column("id", sa.String), sa.column("code", sa.String),
                     sa.column("name", sa.String), sa.column("name_ar", sa.String),
                     sa.column("parent_code", sa.String), sa.column("level", sa.Integer),
                     sa.column("keywords", sa.JSON), sa.column("default_uom", sa.String),
                     sa.column("created_at", sa.DateTime(timezone=True)),
                     sa.column("updated_at", sa.DateTime(timezone=True)))
unit_table = sa.table("unit_conversion", sa.column("id", sa.String), sa.column("from_unit", sa.String),
                      sa.column("to_unit", sa.String), sa.column("factor", sa.Numeric),
                      sa.column("dimension", sa.String), sa.column("created_at", sa.DateTime(timezone=True)),
                      sa.column("updated_at", sa.DateTime(timezone=True)))
call_table = sa.table("country_call_policy", sa.column("id", sa.String), sa.column("country", sa.String),
                      sa.column("allowed_hours_start", sa.Integer), sa.column("allowed_hours_end", sa.Integer),
                      sa.column("recording_consent_mode", sa.String), sa.column("disclosure_text_en", sa.Text),
                      sa.column("disclosure_text_ar", sa.Text), sa.column("max_call_seconds", sa.Integer),
                      sa.column("is_active", sa.Boolean), sa.column("created_at", sa.DateTime(timezone=True)),
                      sa.column("updated_at", sa.DateTime(timezone=True)))


def upgrade() -> None:
    NOW = datetime.now(UTC)
    bind = op.get_bind()
    existing_roles = {row[0] for row in bind.execute(sa.text("SELECT code FROM role")).fetchall()}
    rows = []
    perms = []
    for code, (name, name_ar) in ROLES.items():
        if code in existing_roles:
            continue
        rows.append({"id": str(uuid.uuid4()), "code": code, "name": name, "name_ar": name_ar,
                     "description": "", "permissions": PERMISSIONS[code], "is_system": True,
                     "created_at": NOW, "updated_at": NOW})
        for permission in PERMISSIONS[code]:
            perms.append({"id": str(uuid.uuid4()), "role_code": code, "permission": permission,
                          "created_at": NOW, "updated_at": NOW})
    if rows:
        op.bulk_insert(role_table, rows)
    if perms:
        op.bulk_insert(perm_table, perms)

    existing_plans = {row[0] for row in bind.execute(sa.text("SELECT code FROM plan")).fetchall()}
    plan_rows = [{"id": str(uuid.uuid4()), "code": code, "name": name, "price_monthly": price,
                  "currency": currency, "limits": limits, "features": [], "is_active": True,
                  "created_at": NOW, "updated_at": NOW}
                 for code, name, price, currency, limits in PLANS if code not in existing_plans]
    if plan_rows:
        op.bulk_insert(plan_table, plan_rows)

    existing_tax = {row[0] for row in bind.execute(sa.text("SELECT code FROM taxonomy_node")).fetchall()}
    tax_rows = [{"id": str(uuid.uuid4()), "code": code, "name": name, "name_ar": name_ar,
                 "parent_code": parent, "level": level, "keywords": [], "default_uom": uom,
                 "created_at": NOW, "updated_at": NOW}
                for code, name, name_ar, parent, level, uom in TAXONOMY if code not in existing_tax]
    if tax_rows:
        op.bulk_insert(tax_table, tax_rows)

    existing_units = {(row[0], row[1]) for row in bind.execute(
        sa.text("SELECT from_unit, to_unit FROM unit_conversion")).fetchall()}
    unit_rows = [{"id": str(uuid.uuid4()), "from_unit": f, "to_unit": t, "factor": factor,
                  "dimension": dim, "created_at": NOW, "updated_at": NOW}
                 for f, t, factor, dim in UNITS if (f, t) not in existing_units]
    if unit_rows:
        op.bulk_insert(unit_table, unit_rows)

    existing_calls = {row[0] for row in bind.execute(sa.text("SELECT country FROM country_call_policy")).fetchall()}
    call_rows = [{"id": str(uuid.uuid4()), "country": country, "allowed_hours_start": start,
                  "allowed_hours_end": end, "recording_consent_mode": mode, "disclosure_text_en": en,
                  "disclosure_text_ar": ar, "max_call_seconds": secs, "is_active": True,
                  "created_at": NOW, "updated_at": NOW}
                 for country, start, end, mode, en, ar, secs in CALL_POLICIES if country not in existing_calls]
    if call_rows:
        op.bulk_insert(call_table, call_rows)


def downgrade() -> None:
    op.execute("DELETE FROM role_permission")
    op.execute("DELETE FROM role")
    op.execute("DELETE FROM plan")
    op.execute("DELETE FROM taxonomy_node")
    op.execute("DELETE FROM unit_conversion")
    op.execute("DELETE FROM country_call_policy")
