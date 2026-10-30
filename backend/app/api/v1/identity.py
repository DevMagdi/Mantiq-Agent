"""Auth, session, MFA and user/role administration endpoints (FR-AUTH-1..4)."""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Context, current_context, get_session_dep, require
from app.core.audit import audit
from app.core.config import settings
from app.core.errors import Conflict, Forbidden, NotFound
from app.core.rate_limit import check as rate_check
from app.core.rbac import ALL_PERMISSIONS, ROLE_PERMISSIONS, ROLES, permissions_for
from app.core.security import hash_password, password_policy_errors
from app.db.models.entities import ApprovalLimit, Role, Tenant, User
from app.services import auth as auth_service
from app.services.common import notify

router = APIRouter(tags=["identity"])

REFRESH_COOKIE = "procureai_refresh"
CSRF_COOKIE = "procureai_csrf"


def _set_auth_cookies(response: Response, refresh_token: str) -> str:
    csrf = secrets.token_urlsafe(24)
    secure = settings.is_prod
    response.set_cookie(REFRESH_COOKIE, refresh_token, httponly=True, secure=secure, samesite="lax",
                        max_age=settings.refresh_token_ttl_days * 86400, path="/api/v1/auth")
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, secure=secure, samesite="lax",
                        max_age=settings.refresh_token_ttl_days * 86400, path="/")
    return csrf


class LoginIn(BaseModel):
    email: EmailStr
    password: str
    tenant_slug: str | None = None


class MfaVerifyIn(BaseModel):
    challenge_token: str
    code: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


class MfaCodeIn(BaseModel):
    code: str


class ResetRequestIn(BaseModel):
    email: EmailStr


class ResetConfirmIn(BaseModel):
    token: str
    new_password: str


@router.post("/auth/login", summary="Email + password sign-in (TOTP challenge when enabled)")
async def login(payload: LoginIn, request: Request, response: Response,
                session: AsyncSession = Depends(get_session_dep)):
    rate_check(f"login:{request.client.host if request.client else 'unknown'}", 20)
    result = await auth_service.login(
        session, email=payload.email, password=payload.password, tenant_slug=payload.tenant_slug,
        user_agent=request.headers.get("user-agent", ""), ip=request.client.host if request.client else "",
    )
    await session.commit()
    if not result.get("mfa_required"):
        _set_auth_cookies(response, result.pop("refresh_token", ""))
    return result


@router.post("/auth/mfa/verify", summary="Complete sign-in with a TOTP or backup code")
async def mfa_verify(payload: MfaVerifyIn, request: Request, response: Response,
                     session: AsyncSession = Depends(get_session_dep)):
    rate_check(f"mfa:{request.client.host if request.client else 'unknown'}", 15)
    result = await auth_service.verify_mfa(
        session, challenge_token=payload.challenge_token, code=payload.code,
        user_agent=request.headers.get("user-agent", ""), ip=request.client.host if request.client else "",
    )
    await session.commit()
    _set_auth_cookies(response, result.pop("refresh_token", ""))
    return result


@router.post("/auth/refresh", summary="Rotate the refresh token (single use, CSRF protected)")
async def refresh(request: Request, response: Response, session: AsyncSession = Depends(get_session_dep)):
    refresh_token = request.cookies.get(REFRESH_COOKIE, "")
    csrf_cookie = request.cookies.get(CSRF_COOKIE, "")
    csrf_header = request.headers.get("x-csrf-token", "")
    if not refresh_token:
        raise auth_service.Unauthorized("No session cookie present.")
    if csrf_cookie and csrf_cookie != csrf_header:
        raise Forbidden("CSRF validation failed.")
    result = await auth_service.refresh(session, refresh_token,
                                        user_agent=request.headers.get("user-agent", ""),
                                        ip=request.client.host if request.client else "")
    await session.commit()
    _set_auth_cookies(response, result.pop("refresh_token", ""))
    return result


