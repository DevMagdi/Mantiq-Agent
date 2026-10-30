"""Supplier-facing, no-login surfaces: parameterised web quote forms and the supplier portal.

Tokens are single-purpose, tenant-scoped, expiring and revocable. Everything a supplier submits
is treated as untrusted input: it flows through the same extraction + verification pipeline as an
email reply, so a public form can never create an offer that a buyer has not verified.
"""
from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import NotFound, ValidationFailed
from app.db.models.entities import (
    RFQ,
    ChannelMessage,
    RFQLine,
    Supplier,
    SupplierContact,
    SupplierPortalToken,
    WebFormToken,
)
from app.services import numbers
from app.services.suppliers import contact_pii

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- tokens
def new_token() -> str:
    return secrets.token_urlsafe(32)


async def form_token_for(session: AsyncSession, tenant_id: str, rfq_id: str, supplier_id: str, *,
                         days: int = 14) -> WebFormToken:
    existing = (await session.execute(
        select(WebFormToken).where(WebFormToken.tenant_id == tenant_id, WebFormToken.rfq_id == rfq_id,
                                   WebFormToken.supplier_id == supplier_id,
                                   WebFormToken.expires_at > datetime.now(UTC))
    )).scalars().first()
    if existing:
        return existing
    row = WebFormToken(tenant_id=tenant_id, rfq_id=rfq_id, supplier_id=supplier_id,
                       token=new_token(), expires_at=datetime.now(UTC) + timedelta(days=days))
    session.add(row)
    await session.flush()
    return row


async def _load_form(session: AsyncSession, token: str) -> tuple[WebFormToken, RFQ, Supplier]:
    row = (await session.execute(select(WebFormToken).where(WebFormToken.token == token))).scalar_one_or_none()
    if not row:
        raise NotFound("This quotation link is not valid.")
    if row.expires_at and row.expires_at < datetime.now(UTC):
        raise ValidationFailed("This quotation link has expired.")
    rfq = (await session.execute(select(RFQ).where(RFQ.id == row.rfq_id))).scalar_one_or_none()
    supplier = (await session.execute(select(Supplier).where(Supplier.id == row.supplier_id))).scalar_one_or_none()
    if not rfq or not supplier:
        raise NotFound("This quotation link is no longer available.")
    if rfq.status in {"closed", "cancelled"}:
        raise ValidationFailed("This request for quotation is closed.")
    return row, rfq, supplier


async def form_view(session: AsyncSession, token: str) -> dict:
    row, rfq, supplier = await _load_form(session, token)
    lines = list((await session.execute(
        select(RFQLine).where(RFQLine.rfq_id == rfq.id).order_by(RFQLine.line_no)
    )).scalars().all())
    contact = (await session.execute(
        select(SupplierContact).where(SupplierContact.supplier_id == supplier.id,
                                      SupplierContact.is_primary.is_(True))
    )).scalars().first()
    contact_name = contact.name if contact else ""
    return {
        "rfq": {"code": rfq.code, "title": rfq.title, "currency": rfq.currency, "deadline": rfq.deadline,
                "incoterm": rfq.incoterm, "payment_terms": rfq.payment_terms},
        "supplier": {"name": supplier.name, "name_ar": supplier.name_ar},
        "contact_name": contact_name,
        "lines": [{"id": line.id, "line_no": line.line_no, "description": line.description,
                   "quantity": str(line.quantity), "uom": line.uom} for line in lines],
        "expires_at": row.expires_at,
        "fields": ["unit_price", "currency", "lead_time_days", "validity_days", "payment_terms", "incoterm",
                   "moq", "notes"],
    }


def _quote_text(rfq: RFQ, supplier: Supplier, lines: list[dict], header: dict) -> str:
    """Render the structured submission into the same textual form the extractor understands."""
    parts = [f"Quotation for {rfq.code}", f"Supplier: {supplier.name}"]
    for key, label in (("currency", "Currency"), ("payment_terms", "Payment terms"), ("incoterm", "Incoterm"),
                       ("validity_days", "Validity days"), ("lead_time_days", "Lead time days"),
                       ("moq", "MOQ")):
        if header.get(key) not in (None, ""):
            parts.append(f"{label}: {header[key]}")
    for index, line in enumerate(lines, start=1):
        if not line.get("unit_price"):
            continue
        parts.append(
            f"Line {index}: {line.get('description') or ''} | unit price {line['unit_price']} per "
            f"{line.get('uom') or 'unit'} | quantity {line.get('quantity')}"
        )
    if header.get("notes"):
        parts.append(str(header["notes"])[:1000])
    return "\n".join(parts)


