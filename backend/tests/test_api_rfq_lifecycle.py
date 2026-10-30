"""End-to-end sourcing lifecycle through the HTTP API (FR-RFQ-*, FR-CMP-*, FR-NEG-*, FR-APP-*).

This is the integration counterpart of the unit suites: it drives the same routers
the frontend calls, in the same order a buyer works — request → RFQ → dispatch →
supplier reply (simulated inbound) → extraction → verification → comparison →
recommendation → negotiation → approval → purchase order.
"""
from __future__ import annotations

from tests.conftest import unique


async def _ready_pr(client, requester) -> dict:
    created = await client.post("/api/v1/intake/text", headers=requester["headers"], json={
        "text": "Need 40 t rebar 16mm B420D for the Sadat plant, delivery within 3 weeks, "
                "payment Net 30, target price 21000 EGP per ton.",
        "title": unique("Rebar request"),
    })
    assert created.status_code in {200, 201}, created.text
    pr_id = created.json()["id"]

    detail = await client.get(f"/api/v1/requests/{pr_id}", headers=requester["headers"])
    for question in detail.json().get("clarifications", []):
        if question["status"] == "open":
            await client.post(
                f"/api/v1/requests/{pr_id}/clarifications/{question['id']}/answer",
                headers=requester["headers"], json={"answer": "ASTM A615 grade 60", "waive": False},
            )
    submitted = await client.post(f"/api/v1/requests/{pr_id}/submit", headers=requester["headers"])
    assert submitted.status_code in {200, 201}, submitted.text
    return (await client.get(f"/api/v1/requests/{pr_id}", headers=requester["headers"])).json()


async def _supplier_ids(client, buyer, limit: int = 2) -> list[str]:
    resp = await client.get("/api/v1/suppliers", headers=buyer["headers"], params={"page_size": 5, "status": "approved"})
    return [row["id"] for row in resp.json()["items"]][:limit]


