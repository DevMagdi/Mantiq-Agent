"""End-to-end sweep of the RFQ workspace API surface (FR-RFQ-*, FR-CMP-*, FR-PO-*, FR-VOC-*).

One requisition is driven through the whole commercial path — board, preview, dispatch,
reply, verification, comparison, recommendation, policy, negotiation, approval, purchase
order, voice — so the shapes the UI depends on are pinned by tests rather than by hope.
"""
from __future__ import annotations

from tests.conftest import unique


async def _new_rfq(client, requester, buyer, *, title: str) -> dict:
    pr = await client.post("/api/v1/intake/text", headers=requester["headers"],
                           json={"text": "Need 30 t rebar 16mm B420D, target 20500 EGP per ton.",
                                 "title": title})
    assert pr.status_code in {200, 201}, pr.text
    pr_id = pr.json()["id"]
    detail = (await client.get(f"/api/v1/requests/{pr_id}", headers=requester["headers"])).json()
    for question in detail.get("clarifications", []):
        if question["status"] == "open":
            await client.post(f"/api/v1/requests/{pr_id}/clarifications/{question['id']}/answer",
                              headers=requester["headers"], json={"answer": "ASTM A615 grade 60"})
    assert (await client.post(f"/api/v1/requests/{pr_id}/submit",
                              headers=requester["headers"])).status_code in {200, 201}

    suppliers = (await client.get("/api/v1/suppliers", headers=buyer["headers"],
                                  params={"page_size": 3})).json()["items"]
    created = await client.post("/api/v1/rfqs", headers=buyer["headers"], json={
        "pr_id": pr_id, "supplier_ids": [row["id"] for row in suppliers[:2]],
        "deadline_days": 7, "title": title,
    })
    assert created.status_code == 201, created.text
    rfq = created.json()
    return {"pr_id": pr_id, "rfq": rfq, "suppliers": suppliers}


