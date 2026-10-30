"""Authentication: password login, TOTP MFA, rotating refresh sessions, lockout, reset."""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.config import settings
from app.core.crypto import decrypt_secret_raw, encrypt_secret_raw
from app.core.errors import Forbidden, Unauthorized, ValidationFailed
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_backup_codes,
    hash_backup_code,
    hash_password,
    password_policy_errors,
    provisioning_uri,
    random_password,
    verify_password,
    verify_totp,
)
from app.db.models.entities import RefreshSession, Tenant, User

log = logging.getLogger("procureai.auth")
MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15


async def find_user(session: AsyncSession, email: str, tenant_slug: str | None = None) -> tuple[User, Tenant] | None:
    query = select(User).where(User.email == email.lower().strip())
    if tenant_slug:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == tenant_slug))).scalar_one_or_none()
        if not tenant:
            return None
        query = query.where(User.tenant_id == tenant.id)
    user = (await session.execute(query)).scalars().first()
    if not user:
        return None
    tenant = (await session.execute(select(Tenant).where(Tenant.id == user.tenant_id))).scalar_one_or_none()
    return (user, tenant) if tenant else None


async def issue_tokens(session: AsyncSession, user: User, tenant: Tenant, *, user_agent: str = "",
                       ip: str = "") -> dict:
    access = create_access_token(user_id=user.id, tenant_id=tenant.id, roles=list(user.roles or []),
                                 is_superadmin=user.is_superadmin)
    jti = secrets.token_urlsafe(24)
    refresh = create_refresh_token(user_id=user.id, tenant_id=tenant.id, jti=jti)
    session.add(RefreshSession(tenant_id=tenant.id, user_id=user.id, jti=jti,
                               expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days),
                               user_agent=user_agent[:255], ip=ip[:64]))
    user.last_login_at = datetime.now(UTC)
    user.failed_logins = 0
    await session.flush()
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": settings.access_token_ttl_minutes * 60,
        "user": user_payload(user, tenant),
    }


def user_payload(user: User, tenant: Tenant) -> dict:
    return {
        "id": user.id, "email": user.email, "full_name": user.full_name, "full_name_ar": user.full_name_ar,
        "roles": list(user.roles or []), "is_superadmin": user.is_superadmin,
        "must_change_password": user.must_change_password, "mfa_enabled": user.mfa_enabled,
        "locale": user.locale, "theme": user.theme, "numerals": user.numerals,
        "preferred_currency": user.preferred_currency, "phone": user.phone, "job_title": user.job_title,
        "tenant": {"id": tenant.id, "name": tenant.name, "slug": tenant.slug, "locale": tenant.locale,
                   "timezone": tenant.timezone, "currency": tenant.currency, "plan": tenant.plan_code,
                   "kind": tenant.kind, "data_region": tenant.data_region},
    }


async def login(session: AsyncSession, *, email: str, password: str, tenant_slug: str | None = None,
                user_agent: str = "", ip: str = "") -> dict:
    found = await find_user(session, email, tenant_slug)
    if not found:
        await audit(session, tenant_id=None, action="auth.login_failed", actor_type="anonymous",
                    actor_label=email, entity_type="user", ip=ip, after={"reason": "unknown_email"})
        raise Unauthorized("Invalid email or password.")
    user, tenant = found
    if user.locked_until and user.locked_until > datetime.now(UTC).replace(tzinfo=user.locked_until.tzinfo):
        raise Forbidden("Account temporarily locked after repeated failed sign-ins. Try again later.")
    if not user.is_active:
        raise Forbidden("This account is disabled.")
    if not verify_password(password, user.hashed_password):
        user.failed_logins = (user.failed_logins or 0) + 1
        if user.failed_logins >= MAX_FAILED_LOGINS:
            user.locked_until = datetime.now(UTC) + timedelta(minutes=LOCKOUT_MINUTES)
        await audit(session, tenant_id=tenant.id, action="auth.login_failed", actor_type="user",
                    actor_id=user.id, actor_label=user.email, entity_type="user", entity_id=user.id, ip=ip)
        await session.flush()
        raise Unauthorized("Invalid email or password.")

    if user.mfa_enabled:
        challenge = create_access_token(user_id=user.id, tenant_id=tenant.id, roles=["mfa_challenge"])
        await audit(session, tenant_id=tenant.id, action="auth.mfa_challenge", actor_type="user",
                    actor_id=user.id, actor_label=user.email, ip=ip)
        await session.flush()
        return {"mfa_required": True, "challenge_token": challenge}

    tokens = await issue_tokens(session, user, tenant, user_agent=user_agent, ip=ip)
    await audit(session, tenant_id=tenant.id, action="auth.login", actor_type="user", actor_id=user.id,
                actor_label=user.email, entity_type="user", entity_id=user.id, ip=ip)
    await session.flush()
    return {**tokens, "mfa_required": False}


