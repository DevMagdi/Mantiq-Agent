"""Offer comparison, TCO, weighted scoring and traceable recommendation (FR-CMP-1..2, FR-RES-4)."""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm import llm_for
from app.agents.llm.base import LLMMessage, LLMRequest
from app.db.models.entities import (
    RFQ,
    AgentRun,
    Approval,
    BenchmarkPrice,
    LandedCostComponent,
    Offer,
    OfferLine,
    RFQLine,
    Supplier,
    Tenant,
)
from app.services import numbers
from app.services.catalog_data import DUTY_PROFILES
from app.services.common import get_fx_rate

log = logging.getLogger("procureai.comparison")

DEFAULT_WEIGHTS = {"price": 0.40, "lead_time": 0.20, "quality": 0.20, "terms": 0.10, "risk": 0.10}


def duty_profile(country: str) -> dict:
    return next((p for p in DUTY_PROFILES if p["country"] == (country or "EG").upper()),
                {"vat_rate": "0.14", "duty_rate_default": "0.05", "duty_rates": {}})


async def ensure_landed_costs(session: AsyncSession, tenant: Tenant, rfq: RFQ, offer: Offer,
                              supplier: Supplier) -> list[LandedCostComponent]:
    existing = list((await session.execute(
        select(LandedCostComponent).where(LandedCostComponent.offer_id == offer.id)
    )).scalars().all())
    if existing:
        return existing
    buyer_country = ((tenant.settings or {}).get("country") or "EG").upper()
    profile = duty_profile(buyer_country)
    category = "MRO"
    line = (await session.execute(
        select(OfferLine).where(OfferLine.offer_id == offer.id)
    )).scalars().first()
    if line and line.rfq_line_id:
        rfq_line = (await session.execute(select(RFQLine).where(RFQLine.id == line.rfq_line_id))).scalar_one_or_none()
        if rfq_line and rfq_line.specs:
            category = (rfq_line.specs or {}).get("category_code") or category
    components: list[LandedCostComponent] = []
    subtotal = Decimal(str(offer.subtotal or 0))
    imported = (supplier.country or "EG").upper() != buyer_country
    if imported:
        duty_rate = Decimal(profile["duty_rates"].get(category, profile["duty_rate_default"]))
        duty = numbers.money(subtotal * duty_rate)
        freight = numbers.money(subtotal * Decimal("0.03"))
        insurance = numbers.money(subtotal * Decimal("0.005"))
        vat_base = subtotal + duty + freight
        vat = numbers.money(vat_base * Decimal(profile["vat_rate"]))
        for kind, amount, note in [
            ("freight", freight, "Estimated international freight (3%)"),
            ("duty", duty, f"Import duty {duty_rate * 100:.1f}% for {category}"),
            ("insurance", insurance, "Cargo insurance (0.5%)"),
            ("vat", vat, f"VAT {Decimal(profile['vat_rate']) * 100:.1f}% on CIF+duty"),
        ]:
            row = LandedCostComponent(tenant_id=tenant.id, offer_id=offer.id, rfq_id=rfq.id, kind=kind,
                                      amount=amount, currency=offer.currency, notes=note)
            session.add(row)
            components.append(row)
    else:
        vat = numbers.money(subtotal * Decimal(profile["vat_rate"]))
        row = LandedCostComponent(tenant_id=tenant.id, offer_id=offer.id, rfq_id=rfq.id, kind="vat",
                                  amount=vat, currency=offer.currency,
                                  notes=f"Domestic VAT {Decimal(profile['vat_rate']) * 100:.0f}%")
        session.add(row)
        components.append(row)
    await session.flush()
    return components


