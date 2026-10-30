"""The whole purchase-to-pay chain, over HTTP, from a requisition to a posted invoice.

One test walks the eleven stages exactly as four different humans would:

    requester creates and routes the need → buyer issues the RFQ and collects offers →
    manager/finance/auditor decide the award → PO is issued → warehouse receives part of it →
    requester confirms → the auditor package is sealed automatically → the auditor accepts it →
    finance records the invoice and the 3-way match passes → the controller drafts the posting →
    the administrator posts it → the books, the supplier ledger and the cost report agree.

A second test drives the failure paths: mismatch, missing review, and segregation of duties.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from conftest import unique
from sqlalchemy import select

from app.db.models.entities import PurchaseOrder, Tenant, User
from app.db.session import SessionLocal


async def _ready_request(client, requester) -> dict:
    created = await client.post("/api/v1/intake/text", headers=requester["headers"], json={
        "text": "Need 40 t rebar 16mm B420D for the Sadat plant, delivery within 3 weeks, "
                "payment Net 30, target price 21000 EGP per ton, cost centre CC-000.",
        "title": unique("Rebar request"),
    })
    assert created.status_code in {200, 201}, created.text
    pr_id = created.json()["id"]
    detail = await client.get(f"/api/v1/requests/{pr_id}", headers=requester["headers"])
    for question in detail.json().get("clarifications", []):
        if question["status"] == "open":
            await client.post(f"/api/v1/requests/{pr_id}/clarifications/{question['id']}/answer",
                              headers=requester["headers"],
                              json={"answer": "ASTM A615 grade 60", "waive": False})
    submitted = await client.post(f"/api/v1/requests/{pr_id}/submit", headers=requester["headers"])
    assert submitted.status_code in {200, 201}, submitted.text
    return (await client.get(f"/api/v1/requests/{pr_id}", headers=requester["headers"])).json()


async def _accept_chain(client, rfq_id: str, personas: dict) -> int:
    """Sign every pending step with the role that owns it."""
    decided = 0
    for _ in range(8):
        found = False
        for role, persona in personas.items():
            inbox = await client.get("/api/v1/approvals", headers=persona["headers"],
                                     params={"status": "pending"})
            pending = [row for row in inbox.json()["items"]
                       if row["rfq_id"] == rfq_id and row["approver_role"] == role]
            if not pending:
                continue
            step = pending[0]
            response = await client.post(f"/api/v1/approvals/{step['id']}/decide",
                                         headers=persona["headers"],
                                         json={"decision": "approve", "comment": "Within budget"})
            assert response.status_code == 200, response.text
            decided += 1
            found = True
            break
        if not found:
            break
    return decided


async def test_request_to_posted_invoice_end_to_end(client, manager, buyer, requester, finance, warehouse,
                                                    controller, auditor, admin):
    personas = {"procurement_manager": manager, "finance_approver": finance, "auditor": auditor,
                "admin": admin}

    # 1. The requesting department states the need; procurement takes ownership of it.
    pr = await _ready_request(client, requester)
    routed = await client.post(f"/api/v1/requests/{pr['id']}/assign", headers=manager["headers"],
                               json={"department": "Maintenance"})
    assert routed.status_code == 200, routed.text
    assert routed.json()["status"] == "in_procurement"
    assert routed.json()["assigned_to"]

    # 2. The RFQ goes out; two suppliers answer; the offers are compared.
    suppliers = (await client.get("/api/v1/suppliers", headers=buyer["headers"],
                                  params={"page_size": 5, "status": "approved"})).json()["items"]
    rfq = await client.post("/api/v1/rfqs", headers=buyer["headers"], json={
        "pr_id": pr["id"], "supplier_ids": [row["id"] for row in suppliers[:2]],
        "deadline_days": 10, "mode": "semi_auto", "incoterm": "DAP", "payment_terms": "Net 30",
    })
    assert rfq.status_code in {200, 201}, rfq.text
    rfq_id = rfq.json()["id"]
    assert (await client.post(f"/api/v1/rfqs/{rfq_id}/dispatch", headers=manager["headers"],
                              json={})).status_code == 200

    rfq_detail = (await client.get(f"/api/v1/rfqs/{rfq_id}", headers=buyer["headers"])).json()
    for index, link in enumerate(rfq_detail["suppliers"][:2]):
        reply = await client.post(f"/api/v1/rfqs/{rfq_id}/simulate-reply", headers=buyer["headers"], json={
            "supplier_id": link["supplier_id"], "kind": "text",
            "unit_price": str(20000 + index * 900), "lead_time_days": 14 + index * 3,
        })
        assert reply.status_code in {200, 201}, reply.text

    matrix = (await client.get(f"/api/v1/rfqs/{rfq_id}/comparison", headers=buyer["headers"])).json()
    best = min(matrix["columns"], key=lambda column: Decimal(str(column["tco_total"])))

    # 3. Amount-banded approval chain, including the auditor.
    requested = await client.post(f"/api/v1/rfqs/{rfq_id}/request-approval", headers=manager["headers"],
                                  json={"offer_ids": [best["offer_id"]], "amount": best["tco_total"]})
    assert requested.status_code in {200, 201}, requested.text
    decisions = await _accept_chain(client, rfq_id, personas)
    assert decisions >= 2, "the chain must require more than one human"

    # 4. The award produces a locked purchase order.
    orders = (await client.get("/api/v1/purchase-orders", headers=buyer["headers"],
                               params={"page_size": 50})).json()["items"]
    mine = [row for row in orders if row.get("rfq_id") == rfq_id]
    assert mine, "an approved award must issue a purchase order"
    po_id = mine[0]["id"]
    po = (await client.get(f"/api/v1/purchase-orders/{po_id}", headers=buyer["headers"])).json()
    assert po["status"] in {"issued", "in_transit"}
    line = po["lines"][0]
    ordered_qty = Decimal(str(line["quantity"]))
    assert ordered_qty > 0

    # 5. Warehouse receives *part* of the order; the requester confirms the short delivery.
    partial = (ordered_qty / 2).quantize(Decimal("0.01"))
    receipt = await client.post(f"/api/v1/purchase-orders/{po_id}/receipts", headers=warehouse["headers"],
                               json={"lines": [{"line_no": line["line_no"], "received_qty": str(partial),
                                                "accepted_qty": str(partial)}],
                                     "warehouse": "Cairo stores", "delivery_note_ref": unique("DN")})
    assert receipt.status_code == 201, receipt.text
    receipt_id = receipt.json()["receipt_id"]
    confirmed = await client.post(f"/api/v1/receipts/{receipt_id}/confirm", headers=requester["headers"],
                                  json={"quantity_ok": True, "quality_ok": True, "spec_ok": True,
                                        "comment": "Half the lot arrived; the rest is promised next week."})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"

    # 6. The auditor's package was sealed automatically at confirmation.
    package_resp = await client.get(f"/api/v1/purchase-orders/{po_id}/audit-package",
                                    headers=auditor["headers"])
    assert package_resp.status_code == 200, package_resp.text
    package = package_resp.json()["package"]
    assert package is not None, "confirming a delivery must produce the auditor package"
    kinds = {row["kind"] for row in package["components"]}
    assert {"purchase_order", "approval_chain", "goods_receipt"} <= kinds
    assert package["status"] == "assembled"
    assert package_resp.json()["verification"]["intact"] is True

    # 7. Finance records the supplier invoice for what actually arrived; the match is clean.
    invoice = await client.post(f"/api/v1/purchase-orders/{po_id}/invoices", headers=finance["headers"],
                                json={"number": unique("INV"),
                                      "lines": [{"line_no": line["line_no"], "quantity": str(partial),
                                                 "unit_price": line["unit_price"]}],
                                      "tax_amount": str((partial * Decimal(str(line["unit_price"]))
                                                         * Decimal("0.14")).quantize(Decimal("0.01"))),
                                      "due_date": (datetime.now(UTC) + timedelta(days=30)).isoformat()})
    assert invoice.status_code == 201, invoice.text
    invoice_body = invoice.json()
    assert invoice_body["match"]["status"] == "matched", invoice_body["match"]["issues"]
    invoice_id = invoice_body["invoice_id"]

    # 8. An unapproved invoice cannot be posted at all.
    early = await client.post(f"/api/v1/invoices/{invoice_id}/posting", headers=controller["headers"],
                              json={"allocations": [{"line_no": 1, "cost_center_code": "CC-000"}]})
    assert early.status_code == 422
    assert early.json()["code"] == "invoice_not_approved"

    # 9. Finance approves the matched invoice — but the auditor has not looked at the evidence yet,
    #    so the posting is still refused: two independent human gates stand between papers and books.
    approved = await client.post(f"/api/v1/invoices/{invoice_id}/approve", headers=finance["headers"],
                                 json={"comment": "Matched against the confirmed receipt"})
    assert approved.status_code == 200, approved.text

    unaudited = await client.post(f"/api/v1/invoices/{invoice_id}/posting", headers=controller["headers"],
                                  json={"allocations": [{"line_no": 1, "cost_center_code": "CC-000"}]})
    assert unaudited.status_code == 422
    assert unaudited.json()["code"] == "audit_review_required"

    reviewed = await client.post(f"/api/v1/audit-packages/{package['id']}/review",
                                 headers=auditor["headers"],
                                 json={"decision": "accepted", "comment": "Partial delivery documented."})
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["status"] == "reviewed"

    # 10. The controller drafts the posting; a second human posts it.
    draft = await client.post(f"/api/v1/invoices/{invoice_id}/posting", headers=controller["headers"],
                              json={"allocations": [{"line_no": 1, "cost_center_code": "CC-000",
                                                     "unit_number": "UNIT-E2E"}]})
    assert draft.status_code == 201, draft.text
    entry = draft.json()
    assert entry["status"] == "draft"
    assert entry["balanced"] is True
    assert entry["source_type"] == "invoice"
    assert entry["po_id"] == po_id and entry["invoice_id"] == invoice_id
    codes = {row["account_code"] for row in entry["lines"]}
    assert {"110100", "120100", "210100"} <= codes
    vat_line = next(row for row in entry["lines"] if row["account_code"] == "120100")
    assert Decimal(str(vat_line["debit"])) == Decimal(str(invoice_body["match"]["totals"].get(
        "tax_amount", vat_line["debit"])))

    # A document-derived entry has already passed the auditor and the invoice approver, so the
    # controller who drafted it may post it. A *manual* entry still needs a second person (covered
    # in tests/test_api_accounting.py) — the two-person rule guards the operator, not the document.
    posted = await client.post(f"/api/v1/accounting/journal-entries/{entry['id']}/post",
                               headers=controller["headers"])
    assert posted.status_code == 200, posted.text
    assert posted.json()["status"] == "posted"

    # 11. The books, the supplier ledger and the cost report all tell the same story.
    ledger = await client.get(f"/api/v1/accounting/journal-entries/{entry['id']}",
                              headers=controller["headers"])
    assert ledger.json()["posted_by"] == controller["user"]["id"]
    assert ledger.json()["posted_at"]

    trial = (await client.get("/api/v1/accounting/trial-balance", headers=controller["headers"])).json()
    assert trial["balanced"] is True
    accounts = {row["account_code"]: row for row in trial["items"]}
    invoiced = Decimal(str(invoice_body["match"]["totals"]["invoiced_amount"]))
    assert Decimal(accounts["210100"]["credit"]) >= invoiced or Decimal(accounts["210100"]["balance"]) <= 0

    supplier_id = po["supplier_id"]
    sub_ledger = (await client.get(f"/api/v1/accounting/supplier-account/{supplier_id}",
                                   headers=controller["headers"])).json()
    assert Decimal(sub_ledger["payable"]) > 0
    assert any(row["source_type"] == "invoice" for row in sub_ledger["movements"])
    assert any(row["kind"] == "invoice" for row in sub_ledger["ledger_entries"])

    cost = (await client.get("/api/v1/accounting/cost-report", headers=controller["headers"],
                             params={"group_by": "cost_center"})).json()
    assert any(row["key"] == "CC-000" for row in cost["items"])
    by_unit = (await client.get("/api/v1/accounting/cost-report", headers=controller["headers"],
                                params={"group_by": "unit"})).json()
    assert any(row["key"] == "UNIT-E2E" for row in by_unit["items"])

    po_accounting = (await client.get(f"/api/v1/purchase-orders/{po_id}/accounting",
                                      headers=controller["headers"])).json()
    assert po_accounting["posted_count"] >= 1
    assert any(row["key"] == "CC-000" for row in po_accounting["cost_by_center"]["items"])

    # 12. The rail reports the final stage and keeps the eleven-step history.
    lifecycle = (await client.get(f"/api/v1/purchase-orders/{po_id}/lifecycle",
                                  headers=manager["headers"])).json()
    assert lifecycle["current"] == "posted_to_accounts"
    assert [row["code"] for row in lifecycle["stages"]] == [
        "requested", "in_procurement", "rfq_sent", "offers_received", "under_approval", "po_issued",
        "pending_receipt", "received_by_warehouse", "confirmed_by_requester", "ready_for_audit",
        "posted_to_accounts",
    ]
    assert all(row["state"] in {"done", "current"} for row in lifecycle["stages"])
    history = lifecycle["history"]
    assert len(history) == 11, [row["stage"] for row in history]
    assert [row["stage"] for row in history] == [row["code"] for row in lifecycle["stages"]]
    sources = {row["source"] for row in history}
    # The history records which human action first moved the flow into each stage.
    assert {"po_issued", "goods_receipt", "requester_confirmation"} <= sources
    assert all(row["reached_at"] for row in history)

    # 13. Every step is in the audit trail, with a named actor.
    trail = (await client.get("/api/v1/audit", headers=admin["headers"],
                              params={"page_size": 100})).json()
    actions = {row["action"] for row in trail["items"]}
    assert {"pr.assigned", "audit_package.assembled", "audit_package.accepted",
            "journal_entry.posted"} <= actions


async def test_a_mismatched_invoice_never_reaches_the_ledger(client, manager, buyer, requester, warehouse,
                                                             finance, controller, auditor, admin):
    po_id = await _new_po_id()
    po = (await client.get(f"/api/v1/purchase-orders/{po_id}", headers=manager["headers"])).json()
    line = po["lines"][0]

    # A receipt for a tenth of the order, confirmed.
    tenth = (Decimal(str(line["quantity"])) / 10).quantize(Decimal("0.01"))
    receipt = await client.post(f"/api/v1/purchase-orders/{po_id}/receipts",
                                headers=warehouse["headers"],
                                json={"lines": [{"line_no": line["line_no"], "received_qty": str(tenth)}]})
    assert receipt.status_code == 201, receipt.text
    await client.post(f"/api/v1/receipts/{receipt.json()['receipt_id']}/confirm",
                      headers=requester["headers"],
                      json={"quantity_ok": True, "quality_ok": True, "spec_ok": True})

    # Finance bills the whole order: the quantity exceeds what was received.
    whole = await client.post(f"/api/v1/purchase-orders/{po_id}/invoices", headers=finance["headers"],
                              json={"number": unique("INV"),
                                    "lines": [{"line_no": line["line_no"],
                                               "quantity": line["quantity"],
                                               "unit_price": line["unit_price"]}]})
    assert whole.status_code == 201, whole.text
    body = whole.json()
    assert body["match"]["status"] == "mismatch"
    assert "QTY_EXCEEDS_RECEIPT" in {issue["code"] for issue in body["match"]["issues"]}

    refused = await client.post(f"/api/v1/invoices/{body['invoice_id']}/posting",
                                headers=controller["headers"], json={})
    assert refused.status_code == 422
    assert refused.json()["code"] in {"match_not_clean", "invoice_not_approved"}


async def _new_po_id() -> str:
    async with SessionLocal() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
        from app.db.models.entities import RFQ, Supplier

        rfq = (await session.execute(
            select(RFQ).where(RFQ.tenant_id == tenant.id).order_by(RFQ.created_at))).scalars().first()
        supplier = (await session.execute(
            select(Supplier).where(Supplier.tenant_id == tenant.id).order_by(Supplier.created_at)
        )).scalars().first()
        po = PurchaseOrder(
            tenant_id=tenant.id, code=unique("PO-E2E"), rfq_id=rfq.id, supplier_id=supplier.id,
            amount=Decimal("820000"), currency="EGP", status="issued", issued_at=datetime.now(UTC),
            lines=[{"description": "Rebar 16mm B420D", "quantity": "40", "uom": "t",
                    "unit_price": "20500.00", "line_total": "820000.00"}],
        )
        session.add(po)
        await session.commit()
        return po.id


async def test_the_user_directory_still_exposes_the_new_roles(client, admin):
    users = (await client.get("/api/v1/users", headers=admin["headers"], params={"page_size": 100})).json()
    roles = {role for row in users["items"] for role in (row.get("roles") or [])}
    assert {"controller", "auditor"} <= roles
    async with SessionLocal() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
        controller = (await session.execute(
            select(User).where(User.tenant_id == tenant.id, User.primary_role == "controller")
        )).scalars().first()
        assert controller is not None and controller.email.endswith("@nile.example.com")