async def verify_mfa(session: AsyncSession, *, challenge_token: str, code: str, user_agent: str = "",
                     ip: str = "") -> dict:
    try:
        payload = decode_token(challenge_token, "access")
    except ValueError as exc:
        raise Unauthorized("MFA challenge expired. Please sign in again.") from exc
    user = (await session.execute(select(User).where(User.id == payload["sub"]))).scalar_one_or_none()
    if not user or not user.mfa_enabled:
        raise Unauthorized("MFA challenge is not valid.")
    tenant = (await session.execute(select(Tenant).where(Tenant.id == user.tenant_id))).scalar_one()
    secret = decrypt_secret_raw(user.totp_secret) if user.totp_secret else ""
    valid = verify_totp(secret, code) if secret else False
    if not valid:
        for index, stored in enumerate(list(user.backup_codes or [])):
            if stored == hash_backup_code(code):
                codes = list(user.backup_codes or [])
                codes.pop(index)
                user.backup_codes = codes
                valid = True
                break
    if not valid:
        await audit(session, tenant_id=tenant.id, action="auth.mfa_failed", actor_type="user",
                    actor_id=user.id, actor_label=user.email, ip=ip)
        await session.flush()
        raise Unauthorized("Invalid verification code.")
    tokens = await issue_tokens(session, user, tenant, user_agent=user_agent, ip=ip)
    await audit(session, tenant_id=tenant.id, action="auth.login_mfa", actor_type="user", actor_id=user.id,
                actor_label=user.email, entity_type="user", entity_id=user.id, ip=ip)
    await session.flush()
    return {**tokens, "mfa_required": False}


async def refresh(session: AsyncSession, refresh_token: str, *, user_agent: str = "", ip: str = "") -> dict:
    try:
        payload = decode_token(refresh_token, "refresh")
    except ValueError as exc:
        raise Unauthorized("Session expired. Please sign in again.") from exc
    row = (await session.execute(
        select(RefreshSession).where(RefreshSession.jti == payload.get("jti"))
    )).scalar_one_or_none()
    if not row or row.revoked_at:
        raise Unauthorized("Session revoked. Please sign in again.")
    if row.expires_at < datetime.now(UTC).replace(tzinfo=row.expires_at.tzinfo):
        raise Unauthorized("Session expired. Please sign in again.")
    row.revoked_at = datetime.now(UTC)  # rotation: single use
    user = (await session.execute(select(User).where(User.id == row.user_id))).scalar_one_or_none()
    if not user or not user.is_active:
        raise Unauthorized("Account is not active.")
    tenant = (await session.execute(select(Tenant).where(Tenant.id == user.tenant_id))).scalar_one()
    return await issue_tokens(session, user, tenant, user_agent=user_agent, ip=ip)


async def logout(session: AsyncSession, refresh_token: str) -> None:
    try:
        payload = decode_token(refresh_token, "refresh")
    except ValueError:
        return
    row = (await session.execute(
        select(RefreshSession).where(RefreshSession.jti == payload.get("jti"))
    )).scalar_one_or_none()
    if row:
        row.revoked_at = datetime.now(UTC)
        await session.flush()


async def change_password(session: AsyncSession, user: User, *, current: str, new: str, ip: str = "",
                          request_id: str = "") -> None:
    if not verify_password(current, user.hashed_password):
        raise Unauthorized("Current password is incorrect.")
    errors = password_policy_errors(new, user.email)
    if errors:
        raise ValidationFailed("Password does not meet policy.", extra={"errors": errors})
    user.hashed_password = hash_password(new)
    user.must_change_password = False
    await audit(session, tenant_id=user.tenant_id, action="auth.password_changed", actor_type="user",
                actor_id=user.id, actor_label=user.email, entity_type="user", entity_id=user.id, ip=ip,
                request_id=request_id)
    await session.flush()


async def start_mfa_enrollment(session: AsyncSession, user: User) -> dict:
    secret = user.totp_secret
    if not secret:
        from app.core.security import generate_totp_secret

        secret = generate_totp_secret()
        user.totp_secret = encrypt_secret_raw(secret)
        await session.flush()
    else:
        secret = decrypt_secret_raw(secret)
    return {"secret": secret, "otpauth_uri": provisioning_uri(secret, user.email),
            "issuer": "ProcureAI", "digits": 6, "period": 30}


