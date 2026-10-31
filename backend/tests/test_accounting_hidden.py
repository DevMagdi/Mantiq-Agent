"""Accounting is invisible — and harmless — when `ACCOUNTING_ENABLED=false`.

This is the pilot rule expressed as tests. With the switch off, a deployment must not expose the
module at all: no route, no OpenAPI path, no permission that could drive a menu, no journal drafted on
a receipt/invoice/payment, no chart of accounts seeded, no auditor package assembled, and no lifecycle
stage that promises a review nobody can perform.

The four proofs here are the ones that would catch a regression:

1. every accounting route answers exactly what an unknown route answers (404 + the same body), and
   none of them appears in the OpenAPI schema;
2. the effective permission set contains no accounting permission, for every demo role;
3. the *whole* purchase-to-pay flow still works with the switch off — through payment — while the
   accounting tables gain no rows, and the PO's lifecycle ends at "confirmed by requester";
4. a brand-new tenant bootstrapped with the switch off gets no chart of accounts at all.

The tests build their own app (`create_app(accounting_enabled=False)`) and flip the setting, because
the module boundary is decided when the router is assembled — exactly what a pilot deployment does.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from conftest import DEMO_PASSWORD, unique
from sqlalchemy import func, select

from app.core.config import settings
from app.db.models.entities import (
    AuditPackage,
    CostCenter,
    GLAccount,
    JournalEntry,
    Tenant,
)
from app.db.session import SessionLocal
from app.main import create_app
from app.services.bootstrap import ensure_tenant

ACCOUNTING_ROUTES = [
    ("get", "/api/v1/accounting/accounts"),
    ("get", "/api/v1/accounting/cost-centers"),
    ("get", "/api/v1/accounting/journal-entries"),
    ("get", "/api/v1/accounting/trial-balance"),
    ("get", "/api/v1/accounting/cost-report"),
    ("get", "/api/v1/accounting/coverage"),
    ("get", "/api/v1/accounting/supplier-account/{id}"),
    ("get", "/api/v1/supplier-balances"),
    ("get", "/api/v1/suppliers/{id}/balance"),
    ("get", "/api/v1/purchase-orders/{id}/accounting"),
    ("post", "/api/v1/purchase-orders/{id}/accounting/accrual"),
    ("get", "/api/v1/purchase-orders/{id}/audit-package"),
    ("get", "/api/v1/purchase-orders/{id}/lifecycle"),
    ("post", "/api/v1/invoices/{id}/posting"),
    ("post", "/api/v1/invoices/{id}/payment-posting"),
]


@pytest.fixture
async def hidden_app(monkeypatch):
    """A pilot instance: no accounting router, and every service sees the switch as off."""
    monkeypatch.setattr(settings, "accounting_enabled", False, raising=True)
    application = create_app(accounting_enabled=False)
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def _login(client: httpx.AsyncClient, email: str) -> dict:
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": DEMO_PASSWORD})
    assert response.status_code == 200, response.text
    data = response.json()
    return {"token": data["access_token"], "user": data["user"],
            "headers": {"authorization": f"Bearer {data['access_token']}"}}


async def _counts() -> dict[str, int]:
    async with SessionLocal() as session:
        return {
            "accounts": int((await session.execute(
                select(func.count()).select_from(GLAccount))).scalar_one()),
            "centres": int((await session.execute(
                select(func.count()).select_from(CostCenter))).scalar_one()),
            "journal": int((await session.execute(
                select(func.count()).select_from(JournalEntry))).scalar_one()),
            "packages": int((await session.execute(
                select(func.count()).select_from(AuditPackage))).scalar_one()),
        }


# ---------------------------------------------------------------- 1. the API surface is gone
async def test_every_accounting_route_answers_like_an_unknown_route(hidden_app, buyer):
    unknown = await hidden_app.get("/api/v1/this-endpoint-does-not-exist", headers=buyer["headers"])
    assert unknown.status_code == 404
    for verb, template in ACCOUNTING_ROUTES:
        path = template.replace("{id}", "does-not-matter")
        response = await getattr(hidden_app, verb)(path, headers=buyer["headers"])
        assert response.status_code == 404, f"{verb.upper()} {path} → {response.status_code}"
        # Identical to an unknown URL: no `BLOCK_FEATURE_OFF`, no permission hint, nothing to probe.
        assert response.json() == unknown.json(), f"{verb.upper()} {path} leaked {response.json()}"


async def test_the_openapi_schema_does_not_mention_accounting():
    schema = create_app(accounting_enabled=False).openapi()
    offenders = sorted(
        path for path in schema["paths"]
        if "accounting" in path or "audit-package" in path or "lifecycle" in path
        or path.endswith("/balance") or path.endswith("/supplier-balances")
    )
    assert offenders == []
    # …and the same build with the switch on still documents the whole module.
    enabled = create_app(accounting_enabled=True).openapi()
    assert "/api/v1/accounting/accounts" in enabled["paths"]


# ---------------------------------------------------------------- 2. no capability to hang a menu on
async def test_no_role_receives_an_accounting_permission(hidden_app):
    for email in ("admin@pilot.procureai", "controller@nile.example.com", "finance@nile.example.com",
                  "buyer@nile.example.com", "auditor@nile.example.com"):
        persona = await _login(hidden_app, email)
        me = await hidden_app.get("/api/v1/auth/me", headers=persona["headers"])
        assert me.status_code == 200, me.text
        permissions = set(me.json()["permissions"])
        leaked = sorted(p for p in permissions if p in {
            "accounting:read", "accounting:write", "accounting:post", "accounting:reverse",
            "cost:read", "cost:write", "ledger:read", "audit_package:read", "audit_package:review",
        })
        assert leaked == [], f"{email} still holds {leaked}"
        # The rest of the role still works: this is a module switch, not a lockdown.
        assert {"po:read", "rfq:read"} & permissions


# ---------------------------------------------------------------- 3. the flow still works, quietly
async def test_the_whole_cycle_works_and_writes_no_accounting_rows(hidden_app):
    before = await _counts()
    requester = await _login(hidden_app, "requester@nile.example.com")
    manager = await _login(hidden_app, "manager@nile.example.com")
    buyer = await _login(hidden_app, "buyer@nile.example.com")
    warehouse = await _login(hidden_app, "storekeeper@nile.example.com")
    finance = await _login(hidden_app, "finance@nile.example.com")
    auditor = await _login(hidden_app, "auditor@nile.example.com")
    admin = await _login(hidden_app, "admin@pilot.procureai")

    # 1. request → submit
    created = await hidden_app.post("/api/v1/intake/text", headers=requester["headers"], json={
        "text": "Need 12 t rebar 16mm for the Sadat plant, delivery within 3 weeks, "
                "target price 21000 EGP per ton.",
        "title": unique("Pilot rebar"),
    })
    assert created.status_code in {200, 201}, created.text
    pr_id = created.json()["id"]
    detail_by_id = await hidden_app.get(f"/api/v1/requests/{pr_id}", headers=requester["headers"])
    for question in detail_by_id.json().get("clarifications", []):
        if question["status"] == "open":
            await hidden_app.post(f"/api/v1/requests/{pr_id}/clarifications/{question['id']}/answer",
                                  headers=requester["headers"],
                                  json={"answer": "ASTM A615 grade 60", "waive": False})
    submitted = await hidden_app.post(f"/api/v1/requests/{pr_id}/submit", headers=requester["headers"])
    assert submitted.status_code in {200, 201}, submitted.text
    routed = await hidden_app.post(f"/api/v1/requests/{pr_id}/assign", headers=manager["headers"],
                                   json={"department": "Maintenance"})
    assert routed.status_code == 200, routed.text

    # 2. RFQ → dispatch → replies → comparison
    suppliers = (await hidden_app.get("/api/v1/suppliers", headers=buyer["headers"],
                                      params={"page_size": 5, "status": "approved"})).json()["items"]
    rfq = await hidden_app.post("/api/v1/rfqs", headers=buyer["headers"], json={
        "pr_id": pr_id, "supplier_ids": [row["id"] for row in suppliers[:2]],
        "deadline_days": 10, "mode": "semi_auto", "incoterm": "DAP", "payment_terms": "Net 30",
    })
    assert rfq.status_code in {200, 201}, rfq.text
    rfq_id = rfq.json()["id"]
    assert (await hidden_app.post(f"/api/v1/rfqs/{rfq_id}/dispatch", headers=manager["headers"],
                                  json={})).status_code == 200
    detail = (await hidden_app.get(f"/api/v1/rfqs/{rfq_id}", headers=buyer["headers"])).json()
    for index, link in enumerate(detail["suppliers"][:2]):
        reply = await hidden_app.post(f"/api/v1/rfqs/{rfq_id}/simulate-reply", headers=buyer["headers"],
                                      json={"supplier_id": link["supplier_id"], "kind": "text",
                                            "unit_price": str(20000 + index * 500),
                                            "lead_time_days": 14 + index})
        assert reply.status_code in {200, 201}, reply.text
    matrix = (await hidden_app.get(f"/api/v1/rfqs/{rfq_id}/comparison", headers=buyer["headers"])).json()
    best = min(matrix["columns"], key=lambda column: Decimal(str(column["tco_total"])))

    # 3. approval chain (no accounting permission in the loop)
    requested = await hidden_app.post(f"/api/v1/rfqs/{rfq_id}/request-approval",
                                      headers=manager["headers"],
                                      json={"offer_ids": [best["offer_id"]], "amount": best["tco_total"]})
    assert requested.status_code in {200, 201}, requested.text
    persona_by_role = {"procurement_manager": manager, "finance_approver": finance, "auditor": auditor,
                       "admin": admin}
    for _ in range(8):
        decided = False
        for role, persona in persona_by_role.items():
            inbox = await hidden_app.get("/api/v1/approvals", headers=persona["headers"],
                                         params={"status": "pending"})
            pending = [row for row in inbox.json()["items"]
                       if row["rfq_id"] == rfq_id and row["approver_role"] == role]
            if not pending:
                continue
            response = await hidden_app.post(f"/api/v1/approvals/{pending[0]['id']}/decide",
                                             headers=persona["headers"],
                                             json={"decision": "approve", "comment": "Within budget"})
            assert response.status_code == 200, response.text
            decided = True
            break
        if not decided:
            break

    # 4. the award issues the PO
    orders = (await hidden_app.get("/api/v1/purchase-orders", headers=buyer["headers"],
                                   params={"page_size": 50})).json()["items"]
    po_id = [row["id"] for row in orders if row.get("rfq_id") == rfq_id][0]
    po = (await hidden_app.get(f"/api/v1/purchase-orders/{po_id}", headers=buyer["headers"])).json()
    line = po["lines"][0]
    quantity = Decimal(str(line["quantity"]))

    # 5. receipt → requester confirmation. This is where the auditor package used to be assembled.
    receipt = await hidden_app.post(f"/api/v1/purchase-orders/{po_id}/receipts",
                                    headers=warehouse["headers"],
                                    json={"lines": [{"line_no": line["line_no"],
                                                     "received_qty": str(quantity),
                                                     "accepted_qty": str(quantity)}],
                                          "warehouse": "Cairo stores",
                                          "delivery_note_ref": unique("DN")})
    assert receipt.status_code == 201, receipt.text
    receipt_id = receipt.json()["receipt_id"]
    confirmed = await hidden_app.post(f"/api/v1/receipts/{receipt_id}/confirm",
                                      headers=requester["headers"],
                                      json={"quantity_ok": True, "quality_ok": True, "spec_ok": True,
                                            "comment": "Full lot received."})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"

    # 6. invoice → three-way match → human approval → payment recorded
    invoice = await hidden_app.post(f"/api/v1/purchase-orders/{po_id}/invoices",
                                    headers=finance["headers"],
                                    json={"number": unique("INV"),
                                          "lines": [{"line_no": line["line_no"], "quantity": str(quantity),
                                                     "unit_price": line["unit_price"]}],
                                          "tax_amount": str((quantity * Decimal(str(line["unit_price"]))
                                                             * Decimal("0.14")).quantize(Decimal("0.01"))),
                                          "due_date": (datetime.now(UTC) + timedelta(days=30)).isoformat()})
    assert invoice.status_code == 201, invoice.text
    body = invoice.json()
    assert body["match"]["status"] == "matched", body["match"]["issues"]
    approved = await hidden_app.post(f"/api/v1/invoices/{body['invoice_id']}/approve",
                                     headers=finance["headers"],
                                     json={"comment": "Matches the confirmed receipt"})
    assert approved.status_code == 200, approved.text
    invoice_detail = (await hidden_app.get(f"/api/v1/purchase-orders/{po_id}/invoices",
                                           headers=finance["headers"])).json()["items"]
    total = next(row["total"] for row in invoice_detail if row["id"] == body["invoice_id"])
    payment = await hidden_app.post(f"/api/v1/invoices/{body['invoice_id']}/payments",
                                    headers=finance["headers"],
                                    json={"amount": total, "method": "bank_transfer",
                                          "reference": unique("TRX")})
    assert payment.status_code in {200, 201}, payment.text

    # 7. nothing landed in the books, and no auditor package exists
    after = await _counts()
    assert after == before, f"accounting rows were written with the module off: {before} → {after}"
    async with SessionLocal() as session:
        packages = int((await session.execute(
            select(func.count()).select_from(AuditPackage)
            .where(AuditPackage.po_id == po_id))).scalar_one())
    assert packages == 0, "the auditor package must not be assembled when accounting is off"


# ---------------------------------------------------------------- 4. no seeding, no lifecycle tail
async def test_a_new_tenant_is_bootstrapped_without_a_chart_of_accounts(hidden_app):
    async with SessionLocal() as session:
        tenant = await ensure_tenant(session, name="Pilot Works", slug=unique("pilot-co").lower())
        await session.commit()
        accounts = int((await session.execute(
            select(func.count()).select_from(GLAccount).where(GLAccount.tenant_id == tenant.id)
        )).scalar_one())
        centres = int((await session.execute(
            select(func.count()).select_from(CostCenter).where(CostCenter.tenant_id == tenant.id)
        )).scalar_one())
    assert accounts == 0 and centres == 0


async def test_the_lifecycle_ends_at_the_last_non_accounting_stage(hidden_app):
    async with SessionLocal() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
        from app.services import p2p

        visible = [row[0] for row in p2p.stages()]
        assert "ready_for_audit" not in visible and "posted_to_accounts" not in visible
        assert visible[-1] == "confirmed_by_requester"
        # With the switch on the rail is the full eleven stages again (no deletion, just a view).
        settings.accounting_enabled = True
        try:
            assert len([row[0] for row in p2p.stages()]) == 11
        finally:
            settings.accounting_enabled = False
        assert tenant.id