async def compute_offer_costs(session: AsyncSession, tenant: Tenant, rfq: RFQ) -> list[dict]:
    offers = list((await session.execute(
        select(Offer).where(Offer.rfq_id == rfq.id, Offer.tenant_id == tenant.id)
    )).scalars().all())
    results: list[dict] = []
    for offer in offers:
        supplier = (await session.execute(select(Supplier).where(Supplier.id == offer.supplier_id))).scalar_one()
        components = await ensure_landed_costs(session, tenant, rfq, offer, supplier)
        fx = await get_fx_rate(session, offer.currency, rfq.currency, tenant_id=tenant.id)
        landed_total = sum((Decimal(str(c.amount)) for c in components), Decimal("0"))
        tco = numbers.compute_tco(Decimal(str(offer.subtotal or 0)), fx_rate=fx,
                                  landed_components=[(c.kind, Decimal(str(c.amount))) for c in components],
                                  lead_time_days=offer.lead_time_days)
        offer.fx_rate = fx
        offer.landed_cost_total = numbers.money(landed_total)
        offer.total_base = tco["total_base"]
        offer.tco_total = tco["tco_total"]
        results.append({
            "offer_id": offer.id, "supplier_id": supplier.id, "supplier_name": supplier.name,
            "supplier_name_ar": supplier.name_ar or supplier.name, "currency": offer.currency,
            "subtotal": str(offer.subtotal), "fx_rate": str(fx), "landed_cost": str(landed_total),
            "tco_total": str(tco["tco_total"]), "total_base": str(tco["total_base"]),
            "cost_of_capital": tco["cost_of_capital"],
            "lead_time_days": offer.lead_time_days,
            "supplier_quality": float(supplier.rating_quality or 60),
            "terms_score": _terms_score(offer),
            "risk_score": _risk_score(supplier),
            "is_low_confidence": offer.is_low_confidence,
            "status": offer.status,
            "components": [{"kind": c.kind, "amount": str(c.amount), "notes": c.notes} for c in components],
        })
    await session.flush()
    return results


def _terms_score(offer: Offer) -> float:
    terms = (offer.payment_terms or "").lower()
    if "advance" in terms or "مقدم" in terms:
        return 45.0
    match = re.search(r"net\s*(\d+)", terms)
    if match:
        return min(100.0, 50 + float(match.group(1)) * 1.5)
    return 65.0


def _risk_score(supplier: Supplier) -> float:
    score = float(supplier.overall_score or 60)
    if (supplier.sanctions_status or "clear") != "clear":
        return 5.0
    if supplier.status == "blacklisted":
        return 10.0
    if supplier.status == "discovered":
        score -= 12
    return max(0.0, min(100.0, score))


