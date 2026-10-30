"""Accounting service internals: double entry, GR/IR clearing, variance, reversal and analytics.

These tests own the arithmetic. If a posting rule is wrong here, every later screen is wrong too,
so they assert exact amounts rather than "something was created".
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.models.entities import (
    RFQ,
    AuditPackage,
    CostCenter,
    GoodsReceipt,
    PurchaseOrder,
    Supplier,
    SupplierInvoice,
    Tenant,
)
from app.db.session import SessionLocal
from app.services import accounting, audit_package, numbers


async def _tenant(session) -> Tenant:
    return (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()


async def _po(session, tenant, lines: list[dict] | None = None) -> PurchaseOrder:
    rfq = (await session.execute(
        select(RFQ).where(RFQ.tenant_id == tenant.id).order_by(RFQ.created_at)
    )).scalars().first()
    supplier = (await session.execute(
        select(Supplier).where(Supplier.tenant_id == tenant.id).order_by(Supplier.created_at)
    )).scalars().first()
    rows = lines or [
        {"description": "Steel plate 6mm", "quantity": "10", "uom": "t", "unit_price": "1000.00",
         "line_total": "10000.00"},
        {"description": "Fasteners M12", "quantity": "200", "uom": "pcs", "unit_price": "5.00",
         "line_total": "1000.00"},
    ]
    po = PurchaseOrder(tenant_id=tenant.id, code=f"PO-ACC-{numbers.plain(Decimal(str(datetime.now(UTC).timestamp())))[-6:]}",
                       rfq_id=rfq.id, supplier_id=supplier.id,
                       amount=numbers.money(sum(Decimal(row["line_total"]) for row in rows)),
                       currency="EGP", status="issued", issued_at=datetime.now(UTC), lines=rows)
    session.add(po)
    await session.flush()
    return po


async def _confirmed_receipt(session, tenant, po, quantities: list[str],
                             suffix: str = "") -> GoodsReceipt:
    lines = []
    for index, quantity in enumerate(quantities, start=1):
        lines.append({"line_no": index, "description": po.lines[index - 1]["description"],
                      "uom": po.lines[index - 1]["uom"], "ordered_qty": po.lines[index - 1]["quantity"],
                      "received_qty": quantity, "accepted_qty": quantity, "rejected_qty": "0",
                      "condition": "ok", "note": ""})
    receipt = GoodsReceipt(tenant_id=tenant.id, code=f"GR-ACC-{po.code[-6:]}{suffix}", po_id=po.id,
                           supplier_id=po.supplier_id, status="confirmed", lines=lines,
                           received_at=datetime.now(UTC), confirmed_at=datetime.now(UTC),
                           confirmation_checks={"quantity_ok": True, "quality_ok": True, "spec_ok": True})
    session.add(receipt)
    await session.flush()
    return receipt


async def _accepted_package(session, tenant, po) -> AuditPackage:
    package = await audit_package.assemble(session, tenant, po, generated_by=None)
    await audit_package.review(session, tenant, package, reviewer_id=None, decision="accepted",
                               comment="Evidence complete")
    return package


async def _approved_invoice(session, tenant, po, *, quantities: list[str], prices: list[str],
                            tax: str = "0", number: str | None = None) -> SupplierInvoice:
    from app.services import invoicing

    lines = [{"line_no": index + 1, "description": po.lines[index]["description"],
              "quantity": quantity, "unit_price": prices[index]}
             for index, quantity in enumerate(quantities)]
    created = await invoicing.create_invoice(
        session, tenant, po, number=number or f"INV-{po.code[-6:]}-{quantities[0]}", lines=lines,
        received_by=None, tax_amount=tax,
        due_date=datetime.now(UTC) + timedelta(days=30),
    )
    assert created["ok"], created
    invoice = (await session.execute(select(SupplierInvoice).where(SupplierInvoice.id == created["invoice_id"]))).scalar_one()
    await invoicing.approve_invoice(session, tenant, invoice, approved_by="user-finance", comment="OK")
    return invoice


# ------------------------------------------------------------------ chart & validation
async def test_the_standard_chart_is_seeded_per_tenant():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        accounts = {row.code: row for row in await accounting.list_accounts(session, tenant.id)}
        for code in (accounting.ACCOUNT_INVENTORY, accounting.ACCOUNT_GRIR, accounting.ACCOUNT_AP,
                     accounting.ACCOUNT_VAT_INPUT, accounting.ACCOUNT_BANK,
                     accounting.ACCOUNT_PRICE_VARIANCE):
            assert code in accounts, f"missing control account {code}"
        assert accounts[accounting.ACCOUNT_AP].kind == "liability"
        assert accounts[accounting.ACCOUNT_GRIR].is_control is True


async def test_an_unbalanced_entry_is_refused_before_it_exists():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        with pytest.raises(accounting.AccountingError) as excinfo:
            await accounting.create_entry(
                session, tenant, memo="Half an entry", memo_ar="قيد ناقص",
                lines=[{"account_code": accounting.ACCOUNT_BANK, "debit": "100", "credit": "0"},
                       {"account_code": accounting.ACCOUNT_AP, "credit": "90", "debit": "0"}],
            )
        assert excinfo.value.code == "unbalanced"
        assert "100" in str(excinfo.value) and "90" in str(excinfo.value)
        await session.rollback()


async def test_unknown_and_inactive_accounts_are_refused():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        with pytest.raises(accounting.AccountingError) as unknown:
            await accounting.create_entry(
                session, tenant, lines=[{"account_code": "999999", "debit": "10"},
                                        {"account_code": accounting.ACCOUNT_AP, "credit": "10"}],
            )
        assert unknown.value.code == "unknown_account"

        account = await accounting.create_account(session, tenant, code="990001", name="Suspense",
                                                  name_ar="", kind="asset")
        await accounting.update_account(session, tenant, account, is_active=False)
        with pytest.raises(accounting.AccountingError) as inactive:
            await accounting.create_entry(
                session, tenant, lines=[{"account_code": "990001", "debit": "10"},
                                        {"account_code": accounting.ACCOUNT_AP, "credit": "10"}],
            )
        assert inactive.value.code == "inactive_account"
        await session.rollback()


async def test_a_cost_debit_without_a_cost_centre_is_refused():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        with pytest.raises(accounting.AccountingError) as excinfo:
            await accounting.create_entry(
                session, tenant, lines=[
                    {"account_code": accounting.ACCOUNT_EXPENSE_MATERIALS, "debit": "500"},
                    {"account_code": accounting.ACCOUNT_BANK, "credit": "500"},
                ],
            )
        assert excinfo.value.code == "missing_cost_center"
        await session.rollback()


async def test_an_account_with_posted_lines_cannot_be_deactivated():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        spare = await accounting.create_account(session, tenant, code="160100", name="Prepayments",
                                                name_ar="مدفوعات مقدمة", kind="asset")
        draft = await accounting.create_entry(
            session, tenant, memo="Prepaid insurance",
            lines=[{"account_code": spare.code, "debit": "500"},
                   {"account_code": accounting.ACCOUNT_BANK, "credit": "500"}],
            created_by="u-a",
        )
        # A draft alone does not put an account in use, so deactivation is still allowed.
        await accounting.update_account(session, tenant, spare, is_active=False)
        assert spare.is_active is False
        await accounting.update_account(session, tenant, spare, is_active=True)

        await accounting.post_entry(session, tenant, draft, posted_by="u-b")
        with pytest.raises(accounting.AccountingError) as excinfo:
            await accounting.update_account(session, tenant, spare, is_active=False)
        assert excinfo.value.code == "account_in_use"
        await session.rollback()


# ------------------------------------------------------------------ GR/IR accrual
async def test_receipt_accrual_debits_inventory_and_credits_grir_at_po_cost():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po = await _po(session, tenant)
        await _confirmed_receipt(session, tenant, po, ["4", "100"])
        entry = await accounting.draft_receipt_accrual(
            session, tenant, po, created_by="u1",
            allocations=[{"line_no": 1, "cost_center_code": "CC-000", "unit_number": "UNIT-77"}],
        )
        assert entry.status == "draft"
        by_account: dict[str, Decimal] = {}
        for row in entry.lines:
            by_account[row["account_code"]] = by_account.get(row["account_code"], Decimal("0")) + \
                Decimal(row["debit"]) - Decimal(row["credit"])
        assert by_account[accounting.ACCOUNT_INVENTORY] == Decimal("4500.0000")
        assert by_account[accounting.ACCOUNT_GRIR] == Decimal("-4500.0000")
        assert Decimal(entry.total_debit) == Decimal(entry.total_credit) == Decimal("4500.0000")
        inventory_lines = [row for row in entry.lines if row["account_code"] == accounting.ACCOUNT_INVENTORY]
        assert inventory_lines[0]["unit_number"] == "UNIT-77"
        # A second accrual for the same order is a duplicate, not a second truth.
        with pytest.raises(accounting.AccountingError) as dup:
            await accounting.draft_receipt_accrual(session, tenant, po, created_by="u1")
        assert dup.value.code == "duplicate_accrual"
        await session.rollback()


async def test_a_partial_receipt_only_accrues_the_new_quantity():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po = await _po(session, tenant)
        await _confirmed_receipt(session, tenant, po, ["2", "50"], suffix="-A")
        first = await accounting.draft_receipt_accrual(session, tenant, po, created_by="u1")
        # 2 t at 1000 + 50 pcs at 5.
        assert Decimal(first.total_debit) == Decimal("2250.0000")

        # A second receipt completes the order later: only the increment is accrued.
        await _confirmed_receipt(session, tenant, po, ["2", "50"], suffix="-B")
        second = await accounting.draft_receipt_accrual(session, tenant, po, created_by="u1")
        assert Decimal(second.total_debit) == Decimal("2250.0000")
        total = sum(Decimal(row.total_debit) for row in (first, second))
        assert total == Decimal("4500.0000")
        await session.rollback()


async def test_accrual_needs_a_confirmed_receipt():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po = await _po(session, tenant)
        with pytest.raises(accounting.AccountingError) as excinfo:
            await accounting.draft_receipt_accrual(session, tenant, po, created_by="u1")
        assert excinfo.value.code == "no_confirmed_receipt"
        await session.rollback()


# ------------------------------------------------------------------ invoice posting
async def test_invoice_posting_recognises_cost_when_the_receipt_was_not_accrued():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po = await _po(session, tenant)
        await _confirmed_receipt(session, tenant, po, ["10", "200"])
        await _accepted_package(session, tenant, po)
        invoice = await _approved_invoice(session, tenant, po, quantities=["10", "200"],
                                          prices=["1000.00", "5.00"], tax="1350.00")
        entry = await accounting.draft_invoice_posting(session, tenant, po, invoice, created_by="u-controller")
        lines = entry.lines
        debit = sum(Decimal(row["debit"]) for row in lines)
        credit = sum(Decimal(row["credit"]) for row in lines)
        assert debit == credit, "every posting must balance to the piastre"
        by_account = {}
        for row in lines:
            by_account.setdefault(row["account_code"], Decimal("0"))
            by_account[row["account_code"]] += Decimal(row["debit"]) - Decimal(row["credit"])
        # No accrual was posted, so the invoice itself recognises the cost — once.
        assert accounting.ACCOUNT_GRIR not in by_account
        assert by_account[accounting.ACCOUNT_INVENTORY] == Decimal("11000.0000")
        assert by_account[accounting.ACCOUNT_VAT_INPUT] == Decimal("1350.0000")
        assert by_account[accounting.ACCOUNT_AP] == Decimal("-12350.0000")
        assert entry.match_snapshot["status"] == "matched"
        assert entry.invoice_id == invoice.id and entry.po_id == po.id
        await session.rollback()


async def test_invoice_posting_clears_grir_when_the_receipt_was_accrued():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po = await _po(session, tenant)
        await _confirmed_receipt(session, tenant, po, ["10", "200"])
        await _accepted_package(session, tenant, po)
        accrual = await accounting.draft_receipt_accrual(session, tenant, po, created_by="u-c")
        await accounting.post_entry(session, tenant, accrual, posted_by="u-controller")
        invoice = await _approved_invoice(session, tenant, po, quantities=["10", "200"],
                                          prices=["1000.00", "5.00"], tax="1350.00")
        entry = await accounting.draft_invoice_posting(session, tenant, po, invoice, created_by="u-c")
        by_account = {}
        for row in entry.lines:
            by_account.setdefault(row["account_code"], Decimal("0"))
            by_account[row["account_code"]] += Decimal(row["debit"]) - Decimal(row["credit"])
        assert by_account[accounting.ACCOUNT_GRIR] == Decimal("11000.0000"), "the invoice clears GR/IR"
        assert accounting.ACCOUNT_INVENTORY not in by_account, "inventory was already debited at receipt"
        assert by_account[accounting.ACCOUNT_AP] == Decimal("-12350.0000")
        assert sum(Decimal(row["debit"]) for row in entry.lines) == sum(
            Decimal(row["credit"]) for row in entry.lines)
        await session.rollback()


async def test_a_price_variance_posts_to_variance_and_keeps_the_invoice_balanced():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po = await _po(session, tenant)
        await _confirmed_receipt(session, tenant, po, ["10", "200"])
        await _accepted_package(session, tenant, po)
        # 0.4% above the PO price: inside the 0.5% tolerance, so the match is clean.
        invoice = await _approved_invoice(session, tenant, po, quantities=["10", "200"],
                                          prices=["1004.00", "5.00"])
        assert invoice.match_result["status"] == "matched"
        entry = await accounting.draft_invoice_posting(session, tenant, po, invoice, created_by="u-c")
        variance = [row for row in entry.lines if row["account_code"] == accounting.ACCOUNT_PRICE_VARIANCE]
        assert variance, "a price difference must be recognised, not silently absorbed"
        assert Decimal(variance[0]["debit"]) == Decimal("40.0000")
        debit = sum(Decimal(row["debit"]) for row in entry.lines)
        credit = sum(Decimal(row["credit"]) for row in entry.lines)
        assert debit == credit
        await session.rollback()


async def test_a_favourable_variance_is_a_credit():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po = await _po(session, tenant)
        await _confirmed_receipt(session, tenant, po, ["10", "200"])
        await _accepted_package(session, tenant, po)
        invoice = await _approved_invoice(session, tenant, po, quantities=["10", "200"],
                                          prices=["996.00", "5.00"])
        entry = await accounting.draft_invoice_posting(session, tenant, po, invoice, created_by="u-c")
        variance = [row for row in entry.lines if row["account_code"] == accounting.ACCOUNT_PRICE_VARIANCE]
        assert variance and Decimal(variance[0]["credit"]) == Decimal("40.0000")
        assert sum(Decimal(row["debit"]) for row in entry.lines) == sum(
            Decimal(row["credit"]) for row in entry.lines)
        await session.rollback()


async def test_posting_requires_an_accepted_audit_package():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po = await _po(session, tenant)
        await _confirmed_receipt(session, tenant, po, ["10", "200"])
        invoice = await _approved_invoice(session, tenant, po, quantities=["10", "200"],
                                          prices=["1000.00", "5.00"])
        with pytest.raises(accounting.AccountingError) as missing:
            await accounting.draft_invoice_posting(session, tenant, po, invoice, created_by="u-c")
        assert missing.value.code == "audit_review_required"
        await session.rollback()


async def test_an_unapproved_invoice_cannot_be_posted():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po = await _po(session, tenant)
        await _confirmed_receipt(session, tenant, po, ["10", "200"])
        await _accepted_package(session, tenant, po)
        from app.services import invoicing

        created = await invoicing.create_invoice(
            session, tenant, po, number="INV-UNPOSTED", received_by=None,
            lines=[{"line_no": 1, "quantity": "10", "unit_price": "1000.00"},
                   {"line_no": 2, "quantity": "200", "unit_price": "5.00"}],
        )
        invoice = (await session.execute(
            select(SupplierInvoice).where(SupplierInvoice.id == created["invoice_id"]))).scalar_one()
        with pytest.raises(accounting.AccountingError) as excinfo:
            await accounting.draft_invoice_posting(session, tenant, po, invoice, created_by="u-c")
        assert excinfo.value.code == "invoice_not_approved"
        await session.rollback()


async def test_the_same_invoice_cannot_be_posted_twice():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po = await _po(session, tenant)
        await _confirmed_receipt(session, tenant, po, ["10", "200"])
        await _accepted_package(session, tenant, po)
        invoice = await _approved_invoice(session, tenant, po, quantities=["10", "200"],
                                          prices=["1000.00", "5.00"])
        await accounting.draft_invoice_posting(session, tenant, po, invoice, created_by="u-c")
        with pytest.raises(accounting.AccountingError) as dup:
            await accounting.draft_invoice_posting(session, tenant, po, invoice, created_by="u-c")
        assert dup.value.code == "duplicate_posting"
        await session.rollback()


# ------------------------------------------------------------------ posting & reversal
async def test_posting_writes_lines_and_a_balanced_trial_balance():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po = await _po(session, tenant)
        await _confirmed_receipt(session, tenant, po, ["10", "200"])
        await _accepted_package(session, tenant, po)
        invoice = await _approved_invoice(session, tenant, po, quantities=["10", "200"],
                                          prices=["1000.00", "5.00"], tax="1350.00")
        entry = await accounting.draft_invoice_posting(session, tenant, po, invoice, created_by="u-c")
        result = await accounting.post_entry(session, tenant, entry, posted_by="u-controller")
        assert result["status"] == "posted"
        trial = await accounting.trial_balance(session, tenant.id)
        assert trial["balanced"] is True
        assert Decimal(trial["total_debit"]) == Decimal(trial["total_credit"])
        assert any(row["account_code"] == accounting.ACCOUNT_AP for row in trial["items"])
        await session.rollback()


async def test_posting_twice_is_refused():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        entry = await accounting.create_entry(
            session, tenant, memo="Bank vs equity", memo_ar="",
            lines=[{"account_code": accounting.ACCOUNT_BANK, "debit": "100", "credit": "0"},
                   {"account_code": "300100", "debit": "0", "credit": "100"}],
        )
        await accounting.post_entry(session, tenant, entry, posted_by="u-controller")
        with pytest.raises(accounting.AccountingError) as excinfo:
            await accounting.post_entry(session, tenant, entry, posted_by="u-controller")
        assert excinfo.value.code == "already_posted"
        await session.rollback()


async def test_a_manual_entry_needs_a_second_person_to_post():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        entry = await accounting.create_entry(
            session, tenant, memo="Correction", memo_ar="تصحيح",
            lines=[{"account_code": accounting.ACCOUNT_BANK, "debit": "250", "credit": "0"},
                   {"account_code": "300100", "debit": "0", "credit": "250"}],
            created_by="user-a",
        )
        with pytest.raises(accounting.AccountingError) as excinfo:
            await accounting.post_entry(session, tenant, entry, posted_by="user-a")
        assert excinfo.value.code == "segregation_of_duties"
        posted = await accounting.post_entry(session, tenant, entry, posted_by="user-b")
        assert posted["ok"] is True
        await session.rollback()


async def test_reversal_mirrors_the_entry_and_marks_the_original():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po = await _po(session, tenant)
        await _confirmed_receipt(session, tenant, po, ["10", "200"])
        await _accepted_package(session, tenant, po)
        invoice = await _approved_invoice(session, tenant, po, quantities=["10", "200"],
                                          prices=["1000.00", "5.00"])
        baseline = {row["code"]: Decimal(row["balance"])
                    for row in (await accounting.trial_balance(session, tenant.id))["items"]}
        entry = await accounting.draft_invoice_posting(session, tenant, po, invoice, created_by="u-c")
        await accounting.post_entry(session, tenant, entry, posted_by="u-controller")
        reversal = await accounting.reverse_entry(session, tenant, entry, reversed_by="u-controller",
                                                  reason="Invoice booked against the wrong order")
        assert reversal.reversal_of == entry.id and entry.status == "reversed"
        assert entry.reversed_by == reversal.id
        original = {(row["account_code"], row["debit"], row["credit"]) for row in entry.lines}
        mirrored = {(row["account_code"], row["credit"], row["debit"]) for row in reversal.lines}
        assert original == mirrored
        trial = await accounting.trial_balance(session, tenant.id)
        assert trial["balanced"] is True
        # The pair nets to zero: every account this invoice touched is back where it started.
        touched = {row["account_code"] for row in entry.lines}
        after = {row["code"]: Decimal(row["balance"]) for row in trial["items"]}
        assert {code: after.get(code, Decimal("0")) for code in touched} == \
            {code: baseline.get(code, Decimal("0")) for code in touched}
        await session.rollback()


async def test_reversal_needs_a_reason_and_a_posted_entry():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        entry = await accounting.create_entry(
            session, tenant, memo="Draft", memo_ar="",
            lines=[{"account_code": accounting.ACCOUNT_BANK, "debit": "10", "credit": "0"},
                   {"account_code": "300100", "debit": "0", "credit": "10"}],
        )
        with pytest.raises(accounting.AccountingError) as not_posted:
            await accounting.reverse_entry(session, tenant, entry, reversed_by="u", reason="mistake")
        assert not_posted.value.code == "not_posted"
        await accounting.post_entry(session, tenant, entry, posted_by="u2")
        with pytest.raises(accounting.AccountingError) as no_reason:
            await accounting.reverse_entry(session, tenant, entry, reversed_by="u", reason="  ")
        assert no_reason.value.code == "reason_required"
        await session.rollback()


# ------------------------------------------------------------------ payment & analytics
async def test_payment_posting_settles_the_payable_against_the_bank():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po = await _po(session, tenant)
        await _confirmed_receipt(session, tenant, po, ["10", "200"])
        await _accepted_package(session, tenant, po)
        invoice = await _approved_invoice(session, tenant, po, quantities=["10", "200"],
                                          prices=["1000.00", "5.00"])
        from app.services import invoicing

        await invoicing.record_payment(session, tenant, invoice, amount=invoice.total,
                                       method="bank_transfer", reference="TRF-ACC-1",
                                       actor_id="u-finance")
        entry = await accounting.draft_payment_posting(session, tenant, po, invoice, created_by="u-c")
        ap = [row for row in entry.lines if row["account_code"] == accounting.ACCOUNT_AP][0]
        bank = [row for row in entry.lines if row["account_code"] == accounting.ACCOUNT_BANK][0]
        assert Decimal(ap["debit"]) == Decimal(invoice.total)
        assert Decimal(bank["credit"]) == Decimal(invoice.total)
        with pytest.raises(accounting.AccountingError) as dup:
            await accounting.draft_payment_posting(session, tenant, po, invoice, created_by="u-c")
        assert dup.value.code == "payment_already_posted" or dup.value.code == "duplicate"
        await session.rollback()


async def test_cost_report_groups_posted_cost_by_centre_and_item():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        center = await accounting.ensure_cost_center(session, tenant, code="CC-PLANT-1",
                                                     name="Sadat plant", name_ar="مصنع السادات")
        po = await _po(session, tenant)
        await _confirmed_receipt(session, tenant, po, ["10", "200"])
        entry = await accounting.draft_receipt_accrual(
            session, tenant, po, created_by="u-c",
            allocations=[{"line_no": 1, "cost_center_code": center.code, "unit_number": "U-1"},
                         {"line_no": 2, "cost_center_code": "CC-000"}],
        )
        await accounting.post_entry(session, tenant, entry, posted_by="u-controller")
        report = await accounting.cost_report(session, tenant.id, group_by="cost_center", po_id=po.id)
        by_code = {row["key"]: Decimal(row["amount"]) for row in report["items"]}
        assert by_code[center.code] == Decimal("10000.0000")
        assert by_code["CC-000"] == Decimal("1000.0000")
        assert Decimal(report["total"]) == Decimal("11000.0000")
        units = await accounting.cost_report(session, tenant.id, group_by="unit", po_id=po.id)
        assert {row["key"] for row in units["items"]} >= {"U-1"}
        await session.rollback()


async def test_supplier_account_shows_the_payable_from_posted_entries():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po = await _po(session, tenant)
        await _confirmed_receipt(session, tenant, po, ["10", "200"])
        await _accepted_package(session, tenant, po)
        invoice = await _approved_invoice(session, tenant, po, quantities=["10", "200"],
                                          prices=["1000.00", "5.00"])
        entry = await accounting.draft_invoice_posting(session, tenant, po, invoice, created_by="u-c")
        await accounting.post_entry(session, tenant, entry, posted_by="u-controller")
        account = await accounting.supplier_account(session, tenant.id, po.supplier_id)
        assert Decimal(account["payable"]) >= Decimal(invoice.total)
        assert any(row["source_type"] == "invoice" for row in account["movements"])
        assert account["supplier_name"]
        await session.rollback()


async def test_an_inactive_cost_centre_is_visible_but_not_offered():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        center = await accounting.create_cost_center(session, tenant, code="CC-TEMP", name="Temporary")
        await accounting.update_cost_center(session, center, is_active=False)
        active = {row.code for row in await accounting.list_cost_centers(session, tenant.id)}
        assert "CC-TEMP" not in active
        everything = {row.code for row in await accounting.list_cost_centers(session, tenant.id,
                                                                             active_only=False)}
        assert "CC-TEMP" in everything
        assert CostCenter  # imported for the type checker
        await session.rollback()
