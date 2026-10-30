"""HTTP surface of the accounting module: the books, the analyst dimensions and the audit gate.

The point of these tests is the *permission boundary* as much as the maths: procurement must be able
to look at the books, a controller must be the only one who posts, and an auditor must be able to
review evidence without ever being able to move money.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from conftest import unique
from sqlalchemy import select

from app.db.models.entities import RFQ, PurchaseOrder, Supplier, Tenant
from app.db.session import SessionLocal


async def _seed_po() -> str:
    async with SessionLocal() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
        rfq = (await session.execute(
            select(RFQ).where(RFQ.tenant_id == tenant.id).order_by(RFQ.created_at)
        )).scalars().first()
        supplier = (await session.execute(
            select(Supplier).where(Supplier.tenant_id == tenant.id).order_by(Supplier.created_at)
        )).scalars().first()
        po = PurchaseOrder(
            tenant_id=tenant.id, code=unique("PO-ACC"), rfq_id=rfq.id, supplier_id=supplier.id,
            amount=Decimal("3000"), currency="EGP", status="issued", issued_at=datetime.now(UTC),
            lines=[{"description": "Bearing 6205", "quantity": "100", "uom": "pcs",
                    "unit_price": "30.00", "line_total": "3000.00"}],
        )
        session.add(po)
        await session.commit()
        return po.id


# ------------------------------------------------------------------ read access
async def test_every_purchasing_role_can_read_the_chart_but_not_the_warehouse(client, controller, buyer,
                                                                             requester, warehouse, auditor):
    accounts = await client.get("/api/v1/accounting/accounts", headers=controller["headers"])
    assert accounts.status_code == 200, accounts.text
    codes = {row["code"] for row in accounts.json()["items"]}
    assert {"110100", "110200", "210100"} <= codes

    # Procurement and audit see the chart; the requesting department and stores do not.
    for persona in (buyer, auditor):
        response = await client.get("/api/v1/accounting/accounts", headers=persona["headers"])
        assert response.status_code == 200, f"{persona['user']['email']} should read the chart"

    for persona in (warehouse, requester):
        denied = await client.get("/api/v1/accounting/accounts", headers=persona["headers"])
        assert denied.status_code == 403
        assert denied.json()["required_permission"] == "accounting:read"


async def test_the_journal_surface_is_closed_to_requesters_and_stores(client, requester, warehouse,
                                                                     controller, buyer):
    for persona in (requester, warehouse):
        listed = await client.get("/api/v1/accounting/journal-entries", headers=persona["headers"])
        assert listed.status_code == 403, f"{persona['user']['email']} must not read the journal"
    # A buyer may follow the postings of their own orders; posting stays with the controller.
    assert (await client.get("/api/v1/accounting/journal-entries",
                             headers=buyer["headers"])).status_code == 200
    listed = await client.get("/api/v1/accounting/journal-entries", headers=controller["headers"])
    assert listed.status_code == 200
    assert {"items", "total", "posted", "posted_total"} <= set(listed.json())


async def test_the_trial_balance_is_always_balanced(client, controller):
    response = await client.get("/api/v1/accounting/trial-balance", headers=controller["headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["balanced"] is True
    assert body["total_debit"] == body["total_credit"]


async def test_coverage_tells_the_ui_whether_the_books_are_ready(client, controller):
    response = await client.get("/api/v1/accounting/coverage", headers=controller["headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready"] is True
    assert body["missing_control_accounts"] == []
    assert body["accounts"] >= 11 and body["cost_centers"] >= 1


# ------------------------------------------------------------------ master data
async def test_a_controller_can_add_an_account_and_a_cost_centre(client, controller, auditor):
    code = unique("9")[:6]
    created = await client.post("/api/v1/accounting/accounts", headers=controller["headers"], json={
        "code": code, "name": "Contract labour clearing", "name_ar": "تسوية العمالة المؤقتة",
        "kind": "liability",
    })
    assert created.status_code == 201, created.text
    assert created.json()["kind"] == "liability"

    duplicate = await client.post("/api/v1/accounting/accounts", headers=controller["headers"], json={
        "code": code, "name": "Again", "kind": "liability"})
    assert duplicate.status_code == 422
    assert duplicate.json()["code"] == "duplicate_account"

    centre = await client.post("/api/v1/accounting/cost-centers", headers=controller["headers"], json={
        "code": "CC-SADAT", "name": "Sadat plant", "name_ar": "مصنع السادات"})
    assert centre.status_code == 201, centre.text
    listed = await client.get("/api/v1/accounting/cost-centers", headers=controller["headers"])
    assert any(row["code"] == "CC-SADAT" for row in listed.json()["items"])

    # The auditor reads master data and must not change it.
    denied = await client.post("/api/v1/accounting/accounts", headers=auditor["headers"], json={
        "code": unique("8")[:6], "name": "Nope", "kind": "asset"})
    assert denied.status_code == 403
    assert denied.json()["required_permission"] == "accounting:write"


async def test_an_account_with_posted_activity_cannot_be_deactivated(client, controller, admin):
    created = await client.post("/api/v1/accounting/accounts", headers=controller["headers"], json={
        "code": unique("7")[:6], "name": "Suspense", "kind": "asset"})
    code = created.json()["code"]
    draft = await client.post("/api/v1/accounting/journal-entries", headers=controller["headers"], json={
        "memo": "Suspense movement", "lines": [
            {"account_code": code, "debit": "100.00"},
            {"account_code": "300100", "credit": "100.00"},
        ]})
    assert draft.status_code == 201, draft.text
    entry_id = draft.json()["id"]
    posted = await client.post(f"/api/v1/accounting/journal-entries/{entry_id}/post", headers=admin["headers"])
    assert posted.status_code == 200, posted.text
    blocked = await client.patch(f"/api/v1/accounting/accounts/{code}", headers=controller["headers"],
                                 json={"is_active": False})
    assert blocked.status_code == 422
    assert blocked.json()["code"] == "account_in_use"


# ------------------------------------------------------------------ journal etiquette
async def test_an_unbalanced_manual_entry_is_rejected_with_a_reason(client, controller):
    response = await client.post("/api/v1/accounting/journal-entries", headers=controller["headers"], json={
        "memo": "Half entry", "lines": [
            {"account_code": "100100", "debit": "500"},
            {"account_code": "300100", "credit": "400"},
        ]})
    assert response.status_code == 422
    assert response.json()["code"] == "unbalanced"
    assert "500" in response.json()["detail"] and "400" in response.json()["detail"]


async def test_a_manual_entry_needs_a_second_person_to_post(client, controller, admin):
    draft = await client.post("/api/v1/accounting/journal-entries", headers=controller["headers"], json={
        "memo": "Fuel correction", "memo_ar": "تصحيح وقود",
        "lines": [{"account_code": "100100", "debit": "250"},
                  {"account_code": "300100", "credit": "250"}]})
    assert draft.status_code == 201, draft.text
    entry_id = draft.json()["id"]
    assert draft.json()["status"] == "draft"
    assert draft.json()["balanced"] is True

    self_post = await client.post(f"/api/v1/accounting/journal-entries/{entry_id}/post",
                                  headers=controller["headers"])
    assert self_post.status_code == 422
    assert self_post.json()["code"] == "segregation_of_duties"

    posted = await client.post(f"/api/v1/accounting/journal-entries/{entry_id}/post", headers=admin["headers"])
    assert posted.status_code == 200, posted.text
    assert posted.json()["status"] == "posted"
    assert posted.json()["entry"]["posted_at"]

    reversed_ = await client.post(f"/api/v1/accounting/journal-entries/{entry_id}/reverse",
                                  headers=controller["headers"], json={"reason": "Posted twice by mistake"})
    assert reversed_.status_code == 200, reversed_.text
    assert reversed_.json()["status"] == "reversed"
    detail = await client.get(f"/api/v1/accounting/journal-entries/{entry_id}", headers=controller["headers"])
    assert detail.json()["reversed_by"]


async def test_a_reversal_requires_a_written_reason(client, controller, admin):
    draft = await client.post("/api/v1/accounting/journal-entries", headers=controller["headers"], json={
        "memo": "Seed", "lines": [{"account_code": "100100", "debit": "10"},
                                  {"account_code": "300100", "credit": "10"}]})
    entry_id = draft.json()["id"]
    await client.post(f"/api/v1/accounting/journal-entries/{entry_id}/post", headers=admin["headers"])
    blank = await client.post(f"/api/v1/accounting/journal-entries/{entry_id}/reverse",
                              headers=controller["headers"], json={"reason": " "})
    assert blank.status_code == 422


# ------------------------------------------------------------------ documents -> postings
async def test_the_po_accounting_view_lists_entries_and_cost_by_centre(client, controller, manager):
    po_id = await _seed_po()
    response = await client.get(f"/api/v1/purchase-orders/{po_id}/accounting", headers=controller["headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["po_id"] == po_id
    assert body["entries"] == [] and body["posted_count"] == 0
    assert body["cost_by_center"]["items"] == []
    assert any(row["code"] == "110100" for row in body["accounts"])

    # Procurement can see the same panel; the warehouse cannot.
    assert (await client.get(f"/api/v1/purchase-orders/{po_id}/accounting",
                             headers=manager["headers"])).status_code == 200


async def test_posting_an_invoice_before_the_audit_review_is_refused(client, controller, manager, finance,
                                                                     warehouse, requester):
    from datetime import timedelta

    po_id = await _seed_po()
    detail = (await client.get(f"/api/v1/purchase-orders/{po_id}", headers=manager["headers"])).json()
    line = detail["lines"][0]
    receipt = await client.post(f"/api/v1/purchase-orders/{po_id}/receipts", headers=warehouse["headers"],
                                json={"lines": [{"line_no": 1, "received_qty": line["quantity"]}]})
    assert receipt.status_code == 201, receipt.text
    await client.post(f"/api/v1/receipts/{receipt.json()['receipt_id']}/confirm", headers=requester["headers"],
                      json={"quantity_ok": True, "quality_ok": True, "spec_ok": True})
    invoice = await client.post(f"/api/v1/purchase-orders/{po_id}/invoices", headers=finance["headers"],
                                json={"number": unique("INV"), "lines": [
                                    {"line_no": 1, "quantity": line["quantity"],
                                     "unit_price": line["unit_price"]}],
                                    "due_date": (datetime.now(UTC) + timedelta(days=30)).isoformat()})
    assert invoice.status_code == 201, invoice.text
    invoice_id = invoice.json()["invoice_id"]
    await client.post(f"/api/v1/invoices/{invoice_id}/approve", headers=finance["headers"], json={})

    refused = await client.post(f"/api/v1/invoices/{invoice_id}/posting", headers=controller["headers"],
                                json={})
    assert refused.status_code == 422
    assert refused.json()["code"] == "audit_review_required"


# ------------------------------------------------------------------ audit package routes
async def test_audit_package_endpoints_are_gated(client, controller, auditor, requester, warehouse):
    po_id = await _seed_po()
    for persona in (requester, warehouse):
        denied = await client.get(f"/api/v1/purchase-orders/{po_id}/audit-package",
                                  headers=persona["headers"])
        assert denied.status_code == 403
    allowed = await client.get(f"/api/v1/purchase-orders/{po_id}/audit-package", headers=auditor["headers"])
    assert allowed.status_code == 200
    assert allowed.json()["package"] is None
    assert allowed.json()["status"] == "not_assembled"

    # Nothing to assemble yet: the package needs a confirmed receipt.
    blocked = await client.post(f"/api/v1/purchase-orders/{po_id}/audit-package", headers=auditor["headers"],
                                json={})
    assert blocked.status_code == 422
    assert blocked.json()["code"] == "no_confirmed_receipt"


async def test_only_an_auditor_can_record_the_review_decision(client, controller, auditor):
    response = await client.post("/api/v1/audit-packages/does-not-exist/review", headers=controller["headers"],
                                 json={"decision": "accepted"})
    assert response.status_code == 403
    assert response.json()["required_permission"] == "audit_package:review"
    missing = await client.post("/api/v1/audit-packages/does-not-exist/review", headers=auditor["headers"],
                                json={"decision": "accepted"})
    assert missing.status_code == 404


async def test_cost_report_validates_its_grouping(client, controller, warehouse):
    response = await client.get("/api/v1/accounting/cost-report", headers=controller["headers"],
                                params={"group_by": "colour"})
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_group_by"
    ok = await client.get("/api/v1/accounting/cost-report", headers=controller["headers"],
                          params={"group_by": "cost_center"})
    assert ok.status_code == 200 and ok.json()["group_by"] == "cost_center"
    denied = await client.get("/api/v1/accounting/cost-report", headers=warehouse["headers"])
    assert denied.status_code == 403


async def test_the_supplier_sub_ledger_is_closed_to_stores(client, controller, warehouse):
    po_id = await _seed_po()
    po = (await client.get(f"/api/v1/purchase-orders/{po_id}", headers=controller["headers"])).json()
    supplier_id = po["supplier_id"]
    allowed = await client.get(f"/api/v1/accounting/supplier-account/{supplier_id}",
                               headers=controller["headers"])
    assert allowed.status_code == 200, allowed.text
    assert {"payable", "movements", "ledger_entries"} <= set(allowed.json())
    denied = await client.get(f"/api/v1/accounting/supplier-account/{supplier_id}",
                              headers=warehouse["headers"])
    assert denied.status_code == 403
