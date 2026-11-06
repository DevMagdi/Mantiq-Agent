"""OIDC sign-in against a local identity provider. No account is created by the callback."""
from __future__ import annotations

import contextlib
import json
import socket
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from jwt.algorithms import RSAAlgorithm
from sqlalchemy import func, select

from app.core.config import settings
from app.db.models.entities import Tenant, User
from app.db.session import SessionLocal
from app.services.oidc import clear_discovery_cache

ISSUER_PATH = ""  # filled once the server binds; discovery returns the absolute issuer


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class IdP:
    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "oidc-test"
        self.email = "manager@nile.example.com"
        self.base = ""
        self.codes: dict[str, str] = {}

    def jwk(self) -> dict:
        document = json.loads(RSAAlgorithm.to_jwk(self.key.public_key()))
        document.update({"kid": self.kid, "use": "sig", "alg": "RS256"})
        return document

    def id_token(self, email: str, nonce: str) -> str:
        return jwt.encode(
            {
                "iss": self.base,
                "aud": "procureai-test",
                "sub": "user-1",
                "email": email,
                "email_verified": True,
                "nonce": nonce,
                "iat": 1_700_000_000,
                "exp": 4_000_000_000,
            },
            self.key,
            algorithm="RS256",
            headers={"kid": self.kid},
        )


@contextlib.asynccontextmanager
async def idp_server(idp: IdP):
    import asyncio

    import uvicorn

    app = FastAPI()
    port = _free_port()
    idp.base = f"http://127.0.0.1:{port}"

    @app.get("/.well-known/openid-configuration")
    async def discovery():
        return {
            "issuer": idp.base,
            "authorization_endpoint": f"{idp.base}/authorize",
            "token_endpoint": f"{idp.base}/token",
            "jwks_uri": f"{idp.base}/jwks",
        }

    @app.get("/jwks")
    async def jwks():
        return {"keys": [idp.jwk()]}

    @app.post("/token")
    async def token(request: Request):
        form = {key: str(value) for key, value in (await request.form()).items()}
        code = str(form.get("code") or "")
        if form.get("client_secret") != "secret" or form.get("code_verifier", "") == "":
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        email = idp.codes.get(code)
        if not email:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        # The nonce is whatever the authorize redirect put in the query; the test stored it.
        nonce = idp.codes[f"nonce:{code}"]
        return {"id_token": idp.id_token(email, nonce), "token_type": "Bearer"}

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(50):
            if server.started:
                break
            await asyncio.sleep(0.05)
        yield
    finally:
        server.should_exit = True
        await task


def _arm(monkeypatch, idp: IdP) -> None:
    clear_discovery_cache()
    monkeypatch.setattr(settings, "sso_enabled", True)
    monkeypatch.setattr(settings, "oidc_issuer_url", idp.base)
    monkeypatch.setattr(settings, "oidc_client_id", "procureai-test")
    monkeypatch.setattr(settings, "oidc_client_secret", "secret")
    monkeypatch.setattr(settings, "public_app_url", "http://testserver")
    monkeypatch.setattr(settings, "oidc_redirect_uri", "http://testserver/api/v1/auth/sso/callback")


def _nonce_from_location(location: str) -> tuple[str, str]:
    from urllib.parse import parse_qs, urlparse

    query = parse_qs(urlparse(location).query)
    return query["state"][0], query["nonce"][0]


@pytest.mark.asyncio
async def test_oidc_login_issues_a_session_for_an_existing_user_only(client, monkeypatch):
    idp = IdP()
    async with idp_server(idp):
        _arm(monkeypatch, idp)
        before = None
        async with SessionLocal() as session:
            before = (await session.execute(select(func.count()).select_from(User))).scalar_one()

        start = await client.get("/api/v1/auth/sso/nile/login", follow_redirects=False)
        assert start.status_code == 302, start.text
        assert start.headers["location"].startswith(f"{idp.base}/authorize?")
        state, nonce = _nonce_from_location(start.headers["location"])
        cookie = start.cookies.get("procureai_sso")
        assert cookie

        code = uuid.uuid4().hex
        idp.codes[code] = "manager@nile.example.com"
        idp.codes[f"nonce:{code}"] = nonce
        done = await client.get(
            "/api/v1/auth/sso/callback",
            params={"code": code, "state": state},
            cookies={"procureai_sso": cookie},
            follow_redirects=False,
        )
        assert done.status_code == 302, done.text
        assert done.headers["location"] == "http://testserver/"
        assert "procureai_refresh" in done.cookies

        async with SessionLocal() as session:
            after = (await session.execute(select(func.count()).select_from(User))).scalar_one()
        assert after == before


@pytest.mark.asyncio
async def test_oidc_refuses_an_address_nobody_provisioned(client, monkeypatch):
    idp = IdP()
    async with idp_server(idp):
        _arm(monkeypatch, idp)
        start = await client.get("/api/v1/auth/sso/nile/login", follow_redirects=False)
        state, nonce = _nonce_from_location(start.headers["location"])
        code = uuid.uuid4().hex
        idp.codes[code] = "stranger@not-a-tenant.example"
        idp.codes[f"nonce:{code}"] = nonce
        done = await client.get(
            "/api/v1/auth/sso/callback",
            params={"code": code, "state": state},
            cookies={"procureai_sso": start.cookies.get("procureai_sso")},
            follow_redirects=False,
        )
        assert done.status_code == 302
        assert "sso_error=unknown_user" in done.headers["location"]
        assert "procureai_refresh" not in done.cookies


@pytest.mark.asyncio
async def test_sso_stays_off_until_the_deployment_enables_it(client, monkeypatch):
    monkeypatch.setattr(settings, "sso_enabled", False)
    clear_discovery_cache()
    start = await client.get("/api/v1/auth/sso/nile/login", follow_redirects=False)
    assert start.status_code == 302
    assert "sso_error=disabled" in start.headers["location"]
    config = await client.get("/api/v1/auth/sso/config", params={"tenant_slug": "nile"})
    assert config.json()["enabled"] is False


@pytest.mark.asyncio
async def test_a_tenant_can_opt_out_of_sso(client, monkeypatch):
    idp = IdP()
    async with idp_server(idp):
        _arm(monkeypatch, idp)
        async with SessionLocal() as session:
            tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
            tenant.settings = {**(tenant.settings or {}), "sso": {"enabled": False}}
            await session.commit()
        try:
            start = await client.get("/api/v1/auth/sso/nile/login", follow_redirects=False)
            assert "sso_error=disabled" in start.headers["location"]
        finally:
            async with SessionLocal() as session:
                tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
                settings_map = dict(tenant.settings or {})
                settings_map.pop("sso", None)
                tenant.settings = settings_map
                await session.commit()
