"""Three-way match, payment suggestion and supplier ledger (FR-PO-5, FR-BIL-4).

The contract under test: the match is arithmetic over audited rows, a human approves, the platform
never pays, and the ledger is the single source of truth for what is still owed to a supplier.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from conftest import unique
from sqlalchemy import select

from app.agents import tools as tool_registry
from app.db.models.entities import (
    RFQ,
    GoodsReceipt,
    PurchaseOrder,
    Supplier,
    SupplierInvoice,
    SupplierLedgerEntry,
    Tenant,
    User,
)
from app.db.session import SessionLocal
from app.services import invoicing, receiving

PO_PRICE = Decimal("20500")
FILM_PRICE = Decimal("455")


async def _tenant(session) -> Tenant:
    return (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()


async def _users(session, tenant) -> dict[str, User]:
    rows = (await session.execute(select(User).where(User.tenant_id == tenant.id))).scalars().all()
    return {user.email.split("@")[0]: user for user in rows}


async def _po(session, tenant, *, lines: list[dict] | None = None) -> PurchaseOrder:
    rfq = (await session.execute(
        select(RFQ).where(RFQ.tenant_id == tenant.id).order_by(RFQ.created_at)
    )).scalars().first()
    supplier = (await session.execute(
        select(Supplier).where(Supplier.tenant_id == tenant.id).order_by(Supplier.created_at)
    )).scalars().first()
    rows = lines or [
        {"description": "Rebar 12mm B420D", "quantity": "100", "uom": "t", "unit_price": str(PO_PRICE),
         "line_total": str(PO_PRICE * 100), "rfq_line_id": None},
        {"description": "Stretch film 500mm", "quantity": "40", "uom": "roll", "unit_price": str(FILM_PRICE),
         "line_total": str(FILM_PRICE * 40), "rfq_line_id": None},
    ]
    po = PurchaseOrder(tenant_id=tenant.id, code=unique("PO-T"), rfq_id=rfq.id, supplier_id=supplier.id,
                       amount=sum((Decimal(row["line_total"]) for row in rows), Decimal("0")),
                       currency="EGP", status="issued", issued_at=datetime.now(UTC), lines=rows)
    session.add(po)
    await session.flush()
    return po


async def _receive(session, tenant, po: PurchaseOrder, *, ratio: str = "1", confirm: bool = True,
                   requester_id: str | None = None) -> GoodsReceipt:
    users = await _users(session, tenant)
    created = await receiving.create_receipt(
        session, tenant, po, received_by=users["buyer"].id,
        lines=[{"line_no": row["line_no"], "received_qty": str(row["quantity"] * Decimal(ratio)),
                "accepted_qty": str(row["quantity"] * Decimal(ratio))}
               for row in receiving.po_lines(po)],
    )
    receipt = await session.get(GoodsReceipt, created["receipt_id"])
    if confirm:
        await receiving.confirm_receipt(
            session, tenant, receipt, confirmed_by=requester_id or users["requester"].id,
            checks={"quantity_ok": True, "quality_ok": True, "spec_ok": True}, comment="ok")
    return receipt


def _invoice_lines(po: PurchaseOrder, *, ratio: str = "1", price: Decimal | None = None,
                   line_no: int | None = None) -> list[dict]:
    rows = []
    for row in receiving.po_lines(po):
        if line_no is not None and row["line_no"] != line_no:
            continue
        quantity = row["quantity"] * Decimal(ratio)
        unit = price if price is not None else row["unit_price"]
        rows.append({"line_no": row["line_no"], "description": row["description"], "uom": row["uom"],
                     "quantity": str(quantity), "unit_price": str(unit)})
    return rows


# ------------------------------------------------------------------ matching
async def test_invoice_cannot_match_without_a_confirmed_receipt():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po = await _po(session, tenant)
        result = await invoicing.create_invoice(session, tenant, po, number=unique("INV"),
                                               lines=_invoice_lines(po), received_by=None)
        assert result["ok"] is True
        assert result["match"]["status"] == "incomplete"
        assert result["match"]["can_approve"] is False
        assert any(issue["code"] == "NO_CONFIRMED_RECEIPT" for issue in result["match"]["issues"])
        assert result["status"] == "mismatched", "an unmatched invoice must never sit in 'matched'"
        await session.rollback()


async def test_an_unconfirmed_receipt_never_unlocks_the_match():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po = await _po(session, tenant)
        await _receive(session, tenant, po, confirm=False)
        result = await invoicing.create_invoice(session, tenant, po, number=unique("INV"),
                                               lines=_invoice_lines(po), received_by=None)
        assert result["match"]["status"] == "incomplete"
        assert result["match"]["can_approve"] is False
        await session.rollback()


async def test_clean_match_requires_equal_quantities_and_prices():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po = await _po(session, tenant)
        await _receive(session, tenant, po)
        result = await invoicing.create_invoice(session, tenant, po, number=unique("INV"),
                                               lines=_invoice_lines(po), received_by=None)
        match = result["match"]
        assert match["status"] == "matched", match["issues"]
        assert match["can_approve"] is True
        assert match["issues"] == []
        assert all(row["status"] == "ok" for row in match["lines"])
        await session.rollback()


async def test_over_invoicing_against_a_short_receipt_is_flagged_per_line():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po = await _po(session, tenant)
        await _receive(session, tenant, po, ratio="0.5")
        result = await invoicing.create_invoice(session, tenant, po, number=unique("INV"),
                                               lines=_invoice_lines(po), received_by=None)
        match = result["match"]
        assert match["status"] == "mismatch"
        codes = {issue["code"] for issue in match["issues"]}
        assert "QTY_EXCEEDS_RECEIPT" in codes
        line = next(row for row in match["lines"] if row["line_no"] == 1)
        assert line["status"] == "mismatch"
        assert Decimal(line["available_qty"]) < Decimal(line["invoiced_qty"])
        await session.rollback()


async def test_price_variance_inside_and_outside_the_tenant_tolerance():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po = await _po(session, tenant)
        await _receive(session, tenant, po)

        lines_inside = _invoice_lines(po)
        lines_inside[0]["unit_price"] = str(PO_PRICE * Decimal("1.002"))
        inside = await invoicing.create_invoice(session, tenant, po, number=unique("INV"),
                                               lines=lines_inside, received_by=None)
        assert inside["match"]["status"] == "matched", inside["match"]["issues"]

        lines_outside = _invoice_lines(po)
        lines_outside[0]["unit_price"] = str(PO_PRICE * Decimal("1.05"))
        outside = await invoicing.create_invoice(session, tenant, po, number=unique("INV"),
                                                lines=lines_outside, received_by=None)
        assert outside["match"]["status"] == "mismatch"
        assert any(issue["code"] == "PRICE_VARIANCE" for issue in outside["match"]["issues"])
        await session.rollback()


async def test_partial_receipt_supports_partial_invoicing_then_the_balance():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po = await _po(session, tenant)
        await _receive(session, tenant, po, ratio="0.4")

        first = await invoicing.create_invoice(session, tenant, po, number=unique("INV"),
                                              lines=_invoice_lines(po, ratio="0.4"), received_by=None)
        assert first["match"]["status"] == "matched"

        await _receive(session, tenant, po, ratio="0.6")
        second = await invoicing.create_invoice(session, tenant, po, number=unique("INV"),
                                                lines=_invoice_lines(po, ratio="0.6"), received_by=None)
        assert second["match"]["status"] == "matched", second["match"]["issues"]

        # A third invoice for the same quantity would double-bill the tenant.
        third = await invoicing.create_invoice(session, tenant, po, number=unique("INV"),
                                               lines=_invoice_lines(po, ratio="0.1"), received_by=None)
        assert third["match"]["status"] == "mismatch"
        await session.rollback()


async def test_currency_subtotal_and_total_consistency_are_checked():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po = await _po(session, tenant)
        await _receive(session, tenant, po)

        currency = await invoicing.create_invoice(session, tenant, po, number=unique("INV"), currency="USD",
                                                  lines=_invoice_lines(po), received_by=None)
        assert any(issue["code"] == "CURRENCY_MISMATCH" for issue in currency["match"]["issues"])

        subtotal = await invoicing.create_invoice(session, tenant, po, number=unique("INV"),
                                                  lines=_invoice_lines(po), subtotal="1", received_by=None)
        assert any(issue["code"] == "SUBTOTAL_MISMATCH" for issue in subtotal["match"]["issues"])

        total = await invoicing.create_invoice(session, tenant, po, number=unique("INV"),
                                               lines=_invoice_lines(po), tax_amount="100",
                                               total="1", received_by=None)
        assert any(issue["code"] == "TOTAL_MISMATCH" for issue in total["match"]["issues"])
        await session.rollback()


async def test_duplicate_invoice_numbers_are_refused_per_tenant():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po = await _po(session, tenant)
        await _receive(session, tenant, po)
        number = unique("INV")
        first = await invoicing.create_invoice(session, tenant, po, number=number,
                                               lines=_invoice_lines(po), received_by=None)
        assert first["ok"] is True
        second = await invoicing.create_invoice(session, tenant, po, number=number,
                                                lines=_invoice_lines(po), received_by=None)
        assert second["ok"] is False and "already recorded" in second["error"]
        await session.rollback()


# ------------------------------------------------------------------ approval & suggestion
async def test_approval_writes_the_ledger_and_suggests_a_payment():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        users = await _users(session, tenant)
        po = await _po(session, tenant)
        await _receive(session, tenant, po)
        created = await invoicing.create_invoice(session, tenant, po, number=unique("INV"),
                                                 lines=_invoice_lines(po), received_by=users["buyer"].id,
                                                 due_date=datetime.now(UTC) + timedelta(days=30))
        invoice = await session.get(SupplierInvoice, created["invoice_id"])
        approved = await invoicing.approve_invoice(session, tenant, invoice,
                                                    approved_by=users["finance"].id,
                                                    comment="Matched")
        assert approved["ok"] is True and invoice.status == "approved"
        suggestion = approved["payment_suggestion"]
        assert suggestion["method"] == "bank_transfer"
        assert suggestion["requires_human_approval"] is True
        assert suggestion["execution"] == "outside_platform"
        assert suggestion["amount_due"] == str(invoice.total)
        entry = (await session.execute(
            select(SupplierLedgerEntry).where(SupplierLedgerEntry.invoice_id == invoice.id,
                                              SupplierLedgerEntry.kind == "invoice")
        )).scalars().first()
        assert entry is not None and entry.amount == invoice.total
        await session.rollback()


async def test_a_mismatched_invoice_requires_a_written_override():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po = await _po(session, tenant)
        await _receive(session, tenant, po, ratio="0.5")
        created = await invoicing.create_invoice(session, tenant, po, number=unique("INV"),
                                                 lines=_invoice_lines(po), received_by=None)
        invoice = await session.get(SupplierInvoice, created["invoice_id"])
        refused = await invoicing.approve_invoice(session, tenant, invoice, approved_by=None)
        assert refused["ok"] is False and "override" in refused["error"]

        overridden = await invoicing.approve_invoice(session, tenant, invoice, approved_by=None,
                                                      override_reason="Supplier shipped in one lot; "
                                                                      "receipt split for VAT.")
        assert overridden["ok"] is True and overridden["overridden"] is True
        assert "[match override]" in invoice.notes
        assert invoice.status == "approved"
        await session.rollback()


async def test_approving_twice_is_refused():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po = await _po(session, tenant)
        await _receive(session, tenant, po)
        created = await invoicing.create_invoice(session, tenant, po, number=unique("INV"),
                                                 lines=_invoice_lines(po), received_by=None)
        invoice = await session.get(SupplierInvoice, created["invoice_id"])
        assert (await invoicing.approve_invoice(session, tenant, invoice, approved_by=None))["ok"] is True
        again = await invoicing.approve_invoice(session, tenant, invoice, approved_by=None)
        assert again["ok"] is False and "already" in again["error"]
        await session.rollback()


async def test_cross_border_high_value_invoices_are_suggested_for_a_letter_of_credit():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po = await _po(session, tenant)
        supplier = (await session.execute(select(Supplier).where(Supplier.id == po.supplier_id))).scalar_one()
        supplier.country = "DE"
        invoice = SupplierInvoice(tenant_id=tenant.id, po_id=po.id, supplier_id=supplier.id,
                                  number=unique("INV"), currency="EUR", subtotal=Decimal("300000"),
                                  tax_amount=Decimal("0"), total=Decimal("300000"), status="received",
                                  lines=[])
        session.add(invoice)
        await session.flush()
        suggestion = invoicing.suggest_payment(invoice=invoice, po=po, supplier=supplier, tenant=tenant)
        assert suggestion["method"] == "letter_of_credit"
        assert suggestion["requires_fx"] is True
        assert suggestion["priority"] == "high"
        assert any(reason["code"] == "CROSS_BORDER_HIGH_VALUE" for reason in suggestion["reasons"])
        await session.rollback()


async def test_preferred_suppliers_and_overdue_invoices_raise_the_priority():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po = await _po(session, tenant)
        supplier = (await session.execute(select(Supplier).where(Supplier.id == po.supplier_id))).scalar_one()
        supplier.status = "preferred"
        due = datetime.now(UTC) - timedelta(days=3)
        invoice = SupplierInvoice(tenant_id=tenant.id, po_id=po.id, supplier_id=supplier.id,
                                  number=unique("INV"), currency=tenant.currency,
                                  subtotal=Decimal("1000"), tax_amount=Decimal("0"),
                                  total=Decimal("1000"), status="received", due_date=due, lines=[])
        session.add(invoice)
        await session.flush()
        suggestion = invoicing.suggest_payment(invoice=invoice, po=po, supplier=supplier, tenant=tenant)
        codes = {reason["code"] for reason in suggestion["reasons"]}
        assert suggestion["method"] == "bank_transfer"
        assert suggestion["priority"] == "high"
        assert {"PREFERRED_SUPPLIER", "OVERDUE"} <= codes
        assert suggestion["days_until_due"] < 0
        await session.rollback()


# ------------------------------------------------------------------ payments & ledger
async def _approved_invoice(session, tenant, *, ratio: str = "1"):
    po = await _po(session, tenant)
    await _receive(session, tenant, po, ratio=ratio)
    created = await invoicing.create_invoice(session, tenant, po, number=unique("INV"),
                                            lines=_invoice_lines(po, ratio=ratio), received_by=None)
    invoice = await session.get(SupplierInvoice, created["invoice_id"])
    await invoicing.approve_invoice(session, tenant, invoice, approved_by=None)
    return po, invoice


async def test_payments_require_approval_a_reference_and_cannot_overpay():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po = await _po(session, tenant)
        await _receive(session, tenant, po)
        created = await invoicing.create_invoice(session, tenant, po, number=unique("INV"),
                                                 lines=_invoice_lines(po), received_by=None)
        invoice = await session.get(SupplierInvoice, created["invoice_id"])
        not_approved = await invoicing.record_payment(session, tenant, invoice, amount="100",
                                                      method="bank_transfer", reference="X1")
        assert not_approved["ok"] is False and "approve" in not_approved["error"]

        await invoicing.approve_invoice(session, tenant, invoice, approved_by=None)
        no_reference = await invoicing.record_payment(session, tenant, invoice, amount="100",
                                                      method="bank_transfer", reference="")
        assert no_reference["ok"] is False and "reference" in no_reference["error"]

        overpay = await invoicing.record_payment(session, tenant, invoice, amount=str(invoice.total + 1),
                                                 method="bank_transfer", reference="X1")
        assert overpay["ok"] is False and "exceeds" in overpay["error"]

        negative = await invoicing.record_payment(session, tenant, invoice, amount="-5",
                                                  method="bank_transfer", reference="X1")
        assert negative["ok"] is False
        await session.rollback()


async def test_partial_payment_then_settlement_moves_the_invoice_to_paid():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        _po_row, invoice = await _approved_invoice(session, tenant)
        total = invoice.total
        first = await invoicing.record_payment(session, tenant, invoice, amount=str(total / 2),
                                               method="bank_transfer", reference="TRF-1")
        assert first["ok"] is True and first["status"] == "partially_paid"
        second = await invoicing.record_payment(session, tenant, invoice,
                                                amount=str(total - invoice.paid_amount),
                                                method="bank_transfer", reference="TRF-2")
        assert second["ok"] is True and second["status"] == "paid"
        assert Decimal(second["outstanding"]) == Decimal("0.0000")
        await session.rollback()


async def test_supplier_balance_tracks_invoiced_paid_and_overdue_amounts():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po, invoice = await _approved_invoice(session, tenant)
        before = await invoicing.supplier_balance(session, tenant.id, invoice.supplier_id)
        assert Decimal(before["outstanding"]) >= invoice.total
        assert before["open_invoices"] >= 1

        await invoicing.record_payment(session, tenant, invoice, amount=str(invoice.total),
                                       method="bank_transfer", reference="TRF-9")
        after = await invoicing.supplier_balance(session, tenant.id, invoice.supplier_id)
        # The invoice moved from owed to settled: the balance drops by exactly its total.
        assert Decimal(before["outstanding"]) - Decimal(after["outstanding"]) == invoice.total
        assert Decimal(after["paid_total"]) - Decimal(before["paid_total"]) == invoice.total
        assert after["entries"][0]["kind"] == "payment"
        assert after["entries"][0]["amount"].startswith("-")
        await session.rollback()


async def test_balances_are_listed_per_supplier_with_the_largest_outstanding_first():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        rows = await invoicing.supplier_balances(session, tenant)
        if not rows:
            return
        values = [Decimal(row["outstanding"]) for row in rows]
        assert values == sorted(values, reverse=True)
        assert {"supplier_id", "supplier_name", "outstanding", "overdue", "open_invoices"} <= set(rows[0])


async def test_ledger_entries_are_tenant_scoped():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po, invoice = await _approved_invoice(session, tenant)
        other = Tenant(name=unique("Other"), slug=unique("other"), plan_code="growth", status="active",
                       currency="EGP", settings={})
        session.add(other)
        await session.flush()
        foreign = await invoicing.supplier_balance(session, other.id, invoice.supplier_id)
        assert foreign["entries"] == []
        assert Decimal(foreign["outstanding"]) == Decimal("0")
        await session.rollback()


# ------------------------------------------------------------------ safety invariants
async def test_the_agent_still_has_no_payment_or_invoice_tools():
    names = {spec.name for spec in tool_registry.TOOLS.values()} if isinstance(
        tool_registry.TOOLS, dict) else {spec.name for spec in tool_registry.TOOLS}
    forbidden = {"record_payment", "approve_invoice", "pay_invoice", "execute_payment", "create_invoice",
                 "confirm_receipt", "accept_delivery"}
    assert not (names & forbidden)
    assert {"accept_offer", "sign_contract", "pay_invoice"} <= tool_registry.FORBIDDEN_TOOL_NAMES


async def test_payment_execution_is_not_exposed_anywhere_in_the_service_layer():
    import inspect

    source = inspect.getsource(invoicing)
    assert "def record_payment" in source
    assert "outside_platform" in source
    # No payment gateway call may be made from this module.
    assert "registry.resolve(\"payment\")" not in source
