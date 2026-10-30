"""Data-subject rights: right of access (export) and right to erasure (FR-PRV-*).

Two rules shape this module:

* **Erasure is redaction, never deletion of the commercial record.** GDPR/PDPL do
  not require a buyer to forget that an RFQ was sent; they require the *person* to
  stop being identifiable. So we blank the PII columns (names, e-mail, phone),
  keep the transaction skeleton, and record what was purged in the append-only
  audit log — which is legally privileged and therefore exempt.
* **Every call is tenant-scoped.** A subject e-mail belonging to another tenant is
  invisible here, which is what stops an erasure endpoint from becoming a
  cross-tenant enumeration oracle.

The 30-day statutory deadline is tracked by ``DataErasureRequest.due_at`` (set by
the API layer); ``execute_erasure`` stamps ``completed_at`` so the runbook can
report on-time completion.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.db.models.entities import (
    ChannelMessage,
    DataErasureRequest,
    Supplier,
    SupplierContact,
    Tenant,
    User,
)
from app.services.suppliers import contact_pii

log = logging.getLogger("procureai.privacy")

REDACTED = "[redacted]"

#: Columns that hold direct identifiers, mapped to their redacted value.
CONTACT_PII_COLUMNS = ("name", "email", "phone")


async def _contacts_for_subject(session: AsyncSession, tenant: Tenant, subject_email: str) -> list[SupplierContact]:
    """Contacts whose decrypted e-mail matches the subject.

    PII is envelope-encrypted per tenant, so we cannot search in SQL: we decrypt
    candidate rows for this tenant only. Supplier counts per tenant are small
    (hundreds, not millions) and this endpoint is rate-limited.
    """

    contacts = list((await session.execute(
        select(SupplierContact).where(SupplierContact.tenant_id == tenant.id)
    )).scalars().all())
    matches: list[SupplierContact] = []
    wanted = subject_email.strip().lower()
    for contact in contacts:
        email, _ = await contact_pii(session, tenant, contact)
        if email and email.strip().lower() == wanted:
            matches.append(contact)
    return matches


async def export_subject_data(session: AsyncSession, tenant: Tenant, subject_email: str) -> dict[str, Any]:
    """Machine-readable bundle of everything this tenant holds about a person."""

    wanted = subject_email.strip().lower()
    user = (await session.execute(
        select(User).where(User.tenant_id == tenant.id, User.email == wanted)
    )).scalar_one_or_none()

    contacts = await _contacts_for_subject(session, tenant, wanted)
    supplier_ids = sorted({contact.supplier_id for contact in contacts})
    suppliers = list((await session.execute(
        select(Supplier).where(Supplier.tenant_id == tenant.id, Supplier.id.in_(supplier_ids))
    )).scalars().all()) if supplier_ids else []

    messages = list((await session.execute(
        select(ChannelMessage).where(
            ChannelMessage.tenant_id == tenant.id,
            ChannelMessage.supplier_id.in_(supplier_ids or ["-"]),
        ).order_by(ChannelMessage.created_at)
    )).scalars().all())

    contact_rows = []
    for contact in contacts:
        email, phone = await contact_pii(session, tenant, contact)
        contact_rows.append({
            "supplier_id": contact.supplier_id,
            "name": contact.name,
            "role": contact.role,
            "email": email,
            "phone": phone,
            "language": contact.language,
            "email_opt_in": contact.email_opt_in,
            "whatsapp_opt_in": contact.whatsapp_opt_in,
        })

    return {
        "tenant": {"name": tenant.name, "slug": tenant.slug},
        "subject_email": wanted,
        "generated_at": datetime.now(UTC).isoformat(),
        "user": {
            "id": user.id, "email": user.email, "full_name": user.full_name,
            "job_title": user.job_title, "phone": user.phone, "roles": list(user.roles or []),
            "created_at": user.created_at.isoformat() if user.created_at else None,
        } if user else None,
        "supplier_contacts": contact_rows,
        "suppliers": [{
            "id": supplier.id, "name": supplier.name, "country": supplier.country,
            "city": supplier.city, "status": supplier.status,
        } for supplier in suppliers],
        "messages": [{
            "id": message.id, "rfq_id": message.rfq_id, "channel": message.channel,
            "direction": message.direction, "subject": message.subject,
            "created_at": message.created_at.isoformat() if message.created_at else None,
        } for message in messages],
        "notes": [
            "Audit-log entries are retained as legally privileged records (see SECURITY.md).",
            "Commercial documents (RFQs, offers, purchase orders) are retained for the "
            "statutory procurement period after PII redaction.",
        ],
    }


async def execute_erasure(session: AsyncSession, tenant: Tenant, request: DataErasureRequest) -> dict[str, Any]:
    """Redact the subject's PII and mark the request complete.

    Returns a machine-readable summary so the caller (and the audit trail) can show
    exactly what changed. Idempotent: re-running redacts nothing and reports zero.
    """

    wanted = request.subject_email.strip().lower()
    purged: dict[str, int] = {"supplier_contacts": 0, "users": 0, "messages": 0, "suppliers": 0}

    contacts = await _contacts_for_subject(session, tenant, wanted)
    supplier_ids = sorted({contact.supplier_id for contact in contacts})
    for contact in contacts:
        for column in CONTACT_PII_COLUMNS:
            if getattr(contact, column):
                setattr(contact, column, REDACTED)
                purged["supplier_contacts"] += 1
        contact.pending_update = {}
        contact.email_opt_in = False
        contact.whatsapp_opt_in = False

    user = (await session.execute(
        select(User).where(User.tenant_id == tenant.id, User.email == wanted)
    )).scalar_one_or_none()
    if user:
        user.email = f"erased+{user.id}@{tenant.slug}.invalid"
        user.full_name = REDACTED
        user.full_name_ar = REDACTED
        user.phone = ""
        user.is_active = False
        purged["users"] = 1

    # Free-text bodies that quote the subject are scrubbed; the envelope (who, when,
    # which RFQ) stays so the commercial history remains auditable. We only touch
    # rows that actually contain the identifier — the rest is legitimate business
    # correspondence and must not be destroyed.
    if supplier_ids:
        messages = list((await session.execute(
            select(ChannelMessage).where(
                ChannelMessage.tenant_id == tenant.id,
                ChannelMessage.supplier_id.in_(supplier_ids),
            )
        )).scalars().all())
        for message in messages:
            body = f"{message.body or ''}\n{message.body_ar or ''}\n{message.subject or ''}".lower()
            if wanted and wanted in body:
                message.body = REDACTED
                message.body_ar = REDACTED if message.body_ar else ""
                message.subject = REDACTED
                purged["messages"] += 1

    if supplier_ids:
        suppliers = list((await session.execute(
            select(Supplier).where(Supplier.tenant_id == tenant.id, Supplier.id.in_(supplier_ids))
        )).scalars().all())
        for supplier in suppliers:
            supplier.pending_profile = {}
            purged["suppliers"] += 1

    request.status = "completed"
    request.completed_at = datetime.now(UTC)
    await session.flush()
    await audit(session, tenant_id=tenant.id, action="privacy.erasure_executed", actor_type="system",
                actor_label="privacy", entity_type="data_erasure_request", entity_id=request.id,
                after={"subject_type": request.subject_type, "purged": purged})
    log.info("erasure executed request=%s purged=%s", request.id, purged)
    return purged
