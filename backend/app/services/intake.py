"""Requirement intake: Excel/CSV auto-mapping, free text, voice, OCR scans, email-to-PR (FR-IN-1..7)."""
from __future__ import annotations

import csv
import io
import json
import logging
import re
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import registry
from app.agents.guardrails import scan_untrusted, wrap_untrusted
from app.agents.llm import llm_for
from app.agents.llm.base import LLMMessage, LLMRequest
from app.db.models.entities import (
    Attachment,
    ClarificationQuestion,
    Plant,
    PRLine,
    PurchaseRequisition,
    Tenant,
    User,
)
from app.services import numbers
from app.services.catalog_data import CATEGORIES, COLUMN_SYNONYMS, TAXONOMY
from app.services.common import next_code
from app.services.taxonomy import Classifier, normalize_line, normalize_text

log = logging.getLogger("procureai.intake")

FIELD_ALIASES = COLUMN_SYNONYMS

REQUIRED_FIELDS = ["quantity", "uom", "needed_by", "delivery_location"]
CATEGORY_FIELDS = {
    "30102400": ["grade", "dimensions"],
    "30102300": ["dimensions", "standard"],
    "30111600": ["standard"],
    "31171500": ["brand", "dimensions"],
    "39121600": ["brand"],
    "12180000": ["brand", "grade"],
}

QUESTION_TEXT = {
    "quantity": ("What is the required quantity?", "إيه الكمية المطلوبة؟"),
    "uom": ("Which unit of measure applies (ton, kg, pcs, m)?", "إيه وحدة القياس (طن، كجم، قطعة، متر)؟"),
    "needed_by": ("When is the material needed?", "إمتى محتاجين الخامة؟"),
    "delivery_location": ("Where should it be delivered?", "فين مكان التسليم؟"),
    "grade": ("What grade / specification is required?", "إيه الدرجة أو المواصفة المطلوبة؟"),
    "dimensions": ("What are the exact dimensions / diameter?", "إيه المقاسات أو القطر بالتفصيل؟"),
    "standard": ("Which standard applies?", "إيه المواصفة القياسية المطلوبة؟"),
    "brand": ("Is a specific brand or OEM required?", "فيه ماركة مطلوبة؟"),
}


def classifier_for(tenant: Tenant) -> Classifier:
    learned = (tenant.settings or {}).get("learned_aliases", {})
    return Classifier(TAXONOMY, learned_aliases=learned)


def _match_field(header: str) -> str | None:
    norm = normalize_text(header)
    if not norm:
        return None
    for field, synonyms in FIELD_ALIASES.items():
        for synonym in synonyms:
            syn = normalize_text(synonym)
            if syn and (syn in norm or norm in syn):
                return field
    return None


def detect_delimiter(sample: str) -> str:
    for delimiter in [",", ";", "\t", "|"]:
        if sample.count(delimiter) >= max(1, sample.count("\n")):
            return delimiter
    return ","


def _sheet_rows_from_xlsx(data: bytes) -> list[tuple[str, list[list[str]]]]:
    import openpyxl

    workbook = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    sheets: list[tuple[str, list[list[str]]]] = []
    for sheet in workbook.worksheets:
        rows: list[list[str]] = []
        for row in sheet.iter_rows(values_only=True):
            values = [("" if cell is None else str(cell)).strip() for cell in row]
            if any(values):
                rows.append(values)
        if rows:
            sheets.append((sheet.title, rows))
    return sheets


def _sheet_rows_from_csv(data: bytes) -> list[tuple[str, list[list[str]]]]:
    text = data.decode("utf-8-sig", "ignore")
    delimiter = detect_delimiter(text[:4000])
    rows = [[c.strip() for c in row] for row in csv.reader(io.StringIO(text), delimiter=delimiter)]
    return [("csv", [row for row in rows if any(row)])]


def find_header_row(rows: list[list[str]]) -> tuple[int, dict[int, str]]:
    best_index, best_mapping = 0, {}
    for index, row in enumerate(rows[:15]):
        mapping = {i: field for i, cell in enumerate(row) if (field := _match_field(cell))}
        if len(mapping) > len(best_mapping):
            best_index, best_mapping = index, mapping
    return best_index, best_mapping


