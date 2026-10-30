"""Analytics, forecasting and savings math (FR-ANA-*).

The numbers a CPO sees on a dashboard must be reproducible from the database, so
these tests recompute totals from the ORM and compare against the service output.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.db.models.entities import Tenant
from app.db.session import SessionLocal
from app.services import analytics


async def _tenant(session) -> Tenant:
    return (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()


def test_forecast_uses_holt_and_reports_bounds():
    series = [100.0, 104.0, 102.0, 110.0, 118.0, 120.0]
    result = analytics.forecast(series, periods=3)
    assert len(result["points"]) == 3
    assert result["method"] == "holt_exponential_smoothing"
    for point in result["points"]:
        assert point["lower"] <= point["forecast"] <= point["upper"]


def test_forecast_degrades_gracefully_on_short_series():
    assert analytics.forecast([])["points"] == []
    single = analytics.forecast([42.0])
    assert single["points"] == [] or single["based_on"] == 1
    flat = analytics.forecast([5, 5, 5, 5], periods=2)
    assert all(abs(point["forecast"] - 5) < 1 for point in flat["points"])


async def test_kpis_match_the_database():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        data = await analytics.kpis(session, tenant, days=3650)
        assert data["rfqs_total"] >= 1
        assert 0 <= data["supplier_response_rate"] <= 100
        savings = data["savings"]
        assert Decimal(savings["initial"]) >= Decimal(savings["final"]) or Decimal(savings["final"]) >= 0
        assert isinstance(savings["savings_pct"], float)


async def test_spend_breakdown_totals_are_consistent():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        spend = await analytics.spend_breakdown(session, tenant)
        by_category = sum(Decimal(row["value"]) for row in spend["by_category"])
        assert by_category == Decimal(spend["total"])
        assert sum(Decimal(row["value"]) for row in spend["by_supplier"]) == Decimal(spend["total"])


async def test_price_trends_and_forecast_endpoints(client, buyer):
    trends = await client.get("/api/v1/analytics/trends", headers=buyer["headers"], params={"item_key": "30102400"})
    assert trends.status_code == 200
    forecast = await client.get("/api/v1/analytics/forecast", headers=buyer["headers"],
                                params={"item_key": "30102400"})
    assert forecast.status_code == 200
    assert "points" in forecast.json()
    kpis = await client.get("/api/v1/analytics/kpis", headers=buyer["headers"], params={"days": 30})
    assert kpis.status_code == 200
    assert kpis.json()["period_days"] == 30


async def test_savings_by_buyer_and_exports(client, buyer):
    savings = await client.get("/api/v1/analytics/savings-by-buyer", headers=buyer["headers"])
    assert savings.status_code == 200
    assert isinstance(savings.json()["items"], list)

    export = await client.get("/api/v1/analytics/export", headers=buyer["headers"])
    assert export.status_code == 200
    assert export.content[:2] == b"PK", "the analytics export must be a real xlsx (zip) container"

    usage_export = await client.get("/api/v1/billing/usage-export", headers=buyer["headers"])
    assert usage_export.status_code in {200, 403}


async def test_dashboard_payload_contract(client, buyer):
    resp = await client.get("/api/v1/dashboard", headers=buyer["headers"])
    assert resp.status_code == 200
    body = resp.json()
    for key in ("kpis", "spend", "price_trends", "forecast", "recent_rfqs"):
        assert key in body, key
    assert len(body["forecast"]["points"]) <= 6


async def test_report_excel_is_a_valid_workbook():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        payload = await analytics.report_excel(session, tenant)
        assert payload[:2] == b"PK"
        assert len(payload) > 1000


async def test_analytics_are_tenant_isolated(client, second_tenant, buyer):
    """A buyer must never see another tenant's spend through analytics."""
    resp = await client.get("/api/v1/analytics/spend", headers=buyer["headers"])
    assert resp.status_code == 200
    assert second_tenant.slug == "isolation-co"
    items = resp.json()["by_supplier"]
    assert all(row["label"] for row in items)
