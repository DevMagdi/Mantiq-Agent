"""channels & voice: WhatsApp-first preferences, delivery evidence, AI call turns, assistant audit

Revision ID: 0016_channels_voice
Revises: 0015_accounting
Create Date: 2026-10-31 00:30:00

Phase 3 makes WhatsApp a first-class supplier channel and adds AI voice. Three things need storage
that the earlier schema could not express:

* **preference resolution** — the supplier's preferred/secondary channel, the per-RFQ override and the
  rule that won (`channel_source`);
* **delivery evidence** — sent/delivered/read per message and per invitation, plus an append-only
  `channel_event` log so a conversation can be reconstructed across channels;
* **AI auditability** — one row per policy-checked call turn (`voice_call_turn`), and one row per
  assistant question and answer (`assistant_turn`, `assistant_session`).

It also seeds the phase-3 Arabic/English templates (WhatsApp invitations, reminders, counters, quote
requests and the voice script) for every existing tenant, and grants the new permissions to the roles
that already exist, so a database upgraded without re-running bootstrap still has a working RBAC.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

revision: str = "0016_channels_voice"
down_revision: str | None = "0015_accounting"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_COLUMN = sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), "postgresql")

TENANT_TABLES = ("channel_event", "voice_call_turn", "assistant_session", "assistant_turn")
APPEND_ONLY = ("channel_event", "voice_call_turn", "assistant_turn")

#: Permissions added in phase 3, per role. Mirrors `app/core/rbac.py`.
NEW_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "procurement_manager": ("channel:read", "channel:write", "inbox:read", "inbox:reply",
                            "assistant:use", "voice:call"),
    "buyer": ("channel:read", "channel:write", "inbox:read", "inbox:reply", "assistant:use",
              "voice:call"),
    "requester": ("assistant:use",),
    "finance_approver": ("assistant:use",),
    "controller": ("assistant:use",),
    "auditor": ("assistant:use", "inbox:read", "channel:read"),
    "warehouse": ("assistant:use",),
}

#: The phase-3 templates, inline (a migration may not import application code).
TEMPLATES: list[dict[str, Any]] = [
    {"code": "rfq_invitation", "channel": "whatsapp", "locale": "en", "kind": "invitation",
     "name": "WhatsApp invitation",
     "body": ("Hello {{supplier_name}}, {{tenant_name}} is requesting a quotation for {{title}} "
              "({{rfq_code}}). Deadline {{deadline}}. Reply here with your unit price, lead time and "
              "offer validity, or submit through: {{web_form_url}}"),
     "whatsapp_template_name": "rfq_invitation_en"},
    {"code": "reminder", "channel": "whatsapp", "locale": "ar", "kind": "reminder",
     "name": "تذكير واتساب",
     "body": ("تذكير ودّي {{supplier_name}}: عرض السعر {{rfq_code}} يُغلق {{deadline}}. "
              "يسعدنا استلام سعرك ومدة التوريد هنا، أو عبر {{web_form_url}}"),
     "whatsapp_template_name": "reminder_ar"},
    {"code": "reminder", "channel": "whatsapp", "locale": "en", "kind": "reminder",
     "name": "WhatsApp reminder",
     "body": ("A quick reminder {{supplier_name}}: RFQ {{rfq_code}} closes on {{deadline}}. "
              "Reply with your price and lead time here, or via {{web_form_url}}"),
     "whatsapp_template_name": "reminder_en"},
    {"code": "reminder", "channel": "email", "locale": "en", "kind": "reminder", "name": "Reminder",
     "subject": "Reminder: RFQ {{rfq_code}}",
     "body": ("Dear {{supplier_name}},\nThis is a reminder that RFQ {{rfq_code}} closes on "
              "{{deadline}}.\nPlease send your offer through {{web_form_url}}.\n\nThank you,\n"
              "{{tenant_name}}")},
    {"code": "counter_offer", "channel": "whatsapp", "locale": "ar", "kind": "negotiation",
     "name": "عرض مقابل واتساب",
     "body": ("{{supplier_name}}، بخصوص {{rfq_code}}: {{message_body}} — {{buyer_name}}، "
              "{{tenant_name}}"),
     "whatsapp_template_name": "counter_offer_ar"},
    {"code": "counter_offer", "channel": "whatsapp", "locale": "en", "kind": "negotiation",
     "name": "WhatsApp counter-offer",
     "body": ("{{supplier_name}}, regarding {{rfq_code}}: {{message_body}} — {{buyer_name}}, "
              "{{tenant_name}}"),
     "whatsapp_template_name": "counter_offer_en"},
    {"code": "counter_offer", "channel": "email", "locale": "en", "kind": "negotiation",
     "name": "Counter-offer", "subject": "Counter-offer - {{rfq_code}}",
     "body": ("Dear {{supplier_name}},\n{{message_body}}\nBest regards,\n{{buyer_name}}\n"
              "{{tenant_name}}")},
    {"code": "quote_request", "channel": "whatsapp", "locale": "ar", "kind": "quote_request",
     "name": "طلب عرض سعر منظم",
     "body": ("بخصوص {{rfq_code}}، نحتاج ثلاثة أرقام فقط: سعر الوحدة، مدة التوريد بالأيام، وصلاحية "
              "العرض. الشروط: {{payment_terms}} — التسليم {{delivery_location}}."),
     "whatsapp_template_name": "quote_request_ar"},
    {"code": "quote_request", "channel": "whatsapp", "locale": "en", "kind": "quote_request",
     "name": "Structured quote request",
     "body": ("For {{rfq_code}} we need three numbers: unit price, lead time in days, and offer "
              "validity. Terms: {{payment_terms}} — delivery {{delivery_location}}."),
     "whatsapp_template_name": "quote_request_en"},
    {"code": "missing_info", "channel": "whatsapp", "locale": "ar", "kind": "clarification",
     "name": "استكمال بيانات واتساب",
     "body": ("شكراً {{supplier_name}} على العرض. لنستطيع المقارنة نحتاج: {{missing_fields}}. "
              "يمكنك الرد هنا مباشرة."),
     "whatsapp_template_name": "missing_info_ar"},
    {"code": "missing_info", "channel": "whatsapp", "locale": "en", "kind": "clarification",
     "name": "WhatsApp missing info",
     "body": ("Thank you {{supplier_name}}. To compare offers we still need: {{missing_fields}}. "
              "You can reply here."),
     "whatsapp_template_name": "missing_info_en"},
    {"code": "clarification", "channel": "whatsapp", "locale": "ar", "kind": "clarification",
     "name": "استيضاح واتساب",
     "body": ("{{supplier_name}}، بخصوص {{rfq_code}}: {{question}} — {{buyer_name}}، {{tenant_name}}"),
     "whatsapp_template_name": "clarification_ar"},
    {"code": "voice_script", "channel": "voice", "locale": "ar", "kind": "voice_script",
     "name": "نص مكالمة آلية",
     "body": ("[إفصاح] {{disclosure}}\n[الطلب] {{tenant_name}} بخصوص {{rfq_code}}: "
              "{{quantity_summary}}. الموعد النهائي {{deadline}}.\n[المطلوب] سعر الوحدة، مدة "
              "التوريد، صلاحية العرض، وشروط السداد.")},
    {"code": "voice_script", "channel": "voice", "locale": "en", "kind": "voice_script",
     "name": "AI call script",
     "body": ("[Disclosure] {{disclosure}}\n[Request] {{tenant_name}} regarding {{rfq_code}}: "
              "{{quantity_summary}}. Deadline {{deadline}}.\n[Ask for] unit price, lead time, offer "
              "validity and payment terms.")},
]

#: Existing templates get an explicit kind so the library can group them.
KIND_BY_CODE = {
    "rfq_invitation": "invitation", "reminder": "reminder", "counter_offer": "negotiation",
    "quote_request": "quote_request", "missing_info": "clarification", "clarification": "clarification",
    "voice_script": "voice_script",
}


def _variables(body: str) -> list[str]:
    found: list[str] = []
    cursor = 0
    while True:
        start = body.find("{{", cursor)
        if start == -1:
            return found
        end = body.find("}}", start)
        if end == -1:
            return found
        name = body[start + 2:end].strip()
        if name and name not in found:
            found.append(name)
        cursor = end + 2


def upgrade() -> None:
    now = datetime.now(UTC)

    # ---------------------------------------------------------------- columns
    op.add_column("supplier", sa.Column("preferred_channel", sa.String(length=20), nullable=False,
                                        server_default="email"))
    op.add_column("supplier", sa.Column("secondary_channels", JSON_COLUMN, nullable=False,
                                        server_default="[]"))
    op.add_column("supplier", sa.Column("preferred_language", sa.String(length=8), nullable=False,
                                        server_default="ar"))
    op.add_column("rfq_supplier", sa.Column("channel_override", sa.String(length=20), nullable=True))
    op.add_column("rfq_supplier", sa.Column("channel_source", sa.String(length=24), nullable=False,
                                            server_default="supplier_preference"))
    op.add_column("rfq_supplier", sa.Column("comm_status", sa.String(length=20), nullable=False,
                                            server_default="pending"))
    op.add_column("rfq_supplier", sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("rfq_supplier", sa.Column("read_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("rfq_supplier", sa.Column("reply_count", sa.Integer(), nullable=False,
                                            server_default="0"))
    op.add_column("channel_message", sa.Column("delivery_status", sa.String(length=20), nullable=False,
                                               server_default="pending"))
    op.add_column("channel_message", sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("channel_message", sa.Column("read_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("channel_message", sa.Column("media_type", sa.String(length=24), nullable=False,
                                               server_default=""))
    op.add_column("channel_message", sa.Column("template_code", sa.String(length=80), nullable=False,
                                               server_default=""))
    op.add_column("channel_message", sa.Column("locale", sa.String(length=8), nullable=False,
                                               server_default="ar"))
    op.add_column("channel_message", sa.Column("switched_from", sa.String(length=20), nullable=False,
                                               server_default=""))
    op.add_column("message_template", sa.Column("kind", sa.String(length=40), nullable=False,
                                                server_default="invitation"))
    op.add_column("message_template", sa.Column("description", sa.String(length=300), nullable=False,
                                                server_default=""))
    op.add_column("message_template", sa.Column("variables", JSON_COLUMN, nullable=False,
                                                server_default="[]"))

    # Existing rows are backfilled by the server defaults; drop them so the model stays the only truth.
    with op.batch_alter_table("supplier") as batch:
        batch.alter_column("preferred_channel", server_default=None)
        batch.alter_column("secondary_channels", server_default=None)
        batch.alter_column("preferred_language", server_default=None)
    with op.batch_alter_table("rfq_supplier") as batch:
        batch.alter_column("channel_source", server_default=None)
        batch.alter_column("comm_status", server_default=None)
        batch.alter_column("reply_count", server_default=None)
    with op.batch_alter_table("channel_message") as batch:
        batch.alter_column("delivery_status", server_default=None)
        batch.alter_column("media_type", server_default=None)
        batch.alter_column("template_code", server_default=None)
        batch.alter_column("locale", server_default=None)
        batch.alter_column("switched_from", server_default=None)
    with op.batch_alter_table("message_template") as batch:
        batch.alter_column("kind", server_default=None)
        batch.alter_column("description", server_default=None)
        batch.alter_column("variables", server_default=None)

    op.create_index("ix_rfq_supplier_comm_status", "rfq_supplier", ["comm_status"])
    op.create_index("ix_channel_message_delivery_status", "channel_message", ["delivery_status"])
    op.create_index("ix_channel_message_thread_created", "channel_message",
                    ["thread_id", "created_at"])
    op.create_index("ix_message_template_kind", "message_template", ["kind"])

    # ---------------------------------------------------------------- new tables
    op.create_table(
        "channel_event",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("rfq_id", sa.String(length=36), nullable=True),
        sa.Column("supplier_id", sa.String(length=36), nullable=True),
        sa.Column("thread_id", sa.String(length=36), nullable=True),
        sa.Column("message_id", sa.String(length=36), nullable=True),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("actor_type", sa.String(length=12), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=True),
        sa.Column("detail", JSON_COLUMN, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("tenant_id", "rfq_id", "supplier_id", "thread_id", "message_id", "kind"):
        op.create_index(f"ix_channel_event_{column}", "channel_event", [column])
    op.create_index("ix_channel_event_tenant_created", "channel_event", ["tenant_id", "created_at"])

    op.create_table(
        "voice_call_turn",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("call_id", sa.String(length=36), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("speaker", sa.String(length=12), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_ar", sa.Text(), nullable=False),
        sa.Column("topic", sa.String(length=40), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("block_code", sa.String(length=48), nullable=False),
        sa.Column("reason", sa.String(length=300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_voice_call_turn_tenant_id", "voice_call_turn", ["tenant_id"])
    op.create_index("ix_voice_call_turn_call_id", "voice_call_turn", ["call_id"])

    op.create_table(
        "assistant_session",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("locale", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("permission_snapshot", JSON_COLUMN, nullable=False),
        sa.Column("role_snapshot", JSON_COLUMN, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_assistant_session_tenant_id", "assistant_session", ["tenant_id"])
    op.create_index("ix_assistant_session_user_id", "assistant_session", ["user_id"])

    op.create_table(
        "assistant_turn",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=12), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_ar", sa.Text(), nullable=False),
        sa.Column("intent", sa.String(length=40), nullable=False),
        sa.Column("answer", JSON_COLUMN, nullable=False),
        sa.Column("citations", JSON_COLUMN, nullable=False),
        sa.Column("blocked_code", sa.String(length=48), nullable=False),
        sa.Column("transcript_confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_assistant_turn_tenant_id", "assistant_turn", ["tenant_id"])
    op.create_index("ix_assistant_turn_session_id", "assistant_turn", ["session_id"])

    # ---------------------------------------------------------------- data: kinds + templates
    bind = op.get_bind()
    for code, kind in KIND_BY_CODE.items():
        bind.execute(sa.text("UPDATE message_template SET kind = :kind WHERE code = :code"),
                     {"kind": kind, "code": code})

    tenant_ids = [row[0] for row in bind.execute(sa.text("SELECT id FROM tenant")).fetchall()]
    template_rows: list[dict[str, Any]] = []
    for tenant_id in tenant_ids:
        for template in TEMPLATES:
            exists = bind.execute(sa.text(
                "SELECT count(*) FROM message_template WHERE tenant_id = :t AND code = :c "
                "AND channel = :ch AND locale = :l"
            ), {"t": tenant_id, "c": template["code"], "ch": template["channel"],
                "l": template["locale"]}).scalar_one()
            if exists:
                continue
            template_rows.append({
                "id": str(uuid.uuid4()), "tenant_id": tenant_id, "code": template["code"],
                "channel": template["channel"], "locale": template["locale"],
                "name": template["name"], "subject": template.get("subject", ""),
                "body": template["body"], "whatsapp_template_name": template.get("whatsapp_template_name", ""),
                "version": 1, "is_active": True, "kind": template["kind"],
                "description": f"{template['name']} · {template['channel']}/{template['locale']}",
                "variables": _variables(template["body"]),
                "created_at": now, "updated_at": now,
            })
    if template_rows:
        op.bulk_insert(sa.table(
            "message_template",
            sa.column("id", sa.String), sa.column("tenant_id", sa.String), sa.column("code", sa.String),
            sa.column("channel", sa.String), sa.column("locale", sa.String), sa.column("name", sa.String),
            sa.column("subject", sa.String), sa.column("body", sa.Text),
            sa.column("whatsapp_template_name", sa.String), sa.column("version", sa.Integer),
            sa.column("is_active", sa.Boolean), sa.column("kind", sa.String),
            sa.column("description", sa.String), sa.column("variables", JSON_COLUMN),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        ), template_rows)

    # ---------------------------------------------------------------- data: permissions
    perm_rows: list[dict[str, Any]] = []
    for role_code, permissions in NEW_PERMISSIONS.items():
        role_exists = bind.execute(sa.text("SELECT count(*) FROM role WHERE code = :c"),
                                   {"c": role_code}).scalar_one()
        if not role_exists:
            continue
        for permission in permissions:
            exists = bind.execute(sa.text(
                "SELECT count(*) FROM role_permission WHERE role_code = :r AND permission = :p"
            ), {"r": role_code, "p": permission}).scalar_one()
            if exists:
                continue
            perm_rows.append({"id": str(uuid.uuid4()), "role_code": role_code,
                              "permission": permission, "created_at": now, "updated_at": now})
    if perm_rows:
        op.bulk_insert(sa.table(
            "role_permission", sa.column("id", sa.String), sa.column("role_code", sa.String),
            sa.column("permission", sa.String),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        ), perm_rows)

    # ---------------------------------------------------------------- tenant isolation
    if bind.dialect.name == "postgresql":
        op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON channel_event, voice_call_turn, "
                   "assistant_session, assistant_turn TO procureai_app")
        # A conversation trail and an AI transcript are evidence: append-only for the app role.
        op.execute("REVOKE UPDATE, DELETE ON channel_event, voice_call_turn, assistant_turn "
                   "FROM procureai_app")
        for table in TENANT_TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
            op.execute(f"CREATE POLICY tenant_isolation_{table} ON {table} "
                       "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')) "
                       "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))")


def downgrade() -> None:
    op.drop_index("ix_assistant_turn_session_id", table_name="assistant_turn")
    op.drop_index("ix_assistant_turn_tenant_id", table_name="assistant_turn")
    op.drop_table("assistant_turn")
    op.drop_index("ix_assistant_session_user_id", table_name="assistant_session")
    op.drop_index("ix_assistant_session_tenant_id", table_name="assistant_session")
    op.drop_table("assistant_session")
    op.drop_index("ix_voice_call_turn_call_id", table_name="voice_call_turn")
    op.drop_index("ix_voice_call_turn_tenant_id", table_name="voice_call_turn")
    op.drop_table("voice_call_turn")
    op.drop_index("ix_channel_event_tenant_created", table_name="channel_event")
    for column in ("kind", "message_id", "thread_id", "supplier_id", "rfq_id", "tenant_id"):
        op.drop_index(f"ix_channel_event_{column}", table_name="channel_event")
    op.drop_table("channel_event")

    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM role_permission WHERE permission IN ("
                         "'channel:read','channel:write','inbox:read','inbox:reply','assistant:use',"
                         "'voice:call')"))
    for template in TEMPLATES:
        bind.execute(sa.text("DELETE FROM message_template WHERE code = :c AND channel = :ch "
                             "AND locale = :l"),
                     {"c": template["code"], "ch": template["channel"], "l": template["locale"]})

    op.drop_index("ix_message_template_kind", table_name="message_template")
    op.drop_index("ix_channel_message_thread_created", table_name="channel_message")
    op.drop_index("ix_channel_message_delivery_status", table_name="channel_message")
    op.drop_index("ix_rfq_supplier_comm_status", table_name="rfq_supplier")
    with op.batch_alter_table("message_template") as batch:
        batch.drop_column("variables")
        batch.drop_column("description")
        batch.drop_column("kind")
    with op.batch_alter_table("channel_message") as batch:
        batch.drop_column("switched_from")
        batch.drop_column("locale")
        batch.drop_column("template_code")
        batch.drop_column("media_type")
        batch.drop_column("read_at")
        batch.drop_column("delivered_at")
        batch.drop_column("delivery_status")
    with op.batch_alter_table("rfq_supplier") as batch:
        batch.drop_column("reply_count")
        batch.drop_column("read_at")
        batch.drop_column("delivered_at")
        batch.drop_column("comm_status")
        batch.drop_column("channel_source")
        batch.drop_column("channel_override")
    with op.batch_alter_table("supplier") as batch:
        batch.drop_column("preferred_language")
        batch.drop_column("secondary_channels")
        batch.drop_column("preferred_channel")
