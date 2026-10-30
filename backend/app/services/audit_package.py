"""The auditor's document package (phase 2, requirement 1.7).

A package is assembled from the documents that already exist — it never invents evidence:

* the goods receipt notes (warehouse), with their lines and the requester's confirmation;
* the purchase order and the awarded offer that produced it;
* the full approval record (every step, who decided, when, comment);
* the supplier invoice, its stored three-way match and the payment suggestion;
* the journal entries posted against those documents;
* any attachments registered on the order.

Every component is hashed and the hashes are hashed again into a manifest, so a reviewer can prove
later that the bundle was not altered after it was sealed. Reviewing a package is a human-only act
(``audit_package:review``) that records accepted / queried with a written comment; a query keeps the
order out of accounting until it is answered.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.entities import (
    RFQ,
    Approval,
    AuditPackage,
    Offer,
    PurchaseOrder,
    Supplier,
    Tenant,
    User,
)
from app.services import accounting, invoicing, receiving
from app.services.common import next_code, notify, notify_role

log = logging.getLogger("procureai.audit_package")

STATUS_LABELS = {
    "assembled": ("Ready for review", "جاهزة للمراجعة"),
    "reviewed": ("Reviewed and accepted", "تمت المراجعة والقبول"),
    "queried": ("Query raised by the auditor", "استيضاح من المراجع"),
}

BASIS_EN = "Evidence assembled from the documents recorded in ProcureAI. Hashes are SHA-256 over each component payload; the manifest hash seals the set."
BASIS_AR = "الأدلة مجمّعة من المستندات المسجلة في ProcureAI. كل مكوّن مُوقّع بـ SHA-256، وتوقيع القائمة يجمعها."


def _jsonable(value):  # noqa: ANN001, ANN202
    """Decimals and datetimes become strings so the manifest is stable across dialects."""
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _component(kind: str, label_en: str, label_ar: str, payload: dict, ref_id: str | None = None) -> dict:
    safe = _jsonable(payload)
    return {
        "kind": kind, "label_en": label_en, "label_ar": label_ar, "ref_id": ref_id,
        "hash": accounting.fingerprint(safe), "payload": safe,
    }


async def assemble(session: AsyncSession, tenant: Tenant, po: PurchaseOrder, *,
                   generated_by: str | None = None, invoice_id: str | None = None,
                   notes: str = "") -> AuditPackage:
    """Build (or rebuild) the sealed package for a purchase order."""
    supplier = (await session.execute(select(Supplier).where(Supplier.id == po.supplier_id))).scalar_one_or_none()
    rfq = (await session.execute(select(RFQ).where(RFQ.id == po.rfq_id))).scalar_one_or_none()
    receipts = await receiving.list_receipts(session, tenant.id, po.id)
    invoices = await invoicing.list_invoices(session, tenant.id, po.id)
    if invoice_id:
        invoices = [row for row in invoices if row.id == invoice_id] or invoices
    approvals = list((await session.execute(
        select(Approval).where(Approval.tenant_id == tenant.id, Approval.rfq_id == po.rfq_id)
        .order_by(Approval.step_no)
    )).scalars().all())
    offer = None
    if po.offer_id:
        offer = (await session.execute(select(Offer).where(Offer.id == po.offer_id))).scalar_one_or_none()
    entries = await accounting.postings_for_po(session, tenant.id, po.id)

    approver_names: dict[str, str] = {}
    ids = {row.approver_id for row in approvals if row.approver_id}
    if ids:
        approver_names = {user.id: user.full_name or user.email for user in (await session.execute(
            select(User).where(User.id.in_(ids)))).scalars().all()}

    components: list[dict] = []
    components.append(_component(
        "purchase_order", f"Purchase order {po.code}", f"أمر الشراء {po.code}",
        {"code": po.code, "amount": str(po.amount), "currency": po.currency, "status": po.status,
         "issued_at": po.issued_at.isoformat() if po.issued_at else None,
         "supplier": supplier.name if supplier else "", "lines": receiving.po_lines(po),
         "rfq_code": rfq.code if rfq else None, "document_html": po.document_html},
        po.id,
    ))
    if rfq:
        components.append(_component(
            "rfq", f"Request for quotation {rfq.code}", f"طلب عرض السعر {rfq.code}",
            {"code": rfq.code, "title": rfq.title, "sent_at": rfq.sent_at.isoformat() if rfq.sent_at else None,
             "closed_at": rfq.closed_at.isoformat() if rfq.closed_at else None,
             "payment_terms": rfq.payment_terms, "incoterm": rfq.incoterm},
            rfq.id,
        ))
    if offer:
        components.append(_component(
            "offer", "Awarded offer", "العرض الفائز",
            {"supplier": supplier.name if supplier else "", "currency": offer.currency,
             "subtotal": str(offer.subtotal), "tco_total": str(offer.tco_total),
             "weighted_score": float(offer.weighted_score or 0), "status": offer.status,
             "round_no": offer.round_no},
            offer.id,
        ))
    components.append(_component(
        "approval_chain", "Procurement approval record", "سجل اعتمادات المشتريات",
        {"chain_id": approvals[0].chain_id if approvals else None,
         "steps": [{"step_no": row.step_no, "role": row.approver_role, "status": row.status,
                    "decision": row.decision, "amount": str(row.amount),
                    "decided_at": row.decided_at.isoformat() if row.decided_at else None,
                    "due_at": row.due_at.isoformat() if row.due_at else None,
                    "approver": approver_names.get(row.approver_id or "", ""),
                    "comment": row.comment} for row in approvals]},
        approvals[0].chain_id if approvals and approvals[0].chain_id else None,
    ))
    for receipt in receipts:
        components.append(_component(
            "goods_receipt", f"Goods receipt note {receipt.code}", f"إشعار استلام {receipt.code}",
            {"code": receipt.code, "status": receipt.status, "warehouse": receipt.warehouse,
             "carrier": receipt.carrier, "delivery_note_ref": receipt.delivery_note_ref,
             "received_at": receipt.received_at.isoformat() if receipt.received_at else None,
             "received_by": receipt.received_by, "lines": receipt.lines,
             "discrepancy": receipt.discrepancy,
             "confirmed_at": receipt.confirmed_at.isoformat() if receipt.confirmed_at else None,
             "confirmed_by": receipt.confirmed_by,
             "confirmation_checks": receipt.confirmation_checks,
             "confirmation_comment": receipt.confirmation_comment}, receipt.id,
        ))
    for invoice in invoices:
        components.append(_component(
            "supplier_invoice", f"Supplier invoice {invoice.number}", f"فاتورة المورد {invoice.number}",
            {"number": invoice.number, "status": invoice.status, "currency": invoice.currency,
             "subtotal": str(invoice.subtotal), "tax_amount": str(invoice.tax_amount),
             "total": str(invoice.total), "paid_amount": str(invoice.paid_amount),
             "invoice_date": invoice.invoice_date.isoformat() if invoice.invoice_date else None,
             "approved_by": invoice.approved_by,
             "approved_at": invoice.approved_at.isoformat() if invoice.approved_at else None,
             "lines": invoice.lines, "match_result": invoice.match_result,
             "payment_suggestion": invoice.payment_suggestion}, invoice.id,
        ))
    for entry in entries:
        components.append(_component(
            "journal_entry", f"Journal entry {entry.number} ({entry.status})",
            f"قيد {entry.number} ({entry.status})",
            accounting.entry_view(entry), entry.id,
        ))

    manifest = accounting.fingerprint({"po": po.id, "components": [row["hash"] for row in components]})
    summary = {
        "po_code": po.code,
        "amount": str(po.amount),
        "currency": po.currency,
        "supplier": supplier.name if supplier else "",
        "supplier_ar": (supplier.name_ar if supplier else "") or "",
        "receipts": len(receipts),
        "confirmations": sum(1 for row in receipts if row.status == "confirmed"),
        "invoices": len(invoices),
        "match_status": [row.match_result.get("status") for row in invoices if row.match_result],
        "approvals": len(approvals),
        "journal_entries": len(entries),
        "posted_entries": sum(1 for row in entries if row.status == "posted"),
        "basis_en": BASIS_EN,
        "basis_ar": BASIS_AR,
    }

    package = (await session.execute(
        select(AuditPackage).where(AuditPackage.tenant_id == tenant.id, AuditPackage.po_id == po.id)
        .order_by(AuditPackage.created_at.desc())
    )).scalars().first()
    if package and package.status == "reviewed":
        # New evidence invalidates a previous acceptance: the seal is rebuilt and the review is
        # cleared, so nobody can post against a review that no longer describes the documents.
        package.status = "assembled"
        package.decision = None
        package.reviewer_id = None
        package.reviewed_at = None
    if package and package.status in {"assembled", "queried"}:
        package.components = components
        package.manifest_hash = manifest
        package.summary = summary
        package.generated_by = generated_by
        package.generated_at = datetime.now(UTC)
        package.invoice_id = invoices[-1].id if invoices else None
        package.comment = notes[:2000]
    else:
        package = AuditPackage(
            tenant_id=tenant.id, code=await next_code(session, AuditPackage, tenant.id, "AP"),
            po_id=po.id, invoice_id=invoices[-1].id if invoices else None, status="assembled",
            components=components, manifest_hash=manifest, summary=summary,
            generated_by=generated_by, generated_at=datetime.now(UTC), comment=notes[:2000],
            created_by=generated_by,
        )
        session.add(package)
    await session.flush()
    await notify_role(session, tenant_id=tenant.id, role_code="auditor", kind="audit_package_ready",
                      title=f"Audit package ready: {po.code}", title_ar=f"حزمة مراجعة جاهزة: {po.code}",
                      body=f"{len(components)} documents, sealed {manifest[:12]}…",
                      link=f"/purchase-orders/{po.id}", severity="warning")
    log.info("audit package %s assembled for PO %s (%s components)", package.code, po.code, len(components))
    return package


async def review(session: AsyncSession, tenant: Tenant, package: AuditPackage, *, reviewer_id: str | None,
                 decision: str, comment: str = "") -> dict:
    """The auditor accepts the evidence or raises a query. Never approves money by itself."""
    if decision not in {"accepted", "queried"}:
        raise ValueError("decision must be 'accepted' or 'queried'.")
    if package.status == "reviewed":
        return {"ok": False, "error": f"Package {package.code} is already accepted."}
    if decision == "queried" and not comment.strip():
        return {"ok": False, "error": "A query needs a written explanation for procurement."}
    package.decision = decision
    package.status = "reviewed" if decision == "accepted" else "queried"
    package.reviewer_id = reviewer_id
    package.reviewed_at = datetime.now(UTC)
    package.comment = comment.strip()[:2000] if comment else package.comment
    await session.flush()
    po = (await session.execute(select(PurchaseOrder).where(PurchaseOrder.id == package.po_id))).scalar_one_or_none()
    href = f"/purchase-orders/{package.po_id}"
    if decision == "accepted":
        await notify(session, tenant_id=tenant.id, user_id=po.created_by if po else None,
                     kind="audit_package_accepted", title=f"Audit package {package.code} accepted",
                     title_ar=f"تم قبول حزمة المراجعة {package.code}",
                     body=comment[:300] or "Evidence accepted; the order may be posted to the accounts.",
                     link=href)
    else:
        await notify(session, tenant_id=tenant.id, user_id=po.created_by if po else None,
                     kind="audit_package_queried", title=f"Audit query on {package.code}",
                     title_ar=f"استيضاح من المراجع على {package.code}",
                     body=comment[:300], link=href, severity="warning")
    return {"ok": True, "package_id": package.id, "status": package.status, "decision": decision}


async def latest(session: AsyncSession, tenant_id: str, po_id: str) -> AuditPackage | None:
    return (await session.execute(
        select(AuditPackage).where(AuditPackage.tenant_id == tenant_id, AuditPackage.po_id == po_id)
        .order_by(AuditPackage.created_at.desc())
    )).scalars().first()


def package_view(package: AuditPackage, *, include_payload: bool = True) -> dict:
    label_en, label_ar = STATUS_LABELS.get(package.status, (package.status, package.status))
    components = [
        {key: value for key, value in row.items() if include_payload or key != "payload"}
        for row in (package.components or [])
    ]
    return {
        "id": package.id, "code": package.code, "po_id": package.po_id,
        "invoice_id": package.invoice_id, "status": package.status,
        "status_label": label_en, "status_label_ar": label_ar,
        "manifest_hash": package.manifest_hash, "summary": package.summary or {},
        "components": components, "component_count": len(package.components or []),
        "generated_by": package.generated_by, "generated_at": package.generated_at,
        "reviewer_id": package.reviewer_id, "reviewed_at": package.reviewed_at,
        "decision": package.decision, "comment": package.comment,
        "can_post_to_accounts": package.status == "reviewed" and package.decision == "accepted",
    }


async def verify_manifest(package: AuditPackage) -> dict:
    """Re-hash every component and compare with the seal recorded at assembly."""
    broken: list[str] = []
    for row in package.components or []:
        if accounting.fingerprint(row.get("payload") or {}) != row.get("hash"):
            broken.append(row.get("kind", "component"))
    expected = accounting.fingerprint(
        {"po": package.po_id, "components": [row.get("hash") for row in package.components or []]}
    )
    return {
        "intact": not broken and expected == package.manifest_hash,
        "broken_components": broken,
        "manifest_hash": package.manifest_hash,
        "recomputed": expected,
    }


async def package_documents(package: AuditPackage) -> list[dict]:
    """Table of contents for the printable package (bilingual, ordered as the auditor reads it)."""
    rows: list[dict] = []
    for component in package.components or []:
        rows.append({
            "kind": component.get("kind"),
            "label_en": component.get("label_en"),
            "label_ar": component.get("label_ar"),
            "hash": (component.get("hash") or "")[:16],
        })
    return rows


