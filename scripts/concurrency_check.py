#!/usr/bin/env python
"""Race-condition proof: fire the same dangerous request many times at once, then check the books.

The load test proves the platform stays *fast* under 50 users. This script proves it stays *correct*
when several people press the button that must only work once:

    A. goods receipt      — eight warehouses each receive half the order: at most two may succeed
                            (100 ordered, 50 each), and the total received may never exceed 100;
    B. journal posting    — six identical "post" calls on one draft: exactly one may win, and the
                            entry must have exactly one set of journal lines;
    C. supplier payment   — five identical "record payment" calls for the outstanding amount: exactly
                            one may win, and `paid_amount` may never exceed the invoice total;
    D. approval decision  — five identical approvals of one step: exactly one may win.

Every scenario seeds its own rows, fires the burst through the real HTTP API (so the real service,
session and transaction boundaries are exercised) and then reads the database to check the
invariant. Run it against PostgreSQL — SQLite has no row locks, so the locks themselves are a no-op
there:

    DATABASE_URL=postgresql+asyncpg://... SECRET_KEY=... \
        python scripts/concurrency_check.py --base-url http://localhost:8000

Exit code 0 means every invariant held.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import func, select

from app.db.models.entities import (
    GLAccount,
    Approval,
    JournalEntry,
    JournalLine,
    PurchaseOrder,
    RFQ,
    Supplier,
    SupplierInvoice,
    Tenant,
)
from app.db.session import SessionLocal

DEMO_PASSWORD = "Demo!Pilot2026"
PERSONAS = {
    "admin": "admin@pilot.procureai",
    "manager": "manager@nile.example.com",
    "warehouse": "storekeeper@nile.example.com",
    "finance": "finance@nile.example.com",
    "controller": "controller@nile.example.com",
    "buyer": "buyer@nile.example.com",
}

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name} — {detail}")


async def login(client: httpx.AsyncClient) -> dict[str, dict[str, str]]:
    headers: dict[str, dict[str, str]] = {}
    for role, email in PERSONAS.items():
        response = await client.post("/api/v1/auth/login",
                                     json={"email": email, "password": DEMO_PASSWORD})
        if response.status_code != 200:
            raise SystemExit(f"login failed for {email}: HTTP {response.status_code}")
        headers[role] = {"authorization": f"Bearer {response.json()['access_token']}"}
    return headers


async def _tenant_context():
    async with SessionLocal() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
        rfq = (await session.execute(
            select(RFQ).where(RFQ.tenant_id == tenant.id).order_by(RFQ.created_at)
        )).scalars().first()
        supplier = (await session.execute(
            select(Supplier).where(Supplier.tenant_id == tenant.id).order_by(Supplier.created_at)
        )).scalars().first()
        return tenant, rfq, supplier


async def _seed_po(quantity: str = "100", unit_price: str = "1000") -> PurchaseOrder:
    tenant, rfq, supplier = await _tenant_context()
    async with SessionLocal() as session:
        po = PurchaseOrder(
            tenant_id=tenant.id, code=f"PO-RACE-{uuid.uuid4().hex[:6]}", rfq_id=rfq.id,
            supplier_id=supplier.id, amount=Decimal(quantity) * Decimal(unit_price),
            currency="EGP", status="issued", issued_at=datetime.now(UTC),
            lines=[{"description": "Race-condition test item", "quantity": quantity, "uom": "t",
                    "unit_price": unit_price,
                    "line_total": str(Decimal(quantity) * Decimal(unit_price))}],
        )
        session.add(po)
        await session.commit()
        return po


async def scenario_receipt(client, headers) -> None:
    """A: two half-deliveries fit, eight simultaneous ones may not."""
    po = await _seed_po("100")
    half = "50"
    body = {"lines": [{"line_no": 1, "received_qty": half, "accepted_qty": half}],
            "warehouse": "Race stores"}

    async def post(index: int) -> int:
        response = await client.post(f"/api/v1/purchase-orders/{po.id}/receipts",
                                     headers=headers["warehouse"], json=body)
        return response.status_code

    statuses = await asyncio.gather(*(post(index) for index in range(8)))
    accepted = sum(1 for code in statuses if code == 201)
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(func.count()).select_from(PurchaseOrder).where(PurchaseOrder.id == po.id)
        )).scalar_one()
        assert rows == 1
        from app.services import receiving

        receipts = await receiving.list_receipts(session, po.tenant_id, po.id)
        received = sum(
            (Decimal(str(line.get("accepted_qty") or "0"))
             for receipt in receipts for line in (receipt.lines or [])), Decimal("0"))
    record("goods receipt: no cumulative over-delivery",
           accepted == 2 and received <= Decimal("100"),
           f"{accepted}/8 receipts accepted, {received} of 100 received "
           f"(statuses {sorted(set(statuses))})")


async def scenario_journal_posting(client, headers) -> None:
    """B: one draft, six posters — exactly one posting, one set of lines."""
    tenant, rfq, supplier = await _tenant_context()
    async with SessionLocal() as session:
        accounts = {row.code: row for row in (await session.execute(
            select(GLAccount).where(GLAccount.tenant_id == tenant.id))).scalars().all()}
        assert {"510100", "210100"} <= set(accounts), sorted(accounts)[:6]
        entry = JournalEntry(
            tenant_id=tenant.id, number=f"JE-RACE-{uuid.uuid4().hex[:6]}",
            entry_date=datetime.now(UTC), memo="Concurrency probe", status="draft",
            source_type="manual", currency="EGP", total_debit=Decimal("100"),
            total_credit=Decimal("100"),
            lines=[{"line_no": 1, "account_code": "510100", "description": "probe",
                    "debit": "100", "credit": "0"},
                   {"line_no": 2, "account_code": "210100", "description": "probe",
                    "debit": "0", "credit": "100"}],
        )
        session.add(entry)
        await session.commit()
        entry_id = entry.id

    async def post(_index: int) -> int:
        response = await client.post(f"/api/v1/accounting/journal-entries/{entry_id}/post",
                                     headers=headers["admin"])
        return response.status_code

    statuses = await asyncio.gather(*(post(index) for index in range(6)))
    successes = sum(1 for code in statuses if code == 200)
    async with SessionLocal() as session:
        line_count = (await session.execute(
            select(func.count()).select_from(JournalLine).where(JournalLine.entry_id == entry_id)
        )).scalar_one()
        stored = (await session.execute(
            select(JournalEntry).where(JournalEntry.id == entry_id))).scalar_one()
    record("journal posting: exactly one post, no duplicated lines",
           successes == 1 and line_count == 2 and stored.status == "posted",
           f"{successes}/6 posts accepted, {line_count} journal lines, status {stored.status}")


async def scenario_payment_recording(client, headers) -> None:
    """C: five payments for the outstanding amount — one wins, the invoice is never overpaid."""
    po = await _seed_po("10", "1000")
    tenant, _rfq, _supplier = await _tenant_context()
    async with SessionLocal() as session:
        invoice = SupplierInvoice(
            tenant_id=tenant.id, number=f"INV-RACE-{uuid.uuid4().hex[:6]}", po_id=po.id,
            supplier_id=po.supplier_id, status="approved", currency="EGP",
            subtotal=Decimal("10000"), tax_amount=Decimal("0"), total=Decimal("10000"),
            paid_amount=Decimal("0"), due_date=datetime.now(UTC) + timedelta(days=30),
            lines=[{"line_no": 1, "description": "Race item", "quantity": "10",
                    "unit_price": "1000", "line_total": "10000"}],
            match_result={"status": "matched", "issues": []},
        )
        session.add(invoice)
        await session.commit()
        invoice_id = invoice.id

    body = {"amount": "10000", "method": "bank_transfer", "reference": f"RACE-{uuid.uuid4().hex[:6]}"}

    async def pay(_index: int) -> int:
        response = await client.post(f"/api/v1/invoices/{invoice_id}/payments",
                                     headers=headers["finance"], json=body)
        return response.status_code

    statuses = await asyncio.gather(*(pay(index) for index in range(5)))
    successes = sum(1 for code in statuses if code in {200, 201})
    async with SessionLocal() as session:
        stored = (await session.execute(
            select(SupplierInvoice).where(SupplierInvoice.id == invoice_id))).scalar_one()
    record("supplier payment: one payment recorded, no overpayment",
           successes == 1 and Decimal(str(stored.paid_amount)) <= Decimal("10000"),
           f"{successes}/5 payments accepted, paid {stored.paid_amount} of 10000")


async def scenario_approval_decision(client, headers) -> None:
    """D: five approvals of the same step — one decision, not five."""
    tenant, rfq, _supplier = await _tenant_context()
    async with SessionLocal() as session:
        approval = Approval(
            tenant_id=tenant.id, rfq_id=rfq.id, approver_role="procurement_manager",
            status="pending", step_no=1, amount=Decimal("15000"), currency="EGP",
        )
        session.add(approval)
        await session.commit()
        approval_id = approval.id

    async def decide(_index: int) -> int:
        response = await client.post(f"/api/v1/approvals/{approval_id}/decide",
                                     headers=headers["manager"],
                                     json={"decision": "approve", "comment": "race"})
        return response.status_code

    statuses = await asyncio.gather(*(decide(index) for index in range(5)))
    successes = sum(1 for code in statuses if code == 200)
    async with SessionLocal() as session:
        stored = (await session.execute(
            select(Approval).where(Approval.id == approval_id))).scalar_one()
    record("approval decision: exactly one decision wins",
           successes == 1 and stored.status == "approved",
           f"{successes}/5 decisions accepted, final status {stored.status}")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Race-condition proof for ProcureAI")
    parser.add_argument("--base-url", default="http://localhost:8000")
    args = parser.parse_args()
    if "sqlite" in os.environ.get("DATABASE_URL", ""):
        print("note  this proof needs PostgreSQL: SQLite has no row locks")

    limits = httpx.Limits(max_connections=32, max_keepalive_connections=32)
    async with httpx.AsyncClient(base_url=args.base_url, timeout=httpx.Timeout(60.0),
                                 limits=limits) as client:
        headers = await login(client)
        await scenario_receipt(client, headers)
        await scenario_journal_posting(client, headers)
        await scenario_payment_recording(client, headers)
        await scenario_approval_decision(client, headers)

    failures = [name for name, ok, _detail in results if not ok]
    print()
    if failures:
        print(f"{len(failures)} invariant(s) broken: {', '.join(failures)}")
        return 1
    print("All concurrency invariants held.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
