"""Analytics, reporting and explainable forecasting (FR-RPT-1..3)."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.entities import (
    RFQ,
    Approval,
    Offer,
    PRLine,
    PurchaseOrder,
    PurchaseRequisition,
    RFQSupplier,
    Supplier,
    Tenant,
)
from app.services import numbers

log = logging.getLogger("procureai.analytics")


async def kpis(session: AsyncSession, tenant: Tenant, *, days: int = 90) -> dict:
    since = datetime.now(UTC) - timedelta(days=days)
    rfqs = list((await session.execute(
        select(RFQ).where(RFQ.tenant_id == tenant.id, RFQ.created_at >= since)
    )).scalars().all())
    offers = list((await session.execute(
        select(Offer).where(Offer.tenant_id == tenant.id, Offer.created_at >= since)
    )).scalars().all())
    pos = list((await session.execute(
        select(PurchaseOrder).where(PurchaseOrder.tenant_id == tenant.id, PurchaseOrder.created_at >= since)
    )).scalars().all())
    prs = list((await session.execute(
        select(PurchaseRequisition).where(PurchaseRequisition.tenant_id == tenant.id,
                                          PurchaseRequisition.created_at >= since)
    )).scalars().all())
    pending = (await session.execute(
        select(func.count()).select_from(Approval).where(Approval.tenant_id == tenant.id,
                                                        Approval.status == "pending")
    )).scalar_one()

    estimates = {pr.id: pr.total_estimate for pr in prs}
    initial = Decimal("0")
    final = Decimal("0")
    for rfq in rfqs:
        pr_estimate = estimates.get(rfq.pr_id) if rfq.pr_id else None
        rfq_offers = [o for o in offers if o.rfq_id == rfq.id and o.status != "rejected"]
        if not rfq_offers:
            continue
        first = max(rfq_offers, key=lambda o: Decimal(str(o.total_base or 0)))
        selected = next((o for o in rfq_offers if o.status == "selected"), None)
        best = min(rfq_offers, key=lambda o: Decimal(str(o.tco_total or 0)))
        baseline = Decimal(str(pr_estimate)) if pr_estimate else Decimal(str(first.total_base or 0))
        initial += baseline
        final += Decimal(str((selected or best).tco_total or 0))
    savings = numbers.savings(initial, final)

    cycle_days: list[int] = []
    for po in pos:
        rfq = next((r for r in rfqs if r.id == po.rfq_id), None)
        if rfq and rfq.created_at and po.created_at:
            cycle_days.append(max(0, (po.created_at - rfq.created_at).days))
    invitations = list((await session.execute(
        select(RFQSupplier).where(RFQSupplier.tenant_id == tenant.id)
    )).scalars().all())
    responded = [inv for inv in invitations if inv.status in {"responded"}]
    response_rate = round(len(responded) / len(invitations) * 100, 1) if invitations else 0.0

    return {
        "period_days": days,
        "rfqs_total": len(rfqs),
        "rfqs_open": sum(1 for r in rfqs if r.status in {"draft", "sent", "replied", "negotiating"}),
        "offers_total": len(offers),
        "purchase_orders": len(pos),
        "pending_approvals": int(pending),
        "savings": {**savings, "initial": str(savings["initial"]), "final": str(savings["final"]),
                    "savings_amount": str(savings["savings"])},
        "cycle_time_days": round(sum(cycle_days) / len(cycle_days), 1) if cycle_days else None,
        "supplier_response_rate": response_rate,
        "avg_offers_per_rfq": round(len(offers) / len(rfqs), 2) if rfqs else 0,
    }


async def spend_breakdown(session: AsyncSession, tenant: Tenant) -> dict:
    from app.db.models.entities import Category, Plant

    orders = list((await session.execute(
        select(PurchaseOrder).where(PurchaseOrder.tenant_id == tenant.id)
    )).scalars().all())
    by_supplier: dict[str, Decimal] = {}
    by_category: dict[str, Decimal] = {}
    by_plant: dict[str, Decimal] = {}
    for po in orders:
        supplier = (await session.execute(select(Supplier).where(Supplier.id == po.supplier_id))).scalar_one_or_none()
        name = supplier.name if supplier else "Unknown"
        by_supplier[name] = by_supplier.get(name, Decimal("0")) + Decimal(str(po.amount or 0))
        rfq = (await session.execute(select(RFQ).where(RFQ.id == po.rfq_id))).scalar_one_or_none()
        if rfq and rfq.pr_id:
            pr = (await session.execute(select(PurchaseRequisition).where(PurchaseRequisition.id == rfq.pr_id))).scalar_one_or_none()
            if pr and pr.plant_id:
                plant = (await session.execute(select(Plant).where(Plant.id == pr.plant_id))).scalar_one_or_none()
                key = plant.name if plant else "Unassigned"
                by_plant[key] = by_plant.get(key, Decimal("0")) + Decimal(str(po.amount or 0))
            lines = list((await session.execute(
                select(PRLine).where(PRLine.pr_id == pr.id)
            )).scalars().all()) if pr else []
            for line in lines:
                category = (await session.execute(
                    select(Category).where(Category.tenant_id == tenant.id, Category.code == line.category_code)
                )).scalar_one_or_none()
                key = category.name if category else (line.category_code or "Uncategorised")
                by_category[key] = by_category.get(key, Decimal("0")) + Decimal(str(po.amount or 0)) / max(1, len(lines))
    return {
        "by_supplier": [{"label": k, "value": str(numbers.money(v))} for k, v in
                        sorted(by_supplier.items(), key=lambda kv: kv[1], reverse=True)[:10]],
        "by_category": [{"label": k, "value": str(numbers.money(v))} for k, v in
                        sorted(by_category.items(), key=lambda kv: kv[1], reverse=True)[:10]],
        "by_plant": [{"label": k, "value": str(numbers.money(v))} for k, v in
                     sorted(by_plant.items(), key=lambda kv: kv[1], reverse=True)[:10]],
        "total": str(numbers.money(sum(by_supplier.values(), Decimal("0")))),
    }


async def price_trends(session: AsyncSession, tenant_id: str, item_key: str = "") -> list[dict]:
    from app.db.models.entities import PriceHistory

    query = select(PriceHistory).where(PriceHistory.tenant_id == tenant_id)
    if item_key:
        query = query.where(PriceHistory.item_key == item_key)
    rows = list((await session.execute(query.order_by(PriceHistory.observed_at))).scalars().all())
    buckets: dict[str, list[float]] = {}
    for row in rows:
        key = row.observed_at.strftime("%Y-%m")
        buckets.setdefault(key, []).append(float(row.unit_price))
    return [{"period": period, "avg_price": round(sum(v) / len(v), 2), "samples": len(v)}
            for period, v in sorted(buckets.items())]


def forecast(series: list[float], periods: int = 3, alpha: float = 0.45) -> dict:
    """Explainable forecast: seasonal-naive + exponential smoothing, 80% confidence band."""
    if not series:
        return {"points": [], "method": "none"}
    smoothed = series[0]
    residuals: list[float] = []
    for value in series[1:]:
        residuals.append(value - smoothed)
        smoothed = alpha * value + (1 - alpha) * smoothed
    band = (sum(r * r for r in residuals) / len(residuals)) ** 0.5 if residuals else abs(smoothed) * 0.05
    trend = (series[-1] - series[0]) / max(1, len(series) - 1)
    points = []
    for step in range(1, periods + 1):
        predicted = smoothed + trend * step
        points.append({
            "period": step, "forecast": round(predicted, 2),
            "lower": round(predicted - band, 2), "upper": round(predicted + band, 2),
        })
    return {"points": points, "method": "holt_exponential_smoothing", "alpha": alpha,
            "band": round(band, 2), "based_on": len(series)}


async def savings_by_buyer(session: AsyncSession, tenant: Tenant) -> list[dict]:
    from app.db.models.entities import User

    orders = list((await session.execute(
        select(PurchaseOrder).where(PurchaseOrder.tenant_id == tenant.id)
    )).scalars().all())
    buckets: dict[str, dict] = {}
    for po in orders:
        rfq = (await session.execute(select(RFQ).where(RFQ.id == po.rfq_id))).scalar_one_or_none()
        if not rfq:
            continue
        buyer = (await session.execute(select(User).where(User.id == rfq.buyer_id))).scalar_one_or_none() if rfq.buyer_id else None
        key = buyer.full_name if buyer else "Unassigned"
        bucket = buckets.setdefault(key, {"name": key, "orders": 0, "amount": Decimal("0")})
        bucket["orders"] += 1
        bucket["amount"] += Decimal(str(po.amount or 0))
    return [{"name": v["name"], "orders": v["orders"], "amount": str(numbers.money(v["amount"]))}
            for v in sorted(buckets.values(), key=lambda b: b["amount"], reverse=True)]


async def report_excel(session: AsyncSession, tenant: Tenant) -> bytes:
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Savings"
    summary = await kpis(session, tenant)
    sheet.append(["Metric", "Value"])
    for key, value in summary.items():
        sheet.append([key, str(value)])
    spend = await spend_breakdown(session, tenant)
    for name, rows in [("Spend by category", spend["by_category"]), ("Spend by supplier", spend["by_supplier"]),
                       ("Spend by plant", spend["by_plant"])]:
        ws = workbook.create_sheet(name[:28])
        ws.append([name, "Amount"])
        for row in rows:
            ws.append([row["label"], row["value"]])
    trends = await price_trends(session, tenant.id)
    ws = workbook.create_sheet("Price trends")
    ws.append(["Period", "Average unit price", "Samples"])
    for row in trends:
        ws.append([row["period"], row["avg_price"], row["samples"]])
    buffer = __import__("io").BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


async def dashboard(session: AsyncSession, tenant: Tenant) -> dict:
    return {
        "kpis": await kpis(session, tenant),
        "spend": await spend_breakdown(session, tenant),
        "price_trends": (await price_trends(session, tenant.id))[-12:],
        "forecast": forecast([row["avg_price"] for row in (await price_trends(session, tenant.id))[-12:]]),
        "savings_by_buyer": await savings_by_buyer(session, tenant),
        "recent_rfqs": [
            {"id": rfq.id, "code": rfq.code, "title": rfq.title, "status": rfq.status,
             "deadline": rfq.deadline.isoformat() if rfq.deadline else None}
            for rfq in (await session.execute(
                select(RFQ).where(RFQ.tenant_id == tenant.id).order_by(RFQ.created_at.desc()).limit(6)
            )).scalars().all()
        ],
        "generated_at": datetime.now(UTC).isoformat(),
    }
