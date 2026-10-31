"""Requirement intake and master-data endpoints (FR-IN-1..7, FR-SUP-1..6)."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import Context, require
from app.core.audit import audit
from app.core.config import settings
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.db.models.entities import (
    Attachment,
    Category,
    ClarificationQuestion,
    Plant,
    PRLine,
    PurchaseRequisition,
    Supplier,
    SupplierCertification,
    TaxonomyNode,
    User,
)
from app.services import intake, numbers
from app.services.common import notify
from app.services.discovery import approve_candidates, list_candidates, reject_candidates, run_discovery
from app.services.erp_service import import_requisitions, test_connection
from app.services.portal import create_portal_token
from app.services.suppliers import (
    add_flag,
    create_supplier,
    search_suppliers,
    supplier_brief,
    supplier_briefs,
    update_ratings,
)

log = logging.getLogger("procureai.api.intake")
router = APIRouter(tags=["intake"])


# ------------------------------------------------------------------ masters
@router.get("/plants", summary="List plants")
async def list_plants(ctx: Context = Depends(require("pr:read"))):
    rows = list((await ctx.session.execute(
        select(Plant).where(Plant.tenant_id == ctx.tenant_id, Plant.deleted_at.is_(None)).order_by(Plant.name)
    )).scalars().all())
    return {"items": [{"id": p.id, "code": p.code, "name": p.name, "name_ar": p.name_ar,
                       "city": p.city, "country": p.country, "is_active": p.is_active} for p in rows]}


class PlantIn(BaseModel):
    code: str
    name: str
    name_ar: str = ""
    city: str = ""
    country: str = "EG"
    address: str = ""


@router.post("/plants", status_code=201, summary="Create a plant")
async def create_plant(payload: PlantIn, ctx: Context = Depends(require("settings:update"))):
    plant = Plant(tenant_id=ctx.tenant_id, created_by=ctx.user_id, **payload.model_dump())
    ctx.session.add(plant)
    await ctx.session.flush()
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="plant.created", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="plant", entity_id=plant.id, after=payload.model_dump())
    await ctx.session.commit()
    return {"id": plant.id}


@router.get("/categories", summary="List tenant categories")
async def list_categories(ctx: Context = Depends(require("pr:read"))):
    rows = list((await ctx.session.execute(
        select(Category).where(Category.tenant_id == ctx.tenant_id).order_by(Category.code)
    )).scalars().all())
    return {"items": [{"id": c.id, "code": c.code, "name": c.name, "name_ar": c.name_ar,
                       "taxonomy_code": c.taxonomy_code, "weights": c.default_weights} for c in rows]}


@router.get("/taxonomy", summary="UNSPSC-based taxonomy (searchable)")
async def taxonomy(ctx: Context = Depends(require("pr:read")), q: str = ""):
    rows = list((await ctx.session.execute(select(TaxonomyNode).order_by(TaxonomyNode.code))).scalars().all())
    if q:
        from app.services.taxonomy import normalize_text

        needle = normalize_text(q)
        rows = [r for r in rows if needle in normalize_text(r.name) or needle in normalize_text(r.name_ar)
                or any(needle in normalize_text(k) for k in (r.keywords or []))]
    return {"items": [{"code": r.code, "name": r.name, "name_ar": r.name_ar, "parent_code": r.parent_code,
                       "default_uom": r.default_uom, "keywords": r.keywords} for r in rows]}


@router.get("/unit-conversions", summary="Unit conversion table used by normalization")
async def unit_conversions(ctx: Context = Depends(require("pr:read"))):
    return {"items": [
        {"from": from_unit, "to": to_unit,
         "factor": str(numbers.unit_factor(from_unit, to_unit) or "")}
        for from_unit, to_unit in [("t", "kg"), ("kg", "t"), ("lb", "kg"), ("m", "ft"), ("doz", "pcs"),
                                    ("drum", "l")]
    ]}


# ------------------------------------------------------------------ PRs
@router.get("/requests", summary="List purchase requisitions")
async def list_prs(ctx: Context = Depends(require("pr:read")), status: str | None = None,
                   q: str | None = None, page: int = 1, page_size: int = 25):
    from sqlalchemy import func

    filters = [PurchaseRequisition.tenant_id == ctx.tenant_id, PurchaseRequisition.deleted_at.is_(None)]
    if status:
        filters.append(PurchaseRequisition.status == status)
    if q:
        filters.append(PurchaseRequisition.title.ilike(f"%{q}%"))
    total = (await ctx.session.execute(
        select(func.count()).select_from(PurchaseRequisition).where(*filters)
    )).scalar_one()
    rows = list((await ctx.session.execute(
        select(PurchaseRequisition).where(*filters)
        .order_by(PurchaseRequisition.created_at.desc())
        .limit(page_size).offset((page - 1) * page_size)
    )).scalars().all())
    return {"items": [{
        "id": pr.id, "code": pr.code, "title": pr.title, "status": pr.status, "source": pr.source,
        "currency": pr.currency, "total_estimate": str(pr.total_estimate),
        "confidence": float(pr.confidence or 0), "needed_by": pr.needed_by,
        "clarification_resolved": pr.clarification_resolved, "created_at": pr.created_at,
        "department": pr.department, "assigned_to": pr.assigned_to, "assigned_at": pr.assigned_at,
    } for pr in rows], "total": int(total), "page": page, "page_size": page_size}


@router.get("/requests/{pr_id}", summary="Requisition detail with lines and open questions")
async def get_pr(pr_id: str, ctx: Context = Depends(require("pr:read"))):
    pr = (await ctx.session.execute(
        select(PurchaseRequisition).where(PurchaseRequisition.id == pr_id,
                                          PurchaseRequisition.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not pr:
        raise NotFound("Requisition not found.")
    lines = list((await ctx.session.execute(
        select(PRLine).where(PRLine.pr_id == pr.id).order_by(PRLine.line_no)
    )).scalars().all())
    questions = list((await ctx.session.execute(
        select(ClarificationQuestion).where(ClarificationQuestion.pr_id == pr.id)
        .order_by(ClarificationQuestion.asked_at)
    )).scalars().all())
    from app.services.catalog_data import TAXONOMY
    from app.services.taxonomy import Classifier

    classifier = Classifier(TAXONOMY)
    return {
        "id": pr.id, "code": pr.code, "title": pr.title, "status": pr.status, "source": pr.source,
        "currency": pr.currency, "total_estimate": str(pr.total_estimate),
        "confidence": float(pr.confidence or 0), "needed_by": pr.needed_by,
        "clarification_resolved": pr.clarification_resolved, "notes": pr.notes,
        "plant_id": pr.plant_id, "raw_input": pr.raw_input[:4000],
        "department": pr.department, "assigned_to": pr.assigned_to, "assigned_at": pr.assigned_at,
        "requested_by": pr.requested_by,
        "lines": [{
            "id": line.id, "line_no": line.line_no, "description": line.description,
            "quantity": str(line.quantity), "uom": line.uom, "taxonomy_code": line.taxonomy_code,
            "category_code": line.category_code, "target_price": str(line.target_price or ""),
            "currency": line.currency, "confidence": float(line.normalization_confidence or 0),
            "needs_review": float(line.normalization_confidence or 0) < 0.75,
            "candidates": [c.__dict__ for c in classifier.classify(line.description)],
            "attributes": line.attributes, "quoted_text": line.quoted_text,
        } for line in lines],
        "clarifications": [{
            "id": q.id, "field": q.field, "question": q.question, "question_ar": q.question_ar,
            "status": q.status, "answer": q.answer,
        } for q in questions],
    }


# ------------------------------------------------------------------ Excel intake
@router.post("/intake/excel/preview", summary="Upload Excel/CSV and preview auto-mapping + validation")
async def excel_preview(ctx: Context = Depends(require("pr:create")), file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise ValidationFailed(f"File exceeds {settings.max_upload_mb} MB.")
    filename = file.filename or "upload.xlsx"
    if not filename.lower().endswith((".xlsx", ".xls", ".xlsm", ".csv")):
        raise ValidationFailed("Only .xlsx, .xls, .xlsm and .csv are supported.")
    payload = await intake.parse_spreadsheet(ctx.session, ctx.tenant, data=data, filename=filename)
    attachment = await intake.store_upload(ctx.session, ctx.tenant, data=data, filename=filename,
                                           payload=payload, uploaded_by=ctx.user_id)
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="intake.excel_parsed", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="attachment", entity_id=attachment.id,
                after={"rows": payload["stats"]["rows"]}, ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return {**payload, "attachment_id": attachment.id}


class ImportConfirmIn(BaseModel):
    attachment_id: str
    mapping: dict[str, str] = Field(default_factory=dict)
    rows: list[dict] = Field(default_factory=list)
    plant_code: str | None = None
    title: str | None = None


@router.post("/intake/excel/confirm", status_code=201, summary="Confirm mapping and create the requisition")
async def excel_confirm(payload: ImportConfirmIn, ctx: Context = Depends(require("pr:create"))):
    attachment = (await ctx.session.execute(
        select(Attachment).where(Attachment.id == payload.attachment_id,
                                 Attachment.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not attachment:
        raise NotFound("Upload not found.")
    pr = await intake.confirm_excel_import(ctx.session, ctx.tenant, attachment=attachment,
                                           rows=payload.rows, mapping=payload.mapping,
                                           plant_code=payload.plant_code, requested_by=ctx.user_id,
                                           title=payload.title)
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="intake.excel_imported", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="purchase_requisition", entity_id=pr.id,
                after={"code": pr.code}, ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return {"id": pr.id, "code": pr.code, "status": pr.status,
            "clarification_resolved": pr.clarification_resolved}


# ------------------------------------------------------------------ text / voice / scan / email
class TextIntakeIn(BaseModel):
    text: str = Field(min_length=2, max_length=20000)
    plant_code: str | None = None
    title: str | None = None


@router.post("/intake/text", status_code=201, summary="Create a requisition from free text (AR/EN)")
async def intake_text(payload: TextIntakeIn, ctx: Context = Depends(require("pr:create"))):
    pr = await intake.intake_free_text(ctx.session, ctx.tenant, text=payload.text, source="text",
                                       requested_by=ctx.user_id, plant_code=payload.plant_code,
                                       title=payload.title)
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="intake.text", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="purchase_requisition", entity_id=pr.id,
                after={"code": pr.code}, ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return {"id": pr.id, "code": pr.code, "status": pr.status}


@router.post("/intake/voice", status_code=201, summary="Voice request via the STT adapter")
async def intake_voice(ctx: Context = Depends(require("pr:create")), file: UploadFile = File(...)):
    data = await file.read()
    pr, transcript = await intake.intake_voice(ctx.session, ctx.tenant, data=data,
                                               filename=file.filename or "voice.webm",
                                               requested_by=ctx.user_id)
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="intake.voice", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="purchase_requisition", entity_id=pr.id,
                after={"code": pr.code}, ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return {"id": pr.id, "code": pr.code, "status": pr.status, "transcript": transcript}


@router.post("/intake/scan", status_code=201, summary="Scanned / photographed request via OCR")
async def intake_scan(ctx: Context = Depends(require("pr:create")), file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise ValidationFailed(f"File exceeds {settings.max_upload_mb} MB.")
    pr, text = await intake.intake_scan(ctx.session, ctx.tenant, data=data,
                                        filename=file.filename or "scan.png", requested_by=ctx.user_id)
    await ctx.session.commit()
    return {"id": pr.id, "code": pr.code, "status": pr.status, "ocr_text": text}


class EmailIntakeIn(BaseModel):
    sender: str
    subject: str
    body: str


@router.post("/intake/email", status_code=201, summary="Email-to-PR (inbound address / forwarded mail)")
async def intake_email(payload: EmailIntakeIn, ctx: Context = Depends(require("pr:create"))):
    pr = await intake.intake_email(ctx.session, ctx.tenant, sender=payload.sender, subject=payload.subject,
                                   body=payload.body, requested_by=ctx.user_id)
    await ctx.session.commit()
    return {"id": pr.id, "code": pr.code, "status": pr.status}


@router.get("/intake/inbound-address", summary="Per-tenant inbound email address")
async def inbound_address(ctx: Context = Depends(require("pr:read"))):
    from app.services.common import inbound_address as address

    return {"address": address(ctx.tenant), "note": "Forward or send requirements to this address."}


# ------------------------------------------------------------------ clarifications & normalization
class AnswerIn(BaseModel):
    answer: str | None = None
    waive: bool = False


@router.post("/requests/{pr_id}/clarifications/{question_id}/answer", summary="Answer or waive a clarification")
async def answer_clarification(pr_id: str, question_id: str, payload: AnswerIn,
                               ctx: Context = Depends(require("pr:update"))):
    question = (await ctx.session.execute(
        select(ClarificationQuestion).where(ClarificationQuestion.id == question_id,
                                            ClarificationQuestion.tenant_id == ctx.tenant_id,
                                            ClarificationQuestion.pr_id == pr_id)
    )).scalar_one_or_none()
    if not question:
        raise NotFound("Question not found.")
    if not payload.waive and not payload.answer:
        raise ValidationFailed("Provide an answer or waive the question.")
    await intake.answer_clarification(ctx.session, ctx.tenant, question, answer=payload.answer,
                                      waived=payload.waive, user_id=ctx.user_id)
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="pr.clarification_answered",
                actor_id=ctx.user_id, actor_label=ctx.user.email, entity_type="clarification_question",
                entity_id=question.id, after={"status": question.status}, ip=ctx.ip,
                request_id=ctx.request_id)
    await ctx.session.commit()
    return {"ok": True, "status": question.status}


class NormalizeIn(BaseModel):
    taxonomy_code: str


@router.post("/requests/{pr_id}/lines/{line_id}/normalize", summary="Confirm/correct item normalization")
async def normalize_line_endpoint(pr_id: str, line_id: str, payload: NormalizeIn,
                                  ctx: Context = Depends(require("pr:update"))):
    line = (await ctx.session.execute(
        select(PRLine).where(PRLine.id == line_id, PRLine.pr_id == pr_id, PRLine.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not line:
        raise NotFound("Line not found.")
    node = (await ctx.session.execute(
        select(TaxonomyNode).where(TaxonomyNode.code == payload.taxonomy_code)
    )).scalar_one_or_none()
    if not node:
        raise ValidationFailed("Unknown taxonomy code.")
    await intake.confirm_line_normalization(ctx.session, ctx.tenant, line, payload.taxonomy_code, ctx.user)
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="pr.line_normalized", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="pr_line", entity_id=line.id,
                after={"taxonomy_code": payload.taxonomy_code})
    await ctx.session.commit()
    return {"ok": True, "taxonomy_code": line.taxonomy_code}


@router.post("/requests/{pr_id}/submit", summary="Mark a requisition ready for RFQ creation")
async def submit_pr(pr_id: str, ctx: Context = Depends(require("pr:update"))):
    pr = (await ctx.session.execute(
        select(PurchaseRequisition).where(PurchaseRequisition.id == pr_id,
                                          PurchaseRequisition.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not pr:
        raise NotFound("Requisition not found.")
    open_questions = list((await ctx.session.execute(
        select(ClarificationQuestion).where(ClarificationQuestion.pr_id == pr.id,
                                            ClarificationQuestion.status == "open")
    )).scalars().all())
    if open_questions:
        raise ValidationFailed("Resolve or waive all clarification questions before creating an RFQ.",
                               extra={"open_questions": [q.field for q in open_questions]})
    pr.status = "ready"
    pr.clarification_resolved = True
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="pr.submitted", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="purchase_requisition", entity_id=pr.id)
    await ctx.session.commit()
    return {"ok": True, "status": pr.status}


class AssignIn(BaseModel):
    """Route a requisition to a named procurement owner (phase 2, requirement 1.1)."""

    assigned_to: str | None = None
    department: str = ""


@router.post("/requests/{pr_id}/assign", summary="Route a requisition to a procurement owner")
async def assign_pr(pr_id: str, payload: AssignIn, ctx: Context = Depends(require("pr:update"))):
    pr = (await ctx.session.execute(
        select(PurchaseRequisition).where(PurchaseRequisition.id == pr_id,
                                          PurchaseRequisition.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not pr:
        raise NotFound("Requisition not found.")
    if pr.status in {"cancelled", "rfq_created"}:
        raise Conflict(f"A requisition in status '{pr.status}' can no longer be routed.")
    owner_id = payload.assigned_to or ctx.user_id
    owner = (await ctx.session.execute(
        select(User).where(User.id == owner_id, User.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not owner:
        raise NotFound("The named owner is not a user of this tenant.")
    if not ({owner.primary_role, *(owner.roles or [])} & {"buyer", "procurement_manager", "admin"}):
        raise ValidationFailed("A requisition can only be routed to procurement (buyer or manager).")
    before = {"status": pr.status, "assigned_to": pr.assigned_to, "department": pr.department}
    pr.assigned_to = owner.id
    pr.assigned_at = datetime.now(UTC)
    if payload.department.strip():
        pr.department = payload.department.strip()[:120]
    # Routing is what moves a request from the requesting department into procurement.
    if pr.status in {"draft", "clarifying", "ready"}:
        pr.status = "in_procurement"
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="pr.assigned", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="purchase_requisition", entity_id=pr.id,
                before=before, after={"status": pr.status, "assigned_to": pr.assigned_to,
                                      "department": pr.department},
                ip=ctx.ip, request_id=ctx.request_id)
    await notify(ctx.session, tenant_id=ctx.tenant_id, user_id=owner.id, kind="requisition_assigned",
                 title=f"Requisition {pr.code} routed to you",
                 title_ar=f"تم تحويل الطلب {pr.code} إليك",
                 body=f"{pr.title} · {payload.department or pr.department}".strip(" ·"),
                 link=f"/requests/{pr.id}", severity="warning")
    await ctx.session.commit()
    return {"ok": True, "status": pr.status, "assigned_to": pr.assigned_to,
            "assigned_to_name": owner.full_name or owner.email, "department": pr.department,
            "assigned_at": pr.assigned_at}


# ------------------------------------------------------------------ ERP
@router.get("/erp/test", summary="Test the configured ERP connector")
async def erp_test(ctx: Context = Depends(require("admin:integrations"))):
    return await test_connection()


@router.post("/erp/import", summary="Import requisitions from the ERP connector")
async def erp_import(ctx: Context = Depends(require("pr:create")), limit: int = Query(20, le=100)):
    created = await import_requisitions(ctx.session, ctx.tenant, limit=limit, requested_by=ctx.user_id)
    await ctx.session.commit()
    return {"imported": created, "count": len(created)}


# ------------------------------------------------------------------ suppliers
@router.get("/suppliers", summary="List/search suppliers")
async def suppliers(ctx: Context = Depends(require("supplier:read")), q: str = "",
                    country: str | None = None, category: str | None = None, status: str | None = None,
                    page: int = 1, page_size: int = 25):
    rows, total = await search_suppliers(ctx.session, ctx.tenant_id, q, category=category, country=country,
                                         status=status, limit=page_size, offset=(page - 1) * page_size)
    brief_by_id = await supplier_briefs(ctx.session, ctx.tenant, list(rows))
    briefs = [brief_by_id[row.id] for row in rows]
    return {"items": briefs, "total": total, "page": page}


class SupplierIn(BaseModel):
    name: str
    name_ar: str = ""
    country: str = "EG"
    city: str = ""
    website: str = ""
    categories: list[str] = Field(default_factory=list)
    email: str = ""
    phone: str = ""
    language: str = "ar"
    source_url: str = ""


@router.post("/suppliers", status_code=201, summary="Create a supplier")
async def create_supplier_endpoint(payload: SupplierIn, ctx: Context = Depends(require("supplier:create"))):
    supplier = await create_supplier(ctx.session, ctx.tenant, name=payload.name, name_ar=payload.name_ar,
                                     country=payload.country, city=payload.city, website=payload.website,
                                     categories=payload.categories, email=payload.email, phone=payload.phone,
                                     language=payload.language, source_url=payload.source_url,
                                     created_by=ctx.user_id)
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="supplier.created", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="supplier", entity_id=supplier.id,
                after={"name": supplier.name}, ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    brief = await supplier_brief(ctx.session, ctx.tenant, supplier)
    brief.update({"id": supplier.id, "sanctions_status": supplier.sanctions_status, "status": supplier.status})
    return brief


@router.get("/suppliers/{supplier_id}", summary="Supplier detail with contacts (PII decrypted for authorised users)")
async def supplier_detail(supplier_id: str, ctx: Context = Depends(require("supplier:read"))):
    supplier = (await ctx.session.execute(
        select(Supplier).where(Supplier.id == supplier_id, Supplier.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not supplier:
        raise NotFound("Supplier not found.")
    brief = await supplier_brief(ctx.session, ctx.tenant, supplier)
    brief["notes"] = supplier.notes
    brief["flags"] = supplier.flags
    brief["ratings"] = {
        "price": float(supplier.rating_price or 0), "lead_time": float(supplier.rating_lead_time or 0),
        "quality": float(supplier.rating_quality or 0),
        "responsiveness": float(supplier.rating_responsiveness or 0),
    }
    return brief


class RatingIn(BaseModel):
    price: float | None = None
    lead_time: float | None = None
    quality: float | None = None
    responsiveness: float | None = None


@router.post("/suppliers/{supplier_id}/ratings", summary="Record supplier rating inputs")
async def rate_supplier(supplier_id: str, payload: RatingIn, ctx: Context = Depends(require("supplier:update"))):
    supplier = (await ctx.session.execute(
        select(Supplier).where(Supplier.id == supplier_id, Supplier.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not supplier:
        raise NotFound("Supplier not found.")
    await update_ratings(ctx.session, ctx.tenant, supplier, **payload.model_dump(exclude_none=True),
                         source="manual")
    await ctx.session.commit()
    return {"overall_score": float(supplier.overall_score or 0)}


class FlagIn(BaseModel):
    flag: str = Field(pattern="^(blacklisted|preferred|approved)$")
    reason: str


@router.post("/suppliers/{supplier_id}/flags", summary="Blacklist / prefer / approve a supplier")
async def flag_supplier(supplier_id: str, payload: FlagIn, ctx: Context = Depends(require("supplier:approve"))):
    supplier = (await ctx.session.execute(
        select(Supplier).where(Supplier.id == supplier_id, Supplier.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not supplier:
        raise NotFound("Supplier not found.")
    await add_flag(ctx.session, ctx.tenant, supplier, payload.flag, payload.reason, ctx.user_id)
    await audit(ctx.session, tenant_id=ctx.tenant_id, action=f"supplier.{payload.flag}", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="supplier", entity_id=supplier.id,
                after={"flag": payload.flag, "reason": payload.reason}, ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return {"ok": True, "status": supplier.status}


class CertificationIn(BaseModel):
    code: str
    issuer: str = ""
    expires_at: datetime | None = None
    evidence_url: str = ""


@router.post("/suppliers/{supplier_id}/certifications", status_code=201, summary="Add a certification")
async def add_certification(supplier_id: str, payload: CertificationIn,
                            ctx: Context = Depends(require("supplier:update"))):
    supplier = (await ctx.session.execute(
        select(Supplier).where(Supplier.id == supplier_id, Supplier.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not supplier:
        raise NotFound("Supplier not found.")
    cert = SupplierCertification(tenant_id=ctx.tenant_id, supplier_id=supplier.id, **payload.model_dump())
    ctx.session.add(cert)
    await ctx.session.flush()
    await ctx.session.commit()
    return {"id": cert.id}


@router.post("/suppliers/import", summary="Bulk import suppliers from Excel/CSV")
async def import_suppliers(ctx: Context = Depends(require("supplier:import")), file: UploadFile = File(...)):
    data = await file.read()
    filename = (file.filename or "suppliers.xlsx").lower()
    if filename.endswith(".csv"):
        import csv
        import io

        rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig", "ignore"))))
    else:
        import openpyxl

        workbook = openpyxl.load_workbook(__import__("io").BytesIO(data), data_only=True, read_only=True)
        sheet = workbook.worksheets[0]
        values = list(sheet.iter_rows(values_only=True))
        headers = [str(h or "").strip().lower() for h in values[0]] if values else []
        rows = [dict(zip(headers, [str(c or "").strip() for c in row], strict=False)) for row in values[1:] if any(row)]
    created, skipped = [], []
    for row in rows:
        name = row.get("name") or row.get("supplier") or row.get("اسم المورد") or row.get("الاسم") or ""
        if not name:
            skipped.append({"row": row, "reason": "MISSING_NAME"})
            continue
        supplier = await create_supplier(
            ctx.session, ctx.tenant, name=name, name_ar=row.get("name_ar", "") or row.get("الاسم العربي", ""),
            country=(row.get("country") or "EG")[:2].upper(), city=row.get("city", ""),
            website=row.get("website", ""), email=row.get("email", ""), phone=row.get("phone", ""),
            categories=[c.strip() for c in (row.get("categories") or "").split(",") if c.strip()],
            created_by=ctx.user_id,
        )
        created.append({"id": supplier.id, "name": supplier.name})
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="supplier.imported", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="supplier", after={"created": len(created)}, ip=ctx.ip)
    await ctx.session.commit()
    return {"created": created, "skipped": skipped}


@router.post("/suppliers/{supplier_id}/portal-link", summary="Signed, expiring supplier self-service link")
async def portal_link(supplier_id: str, ctx: Context = Depends(require("supplier:update")),
                      purpose: str = Query("profile", pattern="^(profile|catalog)$")):
    url = await create_portal_token(ctx.session, ctx.tenant, supplier_id, purpose=purpose)
    await ctx.session.commit()
    return {"url": url, "expires_in_days": 14,
            "note": "Share this link with the supplier; no account is required."}


# ------------------------------------------------------------------ discovery
class DiscoveryIn(BaseModel):
    query: str
    country: str = "EG"
    category_code: str | None = None
    max_results: int = Field(24, ge=1, le=60)


@router.post("/suppliers/discovery", status_code=201, summary="Run autonomous supplier discovery")
async def discover(payload: DiscoveryIn, ctx: Context = Depends(require("supplier:discovery"))):
    job = await run_discovery(ctx.session, ctx.tenant, query=payload.query, country=payload.country,
                              category_code=payload.category_code, max_results=payload.max_results,
                              requested_by=ctx.user_id)
    candidates = await list_candidates(ctx.session, ctx.tenant_id, job.id)
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="discovery.run", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="discovery_job", entity_id=job.id,
                after={"query": payload.query, "found": job.found_count}, ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return {"job_id": job.id, "status": job.status, "provider": job.provider,
            "found": job.found_count, "candidates": [{
                "id": c.id, "name": c.name, "name_ar": c.name_ar, "website": c.website,
                "city": c.city, "country": c.country, "confidence": float(c.confidence or 0),
                "source_url": c.source_url, "dedup_status": c.dedup_status, "status": c.status,
                "contact_email": c.contact_email, "evidence": c.evidence,
            } for c in candidates]}


@router.get("/suppliers/discovery/{job_id}", summary="Discovery job results")
async def discovery_job(job_id: str, ctx: Context = Depends(require("supplier:read"))):
    candidates = await list_candidates(ctx.session, ctx.tenant_id, job_id)
    return {"job_id": job_id, "candidates": [{
        "id": c.id, "name": c.name, "name_ar": c.name_ar, "website": c.website, "city": c.city,
        "confidence": float(c.confidence or 0), "source_url": c.source_url,
        "dedup_status": c.dedup_status, "status": c.status, "evidence": c.evidence,
    } for c in candidates]}


class ApproveCandidatesIn(BaseModel):
    candidate_ids: list[str]


@router.post("/suppliers/discovery/approve", summary="Buyer approves discovered candidates (gate before contact)")
async def approve_discovered(payload: ApproveCandidatesIn, ctx: Context = Depends(require("supplier:approve"))):
    suppliers = await approve_candidates(ctx.session, ctx.tenant, payload.candidate_ids, created_by=ctx.user_id)
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="discovery.approved", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="supplier", after={"count": len(suppliers)}, ip=ctx.ip)
    await ctx.session.commit()
    return {"created": [{"id": s.id, "name": s.name} for s in suppliers]}


@router.post("/suppliers/discovery/reject", summary="Reject discovered candidates")
async def reject_discovered(payload: ApproveCandidatesIn, ctx: Context = Depends(require("supplier:approve"))):
    count = await reject_candidates(ctx.session, ctx.tenant_id, payload.candidate_ids)
    await ctx.session.commit()
    return {"rejected": count}
