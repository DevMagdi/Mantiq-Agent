"""SQLAlchemy 2 models for the full ProcureAI domain (SRS 7.1 + supporting tables)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    AuditMixin,
    Base,
    JSONVariant,
    TenantMixin,
    TimestampMixin,
    UTCDateTime,
    UUIDMixin,
)


def money() -> Numeric:
    return Numeric(18, 4)


def qty() -> Numeric:
    return Numeric(18, 6)


# =====================================================================  tenancy & identity
class Tenant(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "tenant"
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="customer")  # customer | platform
    plan_code: Mapped[str] = mapped_column(String(40), default="growth")
    status: Mapped[str] = mapped_column(String(20), default="active")
    locale: Mapped[str] = mapped_column(String(8), default="ar")
    timezone: Mapped[str] = mapped_column(String(64), default="Africa/Cairo")
    currency: Mapped[str] = mapped_column(String(3), default="EGP")
    data_region: Mapped[str] = mapped_column(String(32), default="me-central")
    settings: Mapped[dict] = mapped_column(JSONVariant, default=dict)


class User(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "app_user"
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_user_tenant_email"),)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), default="")
    full_name: Mapped[str] = mapped_column(String(160), default="")
    full_name_ar: Mapped[str] = mapped_column(String(160), default="")
    job_title: Mapped[str] = mapped_column(String(120), default="")
    primary_role: Mapped[str] = mapped_column(String(40), default="requester")
    roles: Mapped[list] = mapped_column(JSONVariant, default=list)
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    totp_secret: Mapped[str] = mapped_column(String(64), default="")
    backup_codes: Mapped[list] = mapped_column(JSONVariant, default=list)
    locale: Mapped[str] = mapped_column(String(8), default="ar")
    theme: Mapped[str] = mapped_column(String(12), default="dark")
    numerals: Mapped[str] = mapped_column(String(12), default="western")
    preferred_currency: Mapped[str] = mapped_column(String(3), default="EGP")
    phone: Mapped[str] = mapped_column(String(40), default="")
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    notification_prefs: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    plant_id: Mapped[str | None] = mapped_column(String(36))


class Role(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "role"
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(80))
    name_ar: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(String(255), default="")
    permissions: Mapped[list] = mapped_column(JSONVariant, default=list)
    is_system: Mapped[bool] = mapped_column(Boolean, default=True)


class RolePermission(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "role_permission"
    __table_args__ = (UniqueConstraint("role_code", "permission", name="uq_role_permission"),)
    role_code: Mapped[str] = mapped_column(String(40), index=True)
    permission: Mapped[str] = mapped_column(String(80))


class UserRoleAssignment(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "user_role_assignment"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    role_code: Mapped[str] = mapped_column(String(40))


class RefreshSession(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "refresh_session"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    jti: Mapped[str] = mapped_column(String(80), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")


class ApiKey(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "api_key"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(120))
    prefix: Mapped[str] = mapped_column(String(16), index=True)
    hashed_key: Mapped[str] = mapped_column(String(128))
    scopes: Mapped[list] = mapped_column(JSONVariant, default=list)
    created_by: Mapped[str | None] = mapped_column(String(36))
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ApprovalLimit(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "approval_limit"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    role_code: Mapped[str] = mapped_column(String(40))
    category_code: Mapped[str | None] = mapped_column(String(60))
    max_amount: Mapped[Decimal] = mapped_column(money(), default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), default="EGP")


# =====================================================================  master data
class Plant(Base, UUIDMixin, AuditMixin, TenantMixin):
    __tablename__ = "plant"
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(160))
    name_ar: Mapped[str] = mapped_column(String(160), default="")
    country: Mapped[str] = mapped_column(String(2), default="EG")
    city: Mapped[str] = mapped_column(String(80), default="")
    address: Mapped[str] = mapped_column(String(255), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class TaxonomyNode(Base, UUIDMixin, TimestampMixin):
    """Global UNSPSC-based taxonomy (reference data, not tenant scoped)."""
    __tablename__ = "taxonomy_node"
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    name_ar: Mapped[str] = mapped_column(String(200), default="")
    parent_code: Mapped[str | None] = mapped_column(String(20))
    level: Mapped[int] = mapped_column(Integer, default=1)
    keywords: Mapped[list] = mapped_column(JSONVariant, default=list)
    default_uom: Mapped[str] = mapped_column(String(16), default="pcs")


class Category(Base, UUIDMixin, AuditMixin, TenantMixin):
    __tablename__ = "category"
    code: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(120))
    name_ar: Mapped[str] = mapped_column(String(120), default="")
    taxonomy_code: Mapped[str | None] = mapped_column(String(20))
    default_weights: Mapped[dict] = mapped_column(JSONVariant, default=dict)


class Item(Base, UUIDMixin, AuditMixin, TenantMixin):
    __tablename__ = "item"
    sku: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(240))
    name_ar: Mapped[str] = mapped_column(String(240), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    category_code: Mapped[str | None] = mapped_column(String(60))
    taxonomy_code: Mapped[str | None] = mapped_column(String(20))
    uom: Mapped[str] = mapped_column(String(16), default="pcs")
    attributes: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    embedding: Mapped[list | None] = mapped_column(JSONVariant)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class UnitConversion(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "unit_conversion"
    __table_args__ = (UniqueConstraint("from_unit", "to_unit", name="uq_unit_conversion"),)
    from_unit: Mapped[str] = mapped_column(String(16))
    to_unit: Mapped[str] = mapped_column(String(16))
    factor: Mapped[Decimal] = mapped_column(Numeric(24, 10))
    dimension: Mapped[str] = mapped_column(String(24), default="mass")


class Supplier(Base, UUIDMixin, AuditMixin, TenantMixin):
    __tablename__ = "supplier"
    name: Mapped[str] = mapped_column(String(240), index=True)
    name_ar: Mapped[str] = mapped_column(String(240), default="")
    legal_name: Mapped[str] = mapped_column(String(240), default="")
    country: Mapped[str] = mapped_column(String(2), default="EG")
    city: Mapped[str] = mapped_column(String(120), default="")
    address: Mapped[str] = mapped_column(String(255), default="")
    website: Mapped[str] = mapped_column(String(255), default="")
    domain: Mapped[str | None] = mapped_column(String(160), index=True)
    vat_id: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(24), default="approved")  # discovered|approved|preferred|blacklisted
    source_url: Mapped[str] = mapped_column(String(500), default="")
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), default=1)
    languages: Mapped[list] = mapped_column(JSONVariant, default=list)
    categories: Mapped[list] = mapped_column(JSONVariant, default=list)
    rating_price: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    rating_lead_time: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    rating_quality: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    rating_responsiveness: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    overall_score: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    sanctions_status: Mapped[str] = mapped_column(String(24), default="clear")
    sanctions_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    notes: Mapped[str] = mapped_column(Text, default="")
    embedding: Mapped[list | None] = mapped_column(JSONVariant)
    flags: Mapped[list] = mapped_column(JSONVariant, default=list)
    # Self-service submissions from the supplier portal stay here until a buyer approves them.
    pending_profile: Mapped[dict] = mapped_column(JSONVariant, default=dict)


class SupplierContact(Base, UUIDMixin, AuditMixin, TenantMixin):
    __tablename__ = "supplier_contact"
    supplier_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(160), default="")
    role: Mapped[str] = mapped_column(String(80), default="")
    email: Mapped[str] = mapped_column(String(255), default="")  # envelope-encrypted at rest
    phone: Mapped[str] = mapped_column(String(64), default="")  # envelope-encrypted at rest
    key_id: Mapped[str] = mapped_column(String(40), default="")
    language: Mapped[str] = mapped_column(String(8), default="ar")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    email_opt_in: Mapped[bool] = mapped_column(Boolean, default=True)
    whatsapp_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)
    # Supplier-submitted contact changes await buyer approval (never applied directly).
    pending_update: Mapped[dict] = mapped_column(JSONVariant, default=dict)


class SupplierCertification(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "supplier_certification"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    supplier_id: Mapped[str] = mapped_column(String(36), index=True)
    code: Mapped[str] = mapped_column(String(60))
    issuer: Mapped[str] = mapped_column(String(120), default="")
    issued_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    evidence_url: Mapped[str] = mapped_column(String(500), default="")


class SupplierFlag(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "supplier_flag"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    supplier_id: Mapped[str] = mapped_column(String(36), index=True)
    flag: Mapped[str] = mapped_column(String(24))  # blacklisted|preferred|approved
    reason: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str | None] = mapped_column(String(36))


class SupplierRatingHistory(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "supplier_rating_history"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    supplier_id: Mapped[str] = mapped_column(String(36), index=True)
    price: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    lead_time: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    quality: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    responsiveness: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    overall: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    source: Mapped[str] = mapped_column(String(60), default="rfq_outcome")


# =====================================================================  requisition & RFQ
class PurchaseRequisition(Base, UUIDMixin, AuditMixin, TenantMixin):
    __tablename__ = "purchase_requisition"
    code: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(240))
    source: Mapped[str] = mapped_column(String(24), default="manual")  # excel|text|voice|ocr|email|erp|manual
    status: Mapped[str] = mapped_column(String(24), default="draft")  # draft|clarifying|ready|rfq_created|cancelled
    plant_id: Mapped[str | None] = mapped_column(String(36))
    requested_by: Mapped[str | None] = mapped_column(String(36))
    needed_by: Mapped[datetime | None] = mapped_column(UTCDateTime)
    currency: Mapped[str] = mapped_column(String(3), default="EGP")
    total_estimate: Mapped[Decimal] = mapped_column(money(), default=Decimal("0"))
    notes: Mapped[str] = mapped_column(Text, default="")
    raw_input: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), default=1)
    clarification_resolved: Mapped[bool] = mapped_column(Boolean, default=True)


class PRLine(Base, UUIDMixin, AuditMixin, TenantMixin):
    __tablename__ = "pr_line"
    pr_id: Mapped[str] = mapped_column(String(36), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(String(400))
    description_ar: Mapped[str] = mapped_column(String(400), default="")
    quantity: Mapped[Decimal] = mapped_column(qty(), default=Decimal("1"))
    uom: Mapped[str] = mapped_column(String(16), default="pcs")
    taxonomy_code: Mapped[str | None] = mapped_column(String(20))
    category_code: Mapped[str | None] = mapped_column(String(60))
    target_price: Mapped[Decimal | None] = mapped_column(money())
    currency: Mapped[str] = mapped_column(String(3), default="EGP")
    needed_by: Mapped[datetime | None] = mapped_column(UTCDateTime)
    attributes: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    normalization_confidence: Mapped[float] = mapped_column(Numeric(5, 4), default=1)
    quoted_text: Mapped[str] = mapped_column(Text, default="")


class RFQ(Base, UUIDMixin, AuditMixin, TenantMixin):
    __tablename__ = "rfq"
    code: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(240))
    pr_id: Mapped[str | None] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    currency: Mapped[str] = mapped_column(String(3), default="EGP")
    incoterm: Mapped[str] = mapped_column(String(12), default="DAP")
    payment_terms: Mapped[str] = mapped_column(String(120), default="Net 30")
    delivery_location: Mapped[str] = mapped_column(String(160), default="")
    deadline: Mapped[datetime | None] = mapped_column(UTCDateTime)
    validity_date: Mapped[datetime | None] = mapped_column(UTCDateTime)
    buyer_id: Mapped[str | None] = mapped_column(String(36))
    mode: Mapped[str] = mapped_column(String(20), default="draft_only")  # draft_only|semi_auto|auto
    template_id: Mapped[str | None] = mapped_column(String(36))
    scoring_weights: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    stage_history: Mapped[list] = mapped_column(JSONVariant, default=list)
    notes: Mapped[str] = mapped_column(Text, default="")


class RFQLine(Base, UUIDMixin, AuditMixin, TenantMixin):
    __tablename__ = "rfq_line"
    rfq_id: Mapped[str] = mapped_column(String(36), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(String(400))
    quantity: Mapped[Decimal] = mapped_column(qty(), default=Decimal("1"))
    uom: Mapped[str] = mapped_column(String(16), default="pcs")
    target_price: Mapped[Decimal | None] = mapped_column(money())
    taxonomy_code: Mapped[str | None] = mapped_column(String(20), index=True)
    specs: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    pr_line_id: Mapped[str | None] = mapped_column(String(36))


class RFQSupplier(Base, UUIDMixin, AuditMixin, TenantMixin):
    __tablename__ = "rfq_supplier"
    __table_args__ = (UniqueConstraint("rfq_id", "supplier_id", name="uq_rfq_supplier"),)
    rfq_id: Mapped[str] = mapped_column(String(36), index=True)
    supplier_id: Mapped[str] = mapped_column(String(36), index=True)
    channel: Mapped[str] = mapped_column(String(20), default="email")
    status: Mapped[str] = mapped_column(String(24), default="invited")
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    reminder_count: Mapped[int] = mapped_column(Integer, default=0)
    last_reminder_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    token: Mapped[str] = mapped_column(String(120), default="", index=True)
    responded_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class MessageTemplate(Base, UUIDMixin, AuditMixin, TenantMixin):
    __tablename__ = "message_template"
    code: Mapped[str] = mapped_column(String(80))
    channel: Mapped[str] = mapped_column(String(20), default="email")
    locale: Mapped[str] = mapped_column(String(8), default="ar")
    name: Mapped[str] = mapped_column(String(160), default="")
    subject: Mapped[str] = mapped_column(String(300), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    whatsapp_template_name: Mapped[str] = mapped_column(String(120), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class WebFormToken(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "web_form_token"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    token: Mapped[str] = mapped_column(String(120), unique=True)
    rfq_id: Mapped[str] = mapped_column(String(36), index=True)
    supplier_id: Mapped[str] = mapped_column(String(36), index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ClarificationQuestion(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "clarification_question"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    pr_id: Mapped[str] = mapped_column(String(36), index=True)
    pr_line_id: Mapped[str | None] = mapped_column(String(36))
    field: Mapped[str] = mapped_column(String(60))
    question: Mapped[str] = mapped_column(String(400))
    question_ar: Mapped[str] = mapped_column(String(400), default="")
    answer: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|answered|waived
    asked_at: Mapped[datetime] = mapped_column(UTCDateTime)
    answered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    answered_by: Mapped[str | None] = mapped_column(String(36))


# =====================================================================  channels & responses
class MessageThread(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "message_thread"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    rfq_id: Mapped[str] = mapped_column(String(36), index=True)
    supplier_id: Mapped[str] = mapped_column(String(36), index=True)
    channel: Mapped[str] = mapped_column(String(20), default="email")
    subject: Mapped[str] = mapped_column(String(300), default="")
    external_thread_id: Mapped[str] = mapped_column(String(200), default="")
    last_message_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ChannelMessage(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "channel_message"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    thread_id: Mapped[str] = mapped_column(String(36), index=True)
    rfq_id: Mapped[str] = mapped_column(String(36), index=True)
    supplier_id: Mapped[str] = mapped_column(String(36), index=True)
    direction: Mapped[str] = mapped_column(String(10))  # inbound|outbound
    channel: Mapped[str] = mapped_column(String(20))
    body: Mapped[str] = mapped_column(Text, default="")
    body_ar: Mapped[str] = mapped_column(Text, default="")
    subject: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(20), default="sent")  # draft|sent|received|failed
    author_type: Mapped[str] = mapped_column(String(12), default="system")  # user|agent|supplier|system
    author_id: Mapped[str | None] = mapped_column(String(36))
    agent_run_id: Mapped[str | None] = mapped_column(String(36))
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    received_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    provider_message_id: Mapped[str] = mapped_column(String(200), default="")
    extra: Mapped[dict] = mapped_column(JSONVariant, default=dict)


class Attachment(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "attachment"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="supplier_reply")
    filename: Mapped[str] = mapped_column(String(255), default="")
    mime: Mapped[str] = mapped_column(String(120), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    storage_key: Mapped[str] = mapped_column(String(300), default="")
    sha256: Mapped[str] = mapped_column(String(64), default="")
    extracted_text: Mapped[str] = mapped_column(Text, default="")
    message_id: Mapped[str | None] = mapped_column(String(36), index=True)
    rfq_id: Mapped[str | None] = mapped_column(String(36), index=True)
    uploaded_by: Mapped[str | None] = mapped_column(String(36))


class ExtractionResult(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "extraction_result"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    rfq_id: Mapped[str] = mapped_column(String(36), index=True)
    supplier_id: Mapped[str] = mapped_column(String(36), index=True)
    message_id: Mapped[str | None] = mapped_column(String(36))
    attachment_id: Mapped[str | None] = mapped_column(String(36))
    offer_id: Mapped[str | None] = mapped_column(String(36))
    source_kind: Mapped[str] = mapped_column(String(20), default="text")
    status: Mapped[str] = mapped_column(String(24), default="pending")
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), default=0)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(80), default="")
    parser_version: Mapped[str] = mapped_column(String(20), default="1.0")
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    issues: Mapped[list] = mapped_column(JSONVariant, default=list)


class ExtractedField(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "extracted_field"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    extraction_id: Mapped[str] = mapped_column(String(36), index=True)
    field_name: Mapped[str] = mapped_column(String(80))
    label: Mapped[str] = mapped_column(String(120), default="")
    value: Mapped[str] = mapped_column(String(400), default="")
    value_numeric: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    currency: Mapped[str | None] = mapped_column(String(3))
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), default=0)
    source_span: Mapped[str] = mapped_column(Text, default="")
    source_offset: Mapped[int] = mapped_column(Integer, default=0)
    page: Mapped[int] = mapped_column(Integer, default=1)
    verified_by: Mapped[str | None] = mapped_column(String(36))
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    is_low_confidence: Mapped[bool] = mapped_column(Boolean, default=False)


class VoiceCall(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "voice_call"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    rfq_id: Mapped[str] = mapped_column(String(36), index=True)
    supplier_id: Mapped[str] = mapped_column(String(36), index=True)
    provider: Mapped[str] = mapped_column(String(20), default="fake")
    provider_call_id: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(24), default="queued")
    country: Mapped[str] = mapped_column(String(2), default="EG")
    disclosure_played: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_given: Mapped[bool] = mapped_column(Boolean, default=False)
    recording_uri: Mapped[str] = mapped_column(String(400), default="")
    transcript: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str] = mapped_column(String(12), default="ar-EG")
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    extracted_offer_id: Mapped[str | None] = mapped_column(String(36))
    summary: Mapped[str] = mapped_column(Text, default="")
    policy_snapshot: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_by: Mapped[str | None] = mapped_column(String(36))


class OptInRegistry(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "opt_in_registry"
    __table_args__ = (UniqueConstraint("tenant_id", "supplier_id", "channel", name="uq_optin"),)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    supplier_id: Mapped[str] = mapped_column(String(36), index=True)
    channel: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="opted_in")
    source: Mapped[str] = mapped_column(String(80), default="manual")
    evidence: Mapped[str] = mapped_column(Text, default="")


class FxRate(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "fx_rate"
    tenant_id: Mapped[str | None] = mapped_column(String(36), index=True)
    base: Mapped[str] = mapped_column(String(3))
    quote: Mapped[str] = mapped_column(String(3))
    rate: Mapped[Decimal] = mapped_column(Numeric(24, 10))
    as_of: Mapped[datetime] = mapped_column(UTCDateTime)
    source: Mapped[str] = mapped_column(String(40), default="manual")


class LandedCostComponent(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "landed_cost_component"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    offer_id: Mapped[str | None] = mapped_column(String(36), index=True)
    rfq_id: Mapped[str | None] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(24))  # freight|duty|vat|insurance|other
    amount: Mapped[Decimal] = mapped_column(money(), default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), default="EGP")
    notes: Mapped[str] = mapped_column(String(200), default="")


# =====================================================================  offers & negotiation
class Offer(Base, UUIDMixin, AuditMixin, TenantMixin):
    __tablename__ = "offer"
    rfq_id: Mapped[str] = mapped_column(String(36), index=True)
    supplier_id: Mapped[str] = mapped_column(String(36), index=True)
    currency: Mapped[str] = mapped_column(String(3), default="EGP")
    incoterm: Mapped[str] = mapped_column(String(12), default="DAP")
    payment_terms: Mapped[str] = mapped_column(String(120), default="")
    lead_time_days: Mapped[int | None] = mapped_column(Integer)
    validity_date: Mapped[datetime | None] = mapped_column(UTCDateTime)
    moq: Mapped[Decimal | None] = mapped_column(qty())
    subtotal: Mapped[Decimal] = mapped_column(money(), default=Decimal("0"))
    fx_rate: Mapped[Decimal] = mapped_column(Numeric(24, 10), default=Decimal("1"))
    landed_cost_total: Mapped[Decimal] = mapped_column(money(), default=Decimal("0"))
    tco_total: Mapped[Decimal] = mapped_column(money(), default=Decimal("0"))
    total_base: Mapped[Decimal] = mapped_column(money(), default=Decimal("0"))
    scores: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    weighted_score: Mapped[float] = mapped_column(Numeric(6, 3), default=0)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), default=0)
    status: Mapped[str] = mapped_column(String(24), default="received")  # received|verified|rejected|selected
    is_low_confidence: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    extraction_id: Mapped[str | None] = mapped_column(String(36))
    round_no: Mapped[int] = mapped_column(Integer, default=1)
    parent_offer_id: Mapped[str | None] = mapped_column(String(36))


class OfferLine(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "offer_line"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    offer_id: Mapped[str] = mapped_column(String(36), index=True)
    rfq_line_id: Mapped[str | None] = mapped_column(String(36))
    description: Mapped[str] = mapped_column(String(400), default="")
    quantity: Mapped[Decimal] = mapped_column(qty(), default=Decimal("0"))
    uom: Mapped[str] = mapped_column(String(16), default="pcs")
    unit_price: Mapped[Decimal] = mapped_column(money(), default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), default="EGP")
    line_total: Mapped[Decimal] = mapped_column(money(), default=Decimal("0"))
    lead_time_days: Mapped[int | None] = mapped_column(Integer)
    moq: Mapped[Decimal | None] = mapped_column(qty())
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), default=0)
    notes: Mapped[str] = mapped_column(String(400), default="")


class PriceHistory(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "price_history"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    item_key: Mapped[str] = mapped_column(String(200), index=True)
    supplier_id: Mapped[str | None] = mapped_column(String(36), index=True)
    rfq_id: Mapped[str | None] = mapped_column(String(36))
    unit_price: Mapped[Decimal] = mapped_column(money())
    currency: Mapped[str] = mapped_column(String(3), default="EGP")
    unit_price_base: Mapped[Decimal] = mapped_column(money())
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime)


class BenchmarkPrice(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "benchmark_price"
    tenant_id: Mapped[str | None] = mapped_column(String(36), index=True)
    item_key: Mapped[str] = mapped_column(String(200), index=True)
    period: Mapped[str] = mapped_column(String(20))
    p25: Mapped[Decimal] = mapped_column(money(), default=Decimal("0"))
    p50: Mapped[Decimal] = mapped_column(money(), default=Decimal("0"))
    p75: Mapped[Decimal] = mapped_column(money(), default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), default="EGP")
    sample_size: Mapped[int] = mapped_column(Integer, default=0)


class NegotiationPolicy(Base, UUIDMixin, AuditMixin, TenantMixin):
    __tablename__ = "negotiation_policy"
    rfq_id: Mapped[str | None] = mapped_column(String(36), index=True)
    supplier_id: Mapped[str | None] = mapped_column(String(36), index=True)
    category_code: Mapped[str | None] = mapped_column(String(60))
    currency: Mapped[str] = mapped_column(String(3), default="EGP")
    target_price: Mapped[Decimal] = mapped_column(money(), default=Decimal("0"))
    walkaway_price: Mapped[Decimal] = mapped_column(money(), default=Decimal("0"))
    max_rounds: Mapped[int] = mapped_column(Integer, default=3)
    allowed_concessions: Mapped[list] = mapped_column(JSONVariant, default=list)
    tone: Mapped[str] = mapped_column(String(20), default="professional")
    mode: Mapped[str] = mapped_column(String(20), default="draft_only")
    value_threshold: Mapped[Decimal] = mapped_column(money(), default=Decimal("0"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")


class NegotiationRound(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "negotiation_round"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    rfq_id: Mapped[str] = mapped_column(String(36), index=True)
    supplier_id: Mapped[str] = mapped_column(String(36), index=True)
    round_no: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="open")
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class NegotiationMessage(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "negotiation_message"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    rfq_id: Mapped[str] = mapped_column(String(36), index=True)
    supplier_id: Mapped[str] = mapped_column(String(36), index=True)
    round_no: Mapped[int] = mapped_column(Integer, default=1)
    direction: Mapped[str] = mapped_column(String(10), default="outbound")
    actor_type: Mapped[str] = mapped_column(String(12), default="agent")
    body: Mapped[str] = mapped_column(Text, default="")
    body_ar: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|sent|received
    channel: Mapped[str] = mapped_column(String(20), default="email")
    proposed_price: Mapped[Decimal | None] = mapped_column(money())
    policy_decision: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    agent_reasoning: Mapped[str] = mapped_column(Text, default="")
    agent_run_id: Mapped[str | None] = mapped_column(String(36))
    offer_id: Mapped[str | None] = mapped_column(String(36))
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


# =====================================================================  approval & PO
class ApprovalChain(Base, UUIDMixin, AuditMixin, TenantMixin):
    __tablename__ = "approval_chain"
    name: Mapped[str] = mapped_column(String(160))
    category_code: Mapped[str | None] = mapped_column(String(60))
    min_amount: Mapped[Decimal] = mapped_column(money(), default=Decimal("0"))
    max_amount: Mapped[Decimal | None] = mapped_column(money())
    currency: Mapped[str] = mapped_column(String(3), default="EGP")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ApprovalStep(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "approval_step"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    chain_id: Mapped[str] = mapped_column(String(36), index=True)
    step_no: Mapped[int] = mapped_column(Integer)
    role_code: Mapped[str] = mapped_column(String(40))
    threshold_amount: Mapped[Decimal] = mapped_column(money(), default=Decimal("0"))
    sla_hours: Mapped[int] = mapped_column(Integer, default=24)


class Approval(Base, UUIDMixin, AuditMixin, TenantMixin):
    __tablename__ = "approval"
    rfq_id: Mapped[str] = mapped_column(String(36), index=True)
    offer_id: Mapped[str | None] = mapped_column(String(36))
    chain_id: Mapped[str | None] = mapped_column(String(36))
    step_no: Mapped[int] = mapped_column(Integer, default=1)
    approver_role: Mapped[str] = mapped_column(String(40))
    approver_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    amount: Mapped[Decimal] = mapped_column(money(), default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), default="EGP")
    decision: Mapped[str | None] = mapped_column(String(20))
    comment: Mapped[str] = mapped_column(Text, default="")
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    delegated_to: Mapped[str | None] = mapped_column(String(36))
    split_award: Mapped[list] = mapped_column(JSONVariant, default=list)


class PurchaseOrder(Base, UUIDMixin, AuditMixin, TenantMixin):
    __tablename__ = "purchase_order"
    code: Mapped[str] = mapped_column(String(40), index=True)
    rfq_id: Mapped[str] = mapped_column(String(36), index=True)
    supplier_id: Mapped[str] = mapped_column(String(36), index=True)
    offer_id: Mapped[str | None] = mapped_column(String(36))
    amount: Mapped[Decimal] = mapped_column(money(), default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), default="EGP")
    status: Mapped[str] = mapped_column(String(20), default="issued")
    issued_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    lines: Mapped[list] = mapped_column(JSONVariant, default=list)
    document_html: Mapped[str] = mapped_column(Text, default="")
    erp_status: Mapped[str] = mapped_column(String(24), default="not_exported")


# =====================================================================  agents & audit
class AgentRun(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "agent_run"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(24), default="running")
    ref_type: Mapped[str | None] = mapped_column(String(40))
    ref_id: Mapped[str | None] = mapped_column(String(36), index=True)
    model: Mapped[str] = mapped_column(String(80), default="fake")
    input_summary: Mapped[str] = mapped_column(Text, default="")
    output_summary: Mapped[str] = mapped_column(Text, default="")
    reasoning: Mapped[str] = mapped_column(Text, default="")
    policy_decision: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str | None] = mapped_column(String(36))
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class AgentStep(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "agent_step"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    step_no: Mapped[int] = mapped_column(Integer, default=1)
    node: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20), default="ok")
    input: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    output: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    reasoning: Mapped[str] = mapped_column(Text, default="")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)


class ToolCall(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "tool_call"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    tool_name: Mapped[str] = mapped_column(String(60))
    args: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    result: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="ok")
    error: Mapped[str] = mapped_column(String(400), default="")
    idempotency_key: Mapped[str] = mapped_column(String(120), default="")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)


class AuditLog(Base, UUIDMixin):
    """Append-only (UPDATE/DELETE revoked in migration 0010)."""
    __tablename__ = "audit_log"
    tenant_id: Mapped[str | None] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    actor_type: Mapped[str] = mapped_column(String(16), default="user")
    actor_id: Mapped[str | None] = mapped_column(String(36))
    actor_label: Mapped[str] = mapped_column(String(160), default="")
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str] = mapped_column(String(60), default="")
    entity_id: Mapped[str | None] = mapped_column(String(36))
    before: Mapped[dict | None] = mapped_column(JSONVariant)
    after: Mapped[dict | None] = mapped_column(JSONVariant)
    ip: Mapped[str] = mapped_column(String(64), default="")
    request_id: Mapped[str] = mapped_column(String(64), default="")


class Notification(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "notification"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(40), default="info")
    title: Mapped[str] = mapped_column(String(240))
    title_ar: Mapped[str] = mapped_column(String(240), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str] = mapped_column(String(300), default="")
    severity: Mapped[str] = mapped_column(String(20), default="info")
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    channel: Mapped[str] = mapped_column(String(20), default="in_app")


class Comment(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "comment"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    rfq_id: Mapped[str | None] = mapped_column(String(36), index=True)
    pr_id: Mapped[str | None] = mapped_column(String(36), index=True)
    author_id: Mapped[str | None] = mapped_column(String(36))
    author_name: Mapped[str] = mapped_column(String(160), default="")
    body: Mapped[str] = mapped_column(Text)
    mentions: Mapped[list] = mapped_column(JSONVariant, default=list)


class SavedView(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "saved_view"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    page: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(120))
    config: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    is_shared: Mapped[bool] = mapped_column(Boolean, default=False)


# =====================================================================  billing & admin
class Plan(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "plan"
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    price_monthly: Mapped[Decimal] = mapped_column(money(), default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    limits: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    features: Mapped[list] = mapped_column(JSONVariant, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Subscription(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "subscription"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    plan_code: Mapped[str] = mapped_column(String(40), default="growth")
    status: Mapped[str] = mapped_column(String(24), default="active")
    seats: Mapped[int] = mapped_column(Integer, default=10)
    period_start: Mapped[datetime | None] = mapped_column(UTCDateTime)
    period_end: Mapped[datetime | None] = mapped_column(UTCDateTime)
    trial_ends_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    provider_ref: Mapped[str] = mapped_column(String(120), default="")


class UsageEvent(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "usage_event"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)  # rfq|message|llm_token|voice_minute
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("1"))
    ref_type: Mapped[str | None] = mapped_column(String(40))
    ref_id: Mapped[str | None] = mapped_column(String(36))
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime)


class Invoice(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "invoice"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    number: Mapped[str] = mapped_column(String(40))
    amount: Mapped[Decimal] = mapped_column(money(), default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    status: Mapped[str] = mapped_column(String(20), default="open")
    issued_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    paid_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    lines: Mapped[list] = mapped_column(JSONVariant, default=list)


class LlmProviderConfig(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "llm_provider_config"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    provider: Mapped[str] = mapped_column(String(40), default="fake")
    model: Mapped[str] = mapped_column(String(80), default="fake-1")
    base_url: Mapped[str] = mapped_column(String(300), default="")
    api_key_enc: Mapped[str] = mapped_column(Text, default="")
    key_id: Mapped[str] = mapped_column(String(40), default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=True)
    monthly_token_budget: Mapped[int] = mapped_column(Integer, default=5_000_000)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    is_self_hosted: Mapped[bool] = mapped_column(Boolean, default=False)


class IntegrationConfig(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "integration_config"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(40))  # smtp|graph|gmail|whatsapp|telephony|erp_sap|...
    name: Mapped[str] = mapped_column(String(120), default="")
    config: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    secrets_enc: Mapped[str] = mapped_column(Text, default="")
    key_id: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[str] = mapped_column(String(24), default="configured")
    last_tested_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class IdempotencyKey(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "idempotency_key"
    __table_args__ = (UniqueConstraint("tenant_id", "key", "endpoint", name="uq_idem"),)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    key: Mapped[str] = mapped_column(String(120))
    endpoint: Mapped[str] = mapped_column(String(160))
    request_hash: Mapped[str] = mapped_column(String(64), default="")
    status_code: Mapped[int] = mapped_column(Integer, default=200)
    response: Mapped[dict] = mapped_column(JSONVariant, default=dict)


class OutboxEvent(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "outbox_event"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    topic: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class RetentionPolicy(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "retention_policy"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    entity: Mapped[str] = mapped_column(String(60))
    retain_days: Mapped[int] = mapped_column(Integer, default=2555)
    notes: Mapped[str] = mapped_column(String(255), default="")


class DataErasureRequest(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "data_erasure_request"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    subject_email: Mapped[str] = mapped_column(String(255))
    subject_type: Mapped[str] = mapped_column(String(20), default="contact")
    status: Mapped[str] = mapped_column(String(24), default="pending")
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime)
    due_at: Mapped[datetime] = mapped_column(UTCDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    notes: Mapped[str] = mapped_column(Text, default="")


class DiscoveryJob(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "discovery_job"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    query: Mapped[str] = mapped_column(String(300))
    category_code: Mapped[str | None] = mapped_column(String(60))
    country: Mapped[str] = mapped_column(String(2), default="EG")
    provider: Mapped[str] = mapped_column(String(40), default="fake")
    status: Mapped[str] = mapped_column(String(24), default="running")
    max_results: Mapped[int] = mapped_column(Integer, default=24)
    found_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(String(400), default="")
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    requested_by: Mapped[str | None] = mapped_column(String(36))


class DiscoveryCandidate(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "discovery_candidate"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    job_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(240))
    name_ar: Mapped[str] = mapped_column(String(240), default="")
    website: Mapped[str] = mapped_column(String(300), default="")
    domain: Mapped[str] = mapped_column(String(160), default="")
    country: Mapped[str] = mapped_column(String(2), default="EG")
    city: Mapped[str] = mapped_column(String(120), default="")
    contact_email: Mapped[str] = mapped_column(String(255), default="")
    contact_phone: Mapped[str] = mapped_column(String(64), default="")
    source_url: Mapped[str] = mapped_column(String(500), default="")
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), default=0.5)
    cluster_key: Mapped[str] = mapped_column(String(160), default="")
    dedup_status: Mapped[str] = mapped_column(String(20), default="novel")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    supplier_id: Mapped[str | None] = mapped_column(String(36))
    evidence: Mapped[dict] = mapped_column(JSONVariant, default=dict)


class SupplierPortalToken(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "supplier_portal_token"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    supplier_id: Mapped[str] = mapped_column(String(36), index=True)
    token: Mapped[str] = mapped_column(String(120), unique=True)
    purpose: Mapped[str] = mapped_column(String(24), default="profile")
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    submitted: Mapped[dict] = mapped_column(JSONVariant, default=dict)


class CountryCallPolicy(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "country_call_policy"
    country: Mapped[str] = mapped_column(String(2), unique=True)
    allowed_hours_start: Mapped[int] = mapped_column(Integer, default=9)
    allowed_hours_end: Mapped[int] = mapped_column(Integer, default=18)
    recording_consent_mode: Mapped[str] = mapped_column(String(20), default="explicit")
    disclosure_text_en: Mapped[str] = mapped_column(Text, default="")
    disclosure_text_ar: Mapped[str] = mapped_column(Text, default="")
    max_call_seconds: Mapped[int] = mapped_column(Integer, default=480)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# convenience relationship-free registry for tests/introspection
ALL_MODELS = [
    Tenant, User, Role, RolePermission, UserRoleAssignment, RefreshSession, ApiKey, ApprovalLimit,
    Plant, TaxonomyNode, Category, Item, UnitConversion, Supplier, SupplierContact,
    SupplierCertification, SupplierFlag, SupplierRatingHistory, PurchaseRequisition, PRLine, RFQ,
    RFQLine, RFQSupplier, MessageTemplate, WebFormToken, ClarificationQuestion, MessageThread,
    ChannelMessage, Attachment, ExtractionResult, ExtractedField, VoiceCall, OptInRegistry, FxRate,
    LandedCostComponent, Offer, OfferLine, PriceHistory, BenchmarkPrice, NegotiationPolicy,
    NegotiationRound, NegotiationMessage, ApprovalChain, ApprovalStep, Approval, PurchaseOrder,
    AgentRun, AgentStep, ToolCall, AuditLog, Notification, Comment, SavedView, Plan, Subscription,
    UsageEvent, Invoice, LlmProviderConfig, IntegrationConfig, IdempotencyKey, OutboxEvent,
    RetentionPolicy, DataErasureRequest, DiscoveryJob, DiscoveryCandidate, SupplierPortalToken,
    CountryCallPolicy,
]


# ---------------------------------------------------------------- composite performance indexes
# Declared here so `alembic check` (model drift) stays clean: migration 0011 creates the same
# indexes for keyset pagination and hot lookups (NFR-PERF-1).
_COMPOSITE_INDEXES = [
    ("ix_rfq_tenant_status_created", RFQ, ["tenant_id", "status", "created_at"]),
    ("ix_pr_tenant_status_created", PurchaseRequisition, ["tenant_id", "status", "created_at"]),
    ("ix_offer_rfq_tco", Offer, ["rfq_id", "tco_total"]),
    ("ix_offer_supplier_created", Offer, ["supplier_id", "created_at"]),
    ("ix_price_history_item_observed", PriceHistory, ["tenant_id", "item_key", "observed_at"]),
    ("ix_channel_message_rfq_created", ChannelMessage, ["rfq_id", "created_at"]),
    ("ix_audit_tenant_created", AuditLog, ["tenant_id", "created_at"]),
    ("ix_usage_tenant_kind_occurred", UsageEvent, ["tenant_id", "kind", "occurred_at"]),
    ("ix_notification_user_unread", Notification, ["user_id", "read_at"]),
]
for _name, _model, _columns in _COMPOSITE_INDEXES:
    Index(_name, *[_model.__table__.c[col] for col in _columns])