async def test_full_rfq_workspace_walkthrough(client, requester, buyer, manager, admin):
    scene = await _new_rfq(client, requester, buyer, title="API surface walkthrough")
    rfq = scene["rfq"]
    rfq_id = rfq["id"]

    # --- board, detail, templates, preview
    board = await client.get("/api/v1/rfqs/board", headers=buyer["headers"])
    assert board.status_code == 200
    codes = [card["code"] for column in board.json()["columns"].values() for card in column]
    assert rfq["code"] in codes

    detail = await client.get(f"/api/v1/rfqs/{rfq_id}", headers=buyer["headers"])
    assert detail.status_code == 200
    assert detail.json()["id"] == rfq_id
    assert detail.json()["lines"]

    templates = await client.get("/api/v1/templates", headers=buyer["headers"], params={"channel": "email"})
    assert templates.status_code == 200
    assert templates.json()["items"]

    for locale in ("ar", "en"):
        preview = await client.get(f"/api/v1/rfqs/{rfq_id}/preview", headers=buyer["headers"],
                                   params={"locale": locale})
        assert preview.status_code == 200
        assert preview.json()["body"], f"the {locale} invitation preview must not be empty"

    patched = await client.patch(f"/api/v1/rfqs/{rfq_id}", headers=buyer["headers"],
                                 json={"payment_terms": "Net 45", "title": "API surface walkthrough v2"})
    assert patched.status_code == 200

    # --- dispatch and reminders
    dispatch = await client.post(f"/api/v1/rfqs/{rfq_id}/dispatch", headers=buyer["headers"], json={})
    assert dispatch.status_code in {200, 201}, dispatch.text
    reminders = await client.post(f"/api/v1/rfqs/{rfq_id}/reminders", headers=buyer["headers"])
    assert reminders.status_code == 200

    # --- a supplier replies, the reply is parsed and verified
    reply = await client.post(f"/api/v1/rfqs/{rfq_id}/simulate-reply", headers=buyer["headers"],
                             json={"supplier_id": scene["suppliers"][0]["id"], "kind": "text",
                                   "unit_price": "20350", "lead_time_days": 18})
    assert reply.status_code in {200, 201}, reply.text
    assert reply.json()["matched"] is True
    extraction_id = reply.json()["extractions"][0]

    queue = await client.get("/api/v1/extractions", headers=buyer["headers"], params={"rfq_id": rfq_id})
    assert queue.status_code == 200
    assert queue.json()["items"]

    extraction = (await client.get(f"/api/v1/extractions/{extraction_id}",
                                   headers=buyer["headers"])).json()
    assert extraction["fields"]
    field = extraction["fields"][0]
    verified = await client.post(
        f"/api/v1/extractions/{extraction_id}/fields/{field['id']}/verify",
        headers=buyer["headers"], json={"value": field["value"] or "20350"})
    assert verified.status_code == 200, verified.text

    missing = await client.post(f"/api/v1/extractions/{extraction_id}/request-missing-info",
                                headers=buyer["headers"])
    assert missing.status_code == 200

    whole = await client.post(f"/api/v1/extractions/{extraction_id}/verify", headers=buyer["headers"])
    assert whole.status_code == 200, whole.text

    # --- comparison, weights, recommendation, benchmark
    matrix = await client.get(f"/api/v1/rfqs/{rfq_id}/comparison", headers=buyer["headers"])
    assert matrix.status_code == 200
    assert matrix.json()["columns"]

    weights = await client.put(f"/api/v1/rfqs/{rfq_id}/weights", headers=buyer["headers"],
                               json={"price": 0.6, "lead_time": 0.25, "quality": 0.15})
    assert weights.status_code == 200, weights.text
    assert abs(sum(weights.json()["weights"].values()) - 1.0) < 0.05
    rejected = await client.put(f"/api/v1/rfqs/{rfq_id}/weights", headers=buyer["headers"],
                                json={"price": 5, "lead_time": 5})
    assert rejected.status_code == 422, "weights that do not sum to 1 must be rejected"

    recommendation = await client.post(f"/api/v1/rfqs/{rfq_id}/recommend", headers=buyer["headers"])
    assert recommendation.status_code == 200, recommendation.text
    assert recommendation.json()["recommended"]

    benchmark = await client.get("/api/v1/benchmark", headers=buyer["headers"],
                                 params={"item_key": "Rebar 12mm B420D"})
    assert benchmark.status_code == 200
    assert "median" in benchmark.json() or "points" in benchmark.json()

    # --- negotiation policy, draft, timeline
    assert (await client.put(f"/api/v1/rfqs/{rfq_id}/policy", headers=buyer["headers"],
                             json={"target_price": "19500", "walkaway_price": "21000"})).status_code == 403
    policy = await client.put(f"/api/v1/rfqs/{rfq_id}/policy", headers=manager["headers"], json={
        "target_price": "19500", "walkaway_price": "21000", "max_rounds": 3,
        "allowed_concessions": ["payment_terms"], "tone": "professional",
        "mode": "semi_auto", "value_threshold": "500000", "currency": "EGP",
    })
    assert policy.status_code == 200, policy.text
    assert policy.json()["version"] == 1

    fetched = await client.get(f"/api/v1/rfqs/{rfq_id}/policy", headers=buyer["headers"])
    assert fetched.status_code == 200
    assert fetched.json()["policy"]["target_price"].startswith("19500")

    comparison = (await client.get(f"/api/v1/rfqs/{rfq_id}/comparison", headers=buyer["headers"])).json()
    column = comparison["columns"][0]
    draft = await client.post(f"/api/v1/rfqs/{rfq_id}/negotiate", headers=buyer["headers"], json={
        "supplier_id": column["supplier_id"], "offer_id": column["offer_id"], "round_no": 2,
        "concession": "payment_terms", "send": False,
    })
    assert draft.status_code == 200, draft.text
    assert draft.json()["status"] in {"draft", "sent", "blocked"}
    assert draft.json()["body_ar"] and draft.json()["body_en"]

    thread = await client.get(f"/api/v1/rfqs/{rfq_id}/suppliers/{column['supplier_id']}/timeline",
                              headers=buyer["headers"])
    assert thread.status_code == 200
    assert thread.json()["messages"]
    assert thread.json()["max_rounds"] == 3

    # --- manual supplier message and thread listing
    sent = await client.post(f"/api/v1/rfqs/{rfq_id}/messages", headers=buyer["headers"], json={
        "supplier_id": column["supplier_id"], "subject": "Clarification",
        "body": "Please confirm the delivery window for the second tranche.", "channel": "email",
    })
    assert sent.status_code == 200, sent.text
    messages = await client.get(f"/api/v1/rfqs/{rfq_id}/messages", headers=buyer["headers"])
    assert messages.status_code == 200
    assert messages.json()["items"]

    # --- approval chain, decisions, purchase order
    requested = await client.post(f"/api/v1/rfqs/{rfq_id}/request-approval", headers=buyer["headers"],
                                  json={"offer_ids": [column["offer_id"]], "amount": column["tco_total"]})
    assert requested.status_code == 200, requested.text
    steps = requested.json()["approvals"]
    assert len(steps) >= 2

    inbox = await client.get("/api/v1/approvals", headers=admin["headers"], params={"status": "pending"})
    assert inbox.status_code == 200

    # A step cannot be decided by a role that is not the assigned approver.
    wrong_role = await client.post(f"/api/v1/approvals/{steps[-1]['id']}/decide",
                                   headers=buyer["headers"], json={"decision": "approve", "comment": "x"})
    assert wrong_role.status_code in {403, 409}

    finance = await client.post("/api/v1/auth/login", json={"email": "finance@nile.example.com",
                                                            "password": "Demo!Pilot2026"})
    finance_headers = {"authorization": f"Bearer {finance.json()['access_token']}"}

    first = await client.post(f"/api/v1/approvals/{steps[0]['id']}/decide", headers=manager["headers"],
                              json={"decision": "approve", "comment": "Within budget"})
    assert first.status_code == 200, first.text
    second = await client.post(f"/api/v1/approvals/{steps[-1]['id']}/decide", headers=finance_headers,
                               json={"decision": "approve", "comment": "Cash confirmed"})
    assert second.status_code == 200, second.text
    assert second.json()["purchase_orders"]

    escalated = await client.post("/api/v1/approvals/escalate", headers=admin["headers"])
    assert escalated.status_code == 200
    assert escalated.json()["escalated"] >= 0

    orders = await client.get("/api/v1/purchase-orders", headers=buyer["headers"])
    assert orders.status_code == 200
    po_id = orders.json()["items"][0]["id"]

    po = await client.get(f"/api/v1/purchase-orders/{po_id}", headers=buyer["headers"])
    assert po.status_code == 200
    assert po.json()["lines"]

    assert (await client.get(f"/api/v1/purchase-orders/{po_id}/pdf",
                             headers=buyer["headers"])).status_code == 403
    pdf = await client.get(f"/api/v1/purchase-orders/{po_id}/pdf", headers=manager["headers"])
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")

    excel = await client.get(f"/api/v1/purchase-orders/{po_id}/excel", headers=manager["headers"])
    assert excel.status_code == 200
    assert excel.content.startswith(b"PK")

    erp = await client.post(f"/api/v1/purchase-orders/{po_id}/export-erp", headers=manager["headers"])
    assert erp.status_code == 200, erp.text