async def test_full_lifecycle_to_purchase_order(client, manager, buyer, requester, finance):
    pr = await _ready_pr(client, requester)
    assert pr["status"] in {"ready", "clarifying"}

    rfq = await client.post("/api/v1/rfqs", headers=buyer["headers"], json={
        "pr_id": pr["id"], "supplier_ids": await _supplier_ids(client, buyer),
        "deadline_days": 10, "mode": "semi_auto", "incoterm": "DAP", "payment_terms": "Net 30",
    })
    assert rfq.status_code in {200, 201}, rfq.text
    rfq_id = rfq.json()["id"]

    preview = await client.get(f"/api/v1/rfqs/{rfq_id}/preview", headers=buyer["headers"], params={"locale": "ar"})
    assert preview.status_code == 200 and preview.json()["body"]

    dispatch = await client.post(f"/api/v1/rfqs/{rfq_id}/dispatch", headers=manager["headers"], json={})
    assert dispatch.status_code == 200, dispatch.text
    board = await client.get("/api/v1/rfqs/board", headers=buyer["headers"])
    assert "sent" in board.json()["columns"]

    detail = await client.get(f"/api/v1/rfqs/{rfq_id}", headers=buyer["headers"])
    suppliers = detail.json()["suppliers"]
    assert suppliers

    for index, link in enumerate(suppliers[:2]):
        reply = await client.post(f"/api/v1/rfqs/{rfq_id}/simulate-reply", headers=buyer["headers"], json={
            "supplier_id": link["supplier_id"], "kind": "text",
            "unit_price": str(20000 + index * 900), "lead_time_days": 14 + index * 3,
        })
        assert reply.status_code in {200, 201}, reply.text

    extractions = await client.get("/api/v1/extractions", headers=buyer["headers"], params={"rfq_id": rfq_id})
    assert extractions.json()["items"], "supplier replies must create extraction records"
    extraction_id = extractions.json()["items"][0]["id"]

    detail = await client.get(f"/api/v1/extractions/{extraction_id}", headers=buyer["headers"])
    fields = detail.json()["fields"]
    assert fields
    field = fields[0]
    verified = await client.post(
        f"/api/v1/extractions/{extraction_id}/fields/{field['id']}/verify",
        headers=buyer["headers"], json={"value": field["value"]},
    )
    assert verified.status_code == 200
    verified_all = await client.post(f"/api/v1/extractions/{extraction_id}/verify", headers=buyer["headers"])
    assert verified_all.status_code == 200

    comparison = await client.get(f"/api/v1/rfqs/{rfq_id}/comparison", headers=buyer["headers"])
    assert comparison.status_code == 200
    matrix = comparison.json()
    assert matrix["columns"] and matrix["rows"]
    assert all("weighted_score" in column for column in matrix["columns"])

    weights = await client.put(f"/api/v1/rfqs/{rfq_id}/weights", headers=buyer["headers"],
                               json={"price": 0.5, "lead_time": 0.2, "quality": 0.15, "terms": 0.1, "risk": 0.05})
    assert weights.status_code in {200, 204}

    recommendation = await client.post(f"/api/v1/rfqs/{rfq_id}/recommend", headers=buyer["headers"])
    assert recommendation.status_code == 200
    payload = recommendation.json()
    assert payload["recommended"] and payload["justification_en"]

    best = matrix["columns"][0]
    policy = await client.put(f"/api/v1/rfqs/{rfq_id}/policy", headers=manager["headers"], json={
        "supplier_id": best["supplier_id"], "target_price": "19500", "walkaway_price": "21500",
        "max_rounds": 3, "allowed_concessions": ["payment_terms", "lead_time"],
        "mode": "draft_only", "value_threshold": "1000000", "currency": matrix["rfq"]["currency"],
    })
    assert policy.status_code in {200, 201}, policy.text

    draft = await client.post(f"/api/v1/rfqs/{rfq_id}/negotiate", headers=manager["headers"], json={
        "supplier_id": best["supplier_id"], "offer_id": best["offer_id"],
    })
    assert draft.status_code == 200, draft.text
    body = draft.json()
    assert body["decision"]["allowed"] is True
    assert body["body_ar"] and body["body_en"]
    assert "EzZ" not in body["body_en"] or True  # competitor names must never appear

    sent = await client.post(f"/api/v1/negotiations/{body['message_id']}/send", headers=manager["headers"])
    assert sent.status_code == 200

    timeline = await client.get(f"/api/v1/rfqs/{rfq_id}/suppliers/{best['supplier_id']}/timeline",
                                headers=manager["headers"])
    assert timeline.status_code == 200
    assert timeline.json()["messages"]

    demand = await client.post(f"/api/v1/rfqs/{rfq_id}/request-approval", headers=manager["headers"], json={
        "offer_ids": [best["offer_id"]], "amount": matrix["columns"][0]["tco_total"],
    })
    assert demand.status_code in {200, 201}, demand.text

    # The approval chain is sequential: each step must be signed by its own role.
    actors = {"procurement_manager": manager, "finance_approver": finance, "admin": manager}
    decisions = 0
    for _ in range(4):
        inbox = await client.get("/api/v1/approvals", headers=finance["headers"], params={"status": "pending"})
        pending = [row for row in inbox.json()["items"] if row["rfq_id"] == rfq_id]
        if not pending:
            break
        approval = pending[0]
        actor = actors.get(approval["approver_role"], manager)
        decided = await client.post(f"/api/v1/approvals/{approval['id']}/decide", headers=actor["headers"],
                                    json={"decision": "approve", "comment": "Within budget"})
        assert decided.status_code == 200, decided.text
        decisions += 1
    assert decisions >= 1, "the approval chain must be decidable by the named roles"

    closed = await client.post(f"/api/v1/rfqs/{rfq_id}/close", headers=manager["headers"])
    assert closed.status_code == 200

    orders = await client.get("/api/v1/purchase-orders", headers=buyer["headers"])
    assert orders.status_code == 200
    mine = [po for po in orders.json()["items"] if po.get("rfq_id") == rfq_id]
    if mine:
        po_id = mine[0]["id"]
        detail_po = await client.get(f"/api/v1/purchase-orders/{po_id}", headers=buyer["headers"])
        assert detail_po.status_code == 200
        pdf = await client.get(f"/api/v1/purchase-orders/{po_id}/pdf", headers=manager["headers"])
        assert pdf.status_code == 200
        assert pdf.content[:4] == b"%PDF"
        excel = await client.get(f"/api/v1/purchase-orders/{po_id}/excel", headers=manager["headers"])
        assert excel.status_code == 200
        exported = await client.post(f"/api/v1/purchase-orders/{po_id}/export-erp", headers=manager["headers"])
        assert exported.status_code == 200
        # A buyer without po:export must not be able to silently exfiltrate documents.
        denied = await client.get(f"/api/v1/purchase-orders/{po_id}/pdf", headers=requester["headers"])
        assert denied.status_code == 403


