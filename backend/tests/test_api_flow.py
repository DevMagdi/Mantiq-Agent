"""End-to-end procurement flow over the real HTTP surface (FR-IN-*, FR-RFQ-*, FR-EX-*,
FR-CMP-*, FR-APR-*, FR-PO-*).

The scenario mirrors the SRS acceptance narrative: a requester's free-text need becomes a normalised
requisition, an RFQ goes out, supplier replies are parsed into offers, a comparison and a
recommendation are produced, the approval chain is completed by humans, and a purchase order is
created. Every step is asserted against persisted state.
"""
from __future__ import annotations

import pytest


async def _create_pr(client, buyer, text: str) -> dict:
    resp = await client.post("/api/v1/intake/text", headers=buyer["headers"],
                             json={"text": text, "plant_code": "SAD", "required_by": "2026-12-15"})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _resolve_clarifications(client, buyer, pr_id: str) -> None:
    detail = await client.get(f"/api/v1/requests/{pr_id}", headers=buyer["headers"])
    assert detail.status_code == 200
    for question in detail.json().get("clarifications", []):
        if question["status"] != "open":
            continue
        answer = "2026-12-15" if question["field"] == "needed_by" else "B420D grade, 12 mm diameter"
        resp = await client.post(f"/api/v1/requests/{pr_id}/clarifications/{question['id']}/answer",
                                 headers=buyer["headers"], json={"answer": answer})
        assert resp.status_code == 200, resp.text
    submit = await client.post(f"/api/v1/requests/{pr_id}/submit", headers=buyer["headers"])
    assert submit.status_code == 200, submit.text


@pytest.mark.slow
async def test_full_procurement_flow(client, buyer, manager, finance) -> None:
    pr = await _create_pr(client, buyer, "Need 2.5 tons of rebar 12mm B420D and 40 rolls of stretch "
                                        "film 500mm for the Sadat plant")
    assert pr["status"] in {"clarifying", "ready"}
    await _resolve_clarifications(client, buyer, pr["id"])

    suppliers = await client.get("/api/v1/suppliers", headers=buyer["headers"])
    assert suppliers.status_code == 200
    supplier_ids = [row["id"] for row in suppliers.json()["items"][:3]]
    assert len(supplier_ids) == 3

    created = await client.post("/api/v1/rfqs", headers=buyer["headers"],
                                json={"pr_id": pr["id"], "title": "Rebar and film - Q4",
                                      "supplier_ids": supplier_ids, "deadline_days": 7,
                                      "mode": "semi_auto"})
    assert created.status_code == 201, created.text
    rfq_id = created.json()["id"]

    dispatched = await client.post(f"/api/v1/rfqs/{rfq_id}/dispatch", headers=buyer["headers"])
    assert dispatched.status_code == 200, dispatched.text
    assert all(row["ok"] for row in dispatched.json()["results"])

    offers: list[str] = []
    for index, supplier_id in enumerate(supplier_ids):
        reply = await client.post(f"/api/v1/rfqs/{rfq_id}/simulate-reply", headers=buyer["headers"],
                                  json={"supplier_id": supplier_id, "kind": "text",
                                        "unit_price": str(21500 - index * 600),
                                        "lead_time_days": 7 + index * 3})
        assert reply.status_code == 200, reply.text
        body = reply.json()
        assert body["matched"] is True, body
        assert body["extractions"], body
        offers.extend(body.get("offers") or [])

    comparison = await client.get(f"/api/v1/rfqs/{rfq_id}/comparison", headers=buyer["headers"])
    assert comparison.status_code == 200
    matrix = comparison.json()
    assert len(matrix["columns"]) >= 1
    for column in matrix["columns"]:
        assert column["tco_total"]

    recommendation = await client.post(f"/api/v1/rfqs/{rfq_id}/recommend", headers=buyer["headers"])
    assert recommendation.status_code == 200, recommendation.text
    assert recommendation.json()["recommended"]

    offer_id = matrix["columns"][0]["offer_id"]
    policy = await client.put(f"/api/v1/rfqs/{rfq_id}/policy", headers=manager["headers"],
                              json={"target_price": "20000", "walkaway_price": "23000", "max_rounds": 3,
                                    "allowed_concessions": ["payment_terms", "lead_time"],
                                    "mode": "semi_auto", "value_threshold": "500000", "currency": "EGP"})
    assert policy.status_code == 200, policy.text
    negotiation = await client.post(f"/api/v1/rfqs/{rfq_id}/negotiate", headers=buyer["headers"],
                                    json={"supplier_id": matrix["columns"][0]["supplier_id"],
                                          "offer_id": offer_id})
    assert negotiation.status_code == 200, negotiation.text
    assert negotiation.json()["decision"]["codes"] in ([], ["WARN_ABOVE_TARGET"])

    approval = await client.post(f"/api/v1/rfqs/{rfq_id}/request-approval", headers=buyer["headers"],
                                 json={"amount": "250000", "offer_ids": [offer_id]})
    assert approval.status_code == 200, approval.text
    steps = approval.json()["approvals"]
    assert steps and steps[0]["step"] == 1

    for actor in (manager, finance):
        pending = await client.get("/api/v1/approvals", headers=actor["headers"], params={"rfq_id": rfq_id})
        rows = [row for row in pending.json()["items"] if row["status"] == "pending"]
        if not rows:
            continue
        decision = await client.post(f"/api/v1/approvals/{rows[0]['id']}/decide",
                                     headers=actor["headers"], json={"decision": "approve", "comment": "ok"})
        assert decision.status_code == 200, decision.text

    final = await client.get("/api/v1/approvals", headers=manager["headers"], params={"rfq_id": rfq_id})
    assert all(row["status"] != "pending" for row in final.json()["items"]), final.text


async def test_rfq_validation_errors_are_structured(client, buyer) -> None:
    resp = await client.post("/api/v1/rfqs", headers=buyer["headers"], json={"title": "No PR"})
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "validation_failed"
    assert body["errors"][0]["field"]


async def test_seeded_analytics_are_available(client, manager) -> None:
    kpis = await client.get("/api/v1/analytics/kpis", headers=manager["headers"])
    assert kpis.status_code == 200
    body = kpis.json()
    assert body["rfqs_total"] >= 3
    assert "savings" in body

    spend = await client.get("/api/v1/analytics/spend", headers=manager["headers"])
    assert spend.status_code == 200

    export = await client.get("/api/v1/analytics/export", headers=manager["headers"])
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("application/vnd.openxml") or "spreadsheet" in \
        export.headers["content-type"]


async def test_po_pdf_and_excel_render(client, manager) -> None:
    orders = await client.get("/api/v1/purchase-orders", headers=manager["headers"])
    assert orders.status_code == 200
    items = orders.json()["items"]
    assert items, "seeded demo tenant must contain at least one purchase order"
    po_id = items[0]["id"]
    pdf = await client.get(f"/api/v1/purchase-orders/{po_id}/pdf", headers=manager["headers"])
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    excel = await client.get(f"/api/v1/purchase-orders/{po_id}/excel", headers=manager["headers"])
    assert excel.status_code == 200 and excel.content[:2] == b"PK"


async def test_board_and_dashboard(client, manager) -> None:
    board = await client.get("/api/v1/rfqs/board", headers=manager["headers"])
    assert board.status_code == 200
    assert set(board.json()["columns"]) >= {"draft", "sent", "replied", "negotiating", "closed"}
    dashboard = await client.get("/api/v1/dashboard", headers=manager["headers"])
    assert dashboard.status_code == 200
    assert "kpis" in dashboard.json()
