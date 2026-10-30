"""Analytics, admin, audit, notifications, billing, platform and public (no-login) endpoints."""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Body, Depends, File, Query, Response, UploadFile
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    Context,
    current_context,
    get_session_dep,
    platform_context,
    require,
)
from app.core.audit import audit, list_audit
from app.core.config import settings
from app.core.crypto import encrypt_text
from app.core.errors import NotFound, ValidationFailed
from app.db.models.entities import (
    AgentRun,
    AgentStep,
    ApiKey,
    Comment,
    DataErasureRequest,
    DiscoveryJob,
    IntegrationConfig,
    Invoice,
    LlmProviderConfig,
    Notification,
    Plan,
    RetentionPolicy,
    Subscription,
    Supplier,
    Tenant,
    ToolCall,
    UsageEvent,
    User,
)
from app.services import analytics
from app.services.billing_helpers import generate_invoice
from app.services.common import usage_summary
from app.services.portal import form_view, portal_view, submit_form, submit_portal

log = logging.getLogger("procureai.api.admin")
router = APIRouter(tags=["admin"])


# ------------------------------------------------------------------ analytics
@router.get("/dashboard", summary="Dashboard payload (KPIs, spend, trends, forecast)")
async def dashboard(ctx: Context = Depends(require("report:read"))):
    return await analytics.dashboard(ctx.session, ctx.tenant)


@router.get("/analytics/kpis", summary="Savings, cycle time and response-rate KPIs")
async def kpis(ctx: Context = Depends(require("report:read")), days: int = Query(90, ge=7, le=730)):
    return await analytics.kpis(ctx.session, ctx.tenant, days=days)


@router.get("/analytics/spend", summary="Spend by category, plant and supplier")
async def spend(ctx: Context = Depends(require("report:read"))):
    return await analytics.spend_breakdown(ctx.session, ctx.tenant)


@router.get("/analytics/trends", summary="Item price trend series")
async def trends(ctx: Context = Depends(require("report:read")), item_key: str = ""):
    return {"items": await analytics.price_trends(ctx.session, ctx.tenant_id, item_key)}


@router.get("/analytics/forecast", summary="Explainable forecast (seasonal naive + exponential smoothing)")
async def forecast(ctx: Context = Depends(require("report:read")), item_key: str = ""):
    rows = await analytics.price_trends(ctx.session, ctx.tenant_id, item_key)
    return analytics.forecast([row["avg_price"] for row in rows[-12:]])


@router.get("/analytics/savings-by-buyer", summary="Savings / spend by buyer")
async def savings_buyer(ctx: Context = Depends(require("report:read"))):
    return {"items": await analytics.savings_by_buyer(ctx.session, ctx.tenant)}


