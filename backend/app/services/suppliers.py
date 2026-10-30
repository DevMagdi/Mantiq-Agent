"""Supplier master data: CRUD helpers, import, scoring, dedup and sanctions screening."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import SupplierCandidateData
from app.core.crypto import decrypt_text, encrypt_text
from app.db.models.entities import (
    Supplier,
    SupplierCertification,
    SupplierContact,
    SupplierFlag,
    SupplierRatingHistory,
    Tenant,
)
from app.services.taxonomy import tokens

# OFAC/EU/UN style list-file entries (shipped sample; production ingests the real lists).
SANCTIONS_SAMPLE = [
    {"name": "Suez Canal Trading FZE", "program": "EU", "country": "AE"},
    {"name": "Delta Industrial Equipment LLC", "program": "OFAC", "country": "RU"},
    {"name": "North Star Metals", "program": "UN", "country": "IR"},
]

WEIGHTS = {"price": 0.30, "lead_time": 0.25, "quality": 0.25, "responsiveness": 0.20}


def normalize_domain(url_or_domain: str) -> str:
    if not url_or_domain:
        return ""
    value = re.sub(r"^https?://", "", url_or_domain.strip().lower())
    value = value.split("/")[0]
    return re.sub(r"^www\.", "", value)


def normalize_phone(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")[-9:]


async def find_duplicates(session: AsyncSession, tenant_id: str, candidate: SupplierCandidateData,
                          existing: list[Supplier]) -> tuple[str, str | None]:
    """Return (dedup_status, matched_supplier_id) using domain/phone/VAT + name trigrams."""
    from app.services.taxonomy import jaccard, trigrams

    domain = normalize_domain(candidate.domain or candidate.website)
    phone = normalize_phone(candidate.phone)
    name_tokens = tokens(candidate.name)
    for supplier in existing:
        if domain and supplier.domain and normalize_domain(supplier.domain) == domain:
            return "duplicate", supplier.id
        if phone and supplier.notes and phone in normalize_phone(supplier.notes):
            return "duplicate", supplier.id
        similarity = jaccard(name_tokens, tokens(supplier.name))
        if similarity >= 0.75 or jaccard(trigrams(candidate.name), trigrams(supplier.name)) >= 0.7:
            return "likely_duplicate", supplier.id
    return "novel", None


def screen_sanctions(name: str, country: str = "") -> tuple[str, list[dict]]:
    """Fuzzy sanctions match (normalised name tokens + country hint)."""
    name_tokens = tokens(name)
    hits: list[dict] = []
    for entry in SANCTIONS_SAMPLE:
        entry_tokens = tokens(entry["name"])
        if not entry_tokens:
            continue
        overlap = len(name_tokens & entry_tokens) / len(name_tokens | entry_tokens)
        country_match = country.upper() == entry["country"] if country else False
        if overlap >= 0.6 or (country_match and overlap >= 0.34):
            hits.append({**entry, "similarity": round(overlap, 3)})
    status = "flagged" if hits else "clear"
    return status, hits


async def upsert_contact(session: AsyncSession, tenant: Tenant, supplier: Supplier, *, name: str,
                         email: str, phone: str, language: str = "ar", is_primary: bool = True,
                         whatsapp: bool = False) -> SupplierContact:
    email_enc, key_id = await encrypt_text(session, tenant, email)
    phone_enc, _ = await encrypt_text(session, tenant, phone)
    contact = SupplierContact(
        tenant_id=tenant.id, supplier_id=supplier.id, name=name, email=email_enc, phone=phone_enc,
        key_id=key_id, language=language, is_primary=is_primary, whatsapp_opt_in=whatsapp,
        email_opt_in=bool(email),
    )
    session.add(contact)
    await session.flush()
    return contact


async def contact_pii(session: AsyncSession, tenant: Tenant, contact: SupplierContact) -> tuple[str, str]:
    email = await decrypt_text(session, tenant, contact.email, contact.key_id)
    phone = await decrypt_text(session, tenant, contact.phone, contact.key_id)
    return email, phone


async def primary_contact(session: AsyncSession, tenant: Tenant, supplier_id: str) -> SupplierContact | None:
    return (await session.execute(
        select(SupplierContact).where(SupplierContact.tenant_id == tenant.id,
                                     SupplierContact.supplier_id == supplier_id)
        .order_by(SupplierContact.is_primary.desc(), SupplierContact.created_at.asc()).limit(1)
    )).scalar_one_or_none()


async def create_supplier(session: AsyncSession, tenant: Tenant, *, name: str, name_ar: str = "",
                          country: str = "EG", city: str = "", website: str = "", source_url: str = "",
                          categories: list[str] | None = None, email: str = "", phone: str = "",
                          language: str = "ar", status: str = "approved", confidence: float = 1.0,
                          created_by: str | None = None) -> Supplier:
    supplier = Supplier(
        tenant_id=tenant.id, name=name, name_ar=name_ar, country=country, city=city, website=website,
        domain=normalize_domain(website), categories=categories or [], source_url=source_url,
        status=status, confidence=Decimal(str(confidence)), languages=[language], created_by=created_by,
    )
    sanctions_status, hits = screen_sanctions(name, country)
    supplier.sanctions_status = sanctions_status
    supplier.sanctions_checked_at = datetime.now(UTC)
    if hits:
        supplier.flags = [{"type": "sanctions", "hits": hits}]
        supplier.status = "blacklisted" if status != "discovered" else status
        supplier.notes = f"Sanctions screening hit: {hits[0]['program']} ({hits[0]['similarity']})."
    session.add(supplier)
    await session.flush()
    if email or phone:
        await upsert_contact(session, tenant, supplier, name="Primary contact", email=email, phone=phone,
                             language=language, whatsapp=bool(phone))
    return supplier


async def add_flag(session: AsyncSession, tenant: Tenant, supplier: Supplier, flag: str, reason: str,
                   created_by: str | None = None) -> SupplierFlag:
    row = SupplierFlag(tenant_id=tenant.id, supplier_id=supplier.id, flag=flag, reason=reason,
                       created_by=created_by)
    session.add(row)
    if flag == "blacklisted":
        supplier.status = "blacklisted"
    elif flag == "preferred":
        supplier.status = "preferred"
    flags = list(supplier.flags or [])
    flags.append({"type": flag, "reason": reason, "at": datetime.now(UTC).isoformat()})
    supplier.flags = flags
    await session.flush()
    return row


def recompute_score(supplier: Supplier) -> Decimal:
    score = (
        Decimal(str(supplier.rating_price or 0)) * Decimal(str(WEIGHTS["price"]))
        + Decimal(str(supplier.rating_lead_time or 0)) * Decimal(str(WEIGHTS["lead_time"]))
        + Decimal(str(supplier.rating_quality or 0)) * Decimal(str(WEIGHTS["quality"]))
        + Decimal(str(supplier.rating_responsiveness or 0)) * Decimal(str(WEIGHTS["responsiveness"]))
    )
    return score.quantize(Decimal("0.01"))


async def update_ratings(session: AsyncSession, tenant: Tenant, supplier: Supplier, *,
                         price: float | None = None, lead_time: float | None = None,
                         quality: float | None = None, responsiveness: float | None = None,
                         source: str = "rfq_outcome") -> Supplier:
    if price is not None:
        supplier.rating_price = Decimal(str(round(price, 2)))
    if lead_time is not None:
        supplier.rating_lead_time = Decimal(str(round(lead_time, 2)))
    if quality is not None:
        supplier.rating_quality = Decimal(str(round(quality, 2)))
    if responsiveness is not None:
        supplier.rating_responsiveness = Decimal(str(round(responsiveness, 2)))
    supplier.overall_score = recompute_score(supplier)
    session.add(SupplierRatingHistory(
        tenant_id=tenant.id, supplier_id=supplier.id, price=supplier.rating_price,
        lead_time=supplier.rating_lead_time, quality=supplier.rating_quality,
        responsiveness=supplier.rating_responsiveness, overall=supplier.overall_score, source=source,
    ))
    await session.flush()
    return supplier


async def rate_from_outcomes(session: AsyncSession, tenant: Tenant, supplier: Supplier,
                             *, responded: bool, lead_time_days: int | None, price_index: float | None) -> Supplier:
    """Blend new outcome signals into the running ratings (exponential smoothing)."""
    def blend(old: Decimal | None, new: float, alpha: float = 0.3) -> Decimal:
        old_f = float(old or 60)
        return Decimal(str(round(old_f * (1 - alpha) + new * alpha, 2)))

    responsiveness = 95.0 if responded else 35.0
    price_score = None
    if price_index is not None:
        price_score = max(0.0, min(100.0, 100 - (price_index - 1) * 100))
    lead_score = None
    if lead_time_days is not None:
        lead_score = max(0.0, min(100.0, 100 - max(0, lead_time_days - 14) * 1.5))
    return await update_ratings(
        session, tenant, supplier,
        price=price_score if price_score is not None else float(supplier.rating_price or 60),
        lead_time=lead_score if lead_score is not None else float(supplier.rating_lead_time or 60),
        quality=float(supplier.rating_quality or 60),
        responsiveness=float(blend(supplier.rating_responsiveness, responsiveness)),
        source="rfq_outcome",
    )


async def search_suppliers(session: AsyncSession, tenant_id: str, query: str = "", *,
                           category: str | None = None, country: str | None = None,
                           status: str | None = None, limit: int = 50, offset: int = 0) -> tuple[list[Supplier], int]:
    from sqlalchemy import String, cast

    filters = [Supplier.tenant_id == tenant_id, Supplier.deleted_at.is_(None)]
    if query:
        like = f"%{query.strip()}%"
        filters.append(or_(Supplier.name.ilike(like), Supplier.name_ar.ilike(like),
                           Supplier.city.ilike(like), cast(Supplier.categories, String).ilike(like)))
    if country:
        filters.append(Supplier.country == country)
    if status:
        filters.append(Supplier.status == status)
    if category:
        filters.append(cast(Supplier.categories, String).ilike(f"%{category}%"))
    total = (await session.execute(select(func.count()).select_from(Supplier).where(*filters))).scalar_one()
    rows = (await session.execute(
        select(Supplier).where(*filters).order_by(Supplier.overall_score.desc(), Supplier.name.asc())
        .limit(limit).offset(offset)
    )).scalars().all()
    return list(rows), int(total)


async def supplier_brief(session: AsyncSession, tenant: Tenant, supplier: Supplier) -> dict:
    contact = await primary_contact(session, tenant, supplier.id)
    email, phone = await contact_pii(session, tenant, contact) if contact else ("", "")
    certs = (await session.execute(
        select(SupplierCertification).where(SupplierCertification.supplier_id == supplier.id)
    )).scalars().all()
    return {
        "id": supplier.id,
        "name": supplier.name,
        "name_ar": supplier.name_ar,
        "country": supplier.country,
        "city": supplier.city,
        "website": supplier.website,
        "status": supplier.status,
        "overall_score": float(supplier.overall_score or 0),
        "categories": supplier.categories or [],
        "contact": {
            "name": contact.name if contact else "",
            "email": email,
            "phone": phone,
            "language": contact.language if contact else "ar",
            "email_opt_in": contact.email_opt_in if contact else False,
            "whatsapp_opt_in": contact.whatsapp_opt_in if contact else False,
        },
        "sanctions_status": supplier.sanctions_status,
        "certifications": [
            {"code": c.code, "issuer": c.issuer, "expires_at": c.expires_at} for c in certs
        ],
        "source_url": supplier.source_url,
        "confidence": float(supplier.confidence or 0),
    }