@router.post("/auth/logout", summary="Revoke the current refresh session")
async def logout(request: Request, response: Response, session: AsyncSession = Depends(get_session_dep)):
    await auth_service.logout(session, request.cookies.get(REFRESH_COOKIE, ""))
    await session.commit()
    response.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me", summary="Current user, tenant, permissions and preferences")
async def me(ctx: Context = Depends(current_context)):
    payload = auth_service.user_payload(ctx.user, ctx.tenant)
    payload["permissions"] = sorted(ctx.permissions)
    payload["inbound_email"] = f"rfq+{ctx.tenant.slug}@{settings.public_app_url.split('//')[-1].split(':')[0]}"
    return payload


class PreferencesIn(BaseModel):
    full_name: str | None = None
    full_name_ar: str | None = None
    job_title: str | None = None
    phone: str | None = None
    locale: str | None = Field(None, pattern="^(ar|en)$")
    preferred_currency: str | None = Field(None, min_length=3, max_length=3)
    notification_prefs: dict | None = None


@router.patch("/auth/me", summary="Update profile and preferences (locale, currency, contacts)")
async def update_me(payload: PreferencesIn, ctx: Context = Depends(current_context)):
    changes = payload.model_dump(exclude_none=True)
    for key, value in changes.items():
        setattr(ctx.user, key, value)
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="user.profile_updated", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="user", entity_id=ctx.user_id, after=changes,
                ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    out = auth_service.user_payload(ctx.user, ctx.tenant)
    out["permissions"] = sorted(ctx.permissions)
    return out


@router.post("/auth/change-password", summary="Change password (enforces the password policy)")
async def change_password(payload: ChangePasswordIn, ctx: Context = Depends(current_context)):
    await auth_service.change_password(ctx.session, ctx.user, current=payload.current_password,
                                       new=payload.new_password, ip=ctx.ip, request_id=ctx.request_id)
    if ctx.user.notification_prefs is None:
        ctx.user.notification_prefs = {}
    await ctx.session.commit()
    await notify(ctx.session, tenant_id=ctx.tenant_id, user_id=ctx.user_id, kind="security",
                 title="Your password was changed", title_ar="تم تغيير كلمة المرور",
                 body="If this was not you, contact your administrator immediately.")
    await ctx.session.commit()
    return {"ok": True}


@router.post("/auth/mfa/setup", summary="Start TOTP enrolment (returns otpauth URI + secret)")
async def mfa_setup(ctx: Context = Depends(current_context)):
    data = await auth_service.start_mfa_enrollment(ctx.session, ctx.user)
    await ctx.session.commit()
    return data


@router.post("/auth/mfa/enable", summary="Confirm TOTP enrolment (returns one-time backup codes)")
async def mfa_enable(payload: MfaCodeIn, ctx: Context = Depends(current_context)):
    data = await auth_service.confirm_mfa_enrollment(ctx.session, ctx.user, payload.code)
    await ctx.session.commit()
    return data


@router.post("/auth/mfa/disable", summary="Disable MFA (requires a valid code)")
async def mfa_disable(payload: MfaCodeIn, ctx: Context = Depends(current_context)):
    await auth_service.disable_mfa(ctx.session, ctx.user, payload.code)
    await ctx.session.commit()
    return {"ok": True}


@router.get("/auth/sessions", summary="List active sessions (devices)")
async def sessions(ctx: Context = Depends(current_context)):
    return {"items": await auth_service.list_sessions(ctx.session, ctx.user)}


@router.delete("/auth/sessions/{session_id}", summary="Revoke a device session")
async def revoke_session(session_id: str, ctx: Context = Depends(current_context)):
    await auth_service.revoke_session(ctx.session, ctx.user, session_id)
    await ctx.session.commit()
    return {"ok": True}


@router.post("/auth/password-reset/request", summary="Request a password reset link (no enumeration)")
async def reset_request(payload: ResetRequestIn, request: Request, session: AsyncSession = Depends(get_session_dep)):
    rate_check(f"reset:{request.client.host if request.client else 'unknown'}", 5)
    await auth_service.request_password_reset(session, payload.email,
                                              ip=request.client.host if request.client else "")
    await session.commit()
    return {"sent": True, "message": "If the address exists, a reset link has been sent."}


