"""OpenID Connect sign-in (authorization code + PKCE).

SSO is off until ``SSO_ENABLED=true`` and an issuer, client id and client secret
are set. A successful login only attaches to a user an administrator already
created in that tenant — there is no just-in-time provisioning, because a
procurement tenant must not gain a buyer because an IdP returned an address.

The transaction (state, nonce, PKCE verifier, tenant) lives in a short-lived
signed cookie, not in a new table. The ID token is accepted only as RS256, and
only after issuer, audience, expiry and nonce match what we sent.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.config import settings
from app.db.models.entities import Tenant, User

log = logging.getLogger("procureai.oidc")

SSO_COOKIE = "procureai_sso"
_TXN_TTL = timedelta(minutes=10)
_HTTP_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_discovery_cache: dict[str, tuple[datetime, dict[str, Any]]] = {}


class SsoError(Exception):
    """A refusal the browser can be redirected with. ``code`` is a stable token, never a secret."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def redirect_uri() -> str:
    if settings.oidc_redirect_uri:
        return settings.oidc_redirect_uri
    return f"{settings.public_app_url.rstrip('/')}{settings.api_prefix}/auth/sso/callback"


def deployment_ready() -> bool:
    return bool(
        settings.sso_enabled
        and settings.oidc_issuer_url
        and settings.oidc_client_id
        and settings.oidc_client_secret
    )


def tenant_allows(tenant: Tenant) -> bool:
    """Global switch first. A tenant can opt out with ``settings.sso.enabled = false``."""
    if not deployment_ready():
        return False
    sso = (tenant.settings or {}).get("sso") or {}
    if sso.get("enabled") is False:
        return False
    return True


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _sign_txn(payload: dict[str, Any]) -> str:
    body = {**payload, "exp": datetime.now(UTC) + _TXN_TTL, "iat": datetime.now(UTC)}
    return jwt.encode(body, settings.secret_key, algorithm="HS256")


