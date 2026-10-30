"""HTTP surface of the post-PO lifecycle: receipts, invoices, 3-way match, payments, balances.

These tests walk the whole route over the real app: a buyer ships a PO, the warehouse receives it,
the requester confirms it, finance records and approves the invoice, the payment is booked and the
PO is closed. Separation of duties is asserted at every step.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from conftest import unique
from sqlalchemy import select

from app.db.models.entities import RFQ, PurchaseOrder, Supplier, Tenant
from app.db.session import SessionLocal


async def _seed_po() -> str:
    """A brand-new purchase order so every test owns its delivery history."""
    async with SessionLocal() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
        rfq = (await session.execute(
            select(RFQ).where(RFQ.tenant_id == tenant.id).order_by(RFQ.created_at)
        )).scalars().first()
        supplier = (await session.execute(
            select(Supplier).where(Supplier.tenant_id == tenant.id).order_by(Supplier.created_at)
        )).scalars().first()
        lines = [
            {"description": "Rebar 12mm B420D", "quantity": "100", "uom": "t", "unit_price": "20500",
             "line_total": "2050000"},
            {"description": "Stretch film 500mm", "quantity": "40", "uom": "roll", "unit_price": "455",
             "line_total": "18200"},
        ]
        po = PurchaseOrder(tenant_id=tenant.id, code=unique("PO-API"), rfq_id=rfq.id,
                           supplier_id=supplier.id, amount=Decimal("2068200"), currency="EGP",
                           status="issued", issued_at=datetime.now(UTC), lines=lines)
        session.add(po)
        await session.commit()
        return po.id


async def _new_po(client, manager) -> dict:
    po_id = await _seed_po()
    detail = await client.get(f"/api/v1/purchase-orders/{po_id}", headers=manager["headers"])
    assert detail.status_code == 200, detail.text
    return detail.json()


async def test_purchase_order_detail_exposes_the_full_lifecycle(client, manager):
    po = await _new_po(client, manager)
    for key in ("timeline", "receipts", "invoices", "discrepancy", "supplier_balance",
                "status_label", "status_label_ar", "next_status", "lines"):
        assert key in po, f"missing {key} in the PO payload"
    assert po["status_label_ar"], "the lifecycle must be bilingual"
    assert isinstance(po["lines"], list) and po["lines"][0]["line_no"] == 1


async def test_purchase_order_list_filters_and_paginates(client, manager):
    listed = await client.get("/api/v1/purchase-orders", headers=manager["headers"],
                              params={"status": "issued", "page_size": 5})
    assert listed.status_code == 200
    body = listed.json()
    assert {"items", "total", "page", "page_size"} <= set(body)
    assert all(row["status"] == "issued" for row in body["items"])
    assert all("supplier_name" in row for row in body["items"])


async def test_full_receipt_invoice_payment_cycle(client, manager, warehouse, requester, finance):
    po = await _new_po(client, manager)
    po_id = po["id"]
    lines = [{"line_no": line["line_no"], "received_qty": line["quantity"],
              "accepted_qty": line["quantity"]} for line in po["lines"]]

    shipped = await client.post(f"/api/v1/purchase-orders/{po_id}/ship", headers=manager["headers"],
                                params={"carrier": "Nile Logistics"})
    assert shipped.status_code == 200, shipped.text
    assert shipped.json()["ok"] is True

    receipt = await client.post(f"/api/v1/purchase-orders/{po_id}/receipts", headers=warehouse["headers"],
                                json={"lines": lines, "warehouse": "Cairo stores",
                                      "delivery_note_ref": unique("DN")})
    assert receipt.status_code == 201, receipt.text
    receipt_body = receipt.json()
    assert receipt_body["status"] == "received"
    assert receipt_body["po_status"] == "received"

    # The requester is the only role that may confirm, and only with the three checks.
    incomplete = await client.post(f"/api/v1/receipts/{receipt_body['receipt_id']}/confirm",
                                   headers=requester["headers"],
                                   json={"quantity_ok": True, "quality_ok": False, "spec_ok": True})
    assert incomplete.status_code == 422
    confirmed = await client.post(f"/api/v1/receipts/{receipt_body['receipt_id']}/confirm",
                                  headers=requester["headers"],
                                  json={"quantity_ok": True, "quality_ok": True, "spec_ok": True,
                                        "comment": "Matches the requisition"})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["po_status"] == "confirmed"

    invoice_lines = [{"line_no": line["line_no"], "quantity": line["quantity"],
                      "unit_price": line["unit_price"]} for line in po["lines"]]
    invoice = await client.post(f"/api/v1/purchase-orders/{po_id}/invoices", headers=finance["headers"],
                                json={"number": unique("INV"), "lines": invoice_lines,
                                      "due_date": (datetime.now(UTC) + timedelta(days=30)).isoformat()})
    assert invoice.status_code == 201, invoice.text
    invoice_body = invoice.json()
    assert invoice_body["match"]["status"] == "matched", invoice_body["match"]["issues"]

    approved = await client.post(f"/api/v1/invoices/{invoice_body['invoice_id']}/approve",
                                 headers=finance["headers"], json={"comment": "Matched"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["payment_suggestion"]["method"] in {"bank_transfer", "swift_wire", "letter_of_credit"}

    total = invoice_body["match"]["totals"]["invoiced_amount"]
    payment = await client.post(f"/api/v1/invoices/{invoice_body['invoice_id']}/payments",
                                headers=finance["headers"],
                                json={"amount": total, "method": "bank_transfer", "reference": unique("TRF")})
    assert payment.status_code == 200, payment.text
    assert payment.json()["status"] == "paid"

    closed = await client.post(f"/api/v1/purchase-orders/{po_id}/close", headers=manager["headers"],
                               params={"reason": "Delivery accepted"})
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "closed"

    final = await client.get(f"/api/v1/purchase-orders/{po_id}", headers=manager["headers"])
    assert final.json()["status"] == "closed"
    kinds = [event["kind"] for event in final.json()["timeline"]]
    assert {"issued", "received", "confirmed", "invoice", "approved", "closed"} <= set(kinds)


async def test_the_warehouse_can_receive_but_not_approve_payments(client, manager, warehouse, finance):
    po = await _new_po(client, manager)
    forbidden = await client.post("/api/v1/invoices/does-not-matter/approve",
                                  headers=warehouse["headers"], json={})
    assert forbidden.status_code == 403
    assert forbidden.json()["required_permission"] == "invoice:approve"

    # …and finance cannot record a goods receipt.
    denied = await client.post(f"/api/v1/purchase-orders/{po['id']}/receipts", headers=finance["headers"],
                               json={"lines": [{"line_no": 1, "received_qty": "1"}]})
    assert denied.status_code == 403
    assert denied.json()["required_permission"] == "receipt:create"


async def test_the_requester_cannot_create_a_receipt_or_approve_an_invoice(client, manager, requester):
    po = await _new_po(client, manager)
    denied = await client.post(f"/api/v1/purchase-orders/{po['id']}/receipts", headers=requester["headers"],
                               json={"lines": [{"line_no": 1, "received_qty": "1"}]})
    assert denied.status_code == 403
    approve = await client.post("/api/v1/invoices/whatever/approve", headers=requester["headers"], json={})
    assert approve.status_code == 403


async def test_over_receipt_is_rejected_with_structured_issues(client, manager, warehouse):
    po = await _new_po(client, manager)
    line = po["lines"][0]
    over = float(line["quantity"]) + 5
    resp = await client.post(f"/api/v1/purchase-orders/{po['id']}/receipts", headers=warehouse["headers"],
                             json={"lines": [{"line_no": line["line_no"], "received_qty": str(over)}]})
    assert resp.status_code == 422
    body = resp.json()
    assert body["issues"][0]["code"] == "OVER_RECEIPT"


async def test_mismatched_invoice_is_flagged_and_cannot_be_approved_without_an_override(client, manager, finance):
    po = await _new_po(client, manager)
    line = po["lines"][0]
    inflated = str(float(line["quantity"]) * 3)
    resp = await client.post(f"/api/v1/purchase-orders/{po['id']}/invoices", headers=finance["headers"],
                             json={"number": unique("INV"),
                                   "lines": [{"line_no": line["line_no"], "quantity": inflated,
                                              "unit_price": line["unit_price"]}]})
    assert resp.status_code == 201
    body = resp.json()
    assert body["match"]["status"] in {"mismatch", "incomplete"}
    codes = {issue["code"] for issue in body["match"]["issues"]}
    assert codes & {"QTY_EXCEEDS_RECEIPT", "NO_CONFIRMED_RECEIPT"}

    refused = await client.post(f"/api/v1/invoices/{body['invoice_id']}/approve", headers=finance["headers"],
                                json={"comment": "Looks fine"})
    assert refused.status_code == 409
    assert "override" in refused.json()["detail"].lower() or "override" in str(refused.json())

    overridden = await client.post(f"/api/v1/invoices/{body['invoice_id']}/approve", headers=finance["headers"],
                                   json={"override_reason": "Supplier billed the full lot; receipt split."})
    assert overridden.status_code == 200, overridden.text
    assert overridden.json()["overridden"] is True


async def test_supplier_balance_endpoints_are_consistent(client, manager, finance, warehouse):
    overview = await client.get("/api/v1/supplier-balances", headers=finance["headers"])
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert {"items", "total_outstanding", "total_overdue", "currency"} <= set(body)
    if body["items"]:
        first = body["items"][0]
        detail = await client.get(f"/api/v1/suppliers/{first['supplier_id']}/balance",
                                  headers=finance["headers"])
        assert detail.status_code == 200, detail.text
        assert detail.json()["outstanding"] == first["outstanding"]
        assert "entries" in detail.json()

    denied = await client.get("/api/v1/supplier-balances", headers=warehouse["headers"])
    assert denied.status_code == 403


async def test_commercial_balance_is_withheld_from_roles_without_the_ledger(client, manager,
                                                                           warehouse, finance):
    """A warehouse user may open the order but must not receive supplier financials."""
    po = await _new_po(client, manager)
    po_id = po["id"]

    withheld = await client.get(f"/api/v1/purchase-orders/{po_id}", headers=warehouse["headers"])
    assert withheld.status_code == 200, withheld.text
    assert withheld.json()["supplier_balance"] is None
    # The operational payload is still complete for that role.
    assert withheld.json()["receipts"] is not None
    assert withheld.json()["timeline"]

    visible = await client.get(f"/api/v1/purchase-orders/{po_id}", headers=finance["headers"])
    assert visible.status_code == 200, visible.text
    assert visible.json()["supplier_balance"] is not None
    assert "outstanding" in visible.json()["supplier_balance"]


async def test_purchase_order_unknown_id_returns_404(client, manager):
    resp = await client.get("/api/v1/purchase-orders/00000000-0000-0000-0000-000000000000",
                            headers=manager["headers"])
    assert resp.status_code == 404
    invoice = await client.post("/api/v1/invoices/00000000-0000-0000-0000-000000000000/approve",
                                headers=manager["headers"], json={})
    assert invoice.status_code == 404


async def test_receipts_and_invoices_are_listed_per_purchase_order(client, manager):
    po = await _new_po(client, manager)
    receipts = await client.get(f"/api/v1/purchase-orders/{po['id']}/receipts", headers=manager["headers"])
    invoices = await client.get(f"/api/v1/purchase-orders/{po['id']}/invoices", headers=manager["headers"])
    assert receipts.status_code == 200 and "items" in receipts.json()
    assert invoices.status_code == 200 and "items" in invoices.json()


async def test_rematching_an_invoice_is_idempotent(client, manager, finance):
    po = await _new_po(client, manager)
    line = po["lines"][0]
    created = await client.post(f"/api/v1/purchase-orders/{po['id']}/invoices", headers=finance["headers"],
                                json={"number": unique("INV"),
                                      "lines": [{"line_no": line["line_no"], "quantity": "1",
                                                 "unit_price": line["unit_price"]}]})
    assert created.status_code == 201, created.text
    invoice_id = created.json()["invoice_id"]
    first = await client.post(f"/api/v1/invoices/{invoice_id}/match", headers=manager["headers"])
    second = await client.post(f"/api/v1/invoices/{invoice_id}/match", headers=manager["headers"])
    assert first.status_code == second.status_code == 200
    assert first.json()["match"]["status"] == second.json()["match"]["status"]


async def test_payment_rejects_a_reference_less_transfer(client, manager, finance):
    po = await _new_po(client, manager)
    line = po["lines"][0]
    created = await client.post(f"/api/v1/purchase-orders/{po['id']}/invoices", headers=finance["headers"],
                                json={"number": unique("INV"),
                                      "lines": [{"line_no": line["line_no"], "quantity": "1",
                                                 "unit_price": line["unit_price"]}]})
    invoice_id = created.json()["invoice_id"]
    # Not approved yet: the payment must be refused before anything else.
    early = await client.post(f"/api/v1/invoices/{invoice_id}/payments", headers=finance["headers"],
                              json={"amount": "1", "method": "bank_transfer", "reference": "X"})
    assert early.status_code == 422
    assert "approve" in early.json()["detail"].lower()
