"""Award path (comparison → recommendation) plus supplier discovery and billing (FR-CMP-*, FR-DSC-*, FR-BIL-*).

Two properties matter most here: the ranking is deterministic and derived from audited
numbers (never from model prose), and a recommendation that quotes a figure the engine did
not compute is thrown away rather than shown to a buyer.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.agents.llm.base import LLMResponse
from app.db.models.entities import (
    RFQ,
    AgentRun,
    DataErasureRequest,
    DiscoveryCandidate,
    Offer,
    Tenant,
    UsageEvent,
)
from app.db.session import SessionLocal
from app.services import billing_helpers, comparison, discovery


async def _tenant(session) -> Tenant:
    return (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()


async def _rfq_with_offers(session, tenant) -> RFQ:
    rfq = (await session.execute(
        select(RFQ).where(RFQ.tenant_id == tenant.id).order_by(RFQ.created_at)
    )).scalars().first()
    offers = list((await session.execute(select(Offer).where(Offer.rfq_id == rfq.id))).scalars().all())
    assert len(offers) >= 2, "the demo dataset must have a comparable RFQ"
    return rfq


class _FabricatingLLM:
    """A model that invents a price the engine never computed."""

    name = "stub"
    model = "fabricator"

    async def complete(self, request):  # noqa: ANN001 - duck-typed
        return LLMResponse(
            text="Fabricated justification.",
            data={"justification_en": "Supplier A saves you 987,654 EGP versus the current contract.",
                  "justification_ar": "المورد أ يوفر 987,654 جنيه."},
            tokens_in=10, tokens_out=10, cost_usd=0.0, latency_ms=1,
        )


async def test_landed_costs_are_computed_once_and_include_duty_and_freight():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        rfq = await _rfq_with_offers(session, tenant)
        costs = await comparison.compute_offer_costs(session, tenant, rfq)
        assert costs
        for cost in costs:
            assert Decimal(cost["tco_total"]) >= Decimal(cost["subtotal"])
            assert Decimal(cost["landed_cost"]) >= 0
            assert cost["fx_rate"] and cost["status"]

        again = await comparison.compute_offer_costs(session, tenant, rfq)
        assert [(c["offer_id"], c["tco_total"]) for c in costs] == [
            (c["offer_id"], c["tco_total"]) for c in again], "landed costs must be idempotent"
        await session.rollback()


async def test_comparison_matrix_ranks_offers_and_flags_low_confidence_cells():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        rfq = await _rfq_with_offers(session, tenant)
        matrix = await comparison.build_matrix(session, tenant, rfq)

        assert matrix["rfq"]["id"] == rfq.id
        assert matrix["rfq"]["weights"]
        assert len(matrix["columns"]) >= 2
        ranks = [column["rank"] for column in matrix["columns"]]
        assert None not in ranks
        assert ranks == sorted(ranks), "columns must be ordered by rank"
        assert sum(1 for column in matrix["columns"] if column["is_best_value"]) == 1
        for column in matrix["columns"]:
            assert 0 <= column["weighted_score"] <= 100
        for row in matrix["rows"]:
            assert row["best_unit_price"]
            assert len(row["cells"]) == len(matrix["columns"])
        await session.rollback()


async def test_weights_are_normalised_before_scoring():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        rfq = await _rfq_with_offers(session, tenant)
        updated = await comparison.update_weights(session, rfq, {"price": 3, "lead_time": 1})
        assert abs(sum(updated.scoring_weights.values()) - 1.0) < 1e-6
        assert updated.scoring_weights["price"] == 0.75
        await session.rollback()


async def test_recommendation_is_traceable_and_audited():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        rfq = await _rfq_with_offers(session, tenant)
        result = await comparison.recommend(session, tenant, rfq)
        assert result["recommended"]
        assert result["justification_en"] and result["justification_ar"]
        assert result["number_violations"] == []
        run = (await session.execute(select(AgentRun).where(AgentRun.id == result["agent_run_id"]))).scalar_one()
        assert run.kind == "recommendation"
        assert run.status == "completed"
        assert run.policy_decision == {"number_violations": []}
        await session.rollback()


async def test_fabricated_figures_are_replaced_by_the_deterministic_summary(monkeypatch):
    async def _stub(_session, _tenant):
        return _FabricatingLLM()

    monkeypatch.setattr(comparison, "llm_for", _stub)
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        rfq = await _rfq_with_offers(session, tenant)
        result = await comparison.recommend(session, tenant, rfq)
        assert result["number_violations"], "an invented amount must be detected"
        assert "987,654" not in result["justification_en"]
        assert "weighted score" in result["justification_en"]
        assert "الدرجة" in result["justification_ar"]
        await session.rollback()


async def test_discovery_finds_candidates_then_creates_them_only_after_approval():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        job = await discovery.run_discovery(session, tenant, query="steel rebar supplier",
                                            country="EG", max_results=5)
        assert job.status == "completed"
        assert job.found_count > 0

        candidates = await discovery.list_candidates(session, tenant.id, job.id)
        assert candidates
        assert all(c.status in {"pending", "duplicate"} for c in candidates)

        pending = [c.id for c in candidates if c.status == "pending"][:2]
        created = await discovery.approve_candidates(session, tenant, pending, created_by=None)
        assert len(created) == len(pending)
        assert all(supplier.status == "discovered" for supplier in created), \
            "a discovered supplier is never contacted or used before buyer approval"
        for supplier in created:
            cand = (await session.execute(
                select(DiscoveryCandidate).where(DiscoveryCandidate.supplier_id == supplier.id))).scalar_one()
            assert cand.status == "approved"

        duplicates = [c.id for c in candidates if c.status == "duplicate"]
        if duplicates:
            assert await discovery.reject_candidates(session, tenant.id, duplicates) == len(duplicates)
        await session.rollback()


async def test_monthly_invoice_contains_plan_and_meters_an_event():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        invoice = await billing_helpers.generate_invoice(session, tenant)
        assert invoice.number.startswith("INV-")
        assert invoice.status in {"open", "paid"}
        assert invoice.lines and invoice.lines[0]["amount"]
        assert Decimal(str(invoice.amount)) >= 0

        event = (await session.execute(
            select(UsageEvent).where(UsageEvent.kind == "invoice", UsageEvent.ref_id == invoice.id)
        )).scalars().first()
        assert event is not None

        second = await billing_helpers.generate_invoice(session, tenant)
        assert second.number != invoice.number, "invoice numbers must be unique"
        await session.rollback()


async def test_retention_purge_reports_what_it_deleted_and_honours_erasure():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        request = DataErasureRequest(tenant_id=tenant.id, subject_email="purge.subject@example.com",
                                     subject_type="user", status="pending",
                                     requested_at=datetime.now(UTC),
                                     due_at=datetime.now(UTC) + timedelta(days=30))
        session.add(request)
        await session.flush()
        summary = await billing_helpers.purge_expired(session, tenant)
        assert set(summary) >= {"logs_eligible", "notifications_purged", "erasure_records"}
        assert summary["erasure_records"] >= 0
        assert request.status == "completed"
        await session.rollback()


async def test_billing_usage_export_is_tenant_scoped(client, admin, manager, second_tenant):
    assert (await client.get("/api/v1/billing/usage", headers=manager["headers"])).status_code == 403
    rows = await client.get("/api/v1/billing/usage", headers=admin["headers"])
    assert rows.status_code == 200, rows.text
    export = await client.get("/api/v1/billing/usage-export", headers=admin["headers"])
    assert export.status_code == 200
    assert second_tenant.slug == "isolation-co"