@router.post("/auth/password-reset/confirm", summary="Complete a password reset")
async def reset_confirm(payload: ResetConfirmIn, session: AsyncSession = Depends(get_session_dep)):
    result = await auth_service.confirm_password_reset(session, payload.token, payload.new_password)
    await session.commit()
    return result


@router.get("/auth/sso/config", summary="SSO availability for the tenant (SAML / OIDC)")
async def sso_config(session: AsyncSession = Depends(get_session_dep), tenant_slug: str | None = None):
    query = select(Tenant).where(Tenant.kind == "customer")
    if tenant_slug:
        query = query.where(Tenant.slug == tenant_slug)
    tenants = list((await session.execute(query)).scalars().all())
    providers = []
    for tenant in tenants:
        sso = (tenant.settings or {}).get("sso", {})
        if sso.get("enabled"):
            providers.append({"tenant_slug": tenant.slug, "tenant_name": tenant.name,
                              "provider": sso.get("provider", "saml"),
                              "login_url": f"{settings.api_prefix}/auth/sso/{tenant.slug}/login"})
    return {"providers": providers,
            "note": "Configure SAML 2.0 or OIDC in Settings → Integrations to enable enterprise SSO."}


# ------------------------------------------------------------------ users & roles
class UserIn(BaseModel):
    email: EmailStr
    full_name: str
    full_name_ar: str = ""
    job_title: str = ""
    roles: list[str]
    password: str | None = None
    plant_id: str | None = None


class UserPatch(BaseModel):
    full_name: str | None = None
    full_name_ar: str | None = None
    job_title: str | None = None
    roles: list[str] | None = None
    is_active: bool | None = None
    plant_id: str | None = None


@router.get("/users", summary="List tenant users")
async def list_users(ctx: Context = Depends(require("admin:users"))):
    rows = list((await ctx.session.execute(
        select(User).where(User.tenant_id == ctx.tenant_id).order_by(User.created_at)
    )).scalars().all())
    return {"items": [{
        "id": u.id, "email": u.email, "full_name": u.full_name, "full_name_ar": u.full_name_ar,
        "roles": u.roles, "is_active": u.is_active, "mfa_enabled": u.mfa_enabled,
        "must_change_password": u.must_change_password, "last_login_at": u.last_login_at,
        "job_title": u.job_title,
    } for u in rows], "total": len(rows)}


@router.post("/users", status_code=201, summary="Create a tenant user")
async def create_user(payload: UserIn, ctx: Context = Depends(require("admin:users"))):
    for role in payload.roles:
        if role not in ROLES or role == "superadmin":
            raise Conflict(f"Role '{role}' cannot be assigned.")
    existing = (await ctx.session.execute(
        select(User).where(User.tenant_id == ctx.tenant_id, User.email == payload.email.lower())
    )).scalar_one_or_none()
    if existing:
        raise Conflict("A user with this email already exists in the tenant.")
    if payload.password:
        errors = password_policy_errors(payload.password, payload.email)
        if errors:
            raise Conflict("Password does not meet policy.", extra={"errors": errors})
    user = User(
        tenant_id=ctx.tenant_id, email=payload.email.lower(), full_name=payload.full_name,
        full_name_ar=payload.full_name_ar, job_title=payload.job_title, roles=payload.roles,
        primary_role=payload.roles[0], hashed_password=hash_password(payload.password or secrets.token_urlsafe(24)),
        must_change_password=True, plant_id=payload.plant_id, locale=ctx.tenant.locale,
    )
    ctx.session.add(user)
    await ctx.session.flush()
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="user.created", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="user", entity_id=user.id,
                after={"email": user.email, "roles": user.roles}, ip=ctx.ip, request_id=ctx.request_id)
    if payload.password:
        await notify(ctx.session, tenant_id=ctx.tenant_id, user_id=user.id, kind="welcome",
                     title="Welcome to ProcureAI", title_ar="أهلاً بك في ProcureAI",
                     body="Please change your password on first sign-in and enable MFA.")
    await ctx.session.commit()
    return {"id": user.id, "email": user.email, "roles": user.roles, "must_change_password": True}