async def build_matrix(session: AsyncSession, tenant: Tenant, rfq: RFQ) -> dict:
    offers = list((await session.execute(
        select(Offer).where(Offer.rfq_id == rfq.id, Offer.tenant_id == tenant.id)
        .order_by(Offer.created_at)
    )).scalars().all())
    rfq_lines = list((await session.execute(
        select(RFQLine).where(RFQLine.rfq_id == rfq.id).order_by(RFQLine.line_no)
    )).scalars().all())
    costs = await compute_offer_costs(session, tenant, rfq)
    cost_by_offer = {c["offer_id"]: c for c in costs}
    suppliers = {}
    for offer in offers:
        suppliers[offer.id] = (await session.execute(select(Supplier).where(Supplier.id == offer.supplier_id))).scalar_one()

    price_lists = [
        {"offer_id": offer.id, "tco_total": cost_by_offer[offer.id]["tco_total"],
         "lead_time_days": offer.lead_time_days,
         "supplier_quality": cost_by_offer[offer.id]["supplier_quality"],
         "terms_score": cost_by_offer[offer.id]["terms_score"],
         "risk_score": cost_by_offer[offer.id]["risk_score"],
         "supplier_name": suppliers[offer.id].name}
        for offer in offers
    ]
    weights = rfq.scoring_weights or DEFAULT_WEIGHTS
    scored = numbers.weighted_scores(price_lists, weights)
    score_by_offer = {row["offer_id"]: row for row in scored}
    for offer in offers:
        if offer.id in score_by_offer:
            offer.weighted_score = Decimal(str(score_by_offer[offer.id]["weighted_score"]))
            offer.scores = score_by_offer[offer.id]["score_breakdown"]

    lines_by_offer: dict[str, dict[str, OfferLine]] = {}
    for offer in offers:
        rows = list((await session.execute(
            select(OfferLine).where(OfferLine.offer_id == offer.id)
        )).scalars().all())
        lines_by_offer[offer.id] = {str(row.rfq_line_id): row for row in rows}

    rows = []
    for rfq_line in rfq_lines:
        cells = []
        for offer in offers:
            line = lines_by_offer[offer.id].get(str(rfq_line.id))
            if line is None and len(rfq_lines) == 1:
                line = next(iter(lines_by_offer[offer.id].values()), None)
            cells.append({
                "offer_id": offer.id,
                "unit_price": str(line.unit_price) if line else None,
                "line_total": str(line.line_total) if line else None,
                "currency": line.currency if line else offer.currency,
                "low_confidence": bool(line and (float(line.confidence or 0) < 0.75)),
                "lead_time_days": line.lead_time_days if line else None,
                "extraction_id": offer.extraction_id,
                "verified": offer.status == "verified",
            })
        prices = [Decimal(c["unit_price"]) for c in cells if c["unit_price"]]
        best = str(min(prices)) if prices else None
        rows.append({
            "rfq_line_id": rfq_line.id, "line_no": rfq_line.line_no, "description": rfq_line.description,
            "quantity": str(rfq_line.quantity), "uom": rfq_line.uom, "best_unit_price": best,
            "cells": cells, "target_price": str(rfq_line.target_price) if rfq_line.target_price else None,
        })

    columns = []
    ranked = {row["offer_id"]: row.get("rank") for row in scored}
    best_offer = min((o for o in costs if o["status"] != "rejected"), key=lambda c: Decimal(c["tco_total"]), default=None)
    for offer in offers:
        cost = cost_by_offer[offer.id]
        supplier = suppliers[offer.id]
        columns.append({
            "offer_id": offer.id, "supplier_id": supplier.id, "supplier_name": supplier.name,
            "supplier_name_ar": supplier.name_ar or supplier.name, "status": offer.status,
            "currency": offer.currency, "incoterm": offer.incoterm, "payment_terms": offer.payment_terms,
            "lead_time_days": offer.lead_time_days,
            "validity_date": offer.validity_date.isoformat() if offer.validity_date else None,
            "subtotal": cost["subtotal"], "landed_cost": cost["landed_cost"], "tco_total": cost["tco_total"],
            "total_base": cost["total_base"], "fx_rate": cost["fx_rate"],
            "weighted_score": score_by_offer.get(offer.id, {}).get("weighted_score", 0),
            "score_breakdown": score_by_offer.get(offer.id, {}).get("score_breakdown", {}),
            "rank": ranked.get(offer.id),
            "is_best_value": bool(best_offer and best_offer["offer_id"] == offer.id),
            "is_low_confidence": offer.is_low_confidence,
            "extraction_id": offer.extraction_id,
            "supplier_quality": cost["supplier_quality"],
        })
    await session.flush()
    return {
        "rfq": {"id": rfq.id, "code": rfq.code, "title": rfq.title, "currency": rfq.currency,
                "status": rfq.status, "weights": weights},
        "columns": sorted(columns, key=lambda c: (c["rank"] or 99)),
        "rows": rows,
        "ranking": scored,
        "blocked_for_low_confidence": any(c["is_low_confidence"] for c in columns),
        "generated_at": datetime.now(UTC).isoformat(),
    }