async def test_template_creation_requires_admin_and_versions_correctly(client, buyer, admin):
    payload = {"code": f"custom_{unique('tmpl')[:10]}", "channel": "email", "locale": "en",
               "name": "Custom invitation", "subject": "RFQ {{rfq_code}}", "body": "Hello {{supplier_name}}"}
    denied = await client.post("/api/v1/templates", headers=buyer["headers"], json=payload)
    assert denied.status_code == 403

    first = await client.post("/api/v1/templates", headers=admin["headers"], json=payload)
    assert first.status_code == 201, first.text
    assert first.json()["version"] == 1
    second = await client.post("/api/v1/templates", headers=admin["headers"], json=payload)
    assert second.status_code == 201
    assert second.json()["version"] == 2


async def test_voice_endpoints_expose_disclosure_and_a_manual_fallback(client, requester, buyer):
    scene = await _new_rfq(client, requester, buyer, title="Voice console")
    rfq_id = scene["rfq"]["id"]
    supplier_id = scene["suppliers"][0]["id"]
    await client.post(f"/api/v1/rfqs/{rfq_id}/dispatch", headers=buyer["headers"], json={})

    script = await client.get(f"/api/v1/rfqs/{rfq_id}/manual-script", headers=buyer["headers"],
                              params={"supplier_id": supplier_id})
    assert script.status_code == 200, script.text
    assert script.json()["script_ar"] and script.json()["script_en"]

    call = await client.post(f"/api/v1/rfqs/{rfq_id}/voice-calls", headers=buyer["headers"],
                             json={"supplier_id": supplier_id, "language": "ar-EG"})
    assert call.status_code == 200, call.text
    body = call.json()
    if body.get("ok"):
        assert body["disclosure_played"] is True, "the AI must disclose itself on every call"
        console = await client.get(f"/api/v1/voice-calls/{body['call_id']}", headers=buyer["headers"])
        assert console.status_code == 200
        assert console.json()["disclosure_played"] is True
        assert console.json()["policy_snapshot"]["disclosure"]
    else:
        # Outside the destination's permitted calling hours, a call must be refused, not placed.
        assert "allowed calling hours" in body["error"] or "opt" in body["error"].lower()


async def test_inbound_webhooks_are_tenant_scoped_and_parse_supplier_replies(client, buyer):
    email_hook = await client.post("/api/v1/inbound/email", headers={"x-tenant-slug": "nile"}, json={
        "message_id": "inbound-email-1", "from": "sales@unknown.example.com",
        "subject": "Re: RFQ", "body": "Please find our quotation attached.",
    })
    assert email_hook.status_code == 200, email_hook.text
    assert email_hook.json()["processed"] == 1

    whatsapp_hook = await client.post("/api/v1/inbound/whatsapp", headers={"x-tenant-slug": "nile"}, json={
        "entry": [{"changes": [{"value": {"messages": [
            {"id": "wamid.1", "from": "+201000000000", "type": "text", "text": {"body": "Unit price 21000 EGP"}},
            {"id": "wamid.2", "from": "+201000000000", "type": "audio", "audio": {"id": "a1"}},
        ]}}]}],
    })
    assert whatsapp_hook.status_code == 200, whatsapp_hook.text
    assert whatsapp_hook.json()["processed"] == 2

    unknown_tenant = await client.post("/api/v1/inbound/email", headers={"x-tenant-slug": "no-such-tenant"},
                                       json={"from": "a@b.c", "subject": "x", "body": "y"})
    assert unknown_tenant.status_code == 404
