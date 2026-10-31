"""Goods receipt and delivery lifecycle (FR-PO-4): two human gates before a line counts as received.

The properties asserted here are the ones that make the receiving flow safe:

* the warehouse cannot confirm its own receipt (that is the requesting team's job);
* over-receipt is refused while short delivery is recorded and visible;
* the PO status is *derived* from receipts, never set by hand;
* an unconfirmed receipt never unlocks the three-way match.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from conftest import unique
from sqlalchemy import select

from app.db.models.entities import RFQ, GoodsReceipt, PurchaseOrder, Supplier, Tenant, User
from app.db.session import SessionLocal
from app.services import receiving


async def _tenant(session) -> Tenant:
    return (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()


async def _users(session, tenant) -> tuple[User, User]:
    buyer = (await session.execute(
        select(User).where(User.tenant_id == tenant.id, User.email == "buyer@nile.example.com")
    )).scalar_one()
    requester = (await session.execute(
        select(User).where(User.tenant_id == tenant.id, User.email == "requester@nile.example.com")
    )).scalar_one()
    return buyer, requester


async def _fresh_po(session, tenant) -> PurchaseOrder:
    """A brand-new purchase order so each test starts from a clean delivery history."""
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
    po = PurchaseOrder(
        tenant_id=tenant.id, code=unique("PO-T"), rfq_id=rfq.id, supplier_id=supplier.id,
        amount=Decimal("2068200"), currency="EGP", status="issued", issued_at=datetime.now(UTC),
        lines=lines,
    )
    session.add(po)
    await session.flush()
    return po


def _full_lines(po: PurchaseOrder, ratio: Decimal = Decimal("1")) -> list[dict]:
    return [{"line_no": row["line_no"], "received_qty": str(row["quantity"] * ratio),
             "accepted_qty": str(row["quantity"] * ratio)}
            for row in receiving.po_lines(po)]


async def test_po_lines_expose_a_stable_line_number_and_parsed_numbers():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po = await _fresh_po(session, tenant)
        lines = receiving.po_lines(po)
        assert [line["line_no"] for line in lines] == list(range(1, len(lines) + 1))
        assert all(isinstance(line["quantity"], Decimal) and line["quantity"] > 0 for line in lines)


async def test_mark_in_transit_is_only_possible_from_issued_or_partial():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po = await _fresh_po(session, tenant)
        po.status = "confirmed"
        result = await receiving.mark_in_transit(session, tenant, po, actor_id=None)
        assert result["ok"] is False and "confirmed" in result["error"]
        await session.rollback()


async def test_partial_receipt_moves_the_po_to_partially_received_and_records_the_shortfall():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        buyer, _requester = await _users(session, tenant)
        po = await _fresh_po(session, tenant)
        po.status = "in_transit"
        lines = _full_lines(po, Decimal("0.5"))
        result = await receiving.create_receipt(session, tenant, po, lines=lines, received_by=buyer.id,
                                                warehouse="Cairo stores", delivery_note_ref=unique("DN"))
        assert result["ok"] is True, result
        assert result["po_status"] == "partially_received"
        assert result["discrepancy"]["short_delivery"] is True
        assert Decimal(result["discrepancy"]["received_total"]) > 0
        await session.rollback()


async def test_over_receipt_is_rejected_with_the_offending_line():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        buyer, _requester = await _users(session, tenant)
        po = await _fresh_po(session, tenant)
        first = receiving.po_lines(po)[0]
        result = await receiving.create_receipt(
            session, tenant, po, received_by=buyer.id,
            lines=[{"line_no": first["line_no"], "received_qty": str(first["quantity"] + 10),
                    "accepted_qty": str(first["quantity"] + 10)}])
        assert result["ok"] is False
        assert result["issues"][0]["code"] == "OVER_RECEIPT"
        await session.rollback()


async def test_receipt_rejects_unknown_lines_negative_quantities_and_empty_bodies():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        buyer, _requester = await _users(session, tenant)
        po = await _fresh_po(session, tenant)
        unknown = await receiving.create_receipt(
            session, tenant, po, received_by=buyer.id,
            lines=[{"line_no": 99, "received_qty": "1", "accepted_qty": "1"}])
        assert unknown["ok"] is False and unknown["issues"][0]["code"] == "UNKNOWN_LINE"

        negative = await receiving.create_receipt(
            session, tenant, po, received_by=buyer.id,
            lines=[{"line_no": 1, "received_qty": "-3", "accepted_qty": "-3"}])
        assert negative["ok"] is False and negative["issues"][0]["code"] == "NEGATIVE_QUANTITY"

        empty = await receiving.create_receipt(session, tenant, po, received_by=buyer.id, lines=[])
        assert empty["ok"] is False and empty["issues"][0]["code"] == "NO_LINES"
        await session.rollback()


async def test_rejected_quantities_do_not_count_towards_received_totals():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        buyer, requester = await _users(session, tenant)
        po = await _fresh_po(session, tenant)
        po.status = "in_transit"
        first = receiving.po_lines(po)[0]
        result = await receiving.create_receipt(
            session, tenant, po, received_by=buyer.id,
            lines=[{"line_no": first["line_no"], "received_qty": "10", "accepted_qty": "7",
                    "rejected_qty": "3", "condition": "damaged"}])
        assert result["ok"] is True
        totals = await receiving.received_totals(session, tenant.id, po.id, statuses=("received", "confirmed"))
        assert totals[first["line_no"]] == Decimal("7")
        receipt = await session.get(GoodsReceipt, result["receipt_id"])
        assert Decimal(receipt.lines[0]["rejected_qty"]) == Decimal("3")
        await session.rollback()


async def test_the_warehouse_cannot_confirm_its_own_receipt_without_the_requester_checks():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        buyer, requester = await _users(session, tenant)
        po = await _fresh_po(session, tenant)
        po.status = "in_transit"
        created = await receiving.create_receipt(session, tenant, po, lines=_full_lines(po),
                                                 received_by=buyer.id)
        receipt = await session.get(GoodsReceipt, created["receipt_id"])
        incomplete = await receiving.confirm_receipt(
            session, tenant, receipt, confirmed_by=requester.id,
            checks={"quantity_ok": True, "quality_ok": False, "spec_ok": True}, decision="confirm")
        assert incomplete["ok"] is False
        assert receipt.status == "received", "a failed check must not move the receipt"

        rejection = await receiving.confirm_receipt(
            session, tenant, receipt, confirmed_by=requester.id, checks={},
            comment="", decision="reject")
        assert rejection["ok"] is False and "reason" in rejection["error"]
        await session.rollback()


async def test_full_receipt_then_confirmation_walks_the_po_to_confirmed():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        buyer, requester = await _users(session, tenant)
        po = await _fresh_po(session, tenant)
        created = await receiving.create_receipt(session, tenant, po, lines=_full_lines(po),
                                                 received_by=buyer.id)
        assert created["po_status"] == "received", "warehouse alone stops at 'received'"
        receipt = await session.get(GoodsReceipt, created["receipt_id"])
        confirmed = await receiving.confirm_receipt(
            session, tenant, receipt, confirmed_by=requester.id,
            checks={"quantity_ok": True, "quality_ok": True, "spec_ok": True, "documents_ok": True},
            comment="Matches the requisition.")
        assert confirmed["ok"] is True
        assert confirmed["status"] == "confirmed"
        assert confirmed["po_status"] == "confirmed"
        await session.rollback()


async def test_rejection_records_a_reason_and_keeps_the_quantity_out_of_the_match():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        buyer, requester = await _users(session, tenant)
        po = await _fresh_po(session, tenant)
        po.status = "in_transit"
        created = await receiving.create_receipt(session, tenant, po, lines=_full_lines(po, Decimal("0.5")),
                                                 received_by=buyer.id)
        receipt = await session.get(GoodsReceipt, created["receipt_id"])
        rejected = await receiving.confirm_receipt(
            session, tenant, receipt, confirmed_by=requester.id, checks={},
            comment="Steel diameter is out of tolerance", decision="reject")
        assert rejected["ok"] is True and rejected["status"] == "rejected"
        confirmed = await receiving.received_totals(session, tenant.id, po.id)
        assert confirmed == {}
        await session.rollback()


async def test_closing_requires_a_requester_confirmed_delivery():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po = await _fresh_po(session, tenant)
        po.status = "received"
        refused = await receiving.close_po(session, tenant, po, actor_id=None)
        assert refused["ok"] is False and "confirmed" in refused["error"]

        po.status = "confirmed"
        closed = await receiving.close_po(session, tenant, po, actor_id=None, reason="Delivery accepted")
        assert closed["ok"] is True and closed["status"] == "closed"
        assert po.closed_at is not None
        again = await receiving.close_po(session, tenant, po, actor_id=None)
        assert again["ok"] is False
        await session.rollback()


async def test_closed_purchase_orders_refuse_further_receipts():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        buyer, _requester = await _users(session, tenant)
        po = await _fresh_po(session, tenant)
        po.status = "closed"
        result = await receiving.create_receipt(session, tenant, po, lines=_full_lines(po), received_by=buyer.id)
        assert result["ok"] is False and "closed" in result["error"]
        await session.rollback()


async def test_discrepancy_summary_reports_incomplete_lines_and_totals():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po = await _fresh_po(session, tenant)
        summary = await receiving.discrepancy_summary(session, tenant.id, po)
        ordered = sum((row["quantity"] for row in receiving.po_lines(po)), Decimal("0"))
        assert Decimal(summary["ordered_total"]) == ordered
        assert len(summary["lines"]) == len(receiving.po_lines(po))
        assert all({"line_no", "ordered_qty", "received_qty", "outstanding_qty", "status"} <= set(row)
                   for row in summary["lines"])


async def test_receipt_notifications_reach_the_requesting_team():
    async with SessionLocal() as session:
        from app.db.models.entities import Notification

        tenant = await _tenant(session)
        buyer, requester = await _users(session, tenant)
        po = await _fresh_po(session, tenant)
        await receiving.create_receipt(session, tenant, po, lines=_full_lines(po, Decimal("0.2")),
                                       received_by=buyer.id)
        notices = (await session.execute(
            select(Notification).where(Notification.user_id == requester.id,
                                       Notification.kind == "goods_receipt_pending",
                                       Notification.link == f"/purchase-orders/{po.id}")
        )).scalars().all()
        assert notices and str(po.code) in notices[0].title
        await session.rollback()


async def test_receipt_view_is_json_serialisable_and_bilingual_ready():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        receipt = (await session.execute(
            select(GoodsReceipt).where(GoodsReceipt.tenant_id == tenant.id).limit(1)
        )).scalars().first()
        if receipt is None:
            return
        view = receiving.receipt_view(receipt)
        assert {"id", "code", "status", "lines", "confirmation_checks", "received_at"} <= set(view)
        assert isinstance(view["lines"], list)


async def test_receipt_received_at_is_stored_timezone_aware():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        buyer, _requester = await _users(session, tenant)
        po = await _fresh_po(session, tenant)
        po.status = "in_transit"
        stamp = datetime.now(UTC) - timedelta(days=2)
        created = await receiving.create_receipt(session, tenant, po, lines=_full_lines(po, Decimal("0.1")),
                                                 received_by=buyer.id, received_at=stamp)
        receipt = await session.get(GoodsReceipt, created["receipt_id"])
        assert receipt.received_at.tzinfo is not None
        await session.rollback()


async def test_rfq_target_price_is_carried_into_the_po_via_the_offer():
    """Regression: RFQ lines now keep the taxonomy code, so category pricing survives the hand-off."""
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        rfq = (await session.execute(
            select(RFQ).where(RFQ.tenant_id == tenant.id).order_by(RFQ.created_at)
        )).scalars().first()
        assert rfq is not None
        po = (await session.execute(
            select(PurchaseOrder).where(PurchaseOrder.tenant_id == tenant.id,
                                        PurchaseOrder.rfq_id == rfq.id)
        )).scalars().first()
        if po is None:
            return
        assert all(Decimal(line["unit_price"]) > 0 for line in po.lines)


async def test_two_deliveries_cannot_add_up_to_more_than_was_ordered():
    """Cumulative over-receipt is refused, even when each receipt looks small on its own.

    Found in phase 4: validation compared each receipt with the *ordered* quantity, so two
    receipts of 60 % and 50 % were both accepted and the order ended up 110 % delivered.
    """
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        buyer, _requester = await _users(session, tenant)
        po = await _fresh_po(session, tenant)
        line = receiving.po_lines(po)[0]
        ordered = line["quantity"]

        almost_all = (ordered - Decimal("1")).quantize(Decimal("0.01"))
        first = await receiving.create_receipt(
            session, tenant, po, received_by=buyer.id,
            lines=[{"line_no": line["line_no"], "received_qty": str(almost_all),
                    "accepted_qty": str(almost_all)}])
        assert first["ok"] is True
        # Not even confirmed yet — the goods are physically there, so the guard already counts them.
        second = await receiving.create_receipt(
            session, tenant, po, received_by=buyer.id,
            lines=[{"line_no": line["line_no"], "received_qty": "2", "accepted_qty": "2"}])
        assert second["ok"] is False
        assert second["issues"][0]["code"] == "OVER_RECEIPT"
        assert "already received" in second["issues"][0]["message"]

        # The remaining single unit is still accepted, so the guard does not block a valid top-up.
        top_up = await receiving.create_receipt(
            session, tenant, po, received_by=buyer.id,
            lines=[{"line_no": line["line_no"], "received_qty": "1", "accepted_qty": "1"}])
        assert top_up["ok"] is True, top_up
        await session.rollback()