async def submit_form(session: AsyncSession, token: str, payload: dict) -> dict:
    from app.services.extraction import ExtractionPayload, ensure_thread, extract_offer

    row, rfq, supplier = await _load_form(session, token)
    currency = (payload.get("currency") or rfq.currency or "EGP").upper()
    if currency not in numbers.SUPPORTED_CURRENCIES:
        raise ValidationFailed(f"Unsupported currency {currency}.")
    lines_payload = payload.get("lines") or []
    if not isinstance(lines_payload, list) or not lines_payload:
        raise ValidationFailed("Submit at least one priced line.")
    cleaned: list[dict] = []
    for line in lines_payload:
        price = numbers.parse_decimal(str(line.get("unit_price", "")))
        if price is None or price <= 0:
            continue
        cleaned.append({**line, "unit_price": str(numbers.money(price))})
    if not cleaned:
        raise ValidationFailed("At least one line must have a positive unit price.")

    body = _quote_text(rfq, supplier, cleaned, payload)
    from app.db.models.entities import Tenant

    tenant = (await session.execute(select(Tenant).where(Tenant.id == rfq.tenant_id))).scalar_one()
    thread = await ensure_thread(session, tenant, rfq, supplier, "web_form", subject=f"RFQ {rfq.code}")
    message = ChannelMessage(
        tenant_id=rfq.tenant_id, thread_id=thread.id, rfq_id=rfq.id, supplier_id=supplier.id,
        direction="inbound", channel="web_form", subject=f"RFQ {rfq.code}", body=body[:20000],
        status="received", author_type="supplier", received_at=datetime.now(UTC),
        extra={"source": "web_form", "structured": True},
    )
    session.add(message)
    thread.last_message_at = datetime.now(UTC)
    await session.flush()

    extraction, offer = await extract_offer(
        session, tenant, rfq=rfq, supplier=supplier,
        payload=ExtractionPayload(text=body, source="web_form"), message_id=message.id,
    )
    row.used_at = datetime.now(UTC)
    await session.flush()
    return {
        "ok": True,
        "message": "Quotation received. The buyer will verify it before it is used in the comparison.",
        "extraction_id": extraction.id if extraction else None,
        "offer_id": offer.id if offer else None,
        "confidence": float(extraction.confidence) if extraction else None,
        "needs_review": bool(extraction.needs_review) if extraction else True,
    }


# --------------------------------------------------------------------------- supplier portal
async def portal_token(session: AsyncSession, tenant_id: str, supplier_id: str, *, purpose: str = "profile",
                       days: int = 30) -> SupplierPortalToken:
    row = SupplierPortalToken(tenant_id=tenant_id, supplier_id=supplier_id, purpose=purpose,
                              token=new_token(), expires_at=datetime.now(UTC) + timedelta(days=days))
    session.add(row)
    await session.flush()
    return row


async def _load_portal(session: AsyncSession, token: str) -> tuple[SupplierPortalToken, Supplier]:
    row = (await session.execute(
        select(SupplierPortalToken).where(SupplierPortalToken.token == token)
    )).scalar_one_or_none()
    if not row:
        raise NotFound("This portal link is not valid.")
    if row.expires_at and row.expires_at < datetime.now(UTC):
        raise ValidationFailed("This portal link has expired.")
    supplier = (await session.execute(select(Supplier).where(Supplier.id == row.supplier_id))).scalar_one_or_none()
    if not supplier:
        raise NotFound("This portal link is no longer available.")
    return row, supplier


async def portal_view(session: AsyncSession, token: str) -> dict:
    row, supplier = await _load_portal(session, token)
    from app.db.models.entities import Tenant

    tenant = (await session.execute(select(Tenant).where(Tenant.id == supplier.tenant_id))).scalar_one()
    contacts = list((await session.execute(
        select(SupplierContact).where(SupplierContact.supplier_id == supplier.id)
    )).scalars().all())
    masked = []
    for contact in contacts:
        email, phone = await contact_pii(session, tenant, contact)
        masked.append({"name": contact.name, "role": contact.role,
                       "email": _mask(email), "phone": _mask(phone), "is_primary": contact.is_primary})
    return {
        "supplier": {"name": supplier.name, "name_ar": supplier.name_ar, "country": supplier.country,
                     "city": supplier.city, "website": supplier.website, "status": supplier.status,
                     "categories": supplier.categories},
        "contacts": masked,
        "requested": row.purpose,
        "expires_at": row.expires_at,
        "message": "Review your company profile and confirm the categories you supply. "
                   "A buyer will approve any change before it affects sourcing.",
    }


def _mask(value: str) -> str:
    if not value:
        return ""
    if "@" in value:
        name, _, domain = value.partition("@")
        return f"{name[:2]}***@{domain}"
    return f"***{value[-3:]}"


async def submit_portal(session: AsyncSession, token: str, payload: dict) -> dict:
    row, supplier = await _load_portal(session, token)
    changes: dict = {}
    if payload.get("categories"):
        categories = [str(c)[:40] for c in payload["categories"]][:30]
        supplier.categories = sorted({*supplier.categories, *categories})
        changes["categories"] = categories
    if payload.get("website"):
        supplier.website = str(payload["website"])[:300]
        changes["website"] = supplier.website
    if payload.get("phone") or payload.get("email"):
        contact = (await session.execute(
            select(SupplierContact).where(SupplierContact.supplier_id == supplier.id,
                                          SupplierContact.is_primary.is_(True))
        )).scalars().first()
        if contact:
            changes["contact_updated"] = True
            contact.pending_update = {"email": str(payload.get("email") or "")[:200],
                                      "phone": str(payload.get("phone") or "")[:60]}
    if payload.get("catalog"):
        changes["catalog_lines"] = len(payload["catalog"])
    supplier.pending_profile = {**(supplier.pending_profile or {}), **changes,
                                "submitted_at": datetime.now(UTC).isoformat()}
    supplier.confidence = Decimal("0.90")
    row.used_at = datetime.now(UTC)
    await session.flush()
    log.info("supplier portal submission supplier=%s changes=%s", supplier.id, list(changes))
    return {"ok": True, "pending_review": True, "changes": list(changes)}


async def create_portal_token(session: AsyncSession, tenant, supplier_id: str, *, purpose: str = "profile",
                              days: int = 14) -> str:
    """Create a supplier portal link (public URL, no login) and return it."""
    row = await portal_token(session, tenant.id, supplier_id, purpose=purpose, days=days)
    return f"{public_base_url()}/portal/{row.token}"


def public_base_url() -> str:
    return settings.public_base_url.rstrip("/")