@router.patch("/users/{user_id}", summary="Update a tenant user")
async def update_user(user_id: str, payload: UserPatch, ctx: Context = Depends(require("admin:users"))):
    user = (await ctx.session.execute(
        select(User).where(User.id == user_id, User.tenant_id == ctx.tenant_id)
    )).scalar_one_or_none()
    if not user:
        raise NotFound("User not found.")
    before = {"roles": user.roles, "is_active": user.is_active}
    changes = payload.model_dump(exclude_none=True)
    if "roles" in changes:
        for role in changes["roles"]:
            if role not in ROLES or role == "superadmin":
                raise Conflict(f"Role '{role}' cannot be assigned.")
        user.primary_role = changes["roles"][0] if changes["roles"] else user.primary_role
    for key, value in changes.items():
        setattr(user, key, value)
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="user.updated", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="user", entity_id=user.id, before=before,
                after=changes, ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return {"ok": True}


@router.get("/roles", summary="Role catalogue with the permission matrix (UI mirror of RBAC)")
async def list_roles(ctx: Context = Depends(require("admin:roles"))):
    rows = list((await ctx.session.execute(select(Role).order_by(Role.code))).scalars().all())
    return {"items": [{"code": r.code, "name": r.name, "name_ar": r.name_ar,
                       "permissions": sorted(r.permissions or ROLE_PERMISSIONS.get(r.code, []))} for r in rows]}


@router.get("/permissions", summary="All permissions known to the platform")
async def list_permissions(ctx: Context = Depends(current_context)):
    return {"items": [{"code": code, "description": desc} for code, desc in ALL_PERMISSIONS.items()],
            "mine": sorted(permissions_for(list(ctx.user.roles or []), ctx.user.is_superadmin))}


class LimitIn(BaseModel):
    role_code: str
    category_code: str | None = None
    max_amount: str
    currency: str = "EGP"


@router.get("/limits", summary="Approval limits per role and category (FR-AUTH-3)")
async def list_limits(ctx: Context = Depends(require("settings:read"))):
    rows = list((await ctx.session.execute(
        select(ApprovalLimit).where(ApprovalLimit.tenant_id == ctx.tenant_id)
    )).scalars().all())
    return {"items": [{"id": r.id, "role_code": r.role_code, "category_code": r.category_code,
                       "max_amount": str(r.max_amount), "currency": r.currency} for r in rows]}


@router.post("/limits", status_code=201, summary="Upsert an approval limit")
async def upsert_limit(payload: LimitIn, ctx: Context = Depends(require("admin:limits"))):
    from decimal import Decimal

    row = (await ctx.session.execute(
        select(ApprovalLimit).where(ApprovalLimit.tenant_id == ctx.tenant_id,
                                    ApprovalLimit.role_code == payload.role_code,
                                    ApprovalLimit.category_code == payload.category_code)
    )).scalar_one_or_none()
    if row:
        row.max_amount = Decimal(payload.max_amount)
        row.currency = payload.currency
    else:
        row = ApprovalLimit(tenant_id=ctx.tenant_id, role_code=payload.role_code,
                            category_code=payload.category_code, max_amount=Decimal(payload.max_amount),
                            currency=payload.currency)
        ctx.session.add(row)
    await ctx.session.flush()
    await audit(ctx.session, tenant_id=ctx.tenant_id, action="approval_limit.upserted", actor_id=ctx.user_id,
                actor_label=ctx.user.email, entity_type="approval_limit", entity_id=row.id,
                after=payload.model_dump(), ip=ctx.ip, request_id=ctx.request_id)
    await ctx.session.commit()
    return {"id": row.id}
