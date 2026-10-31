"""Goods receipt and delivery tracking after the award (FR-PO-4, AC-PO-2).

Two human gates protect the physical side of a purchase:

1. the warehouse records what actually arrived (``create_receipt``);
2. the requesting team confirms it matches the original need (``confirm_receipt``).

Only a *confirmed* receipt counts towards the three-way match, so a warehouse clerk can never
unlock an invoice on their own. Nothing in this module is reachable from an agent tool.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.config import settings
from app.db.models.entities import GoodsReceipt, PurchaseOrder, Supplier, Tenant, User
from app.services import numbers, p2p
from app.services.common import lock_for_update, next_code, notify, notify_role

log = logging.getLogger("procureai.receiving")

# PO lifecycle: issued -> in_transit -> partially_received -> received -> confirmed -> closed
PO_STATUSES = ("issued", "in_transit", "partially_received", "received", "confirmed", "closed")
# A receipt only counts once the requesting team signed off on it.
MATCHABLE_RECEIPT_STATUSES = ("confirmed",)
EPOCH = Decimal("0.000001")


def po_lines(po: PurchaseOrder) -> list[dict]:
    """PO lines normalised with a stable 1-based ``line_no`` (the join key for receipts/invoices)."""
    return [
        {**line, "line_no": index + 1, "quantity": numbers.parse_decimal(line.get("quantity")),
         "unit_price": numbers.parse_decimal(line.get("unit_price"))}
        for index, line in enumerate(po.lines or [])
    ]


async def _confirmed_receipts(session: AsyncSession, tenant_id: str, po_id: str,
                              statuses: tuple[str, ...] = MATCHABLE_RECEIPT_STATUSES) -> list[GoodsReceipt]:
    rows = list((await session.execute(
        select(GoodsReceipt).where(GoodsReceipt.tenant_id == tenant_id, GoodsReceipt.po_id == po_id,
                                   GoodsReceipt.status.in_(statuses))
        .order_by(GoodsReceipt.created_at)
    )).scalars().all())
    return rows


async def received_totals(session: AsyncSession, tenant_id: str, po_id: str, *,
                          statuses: tuple[str, ...] = MATCHABLE_RECEIPT_STATUSES) -> dict[int, Decimal]:
    """Accepted quantity per PO line from receipts in ``statuses``."""
    totals: dict[int, Decimal] = {}
    for receipt in await _confirmed_receipts(session, tenant_id, po_id, statuses):
        for line in receipt.lines or []:
            line_no = int(line.get("line_no") or 0)
            accepted = numbers.parse_decimal(line.get("accepted_qty")) or Decimal("0")
            totals[line_no] = totals.get(line_no, Decimal("0")) + accepted
    return totals


async def mark_in_transit(session: AsyncSession, tenant: Tenant, po: PurchaseOrder, *,
                          actor_id: str | None, carrier: str = "", expected_at: datetime | None = None) -> dict:
    """Buyer records that the supplier dispatched the goods (still not a receipt)."""
    if po.status not in {"issued", "partially_received"}:
        return {"ok": False, "error": f"A purchase order in status '{po.status}' cannot be marked in transit."}
    po.status = "in_transit" if po.status == "issued" else po.status
    await session.flush()
    await notify(session, tenant_id=tenant.id, user_id=po.created_by, kind="po_in_transit",
                 title=f"PO {po.code} is in transit" + (f" via {carrier}" if carrier else ""),
                 title_ar=f"أمر الشراء {po.code} في الطريق" + (f" عبر {carrier}" if carrier else ""),
                 body=(f"Expected {expected_at.date().isoformat()}" if expected_at else "No ETA given."),
                 link=f"/purchase-orders/{po.id}")
    await p2p.touch(session, tenant.id, po, actor_id=actor_id, source="po_in_transit")
    return {"ok": True, "status": po.status, "carrier": carrier,
            "expected_at": expected_at.isoformat() if expected_at else None}


def _validate_lines(ordered: list[dict], lines: list[dict],
                    already_received: dict[int, Decimal] | None = None) -> tuple[list[dict], list[dict]]:
    """Normalise submitted receipt lines and collect blocking/notice issues.

    `already_received` is the accepted quantity booked by earlier receipts (whatever their
    confirmation state), so the *cumulative* quantity is what gets compared with the order. Checking
    each receipt in isolation lets two "small" deliveries add up to more than was ordered — a silent
    over-delivery that the three-way match would happily pay for.
    """
    prior = already_received or {}
    by_line = {row["line_no"]: row for row in ordered}
    cleaned: list[dict] = []
    issues: list[dict] = []
    seen: set[int] = set()
    for raw in lines:
        line_no = int(raw.get("line_no") or 0)
        if line_no not in by_line:
            issues.append({"code": "UNKNOWN_LINE", "line_no": line_no,
                           "message": f"Line {line_no} is not part of this purchase order."})
            continue
        if line_no in seen:
            issues.append({"code": "DUPLICATE_LINE", "line_no": line_no,
                           "message": f"Line {line_no} appears twice in the same receipt."})
            continue
        seen.add(line_no)
        ordered_row = by_line[line_no]
        received = numbers.parse_decimal(raw.get("received_qty")) or Decimal("0")
        rejected = numbers.parse_decimal(raw.get("rejected_qty")) or Decimal("0")
        accepted = numbers.parse_decimal(raw.get("accepted_qty"))
        if accepted is None:
            accepted = received - rejected
        if received < 0 or rejected < 0 or accepted < 0:
            issues.append({"code": "NEGATIVE_QUANTITY", "line_no": line_no,
                           "message": f"Line {line_no} has a negative quantity."})
            continue
        if accepted + rejected > received + EPOCH:
            issues.append({"code": "QUANTITY_OVERFLOW", "line_no": line_no,
                           "message": f"Line {line_no}: accepted + rejected exceeds the received quantity."})
            continue
        booked = prior.get(line_no, Decimal("0"))
        remaining = ordered_row["quantity"] - booked
        if accepted > remaining + EPOCH:
            issues.append({"code": "OVER_RECEIPT", "line_no": line_no,
                           "message": (f"Line {line_no}: accepted {accepted} exceeds the "
                                       f"{numbers.money(max(remaining, Decimal('0')))} still outstanding "
                                       f"on an order of {ordered_row['quantity']} "
                                       f"({numbers.money(booked)} already received).")})
            continue
        cleaned.append({
            "line_no": line_no,
            "description": raw.get("description") or ordered_row.get("description", ""),
            "uom": raw.get("uom") or ordered_row.get("uom", ""),
            "ordered_qty": str(ordered_row["quantity"]),
            "received_qty": str(received),
            "accepted_qty": str(accepted),
            "rejected_qty": str(rejected),
            "condition": str(raw.get("condition") or "ok")[:24],
            "note": str(raw.get("note") or "")[:500],
        })
    if not cleaned:
        issues.append({"code": "NO_LINES", "line_no": 0, "message": "A goods receipt needs at least one line."})
    return cleaned, issues


async def create_receipt(session: AsyncSession, tenant: Tenant, po: PurchaseOrder, *, lines: list[dict],
                         received_by: str | None, delivery_note_ref: str = "", carrier: str = "",
                         warehouse: str = "", notes: str = "",
                         received_at: datetime | None = None) -> dict:
    """Warehouse records the physical delivery. Over-receipt is rejected, short delivery is not."""
    # Serialize concurrent receipts for this order: the cumulative-quantity check below is a
    # read-modify-write on the order's delivered total.
    locked = await lock_for_update(session, PurchaseOrder, po.id, tenant_id=tenant.id)
    if locked is not None:
        po = locked
    if po.status == "closed":
        return {"ok": False, "error": "This purchase order is closed and cannot receive more goods."}
    ordered = po_lines(po)
    if not ordered:
        return {"ok": False, "error": "This purchase order has no lines to receive."}
    # Count everything already booked against the order, including receipts still awaiting the
    # requester's confirmation: an unconfirmed delivery has physically happened.
    already = await received_totals(session, tenant.id, po.id, statuses=("received", "confirmed"))
    cleaned, issues = _validate_lines(ordered, lines, already_received=already)
    blocking = [issue for issue in issues if issue["code"] != "SHORT_DELIVERY"]
    if blocking:
        return {"ok": False, "error": blocking[0]["message"], "issues": blocking}
    if po.status == "issued":
        # Goods cannot arrive before the supplier was told to ship, but a clerk should not be
        # blocked by a missing bookkeeping step: record it and let the timeline show the order.
        po.status = "in_transit"

    receipt = GoodsReceipt(
        tenant_id=tenant.id, code=await next_code(session, GoodsReceipt, tenant.id, "GR"),
        po_id=po.id, supplier_id=po.supplier_id, delivery_note_ref=delivery_note_ref[:80],
        carrier=carrier[:120], warehouse=warehouse[:120], status="received",
        received_by=received_by, received_at=received_at or datetime.now(UTC),
        lines=cleaned, notes=notes[:2000], created_by=received_by,
    )
    session.add(receipt)
    await session.flush()
    receipt.discrepancy = await discrepancy_summary(session, tenant.id, po, receipt)
    await session.flush()

    po.status = await recompute_po_status(session, tenant.id, po)
    supplier = (await session.execute(select(Supplier).where(Supplier.id == po.supplier_id))).scalar_one_or_none()
    await notify_role(session, tenant_id=tenant.id, role_code="requester", kind="goods_receipt_pending",
                      title=f"Goods received for {po.code} - confirmation needed",
                      title_ar=f"تم استلام بضاعة لأمر الشراء {po.code} - مطلوب التأكيد",
                      body=f"Warehouse recorded {receipt.code}. Confirm the items match the request.",
                      link=f"/purchase-orders/{po.id}", severity="warning")
    await p2p.touch(session, tenant.id, po, actor_id=received_by, source="goods_receipt")
    log.info("goods receipt %s recorded for PO %s", receipt.code, po.code)
    return {"ok": True, "receipt_id": receipt.id, "code": receipt.code, "status": receipt.status,
            "po_status": po.status, "supplier": supplier.name if supplier else "",
            "discrepancy": receipt.discrepancy, "issues": issues}


async def discrepancy_summary(session: AsyncSession, tenant_id: str, po: PurchaseOrder,
                              receipt: GoodsReceipt | None = None) -> dict:
    """Ordered vs received vs still outstanding, at PO level and per line."""
    ordered = po_lines(po)
    statuses = ("received", "confirmed")
    totals = await received_totals(session, tenant_id, po.id, statuses=statuses)
    confirmed = await received_totals(session, tenant_id, po.id)
    rows = []
    outstanding_lines = 0
    for line in ordered:
        received = totals.get(line["line_no"], Decimal("0"))
        outstanding = max(Decimal("0"), line["quantity"] - received)
        if outstanding > EPOCH:
            outstanding_lines += 1
        rows.append({
            "line_no": line["line_no"], "description": line.get("description", ""),
            "uom": line.get("uom", ""), "ordered_qty": str(line["quantity"]),
            "received_qty": str(numbers.money(received)), "confirmed_qty": str(numbers.money(
                confirmed.get(line["line_no"], Decimal("0")))),
            "outstanding_qty": str(numbers.money(outstanding)),
            "status": "outstanding" if outstanding > EPOCH else "complete",
        })
    short = [row for row in rows if row["status"] == "outstanding"]
    return {
        "lines": rows,
        "ordered_total": str(numbers.money(sum((row["quantity"] for row in ordered), Decimal("0")))),
        "received_total": str(numbers.money(sum(totals.values(), Decimal("0")))),
        "outstanding_lines": outstanding_lines,
        "short_delivery": bool(short),
        "receipt_code": receipt.code if receipt else "",
    }


def _checks_complete(checks: dict) -> bool:
    required = ("quantity_ok", "quality_ok", "spec_ok")
    return all(bool(checks.get(key)) for key in required)


async def confirm_receipt(session: AsyncSession, tenant: Tenant, receipt: GoodsReceipt, *,
                          confirmed_by: str | None, checks: dict, comment: str = "",
                          decision: str = "confirm") -> dict:
    """The requesting team accepts or rejects what the warehouse recorded."""
    if receipt.status != "received":
        return {"ok": False, "error": f"Receipt {receipt.code} is already {receipt.status}."}
    po = (await session.execute(
        select(PurchaseOrder).where(PurchaseOrder.id == receipt.po_id, PurchaseOrder.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if not po:
        return {"ok": False, "error": "Purchase order not found for this receipt."}
    if decision not in {"confirm", "reject"}:
        return {"ok": False, "error": "decision must be 'confirm' or 'reject'."}
    if decision == "reject" and not comment.strip():
        return {"ok": False, "error": "A rejection needs a reason so purchasing can chase the supplier."}

    receipt.confirmation_checks = {key: bool(checks.get(key)) for key in
                                   ("quantity_ok", "quality_ok", "spec_ok", "documents_ok")}
    receipt.confirmation_comment = comment.strip()[:2000]
    receipt.confirmed_by = confirmed_by
    receipt.confirmed_at = datetime.now(UTC)
    if decision == "confirm":
        if not _checks_complete(receipt.confirmation_checks):
            return {"ok": False, "error": "Quantity, quality and specification checks must all pass."}
        receipt.status = "confirmed"
    else:
        receipt.status = "rejected"

    po.status = await recompute_po_status(session, tenant.id, po)
    await session.flush()
    href = f"/purchase-orders/{po.id}"
    if decision == "confirm":
        await notify(session, tenant_id=tenant.id, user_id=po.created_by, kind="goods_receipt_confirmed",
                     title=f"{receipt.code} confirmed by the requester",
                     title_ar=f"تم تأكيد استلام {receipt.code} من طالب الشراء",
                     body=comment[:300] or "Items match the original request.", link=href)
    else:
        await notify(session, tenant_id=tenant.id, user_id=po.created_by, kind="goods_receipt_rejected",
                     title=f"{receipt.code} rejected by the requester",
                     title_ar=f"تم رفض استلام {receipt.code} من طالب الشراء",
                     body=comment[:300], link=href, severity="warning")
    if decision == "confirm" and settings.accounting_enabled:
        # The auditor's package is assembled automatically the moment the need is confirmed: the
        # evidence is complete at that point, and nothing in the bundle is created by a model.
        #
        # With accounting switched off the package is deliberately *not* assembled: its purpose is to
        # gate the invoice into accounting, and both the review route and the journal it lists would
        # be 404 — a bundle nobody can review, and a PO parked at "ready for audit" forever.
        from app.services import audit_package  # local import: audit_package imports this module

        try:
            package = await audit_package.assemble(session, tenant, po, generated_by=confirmed_by)
            actor = await session.get(User, confirmed_by) if confirmed_by else None
            await audit(session, tenant_id=tenant.id, action="audit_package.assembled",
                        actor_id=confirmed_by, actor_label=actor.email if actor else "requester",
                        entity_type="purchase_order", entity_id=po.id,
                        after={"code": package.code, "components": len(package.components or []),
                               "manifest": package.manifest_hash, "automatic": True})
        except Exception:  # noqa: BLE001 - a package failure must never undo a physical confirmation
            log.exception("could not assemble the audit package for %s", po.code)
    await p2p.touch(session, tenant.id, po, actor_id=confirmed_by, source="requester_confirmation")
    return {"ok": True, "receipt_id": receipt.id, "status": receipt.status, "po_status": po.status}


async def recompute_po_status(session: AsyncSession, tenant_id: str, po: PurchaseOrder) -> str:
    """Derive the PO delivery status from confirmed/unconfirmed receipts, never from a manual flag."""
    if po.status == "closed":
        return po.status
    ordered = po_lines(po)
    if not ordered:
        return po.status
    any_receipt = await received_totals(session, tenant_id, po.id, statuses=("received", "confirmed"))
    confirmed = await received_totals(session, tenant_id, po.id)
    if not any_receipt:
        return "in_transit" if po.status == "in_transit" else ("issued" if po.status == "issued" else po.status)
    fully_accepted = all(confirmed.get(line["line_no"], Decimal("0")) >= line["quantity"] - EPOCH
                         for line in ordered)
    fully_received = all(any_receipt.get(line["line_no"], Decimal("0")) >= line["quantity"] - EPOCH
                         for line in ordered)
    if fully_accepted:
        return "confirmed"
    if fully_received:
        return "received"
    return "partially_received"


async def close_po(session: AsyncSession, tenant: Tenant, po: PurchaseOrder, *, actor_id: str | None,
                   reason: str = "") -> dict:
    """Close a purchase order that the requester already confirmed."""
    if po.status == "closed":
        return {"ok": False, "error": "This purchase order is already closed."}
    if po.status != "confirmed":
        return {"ok": False, "error": (f"A purchase order can only be closed after the requester confirmed "
                                       f"the delivery (current status: {po.status}).")}
    po.status = "closed"
    po.closed_at = datetime.now(UTC)
    await session.flush()
    await notify(session, tenant_id=tenant.id, user_id=po.created_by, kind="po_closed",
                 title=f"PO {po.code} closed", title_ar=f"تم إغلاق أمر الشراء {po.code}",
                 body=reason[:300] or "Delivery fully confirmed.", link=f"/purchase-orders/{po.id}")
    await p2p.touch(session, tenant.id, po, actor_id=actor_id, source="po_closed")
    return {"ok": True, "status": po.status, "closed_at": po.closed_at.isoformat()}


async def list_receipts(session: AsyncSession, tenant_id: str, po_id: str) -> list[GoodsReceipt]:
    return list((await session.execute(
        select(GoodsReceipt).where(GoodsReceipt.tenant_id == tenant_id, GoodsReceipt.po_id == po_id)
        .order_by(GoodsReceipt.created_at.desc())
    )).scalars().all())


def receipt_view(receipt: GoodsReceipt) -> dict:
    return {
        "id": receipt.id, "code": receipt.code, "po_id": receipt.po_id, "supplier_id": receipt.supplier_id,
        "status": receipt.status, "delivery_note_ref": receipt.delivery_note_ref,
        "carrier": receipt.carrier, "warehouse": receipt.warehouse, "lines": receipt.lines or [],
        "discrepancy": receipt.discrepancy or {}, "notes": receipt.notes,
        "received_by": receipt.received_by, "received_at": receipt.received_at,
        "confirmed_by": receipt.confirmed_by, "confirmed_at": receipt.confirmed_at,
        "confirmation_checks": receipt.confirmation_checks or {},
        "confirmation_comment": receipt.confirmation_comment,
    }
