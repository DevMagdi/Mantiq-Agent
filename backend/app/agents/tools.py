"""Fixed, audited agent tool registry.

There is deliberately **no tool** for accepting an offer, signing, paying or editing
specifications (FR-NEG-4). The registry is an allow-list: the orchestrator rejects any
tool name that is not here, and each tool records an audited `tool_call` row.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.models.entities import RFQ, Supplier, Tenant, ToolCall
from app.services import numbers

log = logging.getLogger("procureai.tools")

FORBIDDEN_TOOL_NAMES = {
    "accept_offer", "accept_counter_offer", "sign_contract", "sign", "pay_invoice", "execute_payment",
    "change_specifications", "change_spec", "edit_rfq_specs", "approve_offer", "issue_po", "commit_purchase",
}


class ToolContext(BaseModel):
    tenant_id: str
    user_id: str | None = None
    rfq_id: str | None = None
    task: str = "generic"
    run_id: str | None = None
    idempotency_key: str | None = None
    model_config = {"arbitrary_types_allowed": True}


# ------------------------------------------------------------------ schemas
class SearchSuppliersIn(BaseModel):
    query: str
    country: str = "EG"
    limit: int = Field(10, ge=1, le=50)


class FetchPageIn(BaseModel):
    url: str


class NormalizeItemIn(BaseModel):
    description: str


class CreateRfqDraftIn(BaseModel):
    pr_id: str
    supplier_ids: list[str]
    deadline_days: int = 10
    mode: str = "draft_only"


class SendRfqIn(BaseModel):
    rfq_id: str
    channel: str = "email"


class ParseOfferIn(BaseModel):
    rfq_id: str
    supplier_id: str
    text: str


class ProposeCounterIn(BaseModel):
    rfq_id: str
    supplier_id: str
    offer_id: str
    round_no: int = 1
    concession: str | None = None


class SendMessageIn(BaseModel):
    rfq_id: str
    supplier_id: str
    subject: str
    body: str
    channel: str = "email"


class RequestClarificationIn(BaseModel):
    pr_id: str
    fields: list[str]


class RequestApprovalIn(BaseModel):
    rfq_id: str
    offer_ids: list[str]
    amount: str


class ComputeTcoIn(BaseModel):
    offer_id: str
    rfq_id: str


class PriceBenchmarkIn(BaseModel):
    item_key: str


@dataclass
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    permission: str
    handler: Callable[..., Awaitable[dict[str, Any]]]
    mutating: bool = False
    requires_policy: bool = False
    tags: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ handlers
async def _search_suppliers(session: AsyncSession, tenant: Tenant, ctx: ToolContext, data: SearchSuppliersIn) -> dict:
    from app.services.discovery import run_discovery

    job = await run_discovery(session, tenant, query=data.query, country=data.country,
                              max_results=data.limit, requested_by=ctx.user_id)
    return {"job_id": job.id, "found": job.found_count, "provider": job.provider}


async def _fetch_public_page(session: AsyncSession, tenant: Tenant, ctx: ToolContext, data: FetchPageIn) -> dict:
    from app.adapters.search import PageFetcher

    text = await PageFetcher().fetch(data.url)
    return {"url": data.url, "text": text[:6000], "fetched": bool(text)}


async def _normalize_item(session: AsyncSession, tenant: Tenant, ctx: ToolContext, data: NormalizeItemIn) -> dict:
    from app.services.catalog_data import CATEGORIES
    from app.services.intake import classifier_for
    from app.services.taxonomy import normalize_line

    result = normalize_line(data.description, 1, "pcs", None,
                            classifier=classifier_for(tenant), categories=CATEGORIES)
    return result


async def _create_rfq_draft(session: AsyncSession, tenant: Tenant, ctx: ToolContext, data: CreateRfqDraftIn) -> dict:
    from app.db.models.entities import PurchaseRequisition
    from app.services.rfq import create_rfq_from_pr

    pr = (await session.execute(select(PurchaseRequisition).where(PurchaseRequisition.id == data.pr_id))).scalar_one()
    rfq = await create_rfq_from_pr(session, tenant, pr, supplier_ids=data.supplier_ids,
                                   deadline_days=data.deadline_days, mode=data.mode, buyer_id=ctx.user_id)
    return {"rfq_id": rfq.id, "code": rfq.code, "status": rfq.status}


async def _send_rfq(session: AsyncSession, tenant: Tenant, ctx: ToolContext, data: SendRfqIn) -> dict:
    from app.services.rfq import dispatch_rfq

    rfq = (await session.execute(select(RFQ).where(RFQ.id == data.rfq_id))).scalar_one()
    return await dispatch_rfq(session, tenant, rfq, actor_id=ctx.user_id)


async def _parse_offer(session: AsyncSession, tenant: Tenant, ctx: ToolContext, data: ParseOfferIn) -> dict:
    from app.adapters.base import ExtractionPayload
    from app.services.extraction import extract_offer

    rfq = (await session.execute(select(RFQ).where(RFQ.id == data.rfq_id))).scalar_one()
    supplier = (await session.execute(select(Supplier).where(Supplier.id == data.supplier_id))).scalar_one()
    extraction, offer = await extract_offer(session, tenant, rfq=rfq, supplier=supplier,
                                            payload=ExtractionPayload(text=data.text, source="tool"))
    return {"extraction_id": extraction.id, "offer_id": offer.id if offer else None,
            "needs_review": extraction.needs_review, "issues": extraction.issues}


async def _propose_counter(session: AsyncSession, tenant: Tenant, ctx: ToolContext, data: ProposeCounterIn) -> dict:
    from app.db.models.entities import Offer
    from app.services.negotiation import draft_counter_offer, policy_for

    rfq = (await session.execute(select(RFQ).where(RFQ.id == data.rfq_id))).scalar_one()
    supplier = (await session.execute(select(Supplier).where(Supplier.id == data.supplier_id))).scalar_one()
    offer = (await session.execute(select(Offer).where(Offer.id == data.offer_id))).scalar_one()
    policy = await policy_for(session, tenant, rfq, data.supplier_id)
    if not policy:
        return {"ok": False, "error": "No active negotiation policy."}
    result = await draft_counter_offer(session, tenant, rfq=rfq, supplier=supplier, offer=offer,
                                       policy=policy, round_no=data.round_no,
                                       concession=data.concession, actor_id=ctx.user_id)
    return {k: v for k, v in result.items() if k != "body_en"}


async def _send_message(session: AsyncSession, tenant: Tenant, ctx: ToolContext, data: SendMessageIn) -> dict:
    from app.services.messaging import send_message

    rfq = (await session.execute(select(RFQ).where(RFQ.id == data.rfq_id))).scalar_one()
    supplier = (await session.execute(select(Supplier).where(Supplier.id == data.supplier_id))).scalar_one()
    return await send_message(session, tenant, rfq=rfq, supplier=supplier, subject=data.subject,
                              body=data.body, channel=data.channel, author_type="agent",
                              author_id=ctx.user_id, agent_run_id=ctx.run_id)


async def _request_clarification(session: AsyncSession, tenant: Tenant, ctx: ToolContext, data: RequestClarificationIn) -> dict:
    from app.db.models.entities import PurchaseRequisition
    from app.services.intake import ask_clarifications

    pr = (await session.execute(select(PurchaseRequisition).where(PurchaseRequisition.id == data.pr_id))).scalar_one()
    questions = await ask_clarifications(session, tenant, pr)
    return {"pr_id": pr.id, "questions": [{"field": q.field, "question": q.question} for q in questions]}


async def _request_approval(session: AsyncSession, tenant: Tenant, ctx: ToolContext, data: RequestApprovalIn) -> dict:
    from app.services.approval import request_approval

    rfq = (await session.execute(select(RFQ).where(RFQ.id == data.rfq_id))).scalar_one()
    approvals = await request_approval(session, tenant, rfq, offer_ids=data.offer_ids,
                                       amount=numbers.parse_decimal(data.amount) or 0, requested_by=ctx.user_id)
    return {"approvals": [{"id": a.id, "step": a.step_no, "role": a.approver_role} for a in approvals]}


async def _compute_tco(session: AsyncSession, tenant: Tenant, ctx: ToolContext, data: ComputeTcoIn) -> dict:
    from app.services.comparison import compute_offer_costs

    rfq = (await session.execute(select(RFQ).where(RFQ.id == data.rfq_id))).scalar_one()
    costs = await compute_offer_costs(session, tenant, rfq)
    match = next((c for c in costs if c["offer_id"] == data.offer_id), None)
    return match or {"error": "offer not in this RFQ"}


async def _price_benchmark(session: AsyncSession, tenant: Tenant, ctx: ToolContext, data: PriceBenchmarkIn) -> dict:
    from app.services.comparison import benchmark_for

    return await benchmark_for(session, tenant.id, data.item_key)


# ------------------------------------------------------------------ registry
TOOLS: dict[str, ToolSpec] = {
    "search_suppliers": ToolSpec("search_suppliers", "Discover candidate suppliers from public sources",
                                 SearchSuppliersIn, "supplier:discovery", _search_suppliers, tags=["discovery"]),
    "fetch_public_page": ToolSpec("fetch_public_page", "Fetch a public web page (SSRF-guarded)",
                                  FetchPageIn, "supplier:discovery", _fetch_public_page, tags=["discovery"]),
    "normalize_item": ToolSpec("normalize_item", "Map an item description to the UNSPSC taxonomy",
                               NormalizeItemIn, "pr:create", _normalize_item),
    "create_rfq_draft": ToolSpec("create_rfq_draft", "Create an RFQ draft from a requisition",
                                 CreateRfqDraftIn, "rfq:create", _create_rfq_draft, mutating=True),
    "send_rfq": ToolSpec("send_rfq", "Send an RFQ to its invited suppliers", SendRfqIn, "rfq:send", _send_rfq,
                         mutating=True, requires_policy=True),
    "parse_offer": ToolSpec("parse_offer", "Parse a supplier reply into a structured offer", ParseOfferIn,
                            "offer:verify", _parse_offer, mutating=True),
    "propose_counter": ToolSpec("propose_counter", "Draft a policy-checked counter-offer", ProposeCounterIn,
                                "negotiation:draft", _propose_counter, mutating=True, requires_policy=True),
    "send_message": ToolSpec("send_message", "Send a policy-checked supplier message", SendMessageIn,
                             "negotiation:send", _send_message, mutating=True, requires_policy=True),
    "request_clarification": ToolSpec("request_clarification", "Ask the buyer clarifying questions",
                                      RequestClarificationIn, "pr:update", _request_clarification, mutating=True),
    "request_approval": ToolSpec("request_approval", "Route an award to human approval",
                                 RequestApprovalIn, "approval:read", _request_approval, mutating=True),
    "compute_tco": ToolSpec("compute_tco", "Compute landed cost and TCO for an offer", ComputeTcoIn,
                            "offer:read", _compute_tco),
    "get_price_benchmark": ToolSpec("get_price_benchmark", "Get anonymised price benchmark for an item",
                                    PriceBenchmarkIn, "offer:read", _price_benchmark),
}

assert not (FORBIDDEN_TOOL_NAMES & set(TOOLS)), "Forbidden tools must never be registered"


def json_safe(value: Any) -> Any:
    """Coerce a tool result into something the ``tool_call.result`` JSON column can hold.

    Tools return ``Decimal`` money and taxonomy results straight from the services layer;
    leaking one of those into the audit row would raise on flush and lose the record of a
    call that actually happened, so the boundary is normalised here instead.
    """
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, BaseModel):
        return json_safe(value.model_dump())
    return value


def tool_catalog() -> list[dict]:
    return [{
        "name": spec.name, "description": spec.description, "permission": spec.permission,
        "mutating": spec.mutating, "requires_policy": spec.requires_policy,
        "input_schema": spec.input_model.model_json_schema(), "tags": spec.tags,
    } for spec in TOOLS.values()]


async def invoke_tool(session: AsyncSession, tenant: Tenant, ctx: ToolContext, name: str,
                      payload: dict[str, Any], *, allowed: set[str] | None = None) -> dict:
    """Validate, authorise, execute and audit one tool call."""
    if name in FORBIDDEN_TOOL_NAMES:
        return {"ok": False, "error": f"Tool '{name}' does not exist: the agent may not accept, sign, pay or edit specs."}
    spec = TOOLS.get(name)
    if spec is None:
        return {"ok": False, "error": f"Unknown tool '{name}'"}
    if allowed is not None and name not in allowed:
        return {"ok": False, "error": f"Tool '{name}' is not allowed for task '{ctx.task}'"}
    try:
        args = spec.input_model(**payload)
    except Exception as exc:
        return {"ok": False, "error": f"Invalid arguments: {exc}"}

    from app.core.rbac import has_permission

    if ctx.user_id:
        from app.db.models.entities import User

        user = (await session.execute(select(User).where(User.id == ctx.user_id))).scalar_one_or_none()
        if user and not has_permission(list(user.roles or []), spec.permission, user.is_superadmin):
            return {"ok": False, "error": f"Missing permission '{spec.permission}' for tool '{name}'"}

    started = time.perf_counter()
    record = ToolCall(tenant_id=tenant.id, run_id=ctx.run_id or "", tool_name=name,
                      args=args.model_dump(), idempotency_key=ctx.idempotency_key or "", status="running")
    session.add(record)
    await session.flush()
    try:
        result = await spec.handler(session, tenant, ctx, args)
        record.status = "ok"
        ok = True
    except Exception as exc:  # tool failures are audited, never silently swallowed
        log.warning("tool %s failed: %s", name, exc)
        result = {"ok": False, "error": str(exc)[:400]}
        record.status = "error"
        record.error = str(exc)[:400]
        ok = False
    record.result = json_safe(result) if isinstance(result, dict) else {"result": str(result)}
    record.latency_ms = int((time.perf_counter() - started) * 1000)
    record.created_at = record.created_at or utcnow()
    await session.flush()
    return {**result, "_tool": name, "_ok": ok}