@router.get("/analytics/export", summary="Export the analytics workbook (Excel)")
async def export_analytics(ctx: Context = Depends(require("report:read"))):
    data = await analytics.report_excel(ctx.session, ctx.tenant)
    return Response(content=data,
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="procureai-analytics.xlsx"'})


# ------------------------------------------------------------------ audit & agent activity
@router.get("/audit", summary="Append-only audit trail")
async def audit_log(ctx: Context = Depends(require("audit:read")), action: str | None = None,
                    entity_type: str | None = None, entity_id: str | None = None,
                    actor_type: str | None = None, page: int = 1, page_size: int = 50):
    rows, total = await list_audit(ctx.session, ctx.tenant_id, action=action, entity_type=entity_type,
                                   entity_id=entity_id, actor_type=actor_type,
                                   limit=page_size, offset=(page - 1) * page_size)
    return {"items": [{
        "id": row.id, "created_at": row.created_at, "actor_type": row.actor_type,
        "actor_label": row.actor_label, "action": row.action, "entity_type": row.entity_type,
        "entity_id": row.entity_id, "before": row.before, "after": row.after, "ip": row.ip,
        "request_id": row.request_id,
    } for row in rows], "total": total, "page": page}


@router.get("/agent-runs", summary="Agent activity feed (prompt/tool/reasoning audit)")
async def agent_runs(ctx: Context = Depends(require("audit:read")), kind: str | None = None,
                     page: int = 1, page_size: int = 30):
    query = select(AgentRun).where(AgentRun.tenant_id == ctx.tenant_id)
    if kind:
        query = query.where(AgentRun.kind == kind)
    rows = list((await ctx.session.execute(
        query.order_by(AgentRun.created_at.desc()).limit(page_size).offset((page - 1) * page_size)
    )).scalars().all())
    return {"items": [{
        "id": r.id, "kind": r.kind, "status": r.status, "model": r.model, "ref_type": r.ref_type,
        "ref_id": r.ref_id, "input_summary": r.input_summary, "output_summary": r.output_summary,
        "tokens_in": r.tokens_in, "tokens_out": r.tokens_out, "cost_usd": str(r.cost_usd),
        "latency_ms": r.latency_ms, "policy_decision": r.policy_decision, "created_at": r.created_at,
    } for r in rows]}


@router.get("/agent-runs/{run_id}", summary="Agent run detail with steps and tool calls")
async def agent_run_detail(run_id: str, ctx: Context = Depends(require("audit:read"))):
    run = (await ctx.session.execute(
        select(AgentRun).where(AgentRun.id == run_id, AgentRun.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not run:
        raise NotFound("Agent run not found.")
    steps = list((await ctx.session.execute(
        select(AgentStep).where(AgentStep.run_id == run.id).order_by(AgentStep.step_no)
    )).scalars().all())
    calls = list((await ctx.session.execute(
        select(ToolCall).where(ToolCall.run_id == run.id).order_by(ToolCall.created_at)
    )).scalars().all())
    return {
        "id": run.id, "kind": run.kind, "status": run.status, "model": run.model,
        "input_summary": run.input_summary, "output_summary": run.output_summary,
        "reasoning": run.reasoning, "policy_decision": run.policy_decision,
        "tokens_in": run.tokens_in, "tokens_out": run.tokens_out, "cost_usd": str(run.cost_usd),
        "latency_ms": run.latency_ms, "created_at": run.created_at,
        "steps": [{"step_no": s.step_no, "node": s.node, "status": s.status, "input": s.input,
                   "output": s.output, "reasoning": s.reasoning, "latency_ms": s.latency_ms} for s in steps],
        "tool_calls": [{"id": c.id, "tool": c.tool_name, "args": c.args, "result": c.result,
                        "status": c.status, "error": c.error, "latency_ms": c.latency_ms} for c in calls],
    }


# ------------------------------------------------------------------ notifications & comments
@router.get("/notifications", summary="Notification centre")
async def notifications(ctx: Context = Depends(current_context), unread_only: bool = False):
    query = select(Notification).where(Notification.tenant_id == ctx.tenant_id,
                                       (Notification.user_id == ctx.user_id) | Notification.user_id.is_(None))
    if unread_only:
        query = query.where(Notification.read_at.is_(None))
    rows = list((await ctx.session.execute(
        query.order_by(Notification.created_at.desc()).limit(50)
    )).scalars().all())
    unread = (await ctx.session.execute(
        select(func.count()).select_from(Notification).where(
            Notification.tenant_id == ctx.tenant_id, Notification.read_at.is_(None),
            (Notification.user_id == ctx.user_id) | Notification.user_id.is_(None))
    )).scalar_one()
    return {"items": [{"id": n.id, "kind": n.kind, "title": n.title, "title_ar": n.title_ar,
                       "body": n.body, "link": n.link, "severity": n.severity, "read_at": n.read_at,
                       "created_at": n.created_at} for n in rows], "unread": int(unread)}


@router.post("/notifications/{notification_id}/read", summary="Mark one notification read")
async def mark_read(notification_id: str, ctx: Context = Depends(current_context)):
    row = (await ctx.session.execute(
        select(Notification).where(Notification.id == notification_id,
                                   Notification.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not row:
        raise NotFound("Notification not found.")
    row.read_at = datetime.now(UTC)
    await ctx.session.commit()
    return {"ok": True}


@router.post("/notifications/read-all", summary="Mark all notifications read")
async def mark_all_read(ctx: Context = Depends(current_context)):
    rows = list((await ctx.session.execute(
        select(Notification).where(Notification.tenant_id == ctx.tenant_id,
                                   Notification.user_id == ctx.user_id, Notification.read_at.is_(None))
    )).scalars().all())
    for row in rows:
        row.read_at = datetime.now(UTC)
    await ctx.session.commit()
    return {"updated": len(rows)}


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    rfq_id: str | None = None
    pr_id: str | None = None
    mentions: list[str] = Field(default_factory=list)


@router.post("/comments", status_code=201, summary="Comment on an RFQ/PR with @mentions")
async def create_comment(payload: CommentIn, ctx: Context = Depends(require("comment:create"))):
    comment = Comment(tenant_id=ctx.tenant_id, rfq_id=payload.rfq_id, pr_id=payload.pr_id,
                      author_id=ctx.user_id, author_name=ctx.user.full_name, body=payload.body,
                      mentions=payload.mentions)
    ctx.session.add(comment)
    await ctx.session.flush()
    for user_id in payload.mentions:
        user = (await ctx.session.execute(
            select(User).where(User.id == user_id, User.tenant_id == ctx.tenant_id)
        )).scalar_one_or_none()
        if user:
            from app.services.common import notify

            await notify(ctx.session, tenant_id=ctx.tenant_id, user_id=user.id, kind="mention",
                         title=f"{ctx.user.full_name} mentioned you", title_ar=f"{ctx.user.full_name} أشار إليك",
                         body=payload.body[:200], link=f"/rfqs/{payload.rfq_id}" if payload.rfq_id else "/requests")
    await ctx.session.commit()
    return {"id": comment.id}


@router.get("/comments", summary="List comments for an RFQ or PR")
async def list_comments(ctx: Context = Depends(current_context), rfq_id: str | None = None,
                        pr_id: str | None = None):
    query = select(Comment).where(Comment.tenant_id == ctx.tenant_id)
    if rfq_id:
        query = query.where(Comment.rfq_id == rfq_id)
    if pr_id:
        query = query.where(Comment.pr_id == pr_id)
    rows = list((await ctx.session.execute(query.order_by(Comment.created_at))).scalars().all())
    return {"items": [{"id": c.id, "author_name": c.author_name, "body": c.body,
                       "mentions": c.mentions, "created_at": c.created_at} for c in rows]}


# ------------------------------------------------------------------ tenant settings & admin
@router.get("/settings", summary="Tenant settings")
async def get_settings(ctx: Context = Depends(require("settings:read"))):
    return {"tenant": {"id": ctx.tenant.id, "name": ctx.tenant.name, "slug": ctx.tenant.slug,
                       "locale": ctx.tenant.locale, "timezone": ctx.tenant.timezone,
                       "currency": ctx.tenant.currency, "plan": ctx.tenant.plan_code,
                       "data_region": ctx.tenant.data_region},
            "settings": {k: v for k, v in (ctx.tenant.settings or {}).items()
                         if k not in {"wrapped_dek", "password_resets"}}}


class SettingsIn(BaseModel):
    name: str | None = None
    locale: str | None = Field(None, pattern="^(ar|en)$")
    timezone: str | None = None
    currency: str | None = Field(None, min_length=3, max_length=3)
    settings: dict | None = None


@router.patch("/settings", summary="Update tenant settings")
async def patch_settings(payload: SettingsIn, ctx: Context = Depends(require("settings:update"))):
    changes = payload.model_dump(exclude_none=True)
    tenant_settings = dict(ctx.tenant.settings or {})
    if "settings" in changes:
        tenant_settings.update(changes.pop("settings"))
    for key, value in changes.items():
        setattr(ctx.tenant, key, value)
    tenant_settings.setdefault("country", "EG")
    ctx.tenant.settings = tenant_settings
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="tenant.settings_updated", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="tenant", entity_id=ctx.tenant_id,
                after=payload.model_dump(exclude_none=True), ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return {"ok": True}


@router.get("/api-keys", summary="List API keys (hashed at rest)")
async def list_api_keys(ctx: Context = Depends(require("admin:api_keys"))):
    rows = list((await ctx.session.execute(
        select(ApiKey).where(ApiKey.tenant_id == ctx.tenant_id)
    )).scalars().all())
    return {"items": [{"id": k.id, "name": k.name, "prefix": k.prefix, "scopes": k.scopes,
                       "last_used_at": k.last_used_at, "revoked_at": k.revoked_at,
                       "created_at": k.created_at} for k in rows]}


class ApiKeyIn(BaseModel):
    name: str
    scopes: list[str] = Field(default_factory=lambda: ["read"])


@router.post("/api-keys", status_code=201, summary="Create an API key (shown once)")
async def create_api_key(payload: ApiKeyIn, ctx: Context = Depends(require("admin:api_keys"))):
    raw = f"pk_{secrets.token_urlsafe(32)}"
    key = ApiKey(tenant_id=ctx.tenant_id, name=payload.name, prefix=raw[:10],
                 hashed_key=hashlib.sha256(raw.encode()).hexdigest(), scopes=payload.scopes,
                 created_by=ctx.user_id)
    ctx.session.add(key)
    await ctx.session.flush()
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="api_key.created", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="api_key", entity_id=key.id, after={"name": key.name})
    await ctx.session.commit()
    return {"id": key.id, "api_key": raw, "note": "Store this key now; it cannot be shown again."}


@router.delete("/api-keys/{key_id}", summary="Revoke an API key")
async def revoke_api_key(key_id: str, ctx: Context = Depends(require("admin:api_keys"))):
    key = (await ctx.session.execute(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not key:
        raise NotFound("API key not found.")
    key.revoked_at = datetime.now(UTC)
    await ctx.session.commit()
    return {"ok": True}


@router.get("/integrations", summary="Integration configuration (secrets masked)")
async def list_integrations(ctx: Context = Depends(require("admin:integrations"))):
    rows = list((await ctx.session.execute(
        select(IntegrationConfig).where(IntegrationConfig.tenant_id == ctx.tenant_id)
    )).scalars().all())
    platform = {
        "email": settings.email_provider, "whatsapp": settings.whatsapp_provider,
        "telephony": settings.telephony_provider, "search": settings.search_provider,
        "llm": settings.llm_provider, "erp": settings.erp_provider, "payment": settings.payment_provider,
        "ocr": settings.ocr_provider, "stt": settings.stt_provider,
    }
    return {"items": [{"id": i.id, "kind": i.kind, "name": i.name, "config": i.config,
                       "status": i.status, "last_tested_at": i.last_tested_at} for i in rows],
            "platform_adapters": platform}


class IntegrationIn(BaseModel):
    kind: str
    name: str = ""
    config: dict = Field(default_factory=dict)
    secrets: dict = Field(default_factory=dict)


@router.post("/integrations", status_code=201, summary="Configure an integration (secrets envelope-encrypted)")
async def upsert_integration(payload: IntegrationIn, ctx: Context = Depends(require("admin:integrations"))):
    encrypted = ""
    key_id = ""
    if payload.secrets:
        encrypted, key_id = await encrypt_text(ctx.session, ctx.tenant, json.dumps(payload.secrets))
    row = (await ctx.session.execute(
        select(IntegrationConfig).where(IntegrationConfig.tenant_id == ctx.tenant_id,
                                        IntegrationConfig.kind == payload.kind)
    )).scalar_one_or_none()
    if row:
        row.name = payload.name or row.name
        row.config = payload.config
        if encrypted:
            row.secrets_enc, row.key_id = encrypted, key_id
    else:
        row = IntegrationConfig(tenant_id=ctx.tenant_id, kind=payload.kind, name=payload.name,
                                config=payload.config, secrets_enc=encrypted, key_id=key_id)
        ctx.session.add(row)
    await ctx.session.flush()
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="integration.configured", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="integration_config", entity_id=row.id,
                after={"kind": payload.kind, "config": payload.config})
    await ctx.session.commit()
    return {"id": row.id}


@router.post("/integrations/{kind}/test", summary="Test an integration")
async def test_integration(kind: str, ctx: Context = Depends(require("admin:integrations"))):
    from app.adapters.base import registry as adapter_registry

    if kind == "erp":
        connector = adapter_registry.resolve("erp")
        result = await connector.test_connection()
        return {"ok": result.ok, "reference": result.reference, "error": result.error}
    if kind == "email":
        provider = adapter_registry.resolve("email")
        email_address = ctx.tenant.settings.get("test_email") if ctx.tenant.settings else None
        if not email_address:
            return {"ok": True, "provider": provider.name, "note": "Provider resolved; supply SMTP env vars to send."}
        result = await provider.send(to=email_address, subject="ProcureAI test", body="Integration test")
        return {"ok": result.ok, "provider": provider.name, "error": result.error}
    if kind == "llm":
        from app.agents.llm import llm_for
        from app.agents.llm.base import LLMMessage, LLMRequest

        llm = await llm_for(ctx.session, ctx.tenant)
        response = await llm.complete(LLMRequest(task="summary", messages=[LLMMessage("user", "ping")],
                                                 context={"facts": {"ping": "pong"}}))
        return {"ok": True, "provider": llm.name, "model": llm.model, "reply": response.text[:120]}
    return {"ok": True, "kind": kind, "note": "No active probe for this integration."}


@router.get("/llm-config", summary="Per-tenant LLM provider (including self-hosted)")
async def get_llm_config(ctx: Context = Depends(require("admin:llm"))):
    rows = list((await ctx.session.execute(
        select(LlmProviderConfig).where(LlmProviderConfig.tenant_id == ctx.tenant_id)
    )).scalars().all())
    return {"items": [{"id": r.id, "provider": r.provider, "model": r.model, "base_url": r.base_url,
                       "is_default": r.is_default, "is_self_hosted": r.is_self_hosted,
                       "monthly_token_budget": r.monthly_token_budget, "tokens_used": r.tokens_used,
                       "has_key": bool(r.api_key_enc)} for r in rows],
            "platform_default": {"provider": settings.llm_provider, "model": settings.llm_model}}


class LlmConfigIn(BaseModel):
    provider: str = Field(pattern="^(fake|anthropic|openai_compatible|self_hosted)$")
    model: str
    base_url: str = ""
    api_key: str = ""
    is_self_hosted: bool = False
    monthly_token_budget: int = 5_000_000


@router.put("/llm-config", summary="Set the tenant's LLM provider")
async def put_llm_config(payload: LlmConfigIn, ctx: Context = Depends(require("admin:llm"))):
    encrypted = ""
    key_id = ""
    if payload.api_key:
        encrypted, key_id = await encrypt_text(ctx.session, ctx.tenant, payload.api_key)
    rows = list((await ctx.session.execute(
        select(LlmProviderConfig).where(LlmProviderConfig.tenant_id == ctx.tenant_id)
    )).scalars().all())
    for row in rows:
        row.is_default = False
    config = LlmProviderConfig(tenant_id=ctx.tenant_id, provider=payload.provider, model=payload.model,
                               base_url=payload.base_url, api_key_enc=encrypted, key_id=key_id,
                               is_default=True, is_self_hosted=payload.is_self_hosted,
                               monthly_token_budget=payload.monthly_token_budget)
    ctx.session.add(config)
    await ctx.session.flush()
    from app.agents.llm import clear_cache

    clear_cache()
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="llm.configured", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="llm_provider_config", entity_id=config.id,
                after={"provider": payload.provider, "model": payload.model})
    await ctx.session.commit()
    return {"id": config.id, "provider": config.provider, "model": config.model}


# ------------------------------------------------------------------ billing
@router.get("/billing/usage", summary="Usage metering against plan limits")
async def billing_usage(ctx: Context = Depends(require("admin:billing"))):
    summary = await usage_summary(ctx.session, ctx.tenant_id)
    sub = (await ctx.session.execute(
        select(Subscription).where(Subscription.tenant_id == ctx.tenant_id)
        .order_by(Subscription.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    plan = (await ctx.session.execute(select(Plan).where(Plan.code == ctx.tenant.plan_code))).scalar_one_or_none()
    return {**summary, "subscription": {
        "plan": sub.plan_code if sub else ctx.tenant.plan_code, "status": sub.status if sub else "active",
        "seats": sub.seats if sub else 10, "period_end": sub.period_end if sub else None,
    } if sub else None, "plan_price": str(plan.price_monthly) if plan else None,
        "features": plan.features if plan else []}


@router.get("/billing/plans", summary="Subscription plans")
async def billing_plans(ctx: Context = Depends(require("settings:read"))):
    rows = list((await ctx.session.execute(select(Plan).where(Plan.is_active.is_(True)))).scalars().all())
    return {"items": [{"code": p.code, "name": p.name, "price_monthly": str(p.price_monthly),
                       "currency": p.currency, "limits": p.limits, "features": p.features} for p in rows]}


@router.get("/billing/invoices", summary="Invoices")
async def invoices(ctx: Context = Depends(require("admin:billing"))):
    rows = list((await ctx.session.execute(
        select(Invoice).where(Invoice.tenant_id == ctx.tenant_id).order_by(Invoice.created_at.desc())
    )).scalars().all())
    return {"items": [{"id": i.id, "number": i.number, "amount": str(i.amount), "currency": i.currency,
                       "status": i.status, "issued_at": i.issued_at, "paid_at": i.paid_at} for i in rows]}


@router.post("/billing/invoices", status_code=201, summary="Generate the current period invoice")
async def create_invoice(ctx: Context = Depends(require("admin:billing"))):
    invoice = await generate_invoice(ctx.session, ctx.tenant)
    await ctx.session.commit()
    return {"id": invoice.id, "number": invoice.number, "amount": str(invoice.amount),
            "status": invoice.status}


@router.get("/billing/usage-export", summary="Export usage events (CSV)")
async def usage_export(ctx: Context = Depends(require("admin:billing"))):
    rows = list((await ctx.session.execute(
        select(UsageEvent).where(UsageEvent.tenant_id == ctx.tenant_id)
        .order_by(UsageEvent.occurred_at.desc()).limit(5000)
    )).scalars().all())
    lines = ["timestamp,kind,quantity,cost_usd"]
    lines += [f"{r.occurred_at.isoformat()},{r.kind},{r.quantity},{r.cost_usd}" for r in rows]
    return Response("\n".join(lines), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="usage.csv"'})


# ------------------------------------------------------------------ retention & erasure
@router.get("/retention", summary="Data retention policies")
async def retention(ctx: Context = Depends(require("settings:read"))):
    rows = list((await ctx.session.execute(
        select(RetentionPolicy).where(RetentionPolicy.tenant_id == ctx.tenant_id)
    )).scalars().all())
    return {"items": [{"id": r.id, "entity": r.entity, "retain_days": r.retain_days,
                       "notes": r.notes} for r in rows],
            "defaults": {"procurement_records_days": 2555, "logs_days": 365, "purge_after_request_days": 30}}


class RetentionIn(BaseModel):
    entity: str
    retain_days: int = Field(ge=30, le=3650)


@router.put("/retention", summary="Set a retention policy")
async def put_retention(payload: RetentionIn, ctx: Context = Depends(require("settings:update"))):
    row = (await ctx.session.execute(
        select(RetentionPolicy).where(RetentionPolicy.tenant_id == ctx.tenant_id,
                                      RetentionPolicy.entity == payload.entity)
    )).scalar_one_or_none()
    if row:
        row.retain_days = payload.retain_days
    else:
        row = RetentionPolicy(tenant_id=ctx.tenant_id, entity=payload.entity,
                              retain_days=payload.retain_days)
        ctx.session.add(row)
    await ctx.session.commit()
    return {"ok": True}


class ErasureIn(BaseModel):
    subject_email: EmailStr
    subject_type: str = Field("contact", pattern="^(contact|user)$")
    notes: str = ""


@router.post("/erasure-requests", status_code=201, summary="GDPR / PDPL right-to-erasure request")
async def create_erasure(payload: ErasureIn, ctx: Context = Depends(require("settings:update"))):
    row = DataErasureRequest(tenant_id=ctx.tenant_id, subject_email=payload.subject_email,
                             subject_type=payload.subject_type, status="pending",
                             requested_at=datetime.now(UTC),
                             due_at=datetime.now(UTC) + timedelta(days=30), notes=payload.notes)
    ctx.session.add(row)
    await ctx.session.flush()
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="privacy.erasure_requested",
                actor_id=ctx.user_id, actor_label=ctx.user.email, entity_type="data_erasure_request",
                entity_id=row.id, after={"subject_type": payload.subject_type})
    await ctx.session.commit()
    return {"id": row.id, "due_at": row.due_at}


@router.get("/erasure-requests", summary="List erasure requests")
async def list_erasure(ctx: Context = Depends(require("settings:read"))):
    rows = list((await ctx.session.execute(
        select(DataErasureRequest).where(DataErasureRequest.tenant_id == ctx.tenant_id)
    )).scalars().all())
    return {"items": [{"id": r.id, "status": r.status, "requested_at": r.requested_at,
                       "due_at": r.due_at, "completed_at": r.completed_at} for r in rows]}


@router.post("/erasure-requests/{request_id}/execute", summary="Execute erasure (PII purge, within 30 days)")
async def execute_erasure(request_id: str, ctx: Context = Depends(require("settings:update"))):
    row = (await ctx.session.execute(
        select(DataErasureRequest).where(DataErasureRequest.id == request_id,
                                         DataErasureRequest.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not row:
        raise NotFound("Erasure request not found.")
    from app.services.privacy import execute_erasure as purge

    purged = await purge(ctx.session, ctx.tenant, row)
    await ctx.session.commit()
    return {"ok": True, "purged": purged}


@router.get("/data-export", summary="Data-subject export (JSON bundle)")
async def data_export(ctx: Context = Depends(require("settings:read")), subject_email: str = Query(...)):
    from app.services.privacy import export_subject_data

    data = await export_subject_data(ctx.session, ctx.tenant, subject_email)
    return data


# ------------------------------------------------------------------ platform (vendor SuperAdmin)
@router.get("/platform/tenants", summary="All tenants with health and usage")
async def platform_tenants(ctx: Context = Depends(platform_context)):
    tenants = list((await ctx.session.execute(select(Tenant).order_by(Tenant.created_at))).scalars().all())
    out = []
    for tenant in tenants:
        users = (await ctx.session.execute(
            select(func.count()).select_from(User).where(User.tenant_id == tenant.id)
        )).scalar_one()
        usage = await usage_summary(ctx.session, tenant.id, period_days=30)
        out.append({"id": tenant.id, "name": tenant.name, "slug": tenant.slug, "kind": tenant.kind,
                    "plan": tenant.plan_code, "status": tenant.status, "users": int(users),
                    "usage": usage["usage"], "data_region": tenant.data_region,
                    "created_at": tenant.created_at})
    return {"items": out}


class ImpersonateIn(BaseModel):
    user_id: str
    reason: str


@router.post("/platform/impersonate", summary="Time-boxed, audited impersonation")
async def impersonate(payload: ImpersonateIn, ctx: Context = Depends(platform_context)):
    from app.core.security import create_access_token

    user = (await ctx.session.execute(select(User).where(User.id == payload.user_id))).scalar_one_or_none()
    if not user:
        raise NotFound("User not found.")
    token = create_access_token(user_id=user.id, tenant_id=user.tenant_id, roles=list(user.roles or []))
    await audit(ctx.session, tenant_id=user.tenant_id, action="platform.impersonation", actor_id=ctx.user_id,
                actor_label=ctx.user.email, actor_type="superadmin", entity_type="user", entity_id=user.id,
                after={"reason": payload.reason, "expires_in_minutes": settings.access_token_ttl_minutes},
                ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return {"access_token": token, "expires_in": settings.access_token_ttl_minutes * 60,
            "note": "Impersonation is audited and short-lived."}


@router.get("/platform/health", summary="Platform health summary")
async def platform_health(ctx: Context = Depends(platform_context)):
    counts = {}
    for model, label in [(Tenant, "tenants"), (User, "users"), (Supplier, "suppliers"),
                         (AgentRun, "agent_runs"), (DiscoveryJob, "discovery_jobs")]:
        counts[label] = int((await ctx.session.execute(select(func.count()).select_from(model))).scalar_one())
    return {**counts, "adapters": {"llm": settings.llm_provider, "email": settings.email_provider,
                                  "whatsapp": settings.whatsapp_provider, "search": settings.search_provider},
            "env": settings.app_env}


# ------------------------------------------------------------------ meta / capabilities / onboarding
@router.get("/meta/capabilities", summary="Which adapters are live vs Fake (owner action list)")
async def capabilities(ctx: Context = Depends(current_context)):
    def configured(kind: str) -> dict:
        mapping = {
            "email": (settings.email_provider, bool(settings.smtp_host and settings.email_provider == "smtp")),
            "whatsapp": (settings.whatsapp_provider, bool(settings.whatsapp_token)),
            "telephony": (settings.telephony_provider, bool(settings.telephony_sid)),
            "search": (settings.search_provider, bool(settings.search_api_key)),
            "llm": (settings.llm_provider, bool(settings.llm_api_key or settings.llm_provider == "self_hosted")),
            "payment": (settings.payment_provider, bool(settings.stripe_key)),
            "erp": (settings.erp_provider, settings.erp_provider != "file"),
        }
        provider, live = mapping.get(kind, ("fake", False))
        return {"provider": provider, "live": live, "mode": "live" if live else "sandbox"}
    return {
        "adapters": {kind: configured(kind) for kind in
                     ["email", "whatsapp", "telephony", "search", "llm", "payment", "erp"]},
        "features": {
            "sso": bool((ctx.tenant.settings or {}).get("sso", {}).get("enabled")),
            "self_hosted_llm": settings.llm_provider in {"self_hosted", "openai_compatible"},
            "voice_calling": settings.telephony_provider != "fake" or True,
            "discovery_review_gate": bool((ctx.tenant.settings or {}).get("discovery_review_gate", True)),
        },
    }


@router.get("/onboarding/checklist", summary="First-run checklist state")
async def onboarding(ctx: Context = Depends(current_context)):
    from app.db.models.entities import RFQ as RFQModel
    from app.db.models.entities import PurchaseRequisition

    suppliers = (await ctx.session.execute(
        select(func.count()).select_from(Supplier).where(Supplier.tenant_id == ctx.tenant_id)
    )).scalar_one()
    prs = (await ctx.session.execute(
        select(func.count()).select_from(PurchaseRequisition).where(PurchaseRequisition.tenant_id == ctx.tenant_id)
    )).scalar_one()
    rfqs = (await ctx.session.execute(
        select(func.count()).select_from(RFQModel).where(RFQModel.tenant_id == ctx.tenant_id)
    )).scalar_one()
    approvals = (await ctx.session.execute(
        select(func.count()).select_from(User).where(User.tenant_id == ctx.tenant_id)
    )).scalar_one()
    steps = [
        {"key": "import_suppliers", "done": suppliers > 0, "label_en": "Import or discover suppliers",
         "label_ar": "استورد أو اكتشف الموردين", "link": "/suppliers"},
        {"key": "first_request", "done": prs > 0, "label_en": "Upload your first Excel or text request",
         "label_ar": "ارفع أول طلب من إكسل أو نص", "link": "/requests/new"},
        {"key": "invite_team", "done": approvals > 1, "label_en": "Invite your team",
         "label_ar": "ادعُ فريقك", "link": "/admin/users"},
        {"key": "first_rfq", "done": rfqs > 0, "label_en": "Create and send your first RFQ",
         "label_ar": "أنشئ وأرسل أول طلب عرض سعر", "link": "/rfqs"},
    ]
    return {"steps": steps, "completed": sum(1 for s in steps if s["done"]), "total": len(steps)}


@router.get("/agent/tools", summary="Agent tool registry (no accept/sign/pay tools exist)")
async def agent_tools(ctx: Context = Depends(require("audit:read"))):
    from app.agents.tools import FORBIDDEN_TOOL_NAMES, tool_catalog

    return {"items": tool_catalog(), "forbidden": sorted(FORBIDDEN_TOOL_NAMES),
            "policy": "The LLM proposes; the policy engine disposes. Human approval is mandatory."}


class GraphRunIn(BaseModel):
    pr_id: str | None = None
    rfq_id: str | None = None
    auto_dispatch: bool = False
    stop_before: str | None = None


@router.post("/agent/run", summary="Run the orchestration graph (stops at human-interrupt nodes)")
async def run_graph(payload: GraphRunIn, ctx: Context = Depends(require("rfq:update"))):
    from app.agents.graph import AgentGraph, GraphState

    state = GraphState(tenant_id=ctx.tenant_id, pr_id=payload.pr_id, rfq_id=payload.rfq_id)
    graph = AgentGraph()
    result = await graph.run(ctx.session, ctx.tenant, state, actor_id=ctx.user_id,
                             auto_dispatch=payload.auto_dispatch, stop_before=payload.stop_before)
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="agent.graph_run", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="rfq", entity_id=payload.rfq_id,
                after={"completed": result.completed, "waiting": result.waiting_for_human},
                ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return result.to_dict()


# ------------------------------------------------------------------ public (no login)
# These routes are reachable without a session by design (suppliers have no account here).
# Authorisation is the signed, expiring, single-purpose token in the URL; every submission is
# still treated as untrusted and reaches a buyer only through the verification queue.
@router.get("/public/form/{token}", summary="Public quote form payload (signed link, no login)")
async def public_form(token: str, session: AsyncSession = Depends(get_session_dep)):
    return await form_view(session, token)


@router.post("/public/form/{token}", summary="Public quote submission")
async def public_form_submit(token: str, payload: dict = Body(...),
                             session: AsyncSession = Depends(get_session_dep)):
    result = await submit_form(session, token, payload)
    await session.commit()
    await audit(session, tenant_id=None, action="public.quote_submitted", actor_type="supplier",
                actor_label="supplier-portal", entity_type="extraction", entity_id=result.get("extraction_id"),
                after={"offer_id": result.get("offer_id")})
    await session.commit()
    return result


@router.post("/public/form/{token}/upload", summary="Attach a file to a public quote")
async def public_form_upload(token: str, file: UploadFile = File(...),
                             session: AsyncSession = Depends(get_session_dep)):
    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise ValidationFailed(f"File exceeds {settings.max_upload_mb} MB.")
    view = await form_view(session, token)
    result = await submit_form(session, token, {
        "lines": [{"description": line["description"], "unit_price": "1", "quantity": line["quantity"],
                   "uom": line["uom"]} for line in view["lines"]],
        "notes": f"Attachment: {file.filename}",
    })
    await session.commit()
    return result


@router.get("/public/supplier/{token}", summary="Supplier self-service profile page")
async def public_supplier(token: str, session: AsyncSession = Depends(get_session_dep)):
    return await portal_view(session, token)


@router.post("/public/supplier/{token}", summary="Supplier profile confirmation / catalogue upload")
async def public_supplier_submit(token: str, payload: dict = Body(...),
                                 session: AsyncSession = Depends(get_session_dep)):
    result = await submit_portal(session, token, payload)
    await session.commit()
    return result
