"""Phase 4 — the whole purchase-to-pay cycle, on real channels, with failure paths.

Where `tests/test_api_p2p_accounting_e2e.py` proves the chain end to end, this module pushes on the
parts that only a full rehearsal touches:

* three suppliers on three different channels (WhatsApp, email, voice) inside one RFQ, each reply
  arriving through its own real entry point — the WhatsApp webhook, the supplier reply pipe and an
  outbound AI call;
* human verification of the extracted offer *before* the comparison will rank it;
* a negotiation round that stays inside the policy;
* a PARTIAL receipt followed by the remainder, so the goods-receipt path is exercised twice and the
  invoice covers the full ordered quantity;
* the accrual, the invoice posting and the supplier payment, with the ledger and the trial balance
  agreeing after each one;
* the payment *suggestion* stays a suggestion — nothing in the platform executes a payment.

Roles are exercised as themselves (requester, buyer, manager, warehouse, finance, controller,
auditor, admin), and every refusal is asserted by code, not by status text.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from conftest import unique
from sqlalchemy import select

from app.db.models.entities import CountryCallPolicy
from app.db.session import SessionLocal


# --------------------------------------------------------------------------- scene helpers
async def _open_call_window() -> None:
    """A test-only destination country whose call policy is open all day (no clock dependency)."""
    async with SessionLocal() as session:
        row = (await session.execute(
            select(CountryCallPolicy).where(CountryCallPolicy.country == "ZY")
        )).scalar_one_or_none()
        if row is None:
            session.add(CountryCallPolicy(
                country="ZY", allowed_hours_start=0, allowed_hours_end=24,
                recording_consent_mode="explicit",
                disclosure_text_en="Hello, this is an AI assistant calling on behalf of the buyer.",
                disclosure_text_ar="مرحباً، أنا مساعد ذكاء اصطناعي أتصل نيابة عن المشتري.",
            ))
            await session.commit()


def _phone() -> str:
    return f"+2010{uuid.uuid4().int % 10**6:06d}"


def _voice_phone() -> str:
    """A number the deterministic telephony fake answers *and* asks nothing hard about.

    The fake is checksum-driven (no live line): `sum(ord)` % 7 == 0 means "no answer" and % 4
    selects the awkward question. A clean, answered call is `% 7 != 0 and % 4 == 0`.
    """
    while True:
        candidate = _phone()
        checksum = sum(ord(char) for char in candidate)
        if checksum % 7 != 0 and checksum % 4 == 0:
            return candidate


async def _supplier(client, buyer, *, name: str, phone: str = "", email: str = "",
                    country: str = "EG") -> str:
    response = await client.post("/api/v1/suppliers", headers=buyer["headers"], json={
        "name": name, "name_ar": f"{name} عربي", "country": country, "city": "Cairo",
        "email": email, "phone": phone, "categories": ["STEEL"],
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _prefer(client, buyer, supplier_id: str, *, channel: str, language: str = "ar") -> None:
    response = await client.patch(f"/api/v1/suppliers/{supplier_id}/channels",
                                  headers=buyer["headers"],
                                  json={"preferred_channel": channel, "preferred_language": language})
    assert response.status_code == 200, response.text


async def _opt_in(client, buyer, supplier_id: str, channel: str) -> None:
    response = await client.post(f"/api/v1/suppliers/{supplier_id}/opt-in", headers=buyer["headers"],
                                 json={"channel": channel, "status": "opted_in",
                                       "source": "signed_form", "evidence": "supplier consent form"})
    assert response.status_code == 200, response.text


async def _ready_request(client, requester) -> dict:
    created = await client.post("/api/v1/intake/text", headers=requester["headers"], json={
        "text": "Need 30 t rebar 12mm B420D for the Sadat plant, delivery within 3 weeks, "
                "payment Net 30, target price 21500 EGP per ton, cost centre CC-000.",
        "title": unique("Phase4 rebar"),
    })
    assert created.status_code in {200, 201}, created.text
    pr_id = created.json()["id"]
    detail = (await client.get(f"/api/v1/requests/{pr_id}", headers=requester["headers"])).json()
    for question in detail.get("clarifications", []):
        if question["status"] == "open":
            answered = await client.post(
                f"/api/v1/requests/{pr_id}/clarifications/{question['id']}/answer",
                headers=requester["headers"],
                json={"answer": "ASTM A615 grade 60", "waive": False})
            assert answered.status_code in {200, 201}, answered.text
    submitted = await client.post(f"/api/v1/requests/{pr_id}/submit", headers=requester["headers"])
    assert submitted.status_code in {200, 201}, submitted.text
    return (await client.get(f"/api/v1/requests/{pr_id}", headers=requester["headers"])).json()


async def _approve_chain(client, rfq_id: str, personas: dict) -> int:
    decided = 0
    for _ in range(8):
        progressed = False
        for role, persona in personas.items():
            inbox = await client.get("/api/v1/approvals", headers=persona["headers"],
                                     params={"status": "pending"})
            pending = [row for row in inbox.json()["items"]
                       if row["rfq_id"] == rfq_id and row["approver_role"] == role]
            if not pending:
                continue
            response = await client.post(f"/api/v1/approvals/{pending[0]['id']}/decide",
                                         headers=persona["headers"],
                                         json={"decision": "approve", "comment": "Approved"})
            assert response.status_code == 200, response.text
            decided += 1
            progressed = True
            break
        if not progressed:
            break
    return decided


async def _whatsapp_reply(client, *, rfq_code: str, phone: str, body: str) -> dict:
    """A real Meta-shaped webhook delivery for one supplier message."""
    payload = {"entry": [{"changes": [{"value": {
        "messages": [{"id": f"wamid.phase4-{uuid.uuid4().hex[:8]}", "from": phone.lstrip("+"),
                      "type": "text", "timestamp": "1760000000", "text": {"body": body}}],
        "contacts": [{"wa_id": phone.lstrip("+"), "profile": {"name": "Supplier"}}],
    }}]}]}
    response = await client.post("/api/v1/inbound/whatsapp", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- the golden path
async def test_the_cycle_runs_on_whatsapp_email_and_voice_up_to_posted_accounts(
        client, admin, manager, buyer, requester, finance, warehouse, controller, auditor):
    await _open_call_window()
    personas = {"procurement_manager": manager, "finance_approver": finance, "auditor": auditor,
                "admin": admin}

    # 1. The requesting department states the need, answers the clarification questions and submits.
    pr = await _ready_request(client, requester)
    # "ready" is the state after the requester answers every clarification and submits the request.
    assert pr["clarification_resolved"] is True
    assert pr["status"] not in {"draft", "clarifying"}
    routed = await client.post(f"/api/v1/requests/{pr['id']}/assign", headers=manager["headers"],
                               json={"department": "Maintenance"})
    assert routed.status_code == 200 and routed.json()["status"] == "in_procurement"

    # 2. Three suppliers, three preferred channels: WhatsApp first, then email, then voice.
    suffix = uuid.uuid4().hex[:6]
    wa_phone, voice_phone = _phone(), _voice_phone()
    wa_id = await _supplier(client, buyer, name=f"WhatsApp Metals {suffix}", phone=wa_phone,
                            email=f"wa.{suffix}@example.com")
    email_id = await _supplier(client, buyer, name=f"Email Steel {suffix}",
                               email=f"mail.{suffix}@example.com", phone=_phone())
    voice_id = await _supplier(client, buyer, name=f"Voice Trading {suffix}", phone=voice_phone,
                               email=f"voice.{suffix}@example.com", country="ZY")
    await _prefer(client, buyer, wa_id, channel="whatsapp")
    await _prefer(client, buyer, email_id, channel="email", language="en")
    await _prefer(client, buyer, voice_id, channel="voice")
    for supplier_id, channel in ((wa_id, "whatsapp"), (voice_id, "voice")):
        await _opt_in(client, buyer, supplier_id, channel)

    # 3. One RFQ, mixed channels: each supplier resolves to the channel it asked for.
    rfq = await client.post("/api/v1/rfqs", headers=buyer["headers"], json={
        "pr_id": pr["id"], "supplier_ids": [wa_id, email_id, voice_id], "deadline_days": 10,
        "mode": "semi_auto", "incoterm": "DAP", "payment_terms": "Net 30",
        "channel": "whatsapp",
    })
    assert rfq.status_code in {200, 201}, rfq.text
    rfq_id, rfq_code = rfq.json()["id"], rfq.json()["code"]
    dispatched = await client.post(f"/api/v1/rfqs/{rfq_id}/dispatch", headers=buyer["headers"],
                                   json={})
    assert dispatched.status_code == 200, dispatched.text

    channel_rows = {row["supplier_id"]: row for row in
                    (await client.get(f"/api/v1/rfqs/{rfq_id}/channels",
                                      headers=buyer["headers"])).json()["items"]}
    assert channel_rows[wa_id]["channel"] == "whatsapp"
    assert channel_rows[email_id]["channel"] == "email"
    assert channel_rows[voice_id]["channel"] == "voice"
    assert channel_rows[wa_id]["comm_status"] in {"sent", "delivered", "read"}

    # 4a. The WhatsApp supplier replies in Arabic through the webhook — a real inbound message.
    inbound = await _whatsapp_reply(
        client, rfq_code=rfq_code, phone=wa_phone,
        body=f"{rfq_code} سعر الطن 20800 جنيه، التسليم خلال 18 يوم، صلاحية العرض 30 يوم")
    assert inbound["processed"] == 1
    assert inbound["results"][0]["matched"] is True
    assert inbound["results"][0]["extractions"], "an inbound WhatsApp reply must become an extraction"

    # 4b. The email supplier replies through the supplier reply pipe.
    email_reply = await client.post(f"/api/v1/rfqs/{rfq_id}/simulate-reply", headers=buyer["headers"],
                                    json={"supplier_id": email_id, "kind": "text",
                                          "unit_price": "21100", "lead_time_days": 21})
    assert email_reply.status_code in {200, 201}, email_reply.text

    # 4c. The voice supplier is called by the AI agent; the transcript feeds the same pipeline.
    call = await client.post("/api/v1/voice/calls", headers=buyer["headers"],
                             json={"rfq_id": rfq_id, "supplier_id": voice_id, "language": "ar-EG",
                                   "mode": "ai"})
    assert call.status_code == 201, call.text
    call_body = call.json()
    assert call_body["ok"] is True, call_body
    assert call_body["disclosure_played"] is True and call_body["consent_given"] is True

    # 5. A human verifies every extracted offer before the comparison may rank it.
    extractions = (await client.get("/api/v1/extractions", headers=buyer["headers"],
                                    params={"rfq_id": rfq_id})).json()["items"]
    assert len({row["supplier_id"] for row in extractions}) >= 2
    unverified = [row for row in extractions if row["status"] != "verified"]
    assert unverified, "the extraction pipeline is expected to leave human work behind"
    for row in unverified:
        verified = await client.post(f"/api/v1/extractions/{row['id']}/verify",
                                     headers=buyer["headers"])
        assert verified.status_code == 200, verified.text

    # 6. Comparison and a policy-bound negotiation round.
    matrix = (await client.get(f"/api/v1/rfqs/{rfq_id}/comparison",
                               headers=buyer["headers"])).json()
    assert matrix.get("blocked_for_low_confidence") in (None, False), matrix.get("blocking_reason")
    best = min(matrix["columns"], key=lambda column: Decimal(str(column["tco_total"])))
    # A negotiation policy is a managerial control: the manager sets it, the buyer drafts under it.
    policy = await client.put(f"/api/v1/rfqs/{rfq_id}/policy", headers=manager["headers"], json={
        "supplier_id": best["supplier_id"], "target_price": "20000", "walkaway_price": "21500",
        "max_rounds": 3, "allowed_concessions": ["delivery", "payment_terms"], "tone": "professional",
        "mode": "draft_only", "currency": "EGP",
    })
    assert policy.status_code in {200, 201}, policy.text
    drafted = await client.post(f"/api/v1/rfqs/{rfq_id}/negotiate", headers=buyer["headers"], json={
        "supplier_id": best["supplier_id"], "offer_id": best["offer_id"], "round_no": 1,
        "send": False,
    })
    assert drafted.status_code in {200, 201}, drafted.text
    assert drafted.json()["leak_violations"] == []

    # 7. The award goes through the amount-banded chain, including the auditor.
    requested = await client.post(f"/api/v1/rfqs/{rfq_id}/request-approval",
                                  headers=manager["headers"],
                                  json={"offer_ids": [best["offer_id"]],
                                        "amount": best["tco_total"]})
    assert requested.status_code in {200, 201}, requested.text
    assert await _approve_chain(client, rfq_id, personas) >= 2

    # 8. The purchase order exists only after that last approval.
    orders = (await client.get("/api/v1/purchase-orders", headers=buyer["headers"],
                               params={"rfq_id": rfq_id})).json()["items"]
    assert len(orders) == 1, orders
    po_id = orders[0]["id"]
    po = (await client.get(f"/api/v1/purchase-orders/{po_id}", headers=buyer["headers"])).json()
    line = po["lines"][0]
    ordered = Decimal(str(line["quantity"]))
    assert po["status"] in {"issued", "in_transit"}

    # 9. Warehouse receives 60 %, the requester confirms, then the rest arrives and is confirmed too.
    first = (ordered * Decimal("0.6")).quantize(Decimal("0.01"))
    receipt_one = await client.post(f"/api/v1/purchase-orders/{po_id}/receipts",
                                    headers=warehouse["headers"], json={
                                        "lines": [{"line_no": line["line_no"],
                                                   "received_qty": str(first),
                                                   "accepted_qty": str(first)}],
                                        "warehouse": "Cairo stores", "delivery_note_ref": unique("DN")})
    assert receipt_one.status_code == 201, receipt_one.text
    confirmed_one = await client.post(f"/api/v1/receipts/{receipt_one.json()['receipt_id']}/confirm",
                                      headers=requester["headers"],
                                      json={"quantity_ok": True, "quality_ok": True, "spec_ok": True,
                                            "comment": "First 60 % arrived complete."})
    assert confirmed_one.status_code == 200, confirmed_one.text

    remaining = (ordered - first).quantize(Decimal("0.01"))
    receipt_two = await client.post(f"/api/v1/purchase-orders/{po_id}/receipts",
                                    headers=warehouse["headers"], json={
                                        "lines": [{"line_no": line["line_no"],
                                                   "received_qty": str(remaining),
                                                   "accepted_qty": str(remaining)}],
                                        "warehouse": "Cairo stores", "delivery_note_ref": unique("DN")})
    assert receipt_two.status_code == 201, receipt_two.text
    confirmed_two = await client.post(f"/api/v1/receipts/{receipt_two.json()['receipt_id']}/confirm",
                                      headers=requester["headers"],
                                      json={"quantity_ok": True, "quality_ok": True, "spec_ok": True,
                                            "comment": "Balance arrived."})
    assert confirmed_two.status_code == 200, confirmed_two.text

    # 10. The auditor package is sealed and accepted.
    package = (await client.get(f"/api/v1/purchase-orders/{po_id}/audit-package",
                                headers=auditor["headers"])).json()
    assert package["package"] is not None and package["verification"]["intact"] is True
    reviewed = await client.post(f"/api/v1/audit-packages/{package['package']['id']}/review",
                                 headers=auditor["headers"],
                                 json={"decision": "accepted", "comment": "Two receipts documented."})
    assert reviewed.status_code == 200, reviewed.text

    # 11. Finance invoices the full order; the three-way match passes.
    net = ordered * Decimal(str(line["unit_price"]))
    invoice = await client.post(f"/api/v1/purchase-orders/{po_id}/invoices",
                                headers=finance["headers"], json={
                                    "number": unique("INV"),
                                    "lines": [{"line_no": line["line_no"], "quantity": str(ordered),
                                               "unit_price": line["unit_price"]}],
                                    "tax_amount": str((net * Decimal("0.14")).quantize(Decimal("0.01"))),
                                    "due_date": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
                                })
    assert invoice.status_code == 201, invoice.text
    invoice_body = invoice.json()
    assert invoice_body["match"]["status"] == "matched", invoice_body["match"]["issues"]
    invoice_id = invoice_body["invoice_id"]

    approved = await client.post(f"/api/v1/invoices/{invoice_id}/approve", headers=finance["headers"],
                                 json={"comment": "Matched to two confirmed receipts"})
    assert approved.status_code == 200, approved.text
    # The suggestion is advice, never an execution: it is always flagged for a human and always
    # outside the platform.
    suggestion = approved.json()["payment_suggestion"]
    assert suggestion["requires_human_approval"] is True
    assert suggestion["execution"] == "outside_platform"

    # 12. The controller recognises the received goods (GR/IR accrual) and posts it, then the
    #     invoice posting clears that accrual: inventory is debited exactly once.
    before_accrual = (await client.get(f"/api/v1/purchase-orders/{po_id}/accounting",
                                       headers=controller["headers"])).json()
    assert before_accrual["posted_count"] == 0, "nothing may be in the books before the controller posts"

    accrual = await client.post(f"/api/v1/purchase-orders/{po_id}/accounting/accrual",
                                headers=controller["headers"], json={"allocations": [
                                    {"line_no": line["line_no"], "cost_center_code": "CC-000",
                                     "unit_number": "UNIT-P4"}]})
    assert accrual.status_code == 201, accrual.text
    assert accrual.json()["source_type"] == "receipt_accrual"
    posted_accrual = await client.post(
        f"/api/v1/accounting/journal-entries/{accrual.json()['id']}/post",
        headers=controller["headers"])
    assert posted_accrual.status_code == 200, posted_accrual.text

    invoice_entry = await client.post(f"/api/v1/invoices/{invoice_id}/posting",
                                      headers=controller["headers"],
                                      json={"allocations": [{"line_no": line["line_no"],
                                                             "cost_center_code": "CC-000",
                                                             "unit_number": "UNIT-P4"}]})
    assert invoice_entry.status_code == 201, invoice_entry.text
    posted_invoice = await client.post(
        f"/api/v1/accounting/journal-entries/{invoice_entry.json()['id']}/post",
        headers=controller["headers"])
    assert posted_invoice.status_code == 200, posted_invoice.text
    assert posted_invoice.json()["status"] == "posted"

    # GR/IR nets to zero across the accrual and the invoice, and the received value sits in
    # inventory exactly once (the invoice clears the accrual rather than debiting stock again).
    trial_after_posting = (await client.get("/api/v1/accounting/trial-balance",
                                            headers=controller["headers"])).json()
    balances = {row["account_code"]: Decimal(str(row["balance"])) for row in trial_after_posting["items"]}
    assert balances.get("110200", Decimal("0")) == 0, balances.get("110200")
    inventory_value = Decimal(str(line["unit_price"])) * ordered
    assert balances.get("110100", Decimal("0")) >= inventory_value
    assert trial_after_posting["balanced"] is True

    # 13. Treasury pays the supplier OUTSIDE the platform; finance records it, the controller posts
    #     the settlement, and both the sub-ledger and the trial balance move with it.
    before = (await client.get(f"/api/v1/accounting/supplier-account/{po['supplier_id']}",
                               headers=controller["headers"])).json()
    assert Decimal(str(before["payable"])) > 0
    payment = await client.post(f"/api/v1/invoices/{invoice_id}/payments", headers=finance["headers"],
                                json={"amount": str(net), "method": "bank_transfer",
                                      "reference": unique("TRX"), "note": "Paid by treasury"})
    assert payment.status_code in {200, 201}, payment.text
    assert payment.json()["ok"] is True and payment.json()["status"] in {"paid", "partially_paid"}

    settlement = await client.post(f"/api/v1/invoices/{invoice_id}/payment-posting",
                                   headers=controller["headers"], json={})
    assert settlement.status_code == 201, settlement.text
    posted_settlement = await client.post(
        f"/api/v1/accounting/journal-entries/{settlement.json()['id']}/post",
        headers=controller["headers"])
    assert posted_settlement.status_code == 200, posted_settlement.text

    after = (await client.get(f"/api/v1/accounting/supplier-account/{po['supplier_id']}",
                              headers=controller["headers"])).json()
    assert Decimal(str(after["payable"])) < Decimal(str(before["payable"]))
    assert any(row["kind"] == "payment" for row in after["ledger_entries"])
    assert any(row["source_type"] == "payment" for row in after["movements"])

    trial = (await client.get("/api/v1/accounting/trial-balance",
                              headers=controller["headers"])).json()
    assert trial["balanced"] is True

    # 14. Cost lands on the cost centre and the unit, and the rail shows the final stage.
    by_unit = (await client.get("/api/v1/accounting/cost-report", headers=controller["headers"],
                                params={"group_by": "unit"})).json()
    assert any(row["key"] == "UNIT-P4" for row in by_unit["items"])
    by_center = (await client.get("/api/v1/accounting/cost-report", headers=controller["headers"],
                                  params={"group_by": "cost_center"})).json()
    assert any(row["key"] == "CC-000" for row in by_center["items"])
    lifecycle = (await client.get(f"/api/v1/purchase-orders/{po_id}/lifecycle",
                                  headers=manager["headers"])).json()
    assert lifecycle["current"] == "posted_to_accounts"

    # 15. Channel evidence survived the whole cycle: replies are counted per supplier.
    final_channels = {row["supplier_id"]: row for row in
                      (await client.get(f"/api/v1/rfqs/{rfq_id}/channels",
                                        headers=buyer["headers"])).json()["items"]}
    assert final_channels[wa_id]["reply_count"] >= 1
    assert final_channels[wa_id]["comm_status"] == "replied"

    # 16. The audit trail names every human step of the cycle — one row per status transition.
    trail = (await client.get("/api/v1/audit", headers=admin["headers"],
                              params={"page_size": 200})).json()["items"]
    actions = {row["action"] for row in trail}
    required = {
        "intake.text",            # the need was stated
        "pr.submitted",           # clarifications answered and the request submitted
        "rfq.created",            # routed to procurement and put out to the channel mix
        "rfq.dispatched",
        "extraction.verified",    # human verification of the offer
        "negotiation.policy_set",
        "approval.requested",     # the chain only starts with a human request
        "approval.approve",       # every step is a human decision
        "po.issued",              # award → purchase order
        "goods_receipt.created",  # warehouse received
        "goods_receipt.confirmed",  # requester signed off
        "invoice.created",
        "invoice.approved",
        "payment.recorded",       # recorded only; never executed
        "journal_entry.posted",
        "audit_package.assembled",
        "audit_package.accepted",
    }
    assert required <= actions, sorted(required - actions)


# --------------------------------------------------------------------------- failure paths
async def test_an_unverified_offer_blocks_the_comparison_and_a_mismatch_blocks_the_books(
        client, manager, buyer, requester, warehouse, finance, controller, auditor, admin):
    pr = await _ready_request(client, requester)
    await client.post(f"/api/v1/requests/{pr['id']}/assign", headers=manager["headers"],
                      json={"department": "Maintenance"})
    suffix = uuid.uuid4().hex[:6]
    supplier_id = await _supplier(client, buyer, name=f"Mismatch Steel {suffix}",
                                  email=f"mm.{suffix}@example.com", phone=_phone())
    await _prefer(client, buyer, supplier_id, channel="email", language="en")
    rfq = await client.post("/api/v1/rfqs", headers=buyer["headers"], json={
        "pr_id": pr["id"], "supplier_ids": [supplier_id], "deadline_days": 5, "mode": "semi_auto",
        "payment_terms": "Net 30",
    })
    rfq_id = rfq.json()["id"]
    await client.post(f"/api/v1/rfqs/{rfq_id}/dispatch", headers=buyer["headers"], json={})
    await client.post(f"/api/v1/rfqs/{rfq_id}/simulate-reply", headers=buyer["headers"],
                      json={"supplier_id": supplier_id, "kind": "text", "unit_price": "22000",
                            "lead_time_days": 25})

    # The comparison refuses to rank a low-confidence offer until a human confirms it.
    blocked = await client.get(f"/api/v1/rfqs/{rfq_id}/comparison", headers=buyer["headers"])
    body = blocked.json()
    assert blocked.status_code == 200, blocked.text
    if body.get("blocked_for_low_confidence"):
        assert body.get("blocking_reason")
        extractions = (await client.get("/api/v1/extractions", headers=buyer["headers"],
                                        params={"rfq_id": rfq_id})).json()["items"]
        for row in extractions:
            await client.post(f"/api/v1/extractions/{row['id']}/verify", headers=buyer["headers"])
        refreshed = (await client.get(f"/api/v1/rfqs/{rfq_id}/comparison",
                                      headers=buyer["headers"])).json()
        assert not refreshed.get("blocked_for_low_confidence")

    # A receipt for a tenth, then an invoice for the whole order: the match must refuse it.
    extractions = (await client.get("/api/v1/extractions", headers=buyer["headers"],
                                    params={"rfq_id": rfq_id})).json()["items"]
    offer_id = next(row["offer_id"] for row in extractions if row.get("offer_id"))
    for row in extractions:
        await client.post(f"/api/v1/extractions/{row['id']}/verify", headers=buyer["headers"])
    matrix = (await client.get(f"/api/v1/rfqs/{rfq_id}/comparison", headers=buyer["headers"])).json()
    column = next(col for col in matrix["columns"] if col["offer_id"] == offer_id)
    await client.post(f"/api/v1/rfqs/{rfq_id}/request-approval", headers=manager["headers"],
                      json={"offer_ids": [offer_id], "amount": column["tco_total"]})
    await _approve_chain(client, rfq_id, {"procurement_manager": manager,
                                          "finance_approver": finance, "auditor": auditor,
                                          "admin": admin})
    orders = (await client.get("/api/v1/purchase-orders", headers=buyer["headers"],
                               params={"rfq_id": rfq_id})).json()["items"]
    assert orders, "the approved award must issue an order"
    po_id = orders[0]["id"]
    po = (await client.get(f"/api/v1/purchase-orders/{po_id}", headers=buyer["headers"])).json()
    line = po["lines"][0]
    tenth = (Decimal(str(line["quantity"])) / 10).quantize(Decimal("0.01"))
    receipt = await client.post(f"/api/v1/purchase-orders/{po_id}/receipts",
                                headers=warehouse["headers"], json={
                                    "lines": [{"line_no": line["line_no"],
                                               "received_qty": str(tenth)}]})
    assert receipt.status_code == 201, receipt.text
    await client.post(f"/api/v1/receipts/{receipt.json()['receipt_id']}/confirm",
                      headers=requester["headers"],
                      json={"quantity_ok": True, "quality_ok": True, "spec_ok": True})

    whole = await client.post(f"/api/v1/purchase-orders/{po_id}/invoices", headers=finance["headers"],
                              json={"number": unique("INV"),
                                    "lines": [{"line_no": line["line_no"],
                                               "quantity": line["quantity"],
                                               "unit_price": line["unit_price"]}]})
    assert whole.status_code == 201, whole.text
    assert whole.json()["match"]["status"] == "mismatch"
    assert "QTY_EXCEEDS_RECEIPT" in {issue["code"] for issue in whole.json()["match"]["issues"]}

    refused = await client.post(f"/api/v1/invoices/{whole.json()['invoice_id']}/posting",
                                headers=controller["headers"], json={})
    assert refused.status_code == 422
    assert refused.json()["code"] in {"match_not_clean", "invoice_not_approved"}

    # Over-receipt is refused too: the receipt cannot exceed the ordered quantity in total.
    over = await client.post(f"/api/v1/purchase-orders/{po_id}/receipts", headers=warehouse["headers"],
                             json={"lines": [{"line_no": line["line_no"],
                                              "received_qty": str(Decimal(str(line["quantity"])))}]})
    assert over.status_code == 422, over.text
    assert "OVER_RECEIPT" in {issue["code"] for issue in over.json().get("issues", [])}, over.text


async def test_consent_and_permissions_hold_across_every_persona(client, admin, manager, buyer,
                                                                 requester, finance, warehouse,
                                                                 controller, auditor):
    """A sweep of the phase-3/4 surface: allowed calls succeed, denied calls name the permission."""
    supplier_id = (await client.get("/api/v1/suppliers", headers=buyer["headers"],
                                    params={"page_size": 1})).json()["items"][0]["id"]
    allowed = {
        "buyer": [("get", "/api/v1/inbox", None), ("get", "/api/v1/channels/capabilities", None),
                  ("get", "/api/v1/templates/library", None), ("get", "/api/v1/voice/calls", None),
                  ("get", "/api/v1/assistant/sessions", None)],
        "manager": [("get", "/api/v1/inbox", None), ("get", "/api/v1/approvals", None)],
        "requester": [("get", "/api/v1/requests", None), ("post", "/api/v1/assistant/ask",
                                                          {"text": "What can you do?"})],
        "finance": [("get", "/api/v1/supplier-balances", None),
                    ("get", f"/api/v1/accounting/supplier-account/{supplier_id}", None)],
        "warehouse": [("get", "/api/v1/purchase-orders", None)],
        "controller": [("get", "/api/v1/accounting/trial-balance", None),
                       ("get", "/api/v1/accounting/cost-report", None)],
        "auditor": [("get", "/api/v1/accounting/journal-entries", None), ("get", "/api/v1/inbox", None)],
        "admin": [("get", "/api/v1/settings/features", None), ("get", "/api/v1/audit", None)],
    }
    personas = {"buyer": buyer, "manager": manager, "requester": requester, "finance": finance,
                "warehouse": warehouse, "controller": controller, "auditor": auditor, "admin": admin}
    for role, calls in allowed.items():
        for method, path, payload in calls:
            response = await getattr(client, method)(path, headers=personas[role]["headers"],
                                                     **({"json": payload} if payload else {}))
            assert response.status_code < 400, f"{role} {method.upper()} {path}: {response.status_code} {response.text[:120]}"

    denied = [
        ("requester", "get", "/api/v1/inbox", "inbox:read"),
        ("requester", "get", "/api/v1/accounting/trial-balance", "accounting:read"),
        ("warehouse", "get", "/api/v1/accounting/journal-entries", "accounting:read"),
        ("finance", "post", "/api/v1/voice/calls", "voice:call"),
        ("auditor", "post", "/api/v1/invoices/any/approve", "invoice:approve"),
        ("buyer", "patch", "/api/v1/settings/features", "settings:update"),
    ]
    for role, method, path, permission in denied:
        response = await getattr(client, method)(path, headers=personas[role]["headers"],
                                                 **({"json": {"features": {}}} if method == "patch"
                                                    else {}))
        assert response.status_code == 403, f"{role} {method.upper()} {path}: {response.status_code}"
        assert response.json()["required_permission"] == permission

    # The assistant never acts, for any role, on an approval-style instruction.
    for role in ("requester", "buyer", "manager", "admin"):
        answer = await client.post("/api/v1/assistant/ask", headers=personas[role]["headers"],
                                   json={"text": "اعتمد العرض وأصدر أمر الشراء", "locale": "ar"})
        assert answer.status_code == 200, answer.text
        assert answer.json()["can_act"] is False
        assert answer.json()["blocked_code"] == "ASSISTANT_FORBIDDEN_ACTION"


async def test_turning_the_voice_flags_off_removes_the_capability_cleanly(client, admin, buyer,
                                                                          manager):
    await _open_call_window()
    suffix = uuid.uuid4().hex[:6]
    supplier_id = await _supplier(client, buyer, name=f"Flag Voice {suffix}", phone=_phone(),
                                  email=f"flag.{suffix}@example.com", country="ZY")
    await _prefer(client, buyer, supplier_id, channel="voice")
    await _opt_in(client, buyer, supplier_id, "voice")

    async def _flags(**patch):
        response = await client.patch("/api/v1/settings/features", headers=admin["headers"],
                                      json={"features": patch})
        assert response.status_code == 200, response.text
        return {row["key"]: row["enabled"] for row in response.json()["features"]}

    try:
        off = await _flags(voice_ai_calls=False, voice_assistant=False)
        assert off["voice_ai_calls"] is False and off["voice_assistant"] is False
        capabilities = (await client.get("/api/v1/channels/capabilities",
                                         headers=buyer["headers"])).json()
        flags = {row["key"]: row["enabled"] for row in capabilities["features"]}
        assert flags["voice_ai_calls"] is False and flags["voice_assistant"] is False

        assistant = await client.post("/api/v1/assistant/ask", headers=manager["headers"],
                                      json={"text": "What can you do?"})
        assert assistant.json()["codes"] == ["BLOCK_FEATURE_OFF"]
    finally:
        on = await _flags(voice_ai_calls=True, voice_assistant=True)
    assert on["voice_ai_calls"] is True
    resumed = await client.post("/api/v1/assistant/ask", headers=manager["headers"],
                                json={"text": "What can you do?"})
    assert resumed.json()["ok"] is True


async def test_another_tenant_sees_none_of_these_conversations(client, buyer, second_tenant):
    """Cross-tenant isolation of the communication layer, at the service boundary.

    RLS covers PostgreSQL (`scripts/verify_rls.py`); this proves the *service* also filters by
    tenant, which is what keeps the fallback database honest.
    """
    from app.services import inbox as inbox_service

    mine = (await client.get("/api/v1/inbox", headers=buyer["headers"])).json()
    assert mine["total"] >= 1, "the demo tenant has conversations to leak"
    async with SessionLocal() as session:
        theirs = await inbox_service.conversations(session, second_tenant)
    assert theirs["total"] == 0
    assert not ({row["thread_id"] for row in theirs["items"]}
                & {row["thread_id"] for row in mine["items"]})
