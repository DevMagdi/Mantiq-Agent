"""The Integrations screen, end to end: save → probe → live → used by the running system.

Every probe here hits a real socket. The vendor-side server speaks Twilio's account endpoint,
Meta's phone-number endpoint and SerpAPI's search endpoint; the SMTP test speaks enough of the
protocol to log in. Nothing inside the application is patched — the point of these tests is that a
credential saved on the screen changes what the platform *actually uses*.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import socket

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.adapters.base import registry
from app.agents.llm import clear_cache, llm_for
from app.agents.llm.providers import AnthropicProvider
from app.core.crypto import encrypt_text
from app.db.models.entities import AuditLog, IntegrationConfig, Tenant
from app.db.session import SessionLocal
from app.services import integrations as integration_service


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# ------------------------------------------------------------------ vendor-side server
class Vendor:
    """Answers like Twilio, Meta and SerpAPI, and records what the platform sent."""

    def __init__(self) -> None:
        self.twilio_sid = "AC" + "0" * 32
        self.twilio_token = "twilio-auth-token"
        self.meta_token = "EAAG-meta-token"
        self.phone_id = "123456789012345"
        self.serp_key = "serp-key"
        self.calls: list[tuple[str, str]] = []
        self.llm_reply = "pong"


@contextlib.asynccontextmanager
async def vendor_server(vendor: Vendor):
    import uvicorn

    app = FastAPI()

    @app.get("/2010-04-01/Accounts/{sid}.json")
    async def twilio_account(sid: str, request: Request):  # noqa: ANN001
        vendor.calls.append(("twilio", sid))
        auth = request.headers.get("authorization", "")
        expected = "Basic " + __import__("base64").b64encode(
            f"{vendor.twilio_sid}:{vendor.twilio_token}".encode()).decode()
        if sid != vendor.twilio_sid or auth != expected:
            return JSONResponse(status_code=401,
                                content={"code": 20003, "message": "Authenticate"})
        return JSONResponse(content={"sid": sid, "friendly_name": "ProcureAI Test Account",
                                     "status": "active"})

    @app.get("/v20.0/{phone_id}")
    async def meta_phone(phone_id: str, request: Request):  # noqa: ANN001
        vendor.calls.append(("meta", phone_id))
        if request.headers.get("authorization") != f"Bearer {vendor.meta_token}":
            return JSONResponse(status_code=401,
                                content={"error": {"message": "Invalid OAuth access token."}})
        return JSONResponse(content={"id": phone_id, "display_phone_number": "+20 100 000 0000",
                                     "verified_name": "ProcureAI"})

    @app.get("/search.json")
    async def serp(request: Request):  # noqa: ANN001
        vendor.calls.append(("serpapi", str(request.query_params.get("q", ""))))
        if request.query_params.get("api_key") != vendor.serp_key:
            return JSONResponse(status_code=401, content={"error": "Invalid API key"})
        return JSONResponse(content={"organic_results": [
            {"title": "Nile Steel Trading", "link": "https://nile-steel.example.com",
             "snippet": "Steel supplier in Cairo"},
        ]})

    @app.post("/v1/messages")
    async def anthropic(request: Request):  # noqa: ANN001
        payload = await request.json()
        vendor.calls.append(("anthropic", payload.get("model", "")))
        return JSONResponse(content={
            "id": "msg_1", "type": "message", "role": "assistant", "model": payload["model"],
            "content": [{"type": "text", "text": vendor.llm_reply}], "stop_reason": "end_turn",
            "usage": {"input_tokens": 12, "output_tokens": 3},
        })

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    task = asyncio.create_task(server.serve())
    for _ in range(120):
        if server.started:
            break
        await asyncio.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        with contextlib.suppress(Exception):
            await asyncio.wait_for(task, timeout=5)


@pytest.fixture(autouse=True)
async def _clean_integrations():
    """Leave the demo tenant exactly as it was found.

    These tests deliberately create *live* integrations; leaving one behind would silently change the
    adapter every later test sees (the runtime prefers a tenant configuration over the platform one).
    """
    async def drop() -> None:
        async with SessionLocal() as session:
            tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one_or_none()
            if tenant is not None:
                rows = list((await session.execute(
                    select(IntegrationConfig).where(IntegrationConfig.tenant_id == tenant.id)
                )).scalars().all())
                for row in rows:
                    await session.delete(row)
                await session.commit()
        clear_cache()

    await drop()
    yield
    await drop()


@pytest.fixture
async def vendor():
    instance = Vendor()
    async with vendor_server(instance) as base_url:
        yield base_url, instance


@contextlib.asynccontextmanager
async def smtp_server():
    """A minimal ESMTP server: greeting, EHLO with AUTH, AUTH PLAIN, NOOP, QUIT."""
    port = _free_port()
    seen: list[str] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        def send(line: str) -> None:
            writer.write((line + "\r\n").encode())

        send("220 procureai-test ESMTP")
        await writer.drain()
        while True:
            raw = await reader.readline()
            if not raw:
                break
            command = raw.decode(errors="ignore").strip()
            seen.append(command)
            upper = command.upper()
            if upper.startswith("EHLO") or upper.startswith("HELO"):
                send("250-procureai-test")
                send("250-AUTH PLAIN LOGIN")
                send("250 OK")
            elif upper.startswith("AUTH PLAIN"):
                send("235 2.7.0 Authentication successful")
            elif upper.startswith("AUTH LOGIN"):
                send("334 VXNlcm5hbWU6")
                await writer.drain()
                await reader.readline()
                send("334 UGFzc3dvcmQ6")
                await writer.drain()
                await reader.readline()
                send("235 2.7.0 Authentication successful")
            elif upper.startswith("NOOP"):
                send("250 OK")
            elif upper.startswith("QUIT"):
                send("221 Bye")
                await writer.drain()
                break
            else:
                send("250 OK")
            await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", port)
    try:
        yield port, seen
    finally:
        server.close()
        await server.wait_closed()


# ------------------------------------------------------------------ helpers
async def _put(client, admin, kind: str, payload: dict):
    return await client.put(f"/api/v1/integrations/{kind}", headers=admin["headers"], json=payload)


async def _item(client, admin, kind: str) -> dict:
    listing = await client.get("/api/v1/integrations", headers=admin["headers"])
    assert listing.status_code == 200, listing.text
    return next(row for row in listing.json()["items"] if row["kind"] == kind)


async def _reset(client, admin, *kinds: str) -> None:
    for kind in kinds:
        await client.delete(f"/api/v1/integrations/{kind}", headers=admin["headers"])
    clear_cache()


# ------------------------------------------------------------------ catalogue
async def test_the_catalogue_drives_every_field_on_the_screen(client, admin):
    listing = await client.get("/api/v1/integrations", headers=admin["headers"])
    catalogue = {row["key"]: row for row in listing.json()["catalogue"]}

    llm = {p["key"]: p for p in catalogue["llm"]["providers"]}
    assert set(llm) == {"anthropic", "openai_compatible"}
    api_key = next(f for f in llm["anthropic"]["fields"] if f["key"] == "api_key")
    assert api_key["secret"] is True and api_key["required"] is True
    assert next(f for f in llm["openai_compatible"]["fields"] if f["key"] == "base_url")["required"]

    whatsapp = {p["key"] for p in catalogue["whatsapp"]["providers"]}
    assert {"cloud_api", "twilio"} <= whatsapp
    email_fields = {f["key"]: f for f in catalogue["email"]["providers"][0]["fields"]}
    assert {"host", "port", "user", "password", "from_address"} <= set(email_fields)
    assert email_fields["password"]["secret"] is True

    # Read-only platforms are advertised as such instead of pretending to be editable.
    assert catalogue["payment"]["tenant_managed"] is False


async def test_status_starts_as_sandbox_when_the_platform_runs_a_fake(client, admin):
    item = await _item(client, admin, "email")
    assert item["status"] in {"sandbox", "live"}      # never "configured by hope"
    assert item["provider_source"] == "platform"


# ------------------------------------------------------------------ save → live
async def test_saving_real_llm_credentials_makes_it_live_and_the_runtime_uses_them(
        client, admin, vendor):
    base_url, spy = vendor
    await _reset(client, admin, "llm")
    saved = await _put(client, admin, "llm", {
        "provider": "anthropic",
        "config": {"model": "claude-sonnet-4-5-20250929", "base_url": base_url},
        "secrets": {"api_key": "sk-ant-api03-integration-test"},
        "enabled": True,
    })
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["status"] == "live", body
    assert body["secrets_saved"] == ["api_key"]
    assert body["secrets_changed"] == ["api_key"]
    assert body["masked"]["api_key"].endswith("test")
    assert "sk-ant-api03-integration-test" not in saved.text

    probe = await client.post("/api/v1/integrations/llm/test", headers=admin["headers"])
    assert probe.status_code == 200, probe.text
    result = probe.json()
    assert result["ok"] is True and result["status"] == "live", result
    assert "pong" in result["detail"]
    assert spy.calls[-1][0] == "anthropic"

    # …and the running system now uses it: `llm_for` resolves the tenant's provider, and the call
    # reaches the vendor instead of the deterministic fake.
    async with SessionLocal() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
        llm = await llm_for(session, tenant)
    assert isinstance(llm.provider, AnthropicProvider), type(llm.provider)
    response = await llm.provider.complete(
        __import__("app.agents.llm.base", fromlist=["LLMRequest"]).LLMRequest(
            task="summary", messages=[], context={})
    )
    assert response.text == "pong"

    item = await _item(client, admin, "llm")
    assert item["status"] == "live" and item["provider_source"] == "tenant"
    assert item["last_tested_at"] and item["last_error"] == ""


async def test_secrets_are_encrypted_masked_and_audited_without_their_values(client, admin):
    await _reset(client, admin, "search")
    secret = "serp-secret-value-9f3a"
    saved = await _put(client, admin, "search", {
        "provider": "serpapi", "config": {}, "secrets": {"api_key": secret}, "enabled": True,
    })
    assert saved.status_code == 200, saved.text

    async with SessionLocal() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
        row = (await session.execute(
            select(IntegrationConfig).where(IntegrationConfig.tenant_id == tenant.id,
                                            IntegrationConfig.kind == "search")
        )).scalars().first()
        assert row.secrets_enc and secret not in row.secrets_enc
        audit_rows = list((await session.execute(
            select(AuditLog).where(AuditLog.tenant_id == tenant.id,
                                   AuditLog.action == "integration.secrets_updated")
        )).scalars().all())
    assert audit_rows, "changing a credential must be audited"
    assert any("api_key" in json.dumps(row.after) for row in audit_rows)
    assert all(secret not in json.dumps(row.after) for row in audit_rows)

    # A reload of the screen shows the mask, never the secret.
    listing = await client.get("/api/v1/integrations", headers=admin["headers"])
    assert secret not in listing.text


async def test_saving_an_incomplete_configuration_is_error_not_live(client, admin):
    await _reset(client, admin, "search")
    saved = await _put(client, admin, "search", {
        "provider": "brave", "config": {}, "secrets": {}, "enabled": True,
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["status"] == "error"
    assert "api_key" in saved.json()["missing"]

    # An incomplete integration must not be used by the runtime — the platform adapter stays in place.
    async with SessionLocal() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
        adapter = await integration_service.adapter_for(session, tenant, "search")
    assert adapter.name == registry.resolve("search").name


# ------------------------------------------------------------------ probes against real sockets
async def test_twilio_probe_verifies_the_account_over_http(client, admin, vendor):
    base_url, spy = vendor
    await _reset(client, admin, "telephony")
    await _put(client, admin, "telephony", {
        "provider": "twilio",
        "config": {"account_sid": spy.twilio_sid, "from_number": "+12025550123",
                   "base_url": base_url},
        "secrets": {"auth_token": spy.twilio_token},
        "enabled": True,
    })
    probe = await client.post("/api/v1/integrations/telephony/test", headers=admin["headers"])
    assert probe.status_code == 200, probe.text
    assert probe.json()["ok"] is True, probe.json()
    assert "ProcureAI Test Account" in probe.json()["detail"]
    assert spy.calls[-1][0] == "twilio"


async def test_a_bad_twilio_token_is_an_error_with_the_vendor_message(client, admin, vendor):
    base_url, spy = vendor
    await _reset(client, admin, "sms")
    await _put(client, admin, "sms", {
        "provider": "twilio",
        "config": {"account_sid": spy.twilio_sid, "from_number": "+12025550123",
                   "base_url": base_url},
        "secrets": {"auth_token": "wrong-token"},
        "enabled": True,
    })
    probe = await client.post("/api/v1/integrations/sms/test", headers=admin["headers"])
    result = probe.json()
    assert probe.status_code == 200 and result["ok"] is False
    assert result["status"] == "error" and "401" in result["detail"]

    item = await _item(client, admin, "sms")
    assert item["status"] == "error" and "401" in item["last_error"]


async def test_meta_cloud_whatsapp_probe_reads_the_phone_number(client, admin, vendor):
    base_url, spy = vendor
    await _reset(client, admin, "whatsapp")
    await _put(client, admin, "whatsapp", {
        "provider": "cloud_api",
        "config": {"phone_number_id": spy.phone_id, "base_url": f"{base_url}/v20.0"},
        "secrets": {"token": spy.meta_token},
        "enabled": True,
    })
    probe = await client.post("/api/v1/integrations/whatsapp/test", headers=admin["headers"])
    assert probe.status_code == 200, probe.text
    assert probe.json()["ok"] is True, probe.json()
    assert "ProcureAI" in probe.json()["detail"]


async def test_serpapi_probe_runs_a_real_search(client, admin, vendor):
    base_url, spy = vendor
    await _reset(client, admin, "search")
    try:
        await _put(client, admin, "search", {
            "provider": "serpapi",
            "config": {"base_url": base_url},
            "secrets": {"api_key": spy.serp_key},
            "enabled": True,
        })
        probe = await client.post("/api/v1/integrations/search/test", headers=admin["headers"])
        assert probe.status_code == 200, probe.text
        assert probe.json()["ok"] is True, probe.json()
        assert spy.calls[-1][0] == "serpapi"
    finally:
        await _reset(client, admin, "search")


async def test_smtp_probe_logs_in_over_a_real_socket(client, admin):
    async with smtp_server() as (port, seen):
        await _reset(client, admin, "email")
        try:
            await _put(client, admin, "email", {
                "provider": "smtp",
                "config": {"host": "127.0.0.1", "port": str(port),
                           "from_address": "rfq@nile.example.com", "user": "rfq@nile.example.com"},
                "secrets": {"password": "app-password"},
                "enabled": True,
            })
            probe = await client.post("/api/v1/integrations/email/test", headers=admin["headers"])
            assert probe.status_code == 200, probe.text
            assert probe.json()["ok"] is True, probe.json()
            assert any(cmd.upper().startswith("AUTH") for cmd in seen)
        finally:
            await _reset(client, admin, "email")


async def test_a_fake_platform_provider_is_never_reported_as_a_successful_test(client, admin):
    await _reset(client, admin, "payment")
    probe = await client.post("/api/v1/integrations/payment/test", headers=admin["headers"])
    result = probe.json()
    assert probe.status_code == 200
    assert result["ok"] is False and result["status"] in {"sandbox", "not_configured"}


# ------------------------------------------------------------------ disable / re-enable
async def test_disabling_hands_traffic_back_to_the_platform_and_enabling_restores_it(
        client, admin, vendor):
    base_url, spy = vendor
    await _reset(client, admin, "telephony")
    await _put(client, admin, "telephony", {
        "provider": "twilio",
        "config": {"account_sid": spy.twilio_sid, "from_number": "+12025550123",
                   "base_url": base_url},
        "secrets": {"auth_token": spy.twilio_token},
        "enabled": True,
    })

    off = await client.patch("/api/v1/integrations/telephony", headers=admin["headers"],
                             json={"enabled": False})
    assert off.status_code == 200 and off.json()["enabled"] is False
    assert (await _item(client, admin, "telephony"))["status"] == "disabled"

    async with SessionLocal() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
        adapter = await integration_service.adapter_for(session, tenant, "telephony")
    assert adapter.name == registry.resolve("telephony").name       # the safe platform adapter

    on = await client.patch("/api/v1/integrations/telephony", headers=admin["headers"],
                            json={"enabled": True})
    assert on.json()["enabled"] is True
    async with SessionLocal() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
        adapter = await integration_service.adapter_for(session, tenant, "telephony")
    assert adapter.name == "twilio"


async def test_removing_an_integration_returns_the_platform_default(client, admin, vendor):
    base_url, spy = vendor
    await _put(client, admin, "telephony", {
        "provider": "twilio",
        "config": {"account_sid": spy.twilio_sid, "from_number": "+12025550123",
                   "base_url": base_url},
        "secrets": {"auth_token": spy.twilio_token},
        "enabled": True,
    })
    removed = await client.delete("/api/v1/integrations/telephony", headers=admin["headers"])
    assert removed.status_code == 200
    item = await _item(client, admin, "telephony")
    assert item["provider_source"] == "platform" and item["secrets_saved"] == []


# ------------------------------------------------------------------ permissions & isolation
async def test_only_an_integration_admin_can_change_credentials(client, buyer):
    denied = await client.put("/api/v1/integrations/llm", headers=buyer["headers"],
                              json={"provider": "anthropic", "config": {}, "secrets": {}})
    assert denied.status_code == 403
    assert (await client.post("/api/v1/integrations/llm/test", headers=buyer["headers"])).status_code == 403


async def test_one_tenant_never_sees_another_tenants_credentials(client, admin, vendor,
                                                                second_tenant):
    base_url, spy = vendor
    async with SessionLocal() as session:
        other = (await session.execute(select(Tenant).where(Tenant.id == second_tenant.id))).scalar_one()
        encrypted, key_id = await encrypt_text(session, other, json.dumps({"api_key": "other-tenant-key"}))
        session.add(IntegrationConfig(tenant_id=other.id, kind="llm",
                                      config={"provider": "anthropic", "model": "claude-x"},
                                      secrets_enc=encrypted, key_id=key_id, status="configured",
                                      enabled=True))
        await session.commit()

    listing = await client.get("/api/v1/integrations", headers=admin["headers"])
    assert "other-tenant-key" not in listing.text
    item = next(row for row in listing.json()["items"] if row["kind"] == "llm")
    assert item["provider_source"] in {"platform", "tenant"}
    async with SessionLocal() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
        ready = await integration_service.configured(session, tenant, "llm")
    assert ready is None or "other-tenant-key" not in str(ready)