def _read_txn(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise SsoError("state") from exc


async def discover(issuer: str) -> dict[str, Any]:
    """Fetch the IdP discovery document. Cached for ten minutes; a bad document is not cached."""
    key = issuer.rstrip("/")
    cached = _discovery_cache.get(key)
    if cached and cached[0] > datetime.now(UTC):
        return cached[1]
    url = f"{key}/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        response = await client.get(url)
    if response.status_code != 200:
        raise SsoError("config")
    document = response.json()
    for field in ("authorization_endpoint", "token_endpoint", "jwks_uri", "issuer"):
        if not document.get(field):
            raise SsoError("config")
    if document["issuer"].rstrip("/") != key:
        raise SsoError("config")
    _discovery_cache[key] = (datetime.now(UTC) + _TXN_TTL, document)
    return document


def clear_discovery_cache() -> None:
    _discovery_cache.clear()


async def _tenant(session: AsyncSession, slug: str) -> Tenant:
    tenant = (await session.execute(
        select(Tenant).where(Tenant.slug == slug, Tenant.kind == "customer")
    )).scalar_one_or_none()
    if tenant is None or not tenant_allows(tenant):
        raise SsoError("disabled")
    return tenant


def _issuer_for(tenant: Tenant) -> str:
    sso = (tenant.settings or {}).get("sso") or {}
    return str(sso.get("issuer") or settings.oidc_issuer_url).rstrip("/")


async def start(session: AsyncSession, tenant_slug: str) -> tuple[str, str]:
    """Return ``(authorize_url, signed_cookie)``."""
    tenant = await _tenant(session, tenant_slug)
    issuer = _issuer_for(tenant)
    document = await discover(issuer)
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier, challenge = _pkce()
    cookie = _sign_txn({
        "state": state, "nonce": nonce, "verifier": verifier,
        "slug": tenant.slug, "iss": issuer,
    })
    query = urlencode({
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": redirect_uri(),
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return f"{document['authorization_endpoint']}?{query}", cookie


async def _public_key(jwks_uri: str, token: str):
    """Fetch the matching JWK without a blocking HTTP client.

    ``PyJWKClient`` uses ``urllib`` and would stall the event loop while the
    identity provider is served on the same loop (the test IdP, and any
    in-process gateway). An async GET keeps the process able to answer.
    """
    header = jwt.get_unverified_header(token)
    if header.get("alg") != "RS256":
        raise SsoError("token")
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        response = await client.get(jwks_uri)
    if response.status_code != 200:
        raise SsoError("token")
    keys = response.json().get("keys") or []
    kid = header.get("kid")
    chosen = next((key for key in keys if kid is None or key.get("kid") == kid), None)
    if not isinstance(chosen, dict):
        raise SsoError("token")
    try:
        return RSAAlgorithm.from_jwk(json.dumps(chosen))
    except (jwt.PyJWTError, ValueError) as exc:
        raise SsoError("token") from exc


def _claims(token: str, *, key, issuer: str, nonce: str) -> dict[str, Any]:
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=settings.oidc_client_id,
            issuer=issuer,
            leeway=60,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise SsoError("token") from exc
    if claims.get("nonce") != nonce:
        raise SsoError("token")
    if claims.get("email_verified") is False:
        raise SsoError("token")
    email = str(claims.get("email") or "").strip().lower()
    if not email or "@" not in email:
        raise SsoError("token")
    return {**claims, "email": email}


async def _exchange(document: dict[str, Any], code: str, verifier: str) -> str:
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(),
        "client_id": settings.oidc_client_id,
        "client_secret": settings.oidc_client_secret,
        "code_verifier": verifier,
    }
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        response = await client.post(
            document["token_endpoint"],
            data=form,
            headers={"accept": "application/json"},
        )
    if response.status_code != 200:
        log.info("oidc token endpoint rejected the code: HTTP %s", response.status_code)
        raise SsoError("token")
    token = (response.json() or {}).get("id_token")
    if not isinstance(token, str) or token.count(".") != 2:
        raise SsoError("token")
    return token


async def complete(session: AsyncSession, *, code: str, state: str, cookie: str,
                   user_agent: str = "", ip: str = "") -> dict[str, Any]:
    """Exchange the code and issue our own session for an existing user.

    Returns the same shape as password login (including ``mfa_required``). An
    unknown address is a refusal, not a new account.
    """
    from app.services.auth import issue_tokens

    if not code or not state or not cookie:
        raise SsoError("state")
    txn = _read_txn(cookie)
    if not secrets.compare_digest(str(txn.get("state") or ""), state):
        raise SsoError("state")
    tenant = await _tenant(session, str(txn.get("slug") or ""))
    issuer = str(txn.get("iss") or "")
    if issuer != _issuer_for(tenant):
        raise SsoError("state")
    document = await discover(issuer)
    id_token = await _exchange(document, code, str(txn.get("verifier") or ""))
    key = await _public_key(document["jwks_uri"], id_token)
    claims = _claims(id_token, key=key, issuer=issuer, nonce=str(txn.get("nonce") or ""))
    user = (await session.execute(
        select(User).where(User.tenant_id == tenant.id, User.email == claims["email"])
    )).scalar_one_or_none()
    if user is None or not user.is_active:
        await audit(session, tenant_id=tenant.id, action="auth.sso_unknown_user", actor_type="anonymous",
                    actor_label=claims["email"], entity_type="user", ip=ip,
                    after={"reason": "not_provisioned"})
        await session.flush()
        raise SsoError("unknown_user")
    if user.mfa_enabled:
        # The IdP authenticated the person, but this account also enrolled a local
        # authenticator. We do not bypass it, and we do not put a challenge token
        # in the query string. The operator signs in with password + TOTP instead.
        await audit(session, tenant_id=tenant.id, action="auth.sso_mfa_blocked", actor_type="user",
                    actor_id=user.id, actor_label=user.email, entity_type="user", entity_id=user.id, ip=ip)
        await session.flush()
        raise SsoError("mfa")
    tokens = await issue_tokens(session, user, tenant, user_agent=user_agent, ip=ip)
    await audit(session, tenant_id=tenant.id, action="auth.sso_login", actor_type="user",
                actor_id=user.id, actor_label=user.email, entity_type="user", entity_id=user.id, ip=ip,
                after={"issuer": issuer, "sub": str(claims.get("sub") or "")[:80]})
    await session.flush()
    return {**tokens, "mfa_required": False}