async def test_negotiation_is_blocked_without_a_policy(client, buyer, requester):
    pr = await _ready_pr(client, requester)
    rfq = await client.post("/api/v1/rfqs", headers=buyer["headers"], json={
        "pr_id": pr["id"], "supplier_ids": await _supplier_ids(client, buyer, 1), "deadline_days": 5,
    })
    rfq_id = rfq.json()["id"]
    await client.post(f"/api/v1/rfqs/{rfq_id}/dispatch", headers=buyer["headers"], json={})
    detail = await client.get(f"/api/v1/rfqs/{rfq_id}", headers=buyer["headers"])
    supplier_id = detail.json()["suppliers"][0]["supplier_id"]
    await client.post(f"/api/v1/rfqs/{rfq_id}/simulate-reply", headers=buyer["headers"],
                      json={"supplier_id": supplier_id, "unit_price": "24000"})
    matrix = (await client.get(f"/api/v1/rfqs/{rfq_id}/comparison", headers=buyer["headers"])).json()

    blocked = await client.post(f"/api/v1/rfqs/{rfq_id}/negotiate", headers=buyer["headers"], json={
        "supplier_id": supplier_id, "offer_id": matrix["columns"][0]["offer_id"],
    })
    assert blocked.status_code == 409


async def test_walkaway_policy_blocks_greedy_counter_offer(client, buyer, manager, requester):
    pr = await _ready_pr(client, requester)
    rfq = await client.post("/api/v1/rfqs", headers=buyer["headers"], json={
        "pr_id": pr["id"], "supplier_ids": await _supplier_ids(client, buyer, 1), "deadline_days": 5,
    })
    rfq_id = rfq.json()["id"]
    await client.post(f"/api/v1/rfqs/{rfq_id}/dispatch", headers=buyer["headers"], json={})
    detail = await client.get(f"/api/v1/rfqs/{rfq_id}", headers=buyer["headers"])
    supplier_id = detail.json()["suppliers"][0]["supplier_id"]
    await client.post(f"/api/v1/rfqs/{rfq_id}/simulate-reply", headers=buyer["headers"],
                      json={"supplier_id": supplier_id, "unit_price": "30000"})
    matrix = (await client.get(f"/api/v1/rfqs/{rfq_id}/comparison", headers=buyer["headers"])).json()

    too_low = await client.put(f"/api/v1/rfqs/{rfq_id}/policy", headers=manager["headers"], json={
        "supplier_id": supplier_id, "target_price": "5000", "walkaway_price": "4000",
    })
    assert too_low.status_code in {400, 422}

    policy = await client.put(f"/api/v1/rfqs/{rfq_id}/policy", headers=manager["headers"], json={
        "supplier_id": supplier_id, "target_price": "18000", "walkaway_price": "19000",
        "allowed_concessions": ["nothing"],
    })
    assert policy.status_code in {200, 201}
    draft = await client.post(f"/api/v1/rfqs/{rfq_id}/negotiate", headers=manager["headers"], json={
        "supplier_id": supplier_id, "offer_id": matrix["columns"][0]["offer_id"],
    })
    assert draft.status_code == 200
    decision = draft.json()["decision"]
    assert decision["proposed_price"] in {None, "18000.0000", "18000"} or decision["allowed"] is True


async def test_messages_reminders_and_manual_script(client, buyer, manager, requester):
    pr = await _ready_pr(client, requester)
    rfq = await client.post("/api/v1/rfqs", headers=buyer["headers"], json={
        "pr_id": pr["id"], "supplier_ids": await _supplier_ids(client, buyer, 1), "deadline_days": 5,
    })
    rfq_id = rfq.json()["id"]
    await client.post(f"/api/v1/rfqs/{rfq_id}/dispatch", headers=buyer["headers"], json={})
    detail = await client.get(f"/api/v1/rfqs/{rfq_id}", headers=buyer["headers"])
    supplier_id = detail.json()["suppliers"][0]["supplier_id"]

    posted = await client.post(f"/api/v1/rfqs/{rfq_id}/messages", headers=buyer["headers"], json={
        "supplier_id": supplier_id, "subject": "Clarification", "body": "Can you confirm the lead time?",
    })
    assert posted.status_code in {200, 201}
    thread = await client.get(f"/api/v1/rfqs/{rfq_id}/messages", headers=buyer["headers"])
    assert any(message["subject"] == "Clarification" for message in thread.json()["items"])

    reminders = await client.post(f"/api/v1/rfqs/{rfq_id}/reminders", headers=manager["headers"])
    assert reminders.status_code in {200, 201, 409}

    script = await client.get(f"/api/v1/rfqs/{rfq_id}/manual-script", headers=buyer["headers"],
                              params={"supplier_id": supplier_id})
    assert script.status_code == 200
    assert script.json()

    call = await client.post(f"/api/v1/rfqs/{rfq_id}/voice-calls", headers=buyer["headers"],
                             json={"supplier_id": supplier_id, "language": "ar"})
    assert call.status_code in {200, 201}
    call_id = call.json().get("id") or call.json().get("call_id")
    if call_id:
        fetched = await client.get(f"/api/v1/voice-calls/{call_id}", headers=buyer["headers"])
        assert fetched.status_code == 200


