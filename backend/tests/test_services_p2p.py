"""The eleven-stage purchase-to-pay machine and the auditor's sealed document package.

The stage is *derived* from documents, so these tests build the documents one at a time and assert
that the rail walks forward and never backwards. The package tests care about the seal: a manifest
that verifies, a review that records a human decision, and a re-assembly that invalidates it.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.models.entities import (
    RFQ,
    Approval,
    AuditPackage,
    GoodsReceipt,
    Offer,
    P2PStageEvent,
    PurchaseOrder,
    PurchaseRequisition,
    Supplier,
    Tenant,
)
from app.db.session import SessionLocal
from app.services import audit_package, p2p


async def _tenant(session) -> Tenant:
    return (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()


async def _supplier(session, tenant) -> Supplier:
    return (await session.execute(
        select(Supplier).where(Supplier.tenant_id == tenant.id).order_by(Supplier.created_at)
    )).scalars().first()


async def _requisition(session, tenant, *, status="draft", assigned_to=None) -> PurchaseRequisition:
    pr = PurchaseRequisition(tenant_id=tenant.id, code=f"PR-{datetime.now(UTC).timestamp():.0f}",
                             title="Gearbox overhaul", status=status, department="Maintenance",
                             requested_by=None, assigned_to=assigned_to,
                             assigned_at=datetime.now(UTC) if assigned_to else None,
                             total_estimate=Decimal("12000"), currency="EGP")
    session.add(pr)
    await session.flush()
    return pr


async def _rfq(session, tenant, pr, *, sent: bool) -> RFQ:
    rfq = RFQ(tenant_id=tenant.id, code=f"RFQ-{datetime.now(UTC).timestamp():.0f}", title="Gearbox parts",
              pr_id=pr.id, status="sent" if sent else "draft", currency="EGP",
              sent_at=datetime.now(UTC) if sent else None)
    session.add(rfq)
    await session.flush()
    return rfq


async def _po(session, tenant, rfq, supplier, offer=None, *, status="issued") -> PurchaseOrder:
    po = PurchaseOrder(tenant_id=tenant.id, code=f"PO-{datetime.now(UTC).timestamp():.0f}",
                       rfq_id=rfq.id, supplier_id=supplier.id, offer_id=offer.id if offer else None,
                       amount=Decimal("12000"), currency="EGP", status=status,
                       issued_at=datetime.now(UTC),
                       lines=[{"description": "Gearbox kit", "quantity": "2", "uom": "set",
                               "unit_price": "6000.00", "line_total": "12000.00"}])
    session.add(po)
    await session.flush()
    return po


# ------------------------------------------------------------------ derivation
def test_a_bare_request_is_stage_one():
    # A request with nothing else attached sits at ``requested`` until procurement touches it.
    empty = {"pr": None, "rfq": None, "offers": [], "approvals": [], "receipts": [], "invoices": [],
             "package": None, "posted_invoice_entry": None}
    assert p2p.derive_stage(empty) == "requested"


async def test_an_rfq_in_draft_still_counts_as_procurement_working_on_it():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        supplier = await _supplier(session, tenant)
        pr = await _requisition(session, tenant)
        rfq = await _rfq(session, tenant, pr, sent=False)
        po = await _po(session, tenant, rfq, supplier)
        data = await p2p.evidence(session, po)
        assert p2p.derive_stage(data, None) == "in_procurement"
        assert p2p.derive_stage(data, po) == "po_issued"
        await session.rollback()


async def test_assignment_moves_the_request_into_procurement():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        supplier = await _supplier(session, tenant)
        buyer_id = "buyer-1"
        pr = await _requisition(session, tenant, status="ready")
        rfq = await _rfq(session, tenant, pr, sent=False)
        po = await _po(session, tenant, rfq, supplier)
        assert p2p.derive_stage(await p2p.evidence(session, po), None) == "in_procurement"

        pr.assigned_to = buyer_id
        await session.flush()
        assert p2p.derive_stage(await p2p.evidence(session, po), None) == "in_procurement"
        await session.rollback()


async def test_sending_the_rfq_and_receiving_offers_advance_the_rail():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        supplier = await _supplier(session, tenant)
        pr = await _requisition(session, tenant, status="in_procurement")
        rfq = await _rfq(session, tenant, pr, sent=False)
        po = await _po(session, tenant, rfq, supplier)
        assert p2p.derive_stage(await p2p.evidence(session, po)) == "in_procurement"

        rfq.sent_at = datetime.now(UTC)
        await session.flush()
        assert p2p.derive_stage(await p2p.evidence(session, po)) == "rfq_sent"

        session.add(Offer(tenant_id=tenant.id, rfq_id=rfq.id, supplier_id=supplier.id,
                          subtotal=Decimal("12000"), tco_total=Decimal("12000"), status="received"))
        await session.flush()
        assert p2p.derive_stage(await p2p.evidence(session, po)) == "offers_received"
        await session.rollback()


async def test_a_pending_approval_holds_the_flow_under_approval():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        supplier = await _supplier(session, tenant)
        pr = await _requisition(session, tenant, status="in_procurement")
        rfq = await _rfq(session, tenant, pr, sent=True)
        po = await _po(session, tenant, rfq, supplier)
        step = Approval(tenant_id=tenant.id, rfq_id=rfq.id, step_no=1, approver_role="finance_approver",
                        status="pending", amount=Decimal("12000"), currency="EGP")
        session.add(step)
        await session.flush()
        assert p2p.derive_stage(await p2p.evidence(session, po), None) == "under_approval"

        step.status = "approved"
        step.decision = "approved"
        await session.flush()
        # Every step decided: the rail moves on with the order rather than staying stuck.
        assert p2p.derive_stage(await p2p.evidence(session, po), po) == "po_issued"
        await session.rollback()


async def test_dispatch_moves_the_order_to_pending_receipt():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        supplier = await _supplier(session, tenant)
        pr = await _requisition(session, tenant, status="in_procurement")
        rfq = await _rfq(session, tenant, pr, sent=True)
        po = await _po(session, tenant, rfq, supplier)
        assert p2p.derive_stage(await p2p.evidence(session, po), po) == "po_issued"
        po.status = "in_transit"
        await session.flush()
        assert p2p.derive_stage(await p2p.evidence(session, po), po) == "pending_receipt"
        await session.rollback()


async def test_receipt_then_confirmation_then_package_then_posting():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        supplier = await _supplier(session, tenant)
        pr = await _requisition(session, tenant, status="in_procurement")
        rfq = await _rfq(session, tenant, pr, sent=True)
        po = await _po(session, tenant, rfq, supplier, status="in_transit")

        receipt = GoodsReceipt(tenant_id=tenant.id, code=f"GR-{datetime.now(UTC).timestamp():.0f}",
                               po_id=po.id, supplier_id=supplier.id, status="received",
                               received_at=datetime.now(UTC),
                               lines=[{"line_no": 1, "received_qty": "2", "accepted_qty": "2",
                                       "rejected_qty": "0", "uom": "set",
                                       "description": "Gearbox kit", "ordered_qty": "2"}])
        session.add(receipt)
        await session.flush()
        assert p2p.derive_stage(await p2p.evidence(session, po), po) == "received_by_warehouse"

        receipt.status = "confirmed"
        receipt.confirmed_at = datetime.now(UTC)
        receipt.confirmation_checks = {"quantity_ok": True, "quality_ok": True, "spec_ok": True}
        await session.flush()
        assert p2p.derive_stage(await p2p.evidence(session, po), po) == "confirmed_by_requester"

        session.add(AuditPackage(tenant_id=tenant.id, code=f"AP-{datetime.now(UTC).timestamp():.0f}",
                                 po_id=po.id, status="assembled", components=[], manifest_hash="x",
                                 summary={}, generated_at=datetime.now(UTC)))
        await session.flush()
        assert p2p.derive_stage(await p2p.evidence(session, po), po) == "ready_for_audit"
        await session.rollback()


async def test_a_rejected_receipt_does_not_count_as_a_receipt():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        supplier = await _supplier(session, tenant)
        pr = await _requisition(session, tenant, status="in_procurement")
        rfq = await _rfq(session, tenant, pr, sent=True)
        po = await _po(session, tenant, rfq, supplier, status="in_transit")
        session.add(GoodsReceipt(tenant_id=tenant.id, code=f"GR-{datetime.now(UTC).timestamp():.0f}",
                                 po_id=po.id, supplier_id=supplier.id, status="rejected",
                                 received_at=datetime.now(UTC), lines=[]))
        await session.flush()
        assert p2p.derive_stage(await p2p.evidence(session, po), po) == "pending_receipt"
        await session.rollback()


# ------------------------------------------------------------------ recorded history
async def test_record_stage_is_idempotent_and_writes_the_backlog_once():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        supplier = await _supplier(session, tenant)
        pr = await _requisition(session, tenant, status="in_procurement")
        rfq = await _rfq(session, tenant, pr, sent=True)
        po = await _po(session, tenant, rfq, supplier, status="in_transit")

        await p2p.record_stage(session, tenant.id, po, "pending_receipt", actor_id="u1", source="po.dispatch")
        first = list((await session.execute(
            select(P2PStageEvent).where(P2PStageEvent.po_id == po.id))).scalars().all())
        assert [row.stage for row in first] == ["requested", "in_procurement", "rfq_sent", "offers_received",
                                               "under_approval", "po_issued", "pending_receipt"]
        stamps = {row.stage: row.reached_at for row in first}
        assert all(row.source == "po.dispatch" for row in first)

        await p2p.record_stage(session, tenant.id, po, "pending_receipt", actor_id="u2", source="again")
        second = list((await session.execute(
            select(P2PStageEvent).where(P2PStageEvent.po_id == po.id))).scalars().all())
        assert len(second) == len(first)
        assert {row.stage: row.reached_at for row in second} == stamps
        assert all(row.actor_id == "u1" for row in second)
        await session.rollback()


async def test_touch_returns_and_persists_the_current_stage():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        supplier = await _supplier(session, tenant)
        pr = await _requisition(session, tenant, status="in_procurement")
        rfq = await _rfq(session, tenant, pr, sent=True)
        po = await _po(session, tenant, rfq, supplier)
        stage = await p2p.touch(session, tenant.id, po, actor_id="u1", source="po.issue")
        assert stage == "po_issued"
        stored = list((await session.execute(
            select(P2PStageEvent).where(P2PStageEvent.po_id == po.id))).scalars().all())
        assert stored and stored[-1].stage == "po_issued"
        await session.rollback()


async def test_lifecycle_reports_state_labels_and_the_next_action():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        supplier = await _supplier(session, tenant)
        pr = await _requisition(session, tenant, status="in_procurement")
        rfq = await _rfq(session, tenant, pr, sent=True)
        po = await _po(session, tenant, rfq, supplier, status="in_transit")

        view = await p2p.lifecycle(session, tenant.id, po, record=True, actor_id="u1", source="test")
        assert view["current"] == "pending_receipt"
        assert view["current_label_en"] == "Pending receipt"
        assert view["current_label_ar"] == "بانتظار الاستلام"
        assert view["next_action_en"] == "Record the goods receipt at the warehouse"
        assert len(view["stages"]) == 11
        index = {row["code"]: row for row in view["stages"]}
        assert index["pending_receipt"]["state"] == "current"
        assert index["po_issued"]["state"] == "done"
        assert index["posted_to_accounts"]["state"] == "pending"
        assert [row["order"] for row in view["stages"]] == list(range(11))
        assert all(row["state"] == "done" for row in view["stages"][:6])
        assert view["history"][-1]["stage"] == "pending_receipt"
        assert view["history"][-1]["source"] == "test"
        await session.rollback()


async def test_stage_summary_is_compact_for_lists():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        supplier = await _supplier(session, tenant)
        pr = await _requisition(session, tenant, status="in_procurement")
        rfq = await _rfq(session, tenant, pr, sent=True)
        po = await _po(session, tenant, rfq, supplier)
        summary = await p2p.stage_summary(session, tenant.id, po)
        assert summary == {"stage": "po_issued", "label_en": "PO issued", "label_ar": "صدر أمر الشراء",
                           "ready_for_audit": False, "posted": False}
        await session.rollback()


# ------------------------------------------------------------------ the auditor's package
async def _confirmed_world(session, tenant):
    supplier = await _supplier(session, tenant)
    pr = await _requisition(session, tenant, status="in_procurement")
    rfq = await _rfq(session, tenant, pr, sent=True)
    po = await _po(session, tenant, rfq, supplier, status="in_transit")
    receipt = GoodsReceipt(
        tenant_id=tenant.id, code=f"GR-{datetime.now(UTC).timestamp():.0f}", po_id=po.id,
        supplier_id=supplier.id, status="confirmed", warehouse="Cairo plant",
        delivery_note_ref="DN-99", received_at=datetime.now(UTC), confirmed_at=datetime.now(UTC),
        confirmed_by=None, confirmation_checks={"quantity_ok": True, "quality_ok": True, "spec_ok": True},
        lines=[{"line_no": 1, "description": "Gearbox kit", "ordered_qty": "2", "received_qty": "2",
                "accepted_qty": "2", "rejected_qty": "0", "uom": "set", "condition": "ok", "note": ""}],
    )
    session.add(receipt)
    session.add(Approval(tenant_id=tenant.id, rfq_id=rfq.id, step_no=1, approver_role="finance_approver",
                         status="approved", decision="approved", amount=Decimal("12000"), currency="EGP",
                         decided_at=datetime.now(UTC), comment="Within budget"))
    await session.flush()
    return po, receipt


async def test_assembly_seals_every_document_that_exists():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po, _receipt = await _confirmed_world(session, tenant)
        package = await audit_package.assemble(session, tenant, po, notes="Quarterly sampling")
        assert package.status == "assembled"
        assert package.code.startswith("AP-")
        kinds = [row["kind"] for row in package.components]
        assert kinds[0] == "purchase_order"
        assert {"rfq", "approval_chain", "goods_receipt"} <= set(kinds)
        assert package.summary["receipts"] == 1
        assert package.summary["confirmations"] == 1
        assert package.summary["approvals"] == 1
        assert package.summary["po_code"] == po.code
        assert package.comment == "Quarterly sampling"
        assert len(package.manifest_hash) == 64

        view = audit_package.package_view(package)
        assert view["status_label"] == "Ready for review"
        assert view["status_label_ar"] == "جاهزة للمراجعة"
        assert view["can_post_to_accounts"] is False
        assert view["component_count"] == len(package.components)
        stripped = audit_package.package_view(package, include_payload=False)
        assert all("payload" not in row for row in stripped["components"])
        await session.rollback()


async def test_the_manifest_verifies_and_detects_tampering():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po, _ = await _confirmed_world(session, tenant)
        package = await audit_package.assemble(session, tenant, po)
        intact = await audit_package.verify_manifest(package)
        assert intact["intact"] is True
        assert intact["broken_components"] == []

        # Somebody edits the amount recorded in the package after it was sealed.
        components = [dict(row) for row in package.components]
        components[0]["payload"]["amount"] = "1.00"
        package.components = components
        broken = await audit_package.verify_manifest(package)
        assert broken["intact"] is False
        assert broken["broken_components"] == ["purchase_order"]
        await session.rollback()


async def test_review_records_the_human_decision_and_gates_posting():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po, _ = await _confirmed_world(session, tenant)
        package = await audit_package.assemble(session, tenant, po)

        queried = await audit_package.review(session, tenant, package, reviewer_id=None,
                                            decision="queried", comment="Delivery note reference missing")
        assert queried["status"] == "queried"
        assert audit_package.package_view(package)["can_post_to_accounts"] is False

        empty_query = await audit_package.review(session, tenant, package, reviewer_id=None,
                                                decision="queried", comment="  ")
        assert empty_query["ok"] is False

        accepted = await audit_package.review(session, tenant, package, reviewer_id=None,
                                             decision="accepted", comment="Evidence complete")
        assert accepted["status"] == "reviewed"
        view = audit_package.package_view(package)
        assert view["can_post_to_accounts"] is True
        assert view["decision"] == "accepted"
        assert view["reviewed_at"] is not None

        # An accepted package cannot be reviewed twice.
        again = await audit_package.review(session, tenant, package, reviewer_id=None, decision="queried",
                                          comment="Changed my mind")
        assert again["ok"] is False

        with pytest.raises(ValueError):
            await audit_package.review(session, tenant, package, reviewer_id=None, decision="approved")
        await session.rollback()


async def test_new_evidence_invalidates_a_previous_acceptance():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po, receipt = await _confirmed_world(session, tenant)
        package = await audit_package.assemble(session, tenant, po)
        await audit_package.review(session, tenant, package, reviewer_id=None, decision="accepted",
                                   comment="Fine")
        assert package.status == "reviewed"

        # A second receipt arrives: the sealed set no longer describes the documents.
        session.add(GoodsReceipt(tenant_id=tenant.id, code=f"GR-{datetime.now(UTC).timestamp():.0f}",
                                 po_id=po.id, supplier_id=receipt.supplier_id, status="confirmed",
                                 received_at=datetime.now(UTC), lines=[]))
        await session.flush()
        rebuilt = await audit_package.assemble(session, tenant, po)
        assert rebuilt.id == package.id
        assert rebuilt.status == "assembled"
        assert rebuilt.decision is None and rebuilt.reviewer_id is None and rebuilt.reviewed_at is None
        assert rebuilt.summary["receipts"] == 2
        assert (await audit_package.verify_manifest(rebuilt))["intact"] is True
        await session.rollback()


async def test_latest_returns_the_sealed_package_and_toc_orders_the_documents():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po, _ = await _confirmed_world(session, tenant)
        assert await audit_package.latest(session, tenant.id, po.id) is None
        package = await audit_package.assemble(session, tenant, po)
        found = await audit_package.latest(session, tenant.id, po.id)
        assert found is not None and found.id == package.id
        toc = await audit_package.package_documents(package)
        assert toc[0]["kind"] == "purchase_order"
        assert toc[0]["label_ar"].startswith("أمر الشراء")
        assert all(len(row["hash"]) == 16 for row in toc)
        await session.rollback()
