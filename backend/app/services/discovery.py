"""Autonomous supplier discovery with dedup, confidence and the buyer review gate (FR-SUP-2..3)."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.entities import DiscoveryCandidate, DiscoveryJob, Supplier, Tenant
from app.services import integrations as integration_adapters
from app.services.suppliers import create_supplier, find_duplicates, screen_sanctions

log = logging.getLogger("procureai.discovery")


async def run_discovery(session: AsyncSession, tenant: Tenant, *, query: str, country: str = "EG",
                        category_code: str | None = None, max_results: int = 24,
                        requested_by: str | None = None) -> DiscoveryJob:
    provider = await integration_adapters.adapter_for(session, tenant, "search")
    job = DiscoveryJob(
        tenant_id=tenant.id, query=query, category_code=category_code, country=country,
        provider=getattr(provider, "name", "fake"), status="running", max_results=max_results,
        started_at=datetime.now(UTC), requested_by=requested_by,
    )
    session.add(job)
    await session.flush()

    existing = list((await session.execute(
        select(Supplier).where(Supplier.tenant_id == tenant.id)
    )).scalars().all())

    try:
        candidates = await provider.discover_suppliers(query, country=country, limit=max_results)
    except Exception as exc:  # provider outage is non-fatal
        log.warning("discovery provider failed: %s", exc)
        candidates = []
        job.error = str(exc)[:300]

    seen_domains: set[str] = set()
    for data in candidates:
        dedup_status, matched = await find_duplicates(session, tenant.id, data, existing)
        if data.domain and data.domain in seen_domains:
            dedup_status = "duplicate"
        seen_domains.add(data.domain)
        sanctions_status, hits = screen_sanctions(data.name, data.country)
        confidence = Decimal(str(round(min(0.99, data.confidence), 4)))
        session.add(DiscoveryCandidate(
            tenant_id=tenant.id, job_id=job.id, name=data.name, name_ar=data.name_ar, website=data.website,
            domain=data.domain, country=data.country, city=data.city, contact_email=data.email,
            contact_phone=data.phone, source_url=data.source_url, confidence=confidence,
            cluster_key=data.domain or data.name.lower(), dedup_status=dedup_status,
            status="pending" if dedup_status == "novel" else "duplicate",
            supplier_id=matched, evidence={**data.evidence, "sanctions": sanctions_status,
                                           "sanctions_hits": hits} if hits else data.evidence,
        ))
    job.found_count = len(candidates)
    job.status = "completed"
    job.finished_at = datetime.now(UTC)
    await session.flush()
    return job


async def list_candidates(session: AsyncSession, tenant_id: str, job_id: str) -> list[DiscoveryCandidate]:
    return list((await session.execute(
        select(DiscoveryCandidate).where(DiscoveryCandidate.tenant_id == tenant_id,
                                        DiscoveryCandidate.job_id == job_id)
        .order_by(DiscoveryCandidate.confidence.desc())
    )).scalars().all())


async def approve_candidates(session: AsyncSession, tenant: Tenant, candidate_ids: list[str],
                             *, created_by: str | None = None) -> list[Supplier]:
    """Buyer-approved shortlist becomes real (still `discovered`) suppliers — never auto-contacted."""
    candidates = list((await session.execute(
        select(DiscoveryCandidate).where(DiscoveryCandidate.tenant_id == tenant.id,
                                        DiscoveryCandidate.id.in_(candidate_ids))
    )).scalars().all())
    created: list[Supplier] = []
    for cand in candidates:
        if cand.supplier_id:
            continue
        supplier = await create_supplier(
            session, tenant, name=cand.name, name_ar=cand.name_ar, country=cand.country, city=cand.city,
            website=cand.website, source_url=cand.source_url, email=cand.contact_email,
            phone=cand.contact_phone, status="discovered", confidence=float(cand.confidence),
            created_by=created_by,
        )
        cand.status = "approved"
        cand.supplier_id = supplier.id
        created.append(supplier)
    await session.flush()
    return created


async def reject_candidates(session: AsyncSession, tenant_id: str, candidate_ids: list[str]) -> int:
    rows = list((await session.execute(
        select(DiscoveryCandidate).where(DiscoveryCandidate.tenant_id == tenant_id,
                                        DiscoveryCandidate.id.in_(candidate_ids))
    )).scalars().all())
    for row in rows:
        row.status = "rejected"
    await session.flush()
    return len(rows)