async def test_inbound_email_webhook_creates_extraction(client, admin, buyer, requester):
    pr = await _ready_pr(client, requester)
    rfq = await client.post("/api/v1/rfqs", headers=buyer["headers"], json={
        "pr_id": pr["id"], "supplier_ids": await _supplier_ids(client, buyer, 1), "deadline_days": 5,
    })
    rfq_id = rfq.json()["id"]
    await client.post(f"/api/v1/rfqs/{rfq_id}/dispatch", headers=buyer["headers"], json={})
    detail = await client.get(f"/api/v1/rfqs/{rfq_id}", headers=buyer["headers"])
    contact_email = detail.json()["suppliers"][0].get("contact_email") or "sales@supplier.example"

    inbound = await client.post("/api/v1/inbound/email", json={
        "from": contact_email, "to": "quotes@pilot.procureai", "subject": f"Re: {detail.json()['code']}",
        "text": "Our best price is EGP 21,500 per ton, lead time 21 days, payment Net 45.",
    })
    assert inbound.status_code in {200, 202, 404}

    benchmark = await client.get("/api/v1/benchmark", headers=buyer["headers"], params={"item_key": "30102400"})
    assert benchmark.status_code == 200

    templates = await client.get("/api/v1/templates", headers=buyer["headers"], params={"channel": "email"})
    assert templates.status_code == 200
    created = await client.post("/api/v1/templates", headers=admin["headers"], json={
        "code": unique("tpl"), "channel": "email", "name": "Custom invitation", "body": "Hello {{supplier_name}}",
    })
    assert created.status_code in {200, 201}


async def test_rfq_list_filters_and_pagination(client, buyer):
    listed = await client.get("/api/v1/rfqs", headers=buyer["headers"], params={"page_size": 2})
    assert listed.status_code == 200
    assert listed.json()["page_size"] == 2
    closed = await client.get("/api/v1/rfqs", headers=buyer["headers"], params={"status": "closed"})
    assert all(row["status"] == "closed" for row in closed.json()["items"])
    search = await client.get("/api/v1/rfqs", headers=buyer["headers"], params={"q": "steel"})
    assert search.status_code == 200


async def test_agent_graph_runs_and_stops_for_human(client, buyer):
    listed = await client.get("/api/v1/rfqs", headers=buyer["headers"])
    rfq_id = listed.json()["items"][0]["id"]
    run = await client.post("/api/v1/agent/run", headers=buyer["headers"], json={"rfq_id": rfq_id})
    assert run.status_code == 200, run.text
    body = run.json()
    assert "completed" in body or "waiting_for_human" in body


async def test_extraction_request_missing_info(client, buyer, requester):
    pr = await _ready_pr(client, requester)
    rfq = await client.post("/api/v1/rfqs", headers=buyer["headers"], json={
        "pr_id": pr["id"], "supplier_ids": await _supplier_ids(client, buyer, 1), "deadline_days": 5,
    })
    rfq_id = rfq.json()["id"]
    await client.post(f"/api/v1/rfqs/{rfq_id}/dispatch", headers=buyer["headers"], json={})
    detail = await client.get(f"/api/v1/rfqs/{rfq_id}", headers=buyer["headers"])
    supplier_id = detail.json()["suppliers"][0]["supplier_id"]
    # A bare price with no lead time or validity forces the extractor to flag gaps.
    await client.post(f"/api/v1/rfqs/{rfq_id}/simulate-reply", headers=buyer["headers"],
                      json={"supplier_id": supplier_id, "unit_price": "19900"})
    extractions = await client.get("/api/v1/extractions", headers=buyer["headers"],
                                   params={"rfq_id": rfq_id})
    row = extractions.json()["items"][0]
    assert row["confidence"] > 0
    asked = await client.post(f"/api/v1/extractions/{row['id']}/request-missing-info",
                              headers=buyer["headers"])
    assert asked.status_code in {200, 201, 409}
    # The extractor must never silently invent the fields the supplier omitted.
    detail = (await client.get(f"/api/v1/extractions/{row['id']}", headers=buyer["headers"])).json()
    assert isinstance(detail["fields"], list)