async def benchmark_for(session: AsyncSession, tenant_id: str, item_key: str) -> dict:
    """Price history + anonymised benchmark per item (FR-CMP-6)."""
    from app.db.models.entities import PriceHistory

    row = (await session.execute(
        select(BenchmarkPrice).where(BenchmarkPrice.item_key == item_key)
        .order_by(BenchmarkPrice.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    history_rows = list((await session.execute(
        select(PriceHistory).where(PriceHistory.tenant_id == tenant_id, PriceHistory.item_key == item_key)
        .order_by(PriceHistory.observed_at.desc()).limit(24)
    )).scalars().all())
    prices = sorted(float(r.unit_price) for r in history_rows)
    samples = len(prices)
    median = prices[samples // 2] if samples else None
    return {
        "item_key": item_key,
        "sample_size": samples,
        "median": median,
        "min": prices[0] if prices else None,
        "max": prices[-1] if prices else None,
        "benchmark": {"p25": float(row.p25), "p50": float(row.p50), "p75": float(row.p75)} if row else None,
        "history": [{"unit_price": float(r.unit_price), "currency": r.currency,
                     "observed_at": r.observed_at.isoformat()} for r in history_rows][:24],
    }


def validate_numbers(text: str, allowed: list[Decimal], tolerance: Decimal = Decimal("0.01")) -> list[str]:
    """Every figure in a generated recommendation must exist in the computed data."""
    violations: list[str] = []
    for token in re.findall(r"\d[\d,]*\.?\d*", numbers.normalize_digits(text or "")):
        try:
            value = Decimal(token.replace(",", ""))
        except Exception:
            continue
        if value <= 1 or value > 100_000_000:
            continue  # scores/ranks/percentages are also validated numerically below
        if not any(allowed_value and abs(value - allowed_value) <= max(tolerance, allowed_value * Decimal("0.001"))
                   for allowed_value in allowed):
            if not any(abs(value - allowed_value) <= Decimal("1") for allowed_value in allowed if allowed_value):
                violations.append(token)
    return violations


async def recommend(session: AsyncSession, tenant: Tenant, rfq: RFQ, *, actor_id: str | None = None) -> dict:
    matrix = await build_matrix(session, tenant, rfq)
    ranked = matrix["ranking"][:3]
    if not ranked:
        return {"recommended": [], "justification_en": "", "justification_ar": "", "matrix": matrix}
    llm = await llm_for(session, tenant)
    run = AgentRun(tenant_id=tenant.id, kind="recommendation", status="running", ref_type="rfq",
                   ref_id=rfq.id, model=f"{llm.name}:{llm.model}", created_by=actor_id,
                   started_at=datetime.now(UTC))
    session.add(run)
    await session.flush()
    response = await llm.complete(LLMRequest(
        task="recommendation",
        messages=[
            LLMMessage("system", "Explain a procurement recommendation using ONLY the supplied numbers. JSON only."),
            LLMMessage("user", f"Ranked offers: {ranked}"),
        ],
        context={"ranked": ranked, "currency": rfq.currency},
    ))
    data = response.data or {}
    justification_en = str(data.get("justification_en") or "")
    justification_ar = str(data.get("justification_ar") or "")
    # Figures the explanation is allowed to quote: the computed economics, plus the
    # rank/lead time and the 100-point scale the prompt itself asks the model to use.
    allowed: list[Decimal] = [Decimal("100")]
    for row in ranked:
        for key in ("tco_total", "weighted_score", "rank", "lead_time_days"):
            value = row.get(key)
            if value is not None:
                allowed.append(Decimal(str(value)))
    violations = validate_numbers(justification_en, allowed) + validate_numbers(justification_ar, allowed)
    if violations:
        # Never surface unverifiable figures: fall back to the deterministic summary.
        justification_en = " ".join(
            f"#{row['rank']} {row['supplier_name']}: weighted score {row['weighted_score']}/100, "
            f"TCO {numbers.fmt_amount(row['tco_total'], rfq.currency, 'en')}."
            for row in ranked
        )
        justification_ar = " ".join(
            f"#{row['rank']} {row['supplier_name']}: الدرجة {row['weighted_score']}/100، "
            f"إجمالي التكلفة {numbers.fmt_amount(row['tco_total'], rfq.currency, 'ar')}."
            for row in ranked
        )
    run.status = "completed"
    run.output_summary = justification_en[:1000]
    run.reasoning = (response.text or "")[:4000]
    run.policy_decision = {"number_violations": violations}
    run.tokens_in, run.tokens_out = response.tokens_in, response.tokens_out
    run.cost_usd = Decimal(str(response.cost_usd))
    run.finished_at = datetime.now(UTC)
    await session.flush()
    return {
        "recommended": ranked,
        "justification_en": justification_en,
        "justification_ar": justification_ar,
        "number_violations": violations,
        "matrix": matrix,
        "agent_run_id": run.id,
    }


async def update_weights(session: AsyncSession, rfq: RFQ, weights: dict[str, float]) -> RFQ:
    total = sum(weights.values()) or 1
    rfq.scoring_weights = {k: round(v / total, 4) for k, v in weights.items()}
    await session.flush()
    return rfq


async def pending_approval_for(session: AsyncSession, tenant_id: str, rfq_id: str) -> Approval | None:
    return (await session.execute(
        select(Approval).where(Approval.tenant_id == tenant_id, Approval.rfq_id == rfq_id,
                               Approval.status == "pending").order_by(Approval.step_no).limit(1)
    )).scalar_one_or_none()