def parse_date(value: str) -> datetime | None:
    if not value:
        return None
    cleaned = numbers.normalize_digits(str(value)).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(cleaned[:10], fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return numbers.resolve_relative_date(cleaned)


async def parse_spreadsheet(session: AsyncSession, tenant: Tenant, *, data: bytes, filename: str) -> dict:
    """Parse an upload into preview rows + suggested mapping + row-level validation report."""
    if filename.lower().endswith((".xlsx", ".xlsm", ".xls")):
        sheets = _sheet_rows_from_xlsx(data)
    else:
        sheets = _sheet_rows_from_csv(data)

    plants = list((await session.execute(
        select(Plant).where(Plant.tenant_id == tenant.id)
    )).scalars().all())
    plant_lookup = {normalize_text(p.name): p for p in plants}
    plant_lookup.update({normalize_text(p.code): p for p in plants})
    plant_lookup.update({normalize_text(p.name_ar): p for p in plants if p.name_ar})

    parsed_rows: list[dict] = []
    errors: list[dict] = []
    mapping: dict[str, str] = {}
    sheet_name = ""
    for name, rows in sheets:
        if not rows:
            continue
        header_index, header_mapping = find_header_row(rows)
        if not header_mapping:
            continue
        sheet_name = name
        headers = rows[header_index]
        mapping = {headers[i]: field for i, field in header_mapping.items()}
        for offset, row in enumerate(rows[header_index + 1:], start=header_index + 2):
            if not any(row):
                continue
            record = {}
            for i, field in header_mapping.items():
                record[field] = row[i] if i < len(row) else ""
            row_errors: list[str] = []
            description = (record.get("description") or "").strip()
            if not description:
                row_errors.append("MISSING_DESCRIPTION")
            quantity = numbers.parse_decimal(record.get("quantity"))
            if quantity is None or quantity <= 0:
                row_errors.append("INVALID_QUANTITY")
            uom_raw = record.get("uom") or ""
            uom = numbers.normalize_uom(uom_raw) if uom_raw else ""
            if uom_raw and uom not in numbers.UNIT_DIMENSION:
                row_errors.append("UNKNOWN_UOM")
            needed_by = parse_date(record.get("needed_by") or "")
            if record.get("needed_by") and not needed_by:
                row_errors.append("INVALID_DATE")
            plant = plant_lookup.get(normalize_text(record.get("plant") or ""))
            if record.get("plant") and not plant:
                row_errors.append("UNKNOWN_PLANT")
            spec = record.get("spec") or ""
            normalized = normalize_line(
                f"{description} {spec}".strip(), quantity or 1, uom or "pcs",
                record.get("target_price"), classifier=classifier_for(tenant), categories=CATEGORIES,
            )
            parsed_rows.append({
                "row": offset,
                "description": description,
                "spec": spec,
                "quantity": str(quantity or ""),
                "uom": uom,
                "needed_by": needed_by.isoformat() if needed_by else "",
                "plant": record.get("plant", ""),
                "plant_code": plant.code if plant else "",
                "target_price": str(numbers.parse_decimal(record.get("target_price")) or ""),
                "currency": (record.get("currency") or tenant.currency)[:3].upper(),
                "sku": record.get("sku", ""),
                "taxonomy_code": normalized["taxonomy_code"],
                "taxonomy_name": normalized["taxonomy_name"],
                "category_code": normalized["category_code"],
                "confidence": normalized["confidence"],
                "needs_review": normalized["needs_review"],
                "errors": row_errors,
            })
            if row_errors:
                errors.append({"row": offset, "codes": row_errors})

    # LLM-assisted mapping fallback for headers the synonym table missed.
    unmapped = [h for h in (parsed_rows and list(mapping) or []) if mapping.get(h) is None]
    if unmapped and parsed_rows:
        try:
            llm = await llm_for(session, tenant)
            response = await llm.complete(LLMRequest(
                task="column_mapping",
                messages=[LLMMessage("system", "Map spreadsheet headers to PR fields. JSON only."),
                          LLMMessage("user", wrap_untrusted(json.dumps(unmapped, ensure_ascii=False),
                                                            label="spreadsheet headers"))],
                context={"headers": unmapped},
            ))
            if response.data and isinstance(response.data.get("mapping"), dict):
                mapping.update(response.data["mapping"])
        except Exception:  # never fail an import because the LLM is unavailable
            log.warning("LLM column mapping unavailable", exc_info=True)

    valid_rows = [r for r in parsed_rows if not r["errors"]]
    total_rows = len(parsed_rows) or 1
    return {
        "sheet": sheet_name,
        "sheets": [name for name, _ in sheets],
        "headers": list(mapping),
        "suggested_mapping": {header: (field or "") for header, field in mapping.items()},
        "rows": parsed_rows[:500],
        "errors": errors[:200],
        "stats": {
            "rows": len(parsed_rows),
            "valid": len(valid_rows),
            "invalid": len(parsed_rows) - len(valid_rows),
            "manual_correction_ratio": round(len(parsed_rows) - len(valid_rows) and
                                             len(errors) / total_rows or 0.0, 4),
        },
    }


async def store_upload(session: AsyncSession, tenant: Tenant, *, data: bytes, filename: str,
                       payload: dict, uploaded_by: str | None = None) -> Attachment:
    import hashlib

    storage = registry.resolve("storage")
    key = f"{tenant.id}/intake/{hashlib.sha256(data).hexdigest()[:16]}-{filename}"
    storage.put(key, data)
    attachment = Attachment(
        tenant_id=tenant.id, kind="intake_upload", filename=filename, size_bytes=len(data),
        storage_key=key, sha256=hashlib.sha256(data).hexdigest(), uploaded_by=uploaded_by,
        extracted_text=json.dumps(payload, ensure_ascii=False),
    )
    session.add(attachment)
    await session.flush()
    return attachment


def parse_free_text(text: str, default_currency: str = "EGP") -> dict:
    """Deterministic first pass: quantities, units, descriptions and dates from free text (AR/EN)."""
    cleaned = numbers.normalize_digits(text or "")
    lines = [ln.strip(" -*•\t") for ln in re.split(r"[\n;؛]+", cleaned) if ln.strip()]
    if len(lines) == 1:
        lines = [ln.strip() for ln in re.split(r"(?<=[\d\u0600-\u06FF])\s*[،,]\s*(?=[\u0600-\u06FFA-Za-z])", lines[0]) if ln.strip()] or lines
    parsed: list[dict] = []
    for line in lines:
        quantity = None
        uom = ""
        description = line
        qty_match = re.search(
            r"([\d][\d,.]*)\s*(طن|كجم|كيلو|أطنان|اطنان|ton|tons|tonne|tonnes|kg|kgs|قطعة|قطع|pcs|piece|pieces|متر|meter|meters|لتر|liter|liters|شيكارة|bag|bags|لفة|roll|rolls|درزن|dozen)\b",
            line, re.IGNORECASE,
        )
        if not qty_match:
            qty_match = re.search(
                r"(?:كمية|الكمية|quantity|qty)\s*[:=]?\s*([\d][\d,.]*)\s*([A-Za-z\u0600-\u06FF]+)?",
                line, re.IGNORECASE,
            )
        if qty_match:
            quantity = numbers.parse_decimal(qty_match.group(1))
            unit_raw = qty_match.group(2) if qty_match.lastindex and qty_match.lastindex >= 2 else ""
            uom = numbers.normalize_uom(unit_raw) if unit_raw else ""
            description = (line[: qty_match.start()] + " " + line[qty_match.end():]).strip(" -،,")
        needed_by = numbers.resolve_relative_date(line)
        if needed_by:
            description = re.sub(r"(خلال|بعد|within|in)\s*[\d]+\s*\w+", "", description, flags=re.IGNORECASE).strip()
        parsed.append({
            "description": re.sub(r"\s+", " ", description).strip() or line,
            "quantity": str(quantity) if quantity is not None else "",
            "uom": uom,
            "needed_by": needed_by.isoformat() if needed_by else "",
            "currency": numbers.detect_currency(line, default_currency),
            "source_line": line,
        })
    return {"lines": parsed, "raw": text}


def missing_fields(normalized_lines: list[dict], pr_context: dict) -> list[str]:
    missing: set[str] = set()
    if not normalized_lines:
        missing.add("quantity")
    for line in normalized_lines:
        if not line.get("quantity"):
            missing.add("quantity")
        if not line.get("uom"):
            missing.add("uom")
        if not line.get("needed_by") and not pr_context.get("needed_by"):
            missing.add("needed_by")
        if not pr_context.get("delivery_location"):
            missing.add("delivery_location")
        extra = CATEGORY_FIELDS.get(line.get("taxonomy_code") or "", [])
        for field in extra:
            if not (line.get("attributes") or {}).get(field):
                missing.add(field)
    return sorted(missing)


async def create_pr(
    session: AsyncSession,
    tenant: Tenant,
    *,
    lines: list[dict],
    title: str | None = None,
    source: str = "manual",
    plant_code: str | None = None,
    requested_by: str | None = None,
    currency: str | None = None,
    needed_by: datetime | None = None,
    delivery_location: str = "",
    raw_input: str = "",
    notes: str = "",
) -> PurchaseRequisition:
    pr = PurchaseRequisition(
        tenant_id=tenant.id,
        code=await next_code(session, PurchaseRequisition, tenant.id, "PR"),
        title=title or (lines[0]["description"][:120] if lines else "Purchase request"),
        source=source,
        status="draft",
        requested_by=requested_by,
        currency=(currency or tenant.currency)[:3].upper(),
        needed_by=needed_by,
        notes=notes,
        raw_input=raw_input[:20000],
        total_estimate=Decimal("0"),
    )
    if plant_code:
        plant = (await session.execute(
            select(Plant).where(Plant.tenant_id == tenant.id,
                                (Plant.code == plant_code) | (Plant.name == plant_code))
        )).scalar_one_or_none()
        if plant:
            pr.plant_id = plant.id
    pr.notes = (pr.notes + f"\ndelivery_location:{delivery_location}").strip()
    session.add(pr)
    await session.flush()

    total = Decimal("0")
    confidence_values: list[float] = []
    for index, line in enumerate(lines, start=1):
        normalized = normalize_line(
            f"{line.get('description', '')} {line.get('spec', '')}".strip(),
            line.get("quantity") or 1,
            line.get("uom") or "pcs",
            line.get("target_price"),
            classifier=classifier_for(tenant),
            categories=CATEGORIES,
        )
        quantity = numbers.parse_decimal(line.get("quantity")) or (normalized["quantity"] or Decimal("1"))
        target_price = normalized["target_price"]
        row = PRLine(
            tenant_id=tenant.id,
            pr_id=pr.id,
            line_no=index,
            description=(line.get("description") or normalized["description"])[:400],
            quantity=quantity,
            uom=normalized["uom"],
            taxonomy_code=line.get("taxonomy_code") or normalized["taxonomy_code"],
            category_code=line.get("category_code") or normalized["category_code"],
            target_price=target_price,
            currency=(line.get("currency") or pr.currency)[:3].upper(),
            attributes={**(line.get("attributes") or {}), **({"spec": line["spec"]} if line.get("spec") else {})},
            normalization_confidence=Decimal(str(round(normalized["confidence"], 4))),
            quoted_text=(line.get("source_line") or line.get("description") or "")[:2000],
            needed_by=parse_date(line.get("needed_by") or "") if line.get("needed_by") else needed_by,
        )
        session.add(row)
        confidence_values.append(normalized["confidence"])
        if target_price and quantity:
            total += target_price * quantity
    pr.total_estimate = numbers.money(total)
    pr.confidence = Decimal(str(round(sum(confidence_values) / len(confidence_values), 4))) if confidence_values else Decimal("1")
    await session.flush()
    return pr


async def ask_clarifications(session: AsyncSession, tenant: Tenant, pr: PurchaseRequisition) -> list[ClarificationQuestion]:
    lines = list((await session.execute(
        select(PRLine).where(PRLine.pr_id == pr.id).order_by(PRLine.line_no)
    )).scalars().all())
    context = {"needed_by": pr.needed_by, "delivery_location": "delivery_location:" in (pr.notes or "")}
    missing = missing_fields([
        {"quantity": str(line.quantity) if line.quantity else "", "uom": line.uom,
         "needed_by": line.needed_by, "taxonomy_code": line.taxonomy_code,
         "attributes": line.attributes or {}} for line in lines
    ], context)
    existing = (await session.execute(
        select(ClarificationQuestion).where(ClarificationQuestion.pr_id == pr.id,
                                          ClarificationQuestion.status == "open")
    )).scalars().all()
    existing_fields = {q.field for q in existing}
    created: list[ClarificationQuestion] = []
    for field in missing:
        if field in existing_fields:
            continue
        question_en, question_ar = QUESTION_TEXT.get(field, (f"Please confirm {field}.", f"يرجى تأكيد {field}."))
        row = ClarificationQuestion(
            tenant_id=tenant.id, pr_id=pr.id, field=field, question=question_en, question_ar=question_ar,
            status="open", asked_at=datetime.now(UTC),
        )
        session.add(row)
        created.append(row)
    if created or existing:
        pr.status = "clarifying"
        pr.clarification_resolved = False
    else:
        pr.status = "ready"
        pr.clarification_resolved = True
    await session.flush()
    return created


async def answer_clarification(session: AsyncSession, tenant: Tenant, question: ClarificationQuestion,
                               *, answer: str | None, waived: bool, user_id: str | None) -> ClarificationQuestion:
    question.status = "waived" if waived else "answered"
    question.answer = answer
    question.answered_at = datetime.now(UTC)
    question.answered_by = user_id
    remaining = (await session.execute(
        select(ClarificationQuestion).where(ClarificationQuestion.pr_id == question.pr_id,
                                          ClarificationQuestion.status == "open")
    )).scalars().all()
    pr = (await session.execute(
        select(PurchaseRequisition).where(PurchaseRequisition.id == question.pr_id)
    )).scalar_one()
    pr.clarification_resolved = not remaining
    pr.status = "ready" if not remaining else "clarifying"
    if answer and question.field == "delivery_location":
        pr.notes = f"{pr.notes or ''}\ndelivery_location:{answer}".strip()
    await session.flush()
    return question


async def confirm_excel_import(session: AsyncSession, tenant: Tenant, *, attachment: Attachment,
                              rows: list[dict], mapping: dict[str, str], plant_code: str | None,
                              requested_by: str | None, title: str | None = None) -> PurchaseRequisition:
    payload = json.loads(attachment.extracted_text or "{}")
    source_rows = rows or payload.get("rows", [])
    lines = []
    for row in source_rows:
        if row.get("errors"):
            continue
        lines.append({
            "description": row.get("description", ""),
            "spec": row.get("spec", ""),
            "quantity": row.get("quantity") or "",
            "uom": row.get("uom") or "",
            "needed_by": row.get("needed_by") or "",
            "currency": row.get("currency", tenant.currency),
            "target_price": row.get("target_price") or "",
            "taxonomy_code": row.get("taxonomy_code"),
            "category_code": row.get("category_code"),
            "source_line": row.get("description", ""),
        })
    pr = await create_pr(session, tenant, lines=lines, title=title or attachment.filename.rsplit(".", 1)[0],
                         source="excel", plant_code=plant_code, requested_by=requested_by,
                         currency=tenant.currency)
    await ask_clarifications(session, tenant, pr)
    return pr


async def intake_free_text(session: AsyncSession, tenant: Tenant, *, text: str, source: str = "text",
                           requested_by: str | None = None, plant_code: str | None = None,
                           title: str | None = None) -> PurchaseRequisition:
    scan = scan_untrusted(text)
    parsed = parse_free_text(text, tenant.currency)
    pr = await create_pr(session, tenant, lines=parsed["lines"], title=title, source=source,
                         requested_by=requested_by, plant_code=plant_code, raw_input=scan.cleaned,
                         currency=tenant.currency)
    if scan.flagged:
        pr.notes = f"{pr.notes or ''}\n[security] instruction-like content detected in intake: {','.join(scan.codes)}"
    await ask_clarifications(session, tenant, pr)
    return pr


async def intake_scan(session: AsyncSession, tenant: Tenant, *, data: bytes, filename: str,
                      requested_by: str | None = None) -> tuple[PurchaseRequisition, str]:
    ocr = registry.resolve("ocr")
    result = await ocr.extract(data=data, filename=filename, languages="ara+eng")
    pr = await intake_free_text(session, tenant, text=result.text, source="ocr", requested_by=requested_by,
                               title=f"Scanned request: {filename}")
    return pr, result.text


async def intake_voice(session: AsyncSession, tenant: Tenant, *, data: bytes, filename: str,
                       requested_by: str | None = None) -> tuple[PurchaseRequisition, str]:
    stt = registry.resolve("stt")
    transcript = await stt.transcribe(data=data, filename=filename, language="ar")
    pr = await intake_free_text(session, tenant, text=transcript.text, source="voice",
                                requested_by=requested_by, title="Voice request")
    return pr, transcript.text


async def intake_email(session: AsyncSession, tenant: Tenant, *, sender: str, subject: str, body: str,
                       requested_by: str | None = None) -> PurchaseRequisition:
    text = f"{subject}\n{body}"
    return await intake_free_text(session, tenant, text=text, source="email", requested_by=requested_by,
                                  title=subject[:120] or "Email request")


async def learn_normalization(session: AsyncSession, tenant: Tenant, description: str,
                              taxonomy_code: str) -> None:
    settings_json = dict(tenant.settings or {})
    learned = dict(settings_json.get("learned_aliases", {}))
    learned[normalize_text(description)[:200]] = taxonomy_code
    settings_json["learned_aliases"] = learned
    tenant.settings = settings_json
    session.add(tenant)
    await session.flush()


async def confirm_line_normalization(session: AsyncSession, tenant: Tenant, line: PRLine,
                                     taxonomy_code: str, user: User | None = None) -> PRLine:
    line.taxonomy_code = taxonomy_code
    line.normalization_confidence = Decimal("1.0")
    category = next((c["code"] for c in CATEGORIES if c["taxonomy_code"] == taxonomy_code), None)
    if category:
        line.category_code = category
    await learn_normalization(session, tenant, line.description, taxonomy_code)
    await session.flush()
    return line
