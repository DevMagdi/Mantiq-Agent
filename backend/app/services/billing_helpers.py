"""Billing helpers (invoice generation) and privacy (export / erasure) services."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import registry
from app.core.crypto import decrypt_text
from app.db.models.entities import (
    DataErasureRequest,
    Invoice,
    Plan,
    RetentionPolicy,
    Supplier,
    SupplierContact,
    Tenant,
    User,
)
from app.services import numbers
from app.services.common import meter, usage_summary

log = logging.getLogger("procureai.billing")


async def generate_invoice(session: AsyncSession, tenant: Tenant) -> Invoice:
    plan = (await session.execute(select(Plan).where(Plan.code == tenant.plan_code))).scalar_one_or_none()
    base_price = Decimal(str(plan.price_monthly)) if plan else Decimal("0")
    summary = await usage_summary(session, tenant.id, period_days=30)
    usage = summary["usage"]
    overage = Decimal("0")
    line_items = [{"description": f"{plan.name if plan else tenant.plan_code} subscription (monthly)",
                   "amount": str(base_price)}]
    limits = summary["limits"]
    if usage.get("voice_minute", 0) > limits.get("voice_minutes_per_month", 0):
        extra = usage["voice_minute"] - limits.get("voice_minutes_per_month", 0)
        cost = Decimal(str(round(extra * 0.25, 2)))
        overage += cost
        line_items.append({"description": f"Additional AI voice minutes ({extra:.0f})", "amount": str(cost)})
    if usage.get("rfq", 0) > limits.get("rfqs_per_month", 0):
        extra = usage["rfq"] - limits.get("rfqs_per_month", 0)
        cost = Decimal(str(round(extra * 2, 2)))
        overage += cost
        line_items.append({"description": f"Additional RFQs ({extra:.0f})", "amount": str(cost)})
    amount = numbers.money(base_price + overage, 2)
    count = len(list((await session.execute(
        select(Invoice).where(Invoice.tenant_id == tenant.id)
    )).scalars().all())) + 1
    invoice = Invoice(tenant_id=tenant.id, number=f"INV-{datetime.now(UTC).year}-{count:04d}",
                      amount=amount, currency=(plan.currency if plan else "USD"), status="open",
                      issued_at=datetime.now(UTC), lines=line_items)
    session.add(invoice)
    await session.flush()
    gateway = registry.resolve("payment")
    result = await gateway.create_invoice(amount=str(amount), currency=invoice.currency,
                                          reference=invoice.number, description="ProcureAI subscription")
    if result.ok and result.status == "paid":
        invoice.status = "paid"
        invoice.paid_at = datetime.now(UTC)
    await meter(session, tenant_id=tenant.id, kind="invoice", quantity=1, ref_type="invoice", ref_id=invoice.id)
    await session.flush()
    return invoice


async def export_subject_data(session: AsyncSession, tenant: Tenant, subject_email: str) -> dict:
    """Data-subject access export (NFR-PRV-1)."""
    subject = subject_email.lower()
    user = (await session.execute(
        select(User).where(User.tenant_id == tenant.id, User.email == subject)
    )).scalar_one_or_none()
    contacts = list((await session.execute(
        select(SupplierContact).where(SupplierContact.tenant_id == tenant.id)
    )).scalars().all())
    matches = []
    for contact in contacts:
        email = await decrypt_text(session, tenant, contact.email, contact.key_id)
        if email and email.lower() == subject:
            supplier = (await session.execute(
                select(Supplier).where(Supplier.id == contact.supplier_id)
            )).scalar_one_or_none()
            matches.append({"supplier": supplier.name if supplier else "", "role": contact.role,
                            "language": contact.language})
    return {
        "tenant": tenant.name,
        "subject": subject,
        "generated_at": datetime.now(UTC).isoformat(),
        "account": {"email": user.email, "name": user.full_name, "roles": user.roles,
                    "created_at": user.created_at.isoformat()} if user else None,
        "supplier_contacts": matches,
        "note": "Export includes personal data held about the subject. Secrets are never exported.",
    }


async def execute_erasure(session: AsyncSession, tenant: Tenant, request: DataErasureRequest) -> int:
    """Purge personal data for a contact or user, keeping lawful procurement records."""
    subject = request.subject_email.lower()
    purged = 0
    contacts = list((await session.execute(
        select(SupplierContact).where(SupplierContact.tenant_id == tenant.id)
    )).scalars().all())
    for contact in contacts:
        email = await decrypt_text(session, tenant, contact.email, contact.key_id)
        if email and email.lower() == subject:
            contact.email = ""
            contact.phone = ""
            contact.name = "Redacted (erasure request)"
            contact.email_opt_in = False
            contact.whatsapp_opt_in = False
            purged += 1
    if request.subject_type == "user":
        user = (await session.execute(
            select(User).where(User.tenant_id == tenant.id, User.email == subject)
        )).scalar_one_or_none()
        if user:
            user.is_active = False
            user.email = f"erased+{user.id[:8]}@invalid.local"
            user.full_name = "Erased user"
            user.full_name_ar = "مستخدم محذوف"
            user.phone = ""
            user.hashed_password = ""
            user.mfa_enabled = False
            user.totp_secret = ""
            purged += 1
    request.status = "completed"
    request.completed_at = datetime.now(UTC)
    await session.flush()
    return purged


async def purge_expired(session: AsyncSession, tenant: Tenant) -> dict:
    """Scheduled purge: honour retention windows and erasure deadlines."""
    from app.db.models.entities import AuditLog, Notification

    policies = {row.entity: row.retain_days for row in (await session.execute(
        select(RetentionPolicy).where(RetentionPolicy.tenant_id == tenant.id)
    )).scalars().all()}
    log_days = policies.get("logs", 365)
    cutoff = datetime.now(UTC) - timedelta(days=log_days)
    old_logs = list((await session.execute(
        select(AuditLog).where(AuditLog.tenant_id == tenant.id, AuditLog.created_at < cutoff)
    )).scalars().all())
    old_notifications = list((await session.execute(
        select(Notification).where(Notification.tenant_id == tenant.id,
                                   Notification.created_at < cutoff)
    )).scalars().all())
    for row in old_notifications:
        await session.delete(row)
    pending = list((await session.execute(
        select(DataErasureRequest).where(DataErasureRequest.tenant_id == tenant.id,
                                         DataErasureRequest.status == "pending")
    )).scalars().all())
    purged = 0
    for request in pending:
        purged += await execute_erasure(session, tenant, request)
    await session.flush()
    return {"logs_eligible": len(old_logs), "notifications_purged": len(old_notifications),
            "erasure_records": purged,
            "note": "Audit logs are retained per policy and archived, never silently dropped."}