async def confirm_mfa_enrollment(session: AsyncSession, user: User, code: str) -> dict:
    secret = decrypt_secret_raw(user.totp_secret) if user.totp_secret else ""
    if not verify_totp(secret, code):
        raise Unauthorized("Invalid code. Check your authenticator app and try again.")
    codes = generate_backup_codes()
    user.mfa_enabled = True
    user.backup_codes = [hash_backup_code(c) for c in codes]
    await audit(session, tenant_id=user.tenant_id, action="auth.mfa_enabled", actor_type="user",
                actor_id=user.id, actor_label=user.email, entity_type="user", entity_id=user.id)
    await session.flush()
    return {"enabled": True, "backup_codes": codes, "warning": "Store these codes securely; they are shown once."}


async def disable_mfa(session: AsyncSession, user: User, code: str) -> None:
    secret = decrypt_secret_raw(user.totp_secret) if user.totp_secret else ""
    if not verify_totp(secret, code):
        raise Unauthorized("Invalid code.")
    user.mfa_enabled = False
    user.backup_codes = []
    await audit(session, tenant_id=user.tenant_id, action="auth.mfa_disabled", actor_type="user",
                actor_id=user.id, actor_label=user.email, entity_type="user", entity_id=user.id)
    await session.flush()


def _reset_token(user: User) -> str:
    raw = f"{user.id}:{int(datetime.now(UTC).timestamp())}"
    return secrets.token_urlsafe(32) + "." + hashlib.sha256(raw.encode()).hexdigest()[:16]


async def request_password_reset(session: AsyncSession, email: str, ip: str = "") -> dict:
    """Always returns success (no account enumeration). Sends a link through the email adapter."""
    found = await find_user(session, email)
    if not found:
        return {"sent": True}
    user, tenant = found
    token = _reset_token(user)
    settings_json = dict(tenant.settings or {})
    resets = dict(settings_json.get("password_resets", {}))
    resets[user.id] = {"token": token, "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}
    settings_json["password_resets"] = resets
    tenant.settings = settings_json
    session.add(tenant)
    from app.adapters.base import registry

    link = f"{settings.public_app_url}/reset-password?token={token}"
    await registry.resolve("email").send(
        to=user.email, subject="Reset your ProcureAI password",
        body=f"Use this link within one hour to reset your password:\n{link}\n\nIf you did not request it, ignore this email.",
    )
    await audit(session, tenant_id=tenant.id, action="auth.password_reset_requested", actor_type="user",
                actor_id=user.id, actor_label=user.email, ip=ip)
    await session.flush()
    return {"sent": True}


async def confirm_password_reset(session: AsyncSession, token: str, new_password: str) -> dict:
    users = list((await session.execute(select(User))).scalars().all())
    for user in users:
        tenant = (await session.execute(select(Tenant).where(Tenant.id == user.tenant_id))).scalar_one_or_none()
        if not tenant:
            continue
        resets = (tenant.settings or {}).get("password_resets", {})
        entry = resets.get(user.id)
        if entry and entry.get("token") == token:
            if datetime.fromisoformat(entry["expires_at"]) < datetime.now(UTC):
                raise ValidationFailed("This reset link has expired.")
            errors = password_policy_errors(new_password, user.email)
            if errors:
                raise ValidationFailed("Password does not meet policy.", extra={"errors": errors})
            user.hashed_password = hash_password(new_password)
            user.must_change_password = False
            resets.pop(user.id, None)
            tenant.settings = {**(tenant.settings or {}), "password_resets": resets}
            await audit(session, tenant_id=tenant.id, action="auth.password_reset_completed",
                        actor_type="user", actor_id=user.id, actor_label=user.email)
            await session.flush()
            return {"ok": True, "email": user.email, "tenant_slug": tenant.slug}
    raise ValidationFailed("Invalid or already used reset link.")


async def list_sessions(session: AsyncSession, user: User) -> list[dict]:
    rows = list((await session.execute(
        select(RefreshSession).where(RefreshSession.user_id == user.id, RefreshSession.revoked_at.is_(None))
        .order_by(RefreshSession.created_at.desc())
    )).scalars().all())
    return [{"id": r.id, "created_at": r.created_at, "expires_at": r.expires_at, "ip": r.ip,
             "user_agent": r.user_agent} for r in rows]


async def revoke_session(session: AsyncSession, user: User, session_id: str) -> None:
    row = (await session.execute(
        select(RefreshSession).where(RefreshSession.id == session_id, RefreshSession.user_id == user.id)
    )).scalar_one_or_none()
    if row:
        row.revoked_at = datetime.now(UTC)
        await session.flush()


def generate_password() -> str:
    return random_password()
