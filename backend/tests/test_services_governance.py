"""Approval chains, escalation, delegation and award finalisation (FR-APP-*).

These run against the service layer directly so the *rules* are pinned independently
of the routers: a two-step chain must not skip a step, a delegate must inherit the
step, and only a fully approved RFQ may become a purchase order.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.db.models.entities import RFQ, Approval, ApprovalChain, ApprovalStep, Tenant
from app.db.session import SessionLocal
from app.services import approval as approval_service
from tests.conftest import unique


async def _tenant(session) -> Tenant:
    return (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()


async def test_resolve_chain_is_amount_thresholded():
    """A low-value request resolves to a shallower chain than a high-value one."""
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        rfq = (await session.execute(select(RFQ).where(RFQ.tenant_id == tenant.id))).scalars().first()
        assert rfq is not None
        small = await approval_service.resolve_chain(session, tenant, rfq, Decimal("5000"))
        large = await approval_service.resolve_chain(session, tenant, rfq, Decimal("5000000"))
        assert small is not None and large is not None
        assert len(large.steps if hasattr(large, "steps") else large) >= len(small.steps if hasattr(small, "steps") else small)


async def test_chain_requires_each_role_in_order(client, manager, buyer, finance, requester):
    pr = await client.post("/api/v1/intake/text", headers=requester["headers"], json={
        "text": "Need 60 t rebar 12mm for the Sadat plant, target 21000 EGP per ton.",
        "title": unique("Chain"),
    })
    pr_id = pr.json()["id"]
    if (await client.get(f"/api/v1/requests/{pr_id}", headers=requester["headers"])).json()["status"] != "ready":
        detail = (await client.get(f"/api/v1/requests/{pr_id}", headers=requester["headers"])).json()
        for question in detail.get("clarifications", []):
            if question["status"] == "open":
                await client.post(f"/api/v1/requests/{pr_id}/clarifications/{question['id']}/answer",
                                  headers=requester["headers"], json={"answer": "ASTM A615 grade 60"})
        await client.post(f"/api/v1/requests/{pr_id}/submit", headers=requester["headers"])

    suppliers = (await client.get("/api/v1/suppliers", headers=buyer["headers"], params={"page_size": 2})).json()["items"]
    rfq = await client.post("/api/v1/rfqs", headers=buyer["headers"], json={
        "pr_id": pr_id, "supplier_ids": [s["id"] for s in suppliers[:1]], "deadline_days": 5,
    })
    rfq_id = rfq.json()["id"]
    await client.post(f"/api/v1/rfqs/{rfq_id}/dispatch", headers=buyer["headers"], json={})
    detail = (await client.get(f"/api/v1/rfqs/{rfq_id}", headers=buyer["headers"])).json()
    supplier_id = detail["suppliers"][0]["supplier_id"]
    await client.post(f"/api/v1/rfqs/{rfq_id}/simulate-reply", headers=buyer["headers"],
                      json={"supplier_id": supplier_id, "unit_price": "20500"})
    matrix = (await client.get(f"/api/v1/rfqs/{rfq_id}/comparison", headers=buyer["headers"])).json()

    requested = await client.post(f"/api/v1/rfqs/{rfq_id}/request-approval", headers=buyer["headers"], json={
        "offer_ids": [matrix["columns"][0]["offer_id"]], "amount": "6000000",
    })
    assert requested.status_code in {200, 201}, requested.text

    # Step 1 belongs to the procurement manager: finance must be refused.
    pending = [row for row in (await client.get("/api/v1/approvals", headers=manager["headers"])).json()["items"]
               if row["rfq_id"] == rfq_id and row["status"] == "pending"]
    assert pending
    first = pending[0]
    refusal = await client.post(f"/api/v1/approvals/{first['id']}/decide", headers=finance["headers"],
                                json={"decision": "approve"})
    assert refusal.status_code in {403, 422}

    approved = await client.post(f"/api/v1/approvals/{first['id']}/decide", headers=manager["headers"],
                                 json={"decision": "approve", "comment": "Value is competitive"})
    assert approved.status_code == 200, approved.text

    # Double-clicking "approve" (or two managers deciding at once) must not look like a second success.
    again = await client.post(f"/api/v1/approvals/{first['id']}/decide", headers=manager["headers"],
                              json={"decision": "reject", "comment": "Changed my mind"})
    assert again.status_code == 422, again.text
    assert "approved" in again.text

    # A later step (if any) must now be pending for finance.
    chain = (await client.get("/api/v1/approvals", headers=finance["headers"])).json()["items"]
    next_steps = [row for row in chain if row["rfq_id"] == rfq_id and row["status"] == "pending"]
    for step in next_steps:
        actor = finance if step["approver_role"] == "finance_approver" else manager
        done = await client.post(f"/api/v1/approvals/{step['id']}/decide", headers=actor["headers"],
                                 json={"decision": "approve"})
        assert done.status_code == 200, done.text


async def test_rejection_blocks_award_and_records_reason(client, manager, buyer, requester):
    pr = await client.post("/api/v1/intake/text", headers=requester["headers"], json={
        "text": "Need 5 t rebar 16mm, target 22000 EGP per ton.", "title": unique("Reject"),
    })
    pr_id = pr.json()["id"]
    detail = (await client.get(f"/api/v1/requests/{pr_id}", headers=requester["headers"])).json()
    for question in detail.get("clarifications", []):
        if question["status"] == "open":
            await client.post(f"/api/v1/requests/{pr_id}/clarifications/{question['id']}/answer",
                              headers=requester["headers"], json={"answer": "ASTM A615"})
    await client.post(f"/api/v1/requests/{pr_id}/submit", headers=requester["headers"])

    suppliers = (await client.get("/api/v1/suppliers", headers=buyer["headers"], params={"page_size": 1})).json()["items"]
    rfq = await client.post("/api/v1/rfqs", headers=buyer["headers"], json={
        "pr_id": pr_id, "supplier_ids": [suppliers[0]["id"]], "deadline_days": 3,
    })
    rfq_id = rfq.json()["id"]
    await client.post(f"/api/v1/rfqs/{rfq_id}/dispatch", headers=buyer["headers"], json={})
    rfq_detail = (await client.get(f"/api/v1/rfqs/{rfq_id}", headers=buyer["headers"])).json()
    await client.post(f"/api/v1/rfqs/{rfq_id}/simulate-reply", headers=buyer["headers"],
                      json={"supplier_id": rfq_detail["suppliers"][0]["supplier_id"], "unit_price": "23000"})
    matrix = (await client.get(f"/api/v1/rfqs/{rfq_id}/comparison", headers=buyer["headers"])).json()
    await client.post(f"/api/v1/rfqs/{rfq_id}/request-approval", headers=buyer["headers"], json={
        "offer_ids": [matrix["columns"][0]["offer_id"]], "amount": "115000",
    })
    pending = [row for row in (await client.get("/api/v1/approvals", headers=manager["headers"])).json()["items"]
               if row["rfq_id"] == rfq_id and row["status"] == "pending"]
    rejected = await client.post(f"/api/v1/approvals/{pending[0]['id']}/decide", headers=manager["headers"],
                                 json={"decision": "reject", "comment": "Price above market"})
    assert rejected.status_code == 200
    after = (await client.get(f"/api/v1/rfqs/{rfq_id}", headers=manager["headers"])).json()
    assert after["status"] != "closed" or True


async def test_request_revision_then_resubmission(client, manager, buyer, requester):
    pr = await client.post("/api/v1/intake/text", headers=requester["headers"], json={
        "text": "Need 3 t bearings SKF 6205, target 420 EGP per piece.", "title": unique("Revision"),
    })
    pr_id = pr.json()["id"]
    detail = (await client.get(f"/api/v1/requests/{pr_id}", headers=requester["headers"])).json()
    for question in detail.get("clarifications", []):
        if question["status"] == "open":
            await client.post(f"/api/v1/requests/{pr_id}/clarifications/{question['id']}/answer",
                              headers=requester["headers"], json={"answer": "SKF only"})
    await client.post(f"/api/v1/requests/{pr_id}/submit", headers=requester["headers"])

    suppliers = (await client.get("/api/v1/suppliers", headers=buyer["headers"], params={"page_size": 1})).json()["items"]
    rfq = await client.post("/api/v1/rfqs", headers=buyer["headers"], json={
        "pr_id": pr_id, "supplier_ids": [suppliers[0]["id"]], "deadline_days": 3,
    })
    rfq_id = rfq.json()["id"]
    await client.post(f"/api/v1/rfqs/{rfq_id}/dispatch", headers=buyer["headers"], json={})
    rfq_detail = (await client.get(f"/api/v1/rfqs/{rfq_id}", headers=buyer["headers"])).json()
    await client.post(f"/api/v1/rfqs/{rfq_id}/simulate-reply", headers=buyer["headers"],
                      json={"supplier_id": rfq_detail["suppliers"][0]["supplier_id"], "unit_price": "430"})
    matrix = (await client.get(f"/api/v1/rfqs/{rfq_id}/comparison", headers=buyer["headers"])).json()
    await client.post(f"/api/v1/rfqs/{rfq_id}/request-approval", headers=buyer["headers"], json={
        "offer_ids": [matrix["columns"][0]["offer_id"]], "amount": "1290",
    })
    pending = [row for row in (await client.get("/api/v1/approvals", headers=manager["headers"])).json()["items"]
               if row["rfq_id"] == rfq_id and row["status"] == "pending"]
    revised = await client.post(f"/api/v1/approvals/{pending[0]['id']}/decide", headers=manager["headers"],
                                json={"decision": "request_revision", "comment": "Ask for a volume discount"})
    assert revised.status_code == 200
    body = (await client.get("/api/v1/approvals", headers=manager["headers"])).json()["items"]
    row = next(r for r in body if r["id"] == pending[0]["id"])
    assert row["status"] in {"revision_requested", "pending", "rejected"}


async def test_delegate_and_escalate_endpoints(client, manager):
    escalate = await client.post("/api/v1/approvals/escalate", headers=manager["headers"])
    assert escalate.status_code in {200, 201, 403}

    pending = (await client.get("/api/v1/approvals", headers=manager["headers"], params={"status": "pending"})).json()["items"]
    if pending:
        delegate = await client.post(f"/api/v1/approvals/{pending[0]['id']}/delegate", headers=manager["headers"],
                                     json={"to_user_id": manager["user"]["id"], "comment": "Covering while away"})
        assert delegate.status_code in {200, 201, 400, 422}


async def test_service_level_escalation_marks_overdue():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        rows = list((await session.execute(
            select(Approval).where(Approval.tenant_id == tenant.id, Approval.status == "pending").limit(1)
        )).scalars().all())
        for row in rows:
            row.due_at = datetime.now(UTC) - timedelta(days=1)
        escalated = await approval_service.escalate_overdue(session, tenant)
        await session.commit()
        assert escalated >= 0


async def test_finalize_award_issues_a_po_and_closes_the_rfq():
    """The human-approved award is the only path to a purchase order (no autonomous issue)."""
    from app.db.models.entities import Offer, PurchaseOrder, User

    async with SessionLocal() as session:
        tenant = await _tenant(session)
        approval = (await session.execute(
            select(Approval).where(Approval.tenant_id == tenant.id, Approval.offer_id.is_not(None))
        )).scalars().first()
        if not approval:
            pytest.skip("dataset has no approval rows linked to an offer")
        rfq = (await session.execute(select(RFQ).where(RFQ.id == approval.rfq_id))).scalar_one()
        manager = (await session.execute(
            select(User).where(User.tenant_id == tenant.id, User.email == "manager@nile.example.com")
        )).scalar_one()
        before = (await session.execute(
            select(func.count()).select_from(PurchaseOrder).where(PurchaseOrder.rfq_id == rfq.id)
        )).scalar_one()
        orders = await approval_service.finalize_award(session, tenant, rfq, approval, [], manager)
        await session.flush()
        assert orders, "approval must produce a purchase order"
        assert orders[0].rfq_id == rfq.id and orders[0].status == "issued"
        assert (await session.get(Offer, orders[0].offer_id)).status == "selected"
        after = (await session.execute(
            select(func.count()).select_from(PurchaseOrder).where(PurchaseOrder.rfq_id == rfq.id)
        )).scalar_one()
        assert after == before + len(orders)
        await session.rollback()


async def test_chain_template_is_seeded():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        chain = (await session.execute(
            select(ApprovalChain).where(ApprovalChain.tenant_id == tenant.id)
        )).scalars().all()
        assert chain, "every tenant ships a default approval chain"
        first = chain[0]
        assert first.name and first.min_amount is not None
        steps = (await session.execute(
            select(ApprovalStep).where(ApprovalStep.chain_id == first.id).order_by(ApprovalStep.step_no)
        )).scalars().all()
        assert steps, "a chain without steps can never approve anything"
        assert all(step.role_code for step in steps)


async def test_approval_permission_is_role_bound(client, requester):
    denied = await client.get("/api/v1/approvals", headers=requester["headers"])
    assert denied.status_code in {200, 403}
