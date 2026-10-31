#!/usr/bin/env python
"""End-to-end journeys against the real stack — no mocks inside the application.

What this script actually starts, on this machine, in one run:

* a **SQLite database** built by the shipped Alembic migrations, then bootstrapped and seeded with the
  demo tenant (use ``--database-url`` to point it at PostgreSQL instead);
* a **real Redis server** (the binary bundled with ``redislite``) and **two API replicas** on separate
  ports, both connected to it — this is the multi-replica deployment the fan-out is for;
* a **vendor-side HTTP server** that speaks the Twilio Messages API, the Anthropic Messages API and the
  OpenAI chat-completions API, so the Twilio and LLM adapters make genuine network calls;
* a browser-equivalent client: HTTP + WebSocket, one socket on each replica.

The journeys: sign in / sign out (including token revocation), requisition → RFQ → dispatch, an
outbound WhatsApp quote request that Twilio really receives, an inbound Twilio webhook verified with a
computed ``X-Twilio-Signature``, the resulting live event arriving on **both** replicas, and a supplier
reply extracted by a real LLM over HTTP.

Usage::

    python scripts/e2e_journeys.py                 # SQLite + bundled Redis, ports chosen for you
    python scripts/e2e_journeys.py --database-url postgresql+asyncpg://… --redis-url redis://…
    python scripts/e2e_journeys.py --keep          # leave the database directory behind for inspection

Exit code 0 means every journey passed. Any failed assertion prints the step and exits 1.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
#: The harness seeds its own throwaway tenant, so it must not use the password the demo dataset ships
#: with: a staging deployment refuses to start while BOOTSTRAP_DEMO_PASSWORD is the public default
#: (docs/DECISIONS.md 128). This value exists only inside this script.
DEMO_PASSWORD = "E2E!Pilot-9c3f#harness"
TWILIO_SID = "ACe2e0000000000000000000000000001"
TWILIO_TOKEN = "e2e-auth-token"
TWILIO_WHATSAPP_FROM = "whatsapp:+14155238886"
PHONE = "+201234567890"
EMAIL = "quotes@nile-steel.example"

PASSED: list[str] = []


# --------------------------------------------------------------------------- helpers
def say(step: str, detail: str = "") -> None:
    PASSED.append(step)
    print(f"  PASS  {step}{(' — ' + detail) if detail else ''}", flush=True)


def fail(step: str, detail: str) -> None:
    print(f"\n  FAIL  {step}\n        {detail}\n", file=sys.stderr, flush=True)
    raise SystemExit(1)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def wait_http(url: str, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient(timeout=3) as client:
        while time.monotonic() < deadline:
            with contextlib.suppress(Exception):
                if (await client.get(url)).status_code < 500:
                    return
            await asyncio.sleep(0.4)
    raise RuntimeError(f"{url} never became ready")


def redis_binary() -> str | None:
    found = shutil.which("redis-server")
    if found:
        return found
    try:
        import redislite
    except Exception:  # noqa: BLE001 - optional dev dependency
        return None
    candidate = Path(redislite.__file__).parent / "bin" / "redis-server"
    return str(candidate) if candidate.exists() else None


# --------------------------------------------------------------------------- vendor stub
@dataclass
class Vendor:
    """Records what the application sent, in the vendor's own wire format."""

    twilio_messages: list[dict] = field(default_factory=list)
    twilio_account_calls: list[str] = field(default_factory=list)
    llm_calls: list[dict] = field(default_factory=list)


def build_vendor_app(vendor: Vendor) -> Any:
    # `from __future__ import annotations` turns these annotations into strings; FastAPI resolves
    # them against module globals, so the framework imports stay at module level in this script.
    app = FastAPI()

    @app.get("/2010-04-01/Accounts/{sid}.json")
    async def twilio_account(sid: str, request: Request):
        vendor.twilio_account_calls.append(sid)
        expected = "Basic " + __import__("base64").b64encode(
            f"{TWILIO_SID}:{TWILIO_TOKEN}".encode()).decode()
        if request.headers.get("authorization") != expected:
            return JSONResponse(status_code=401,
                                content={"code": 20003, "message": "Authenticate"})
        return JSONResponse(content={"sid": sid, "friendly_name": "ProcureAI E2E",
                                     "status": "active"})

    @app.post("/2010-04-01/Accounts/{sid}/Messages.json")
    async def twilio_messages(sid: str, request: Request):
        from urllib.parse import parse_qs

        body = await request.body()
        vendor.twilio_messages.append({key: values[-1] for key, values in
                                       parse_qs(body.decode()).items()})
        return JSONResponse(status_code=201, content={
            "sid": f"SM{len(vendor.twilio_messages):04d}", "status": "queued"})

    @app.post("/v1/messages")
    async def anthropic(request: Request):
        payload = await request.json()
        vendor.llm_calls.append(payload)
        offer = {
            "fields": {
                "unit_price": {"value": "20500"}, "currency": {"value": "EGP"},
                "quantity": {"value": "40"}, "uom": {"value": "t"},
                "lead_time_days": {"value": "21"}, "validity_days": {"value": "14"},
                "payment_terms": {"value": "Net 30"},
            },
            "lines": [],
        }
        content = ([{"type": "tool_use", "id": "toolu_e2e", "name": payload["tools"][0]["name"],
                     "input": offer}] if payload.get("tools")
                   else [{"type": "text", "text": "Offer is within target."}])
        return JSONResponse(content={"id": "msg_e2e", "type": "message", "role": "assistant",
                                     "model": payload["model"], "content": content,
                                     "stop_reason": "tool_use",
                                     "usage": {"input_tokens": 640, "output_tokens": 96}})

    @app.post("/v1/chat/completions")
    async def openai(request: Request):
        payload = await request.json()
        vendor.llm_calls.append(payload)
        return JSONResponse(content={
            "id": "chatcmpl_e2e", "object": "chat.completion", "model": payload["model"],
            "choices": [{"index": 0, "message": {"role": "assistant",
                                                 "content": json.dumps({"fields": {}})},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}})

    return app


async def serve(app: Any, port: int) -> Any:
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.05)
    return server, task


# --------------------------------------------------------------------------- journey client
class Api:
    def __init__(self, base: str) -> None:
        self.base = base
        self.client = httpx.AsyncClient(base_url=base, timeout=60)

    async def close(self) -> None:
        await self.client.aclose()

    async def token(self, email: str) -> dict[str, str]:
        response = await self.client.post("/api/v1/auth/login",
                                          json={"email": email, "password": DEMO_PASSWORD})
        if response.status_code != 200:
            fail("sign in", f"{email} → {response.status_code} {response.text[:300]}")
        return {"authorization": f"Bearer {response.json()['access_token']}"}

    async def get(self, path: str, headers: dict[str, str], **kwargs: Any) -> Any:
        response = await self.client.get(path, headers=headers, **kwargs)
        return response

    async def post(self, path: str, headers: dict[str, str], **kwargs: Any) -> Any:
        return await self.client.post(path, headers=headers, **kwargs)

    async def put(self, path: str, headers: dict[str, str], **kwargs: Any) -> Any:
        return await self.client.put(path, headers=headers, **kwargs)

    async def patch(self, path: str, headers: dict[str, str], **kwargs: Any) -> Any:
        return await self.client.patch(path, headers=headers, **kwargs)


async def ws_client(base: str, token: str, bucket: list[dict], label: str) -> Any:
    import websockets

    url = base.replace("http://", "ws://").replace("https://", "wss://")
    socket_ = await websockets.connect(f"{url}/ws?token={token}", open_timeout=15)

    async def pump() -> None:
        try:
            async for raw in socket_:
                bucket.append(json.loads(raw))
        except Exception:  # noqa: BLE001 - the socket closing is not a test failure by itself
            return

    pump_task = asyncio.create_task(pump())
    for _ in range(100):  # the first frame is the "connected" greeting
        if bucket:
            break
        await asyncio.sleep(0.05)
    if not bucket or bucket[0].get("type") != "connected":
        raise RuntimeError(f"{label}: no connected frame (got {bucket[:1]})")
    return socket_, pump_task


async def wait_for_event(bucket: list[dict], event: str, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for frame in bucket:
            if frame.get("type") == event:
                return frame
        await asyncio.sleep(0.1)
    raise TimeoutError(f"event {event!r} never arrived (saw {[f.get('type') for f in bucket]})")


def twilio_signature(url: str, params: dict[str, str], token: str = TWILIO_TOKEN) -> str:
    import base64
    import hashlib
    import hmac

    payload = url + "".join(f"{key}{value}" for key, value in sorted(params.items()))
    return base64.b64encode(hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()).decode()


# --------------------------------------------------------------------------- environment
@dataclass
class Stack:
    database_url: str
    redis_url: str | None
    vendor_url: str
    replicas: list[tuple[str, subprocess.Popen]] = field(default_factory=list)

    def stop(self) -> None:
        for _base, process in self.replicas:
            process.terminate()
        for _base, process in self.replicas:
            with contextlib.suppress(Exception):
                process.wait(timeout=10)


def prepare_database(database_url: str, env: dict[str, str]) -> None:
    print("• preparing the database (alembic upgrade head → bootstrap → seed demo)", flush=True)
    for command, label in (
        ([sys.executable, "-m", "app.cli", "migrate"], "migrations"),
        ([sys.executable, "-m", "app.cli", "bootstrap", "--demo"], "bootstrap"),
        ([sys.executable, "-m", "app.cli", "seed-demo"], "demo seed"),
    ):
        result = subprocess.run(command, check=False, cwd=BACKEND, env=env,
                                capture_output=True, text=True)
        if result.returncode != 0:
            fail(label, (result.stdout + result.stderr)[-2000:])
        print(f"  {label}: ok", flush=True)



def replica_env(base_env: dict[str, str], vendor_url: str, redis_url: str | None,
                public_url: str) -> dict[str, str]:
    env = dict(base_env)
    env.update({
        "APP_ENV": "staging",
        # A staging box is strict by default; this run deliberately keeps the *optional* providers
        # on their fakes (search, OCR, ERP) while the ones under test are real.
        "ALLOW_FAKE_PROVIDERS": "true",
        # The platform itself is left on the fakes: everything real in this run has to come from the
        # Integrations screen, which is exactly what the journeys below then verify.
        "WHATSAPP_PROVIDER": "fake",
        "SMS_PROVIDER": "fake",
        "TELEPHONY_PROVIDER": "fake",
        "TWILIO_WEBHOOK_URL": f"{public_url}/api/v1/inbound/whatsapp",
        "LLM_PROVIDER": "fake",
        "REDIS_URL": redis_url or "",
        "WS_FANOUT_ENABLED": "true" if redis_url else "false",
        "RATE_LIMIT_ENABLED": "false",
        "DEMO_SEED_ON_START": "false",
        "BOOTSTRAP_ON_START": "false",
        "PUBLIC_BASE_URL": public_url,
        "CONTACT_HOURS_START": "0",
        "CONTACT_HOURS_END": "24",
    })
    return env


def start_replica(env: dict[str, str], port: int) -> subprocess.Popen:
    log = open(Path(tempfile.gettempdir()) / f"e2e-replica-{port}.log", "w")  # noqa: SIM115
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(port), "--loop", "uvloop", "--http", "httptools"],
        cwd=BACKEND, env=env, stdout=log, stderr=subprocess.STDOUT,
    )


# --------------------------------------------------------------------------- journeys
async def journey_auth(api: Api) -> dict[str, str]:
    """Sign in, use the session, sign out — and prove the refresh session is really dead.

    The access token is a short-lived JWT (60 minutes) and stays valid on its own; what a sign-out
    must kill is the refresh session, otherwise a stolen cookie keeps minting access tokens.
    """
    headers = await api.token("buyer@nile.example.com")
    me = await api.get("/api/v1/auth/me", headers)
    if me.status_code != 200:
        fail("sign in", me.text[:300])
    say("sign in", f"{me.json()['email']} · {len(me.json()['permissions'])} permissions")

    login = await api.client.post("/api/v1/auth/login",
                                  json={"email": "buyer@nile.example.com", "password": DEMO_PASSWORD})
    if "procureai_refresh" not in login.cookies:
        fail("sign in", "no refresh cookie was issued")
    csrf = login.cookies.get("procureai_csrf", "")
    rotated = await api.client.post("/api/v1/auth/refresh", headers={"x-csrf-token": csrf})
    if rotated.status_code != 200 or not rotated.json().get("access_token"):
        fail("refresh", rotated.text[:300])
    say("token refresh", "the single-use refresh cookie mints a new access token")

    out = await api.post("/api/v1/auth/logout", headers, json={})
    if out.status_code >= 400:
        fail("sign out", out.text[:300])
    reused = await api.client.post("/api/v1/auth/refresh", headers={"x-csrf-token": csrf})
    if reused.status_code not in {401, 403}:
        fail("sign out", f"a revoked refresh session was accepted again ({reused.status_code})")
    me_after = await api.get("/api/v1/auth/me", headers)
    if me_after.status_code != 200:
        fail("sign out", f"the short-lived access token should still work, got {me_after.status_code}")
    say("sign out", "the refresh session is revoked; the access token expires on its own")
    return headers


async def journey_rfq(api: Api, requester_headers: dict[str, str],
                      buyer_headers: dict[str, str]) -> tuple[str, str]:
    intake = await api.post("/api/v1/intake/text", requester_headers, json={
        "text": "Need 40 t rebar 16mm, target 21000 EGP per ton.", "title": "E2E journey",
    })
    if intake.status_code >= 400:
        fail("requisition intake", intake.text[:400])
    pr_id = intake.json()["id"]
    detail = (await api.get(f"/api/v1/requests/{pr_id}", requester_headers)).json()
    for question in detail.get("clarifications", []):
        if question["status"] == "open":
            await api.post(f"/api/v1/requests/{pr_id}/clarifications/{question['id']}/answer",
                           requester_headers, json={"answer": "ASTM A615 grade 60"})
    submitted = await api.post(f"/api/v1/requests/{pr_id}/submit", requester_headers)
    if submitted.status_code >= 400:
        fail("requisition submit", submitted.text[:400])
    say("requisition", f"{detail['code']} parsed, clarified and submitted")

    supplier = await api.post("/api/v1/suppliers", buyer_headers, json={
        "name": "Nile Steel Trading", "name_ar": "حديد النيل", "country": "EG",
        "categories": ["steel"], "email": EMAIL, "phone": PHONE, "language": "ar",
    })
    if supplier.status_code not in {200, 201}:
        fail("create supplier", supplier.text[:300])
    supplier_id = supplier.json()["id"]

    rfq = await api.post("/api/v1/rfqs", buyer_headers, json={
        "pr_id": pr_id, "supplier_ids": [supplier_id], "deadline_days": 7,
    })
    if rfq.status_code >= 400:
        fail("create RFQ", rfq.text[:400])
    rfq_id = rfq.json()["id"]
    dispatched = await api.post(f"/api/v1/rfqs/{rfq_id}/dispatch", buyer_headers, json={})
    if dispatched.status_code >= 400:
        fail("dispatch RFQ", dispatched.text[:400])
    code = rfq.json().get("code") or (
        await api.get(f"/api/v1/rfqs/{rfq_id}", buyer_headers)).json()["code"]
    say("RFQ", f"{code} created and dispatched to 1 supplier")
    return rfq_id, supplier_id, code


async def journey_whatsapp_out(api: Api, buyer_headers: dict[str, str], rfq_id: str,
                               supplier_id: str, vendor: Vendor) -> None:
    opt_in = await api.post(f"/api/v1/suppliers/{supplier_id}/opt-in", buyer_headers,
                            json={"channel": "whatsapp", "status": "opted_in",
                                  "evidence": "E2E: supplier agreed on the phone", "source": "manual"})
    if opt_in.status_code >= 400:
        fail("WhatsApp opt-in", opt_in.text[:300])

    sent = await api.post(f"/api/v1/rfqs/{rfq_id}/whatsapp", buyer_headers,
                          json={"supplier_id": supplier_id, "kind": "quote_request"})
    if sent.status_code >= 400:
        fail("WhatsApp send", sent.text[:400])
    body = sent.json()
    if body.get("ok") is not True:
        fail("WhatsApp send", json.dumps(body)[:400])
    if not vendor.twilio_messages:
        fail("WhatsApp send", "the Twilio server received nothing")
    delivered = vendor.twilio_messages[-1]
    if delivered.get("To") != f"whatsapp:{PHONE}" or "From" not in delivered:
        fail("WhatsApp send", json.dumps(delivered)[:400])
    say("WhatsApp outbound", f"Twilio accepted a quote request to {delivered['To']}")


async def journey_whatsapp_in(api: Api, rfq_code: str, sockets: dict[str, list[dict]]) -> None:
    params = {
        "MessageSid": "SM_e2e_inbound_1",
        "From": f"whatsapp:{PHONE}",
        "To": TWILIO_WHATSAPP_FROM,
        "Body": (f"بخصوص {rfq_code}: سعر الطن 20500 جنيه، الكمية 40 طن، "
                 "التسليم خلال 21 يوم، صلاحية العرض 14 يوم"),
        "ProfileName": "Nile Steel Trading",
        "NumMedia": "0",
    }
    url = f"{api.base}/api/v1/inbound/whatsapp"
    # Twilio posts percent-encoded pairs and signs the *decoded* values, so the body is encoded the
    # way Twilio encodes it ("+" in a phone number must not become a space).
    from urllib.parse import urlencode

    form = urlencode(params)
    unsigned = await api.client.post("/api/v1/inbound/whatsapp", content=form,
                                     headers={"content-type": "application/x-www-form-urlencoded"})
    if unsigned.status_code != 401:
        fail("webhook signature", f"an unsigned webhook must be refused, got {unsigned.status_code}")

    signed = await api.client.post(
        "/api/v1/inbound/whatsapp", content=form,
        headers={"content-type": "application/x-www-form-urlencoded",
                 "X-Twilio-Signature": twilio_signature(url, params)})
    if signed.status_code >= 400:
        fail("inbound WhatsApp", signed.text[:400])
    say("inbound WhatsApp", "unsigned webhook refused (401); signed webhook accepted")

    frames = {}
    for replica, bucket in sockets.items():
        try:
            frames[replica] = await wait_for_event(bucket, "offer.extracted")
        except TimeoutError as exc:
            fail("live event fan-out", f"{replica}: {exc}")
    say("live event fan-out", "offer.extracted reached both replicas' sockets "
        f"({', '.join(sorted(frames))})")


async def journey_llm(api: Api, buyer_headers: dict[str, str], rfq_id: str,
                      supplier_id: str, vendor: Vendor) -> None:
    before = len(vendor.llm_calls)
    reply = await api.post(f"/api/v1/rfqs/{rfq_id}/simulate-reply", buyer_headers, json={
        "supplier_id": supplier_id, "unit_price": "20000", "lead_time_days": 18,
        "validity_days": 21,
    })
    if reply.status_code >= 400:
        fail("supplier reply", reply.text[:400])
    if len(vendor.llm_calls) == before:
        fail("LLM extraction", "the Anthropic endpoint received no request")

    queue = await api.get("/api/v1/extractions", buyer_headers, params={"page_size": 50})
    if queue.status_code >= 400:
        fail("extraction queue", queue.text[:300])
    rows = queue.json().get("items", [])
    models = {row.get("model") for row in rows if row.get("rfq_id") == rfq_id}
    if not any(model and model.startswith("anthropic:") for model in models):
        fail("LLM extraction", f"no extraction recorded the real provider (saw {models})")
    say("LLM extraction", f"a supplier reply was read by the real provider ({sorted(models)})")



async def journey_integrations(api: Api, admin_headers: dict[str, str], vendor_url: str,
                               vendor: Vendor) -> None:
    """The Integrations screen: save real credentials, probe them, and go Live — with no .env edit."""
    listing = await api.get("/api/v1/integrations", admin_headers)
    if listing.status_code != 200:
        fail("integrations", listing.text[:300])
    body = listing.json()
    items = {row["kind"]: row for row in body["items"]}
    if items["llm"]["status"] != "sandbox":
        fail("integrations", f"expected the platform fake to read as sandbox, got {items['llm']['status']}")
    if body["platform_adapters"]["llm"] != "fake":
        fail("integrations", "the platform adapter should still be the fake at this point")

    secret = "sk-ant-e2e-tenant-key"
    llm = await api.put("/api/v1/integrations/llm", admin_headers, json={
        "provider": "anthropic",
        "config": {"model": "claude-sonnet-4-5-20250929", "base_url": vendor_url},
        "secrets": {"api_key": secret},
        "enabled": True,
    })
    if llm.status_code != 200 or llm.json()["status"] != "live":
        fail("integrations", f"saving the model returned {llm.status_code} {llm.text[:300]}")
    if secret in llm.text:
        fail("integrations", "the API echoed the secret back to the browser")
    if not llm.json()["masked"].get("api_key", "").endswith(secret[-4:]):
        fail("integrations", "the mask does not identify the stored key")

    probe = await api.post("/api/v1/integrations/llm/test", admin_headers)
    if probe.status_code != 200 or not probe.json()["ok"]:
        fail("integrations", f"model probe failed: {probe.text[:300]}")

    whatsapp = await api.put("/api/v1/integrations/whatsapp", admin_headers, json={
        "provider": "twilio",
        "config": {"account_sid": TWILIO_SID, "from_number": TWILIO_WHATSAPP_FROM,
                   "base_url": vendor_url},
        "secrets": {"auth_token": TWILIO_TOKEN},
        "enabled": True,
    })
    if whatsapp.status_code != 200 or whatsapp.json()["status"] != "live":
        fail("integrations", f"saving WhatsApp returned {whatsapp.status_code} {whatsapp.text[:300]}")
    wa_probe = await api.post("/api/v1/integrations/whatsapp/test", admin_headers)
    if wa_probe.status_code != 200 or not wa_probe.json()["ok"]:
        fail("integrations", f"WhatsApp probe failed: {wa_probe.text[:300]}")
    if not vendor.twilio_account_calls:
        fail("integrations", "the probe never reached the Twilio account endpoint")

    after = await api.get("/api/v1/integrations", admin_headers)
    if secret in after.text:
        fail("integrations", "a reload of the screen exposes the stored secret")
    statuses = {row["kind"]: row["status"] for row in after.json()["items"]}
    say("integrations", f"fake → live from the screen: llm={statuses['llm']}, "
                         f"whatsapp={statuses['whatsapp']}, platform still "
                         f"{after.json()['platform_adapters']['llm']}")


async def _anthropic_extractions(api: Api, headers: dict[str, str], rfq_id: str) -> int:
    queue = await api.get("/api/v1/extractions", headers, params={"page_size": 100})
    rows = queue.json().get("items", [])
    return sum(1 for row in rows
               if row.get("rfq_id") == rfq_id and str(row.get("model") or "").startswith("anthropic:"))


async def journey_integration_safety(api: Api, admin_headers: dict[str, str],
                                     buyer_headers: dict[str, str], rfq_id: str,
                                     supplier_id: str, vendor: Vendor) -> None:
    """Disabling the model must fall back safely; re-enabling must restore it."""
    before = await _anthropic_extractions(api, buyer_headers, rfq_id)
    off = await api.patch("/api/v1/integrations/llm", admin_headers, json={"enabled": False})
    if off.status_code != 200 or off.json()["enabled"] is not False:
        fail("integration safety", f"disabling returned {off.status_code} {off.text[:200]}")
    listing = await api.get("/api/v1/integrations", admin_headers)
    llm = next(row for row in listing.json()["items"] if row["kind"] == "llm")
    if llm["status"] != "disabled":
        fail("integration safety", f"disabled integration still reports {llm['status']}")

    calls = len(vendor.llm_calls)
    reply = await api.post(f"/api/v1/rfqs/{rfq_id}/simulate-reply", buyer_headers, json={
        "supplier_id": supplier_id, "unit_price": "19900", "lead_time_days": 20,
        "validity_days": 14,
    })
    if reply.status_code >= 400:
        fail("integration safety", reply.text[:300])
    if len(vendor.llm_calls) != calls:
        fail("integration safety", "the disabled provider was still called")
    if await _anthropic_extractions(api, buyer_headers, rfq_id) != before:
        fail("integration safety", "the extraction still claims a real model while the provider is off")

    on = await api.patch("/api/v1/integrations/llm", admin_headers, json={"enabled": True})
    if on.json()["enabled"] is not True:
        fail("integration safety", "re-enabling failed")
    await api.post(f"/api/v1/rfqs/{rfq_id}/simulate-reply", buyer_headers, json={
        "supplier_id": supplier_id, "unit_price": "19800", "lead_time_days": 19,
        "validity_days": 14,
    })
    if await _anthropic_extractions(api, buyer_headers, rfq_id) <= before:
        fail("integration safety", "re-enabling did not restore the real model")
    say("integration safety", "disabling falls back without calling the vendor; re-enabling restores it")


async def journey_features(api: Api, admin_headers: dict[str, str], buyer_headers: dict[str, str],
                           rfq_id: str, supplier_id: str) -> None:
    caps = await api.get("/api/v1/channels/capabilities", admin_headers)
    if caps.status_code != 200:
        fail("capabilities", caps.text[:300])
    body = caps.json()
    provider_name = body["whatsapp"]["provider"]
    if not provider_name.startswith("twilio"):
        fail("capabilities", f"provider is {provider_name!r}, expected the real Twilio adapter")
    name = {row["key"]: row for row in body["features"]}
    say("capabilities", f"{len(body['channels'])} channels, provider={provider_name}, "
        f"{len(name)} feature flags")

    off = await api.client.patch("/api/v1/settings/features", headers=admin_headers,
                                 json={"features": {"whatsapp": False}})
    if off.status_code != 200:
        fail("feature flag", off.text[:300])
    try:
        blocked = await api.post(f"/api/v1/rfqs/{rfq_id}/whatsapp", buyer_headers,
                                 json={"supplier_id": supplier_id, "kind": "text", "body": "hi"})
        if blocked.status_code >= 500:
            fail("feature flag", blocked.text[:300])
        codes = (blocked.json() or {}).get("codes") or []
        if "BLOCK_FEATURE_OFF" not in codes:
            fail("feature flag", f"expected BLOCK_FEATURE_OFF, got {blocked.status_code} {codes}")
    finally:
        await api.client.patch("/api/v1/settings/features", headers=admin_headers,
                               json={"features": {"whatsapp": True}})
    say("feature flag", "turning WhatsApp off is enforced server-side, and back on")


# --------------------------------------------------------------------------- main
async def run(args: argparse.Namespace) -> None:
    workdir = Path(tempfile.mkdtemp(prefix="procureai-e2e-"))
    database_url = args.database_url or f"sqlite+aiosqlite:///{workdir / 'e2e.db'}"
    base_env = dict(os.environ)
    base_env.pop("PYTHONPATH", None)
    base_env.update({
        "DATABASE_URL": database_url,
        "ALLOW_SQLITE_FALLBACK": "true" if database_url.startswith("sqlite") else "false",
        "SECRET_KEY": base_env.get("SECRET_KEY", "e2e-secret-key-0123456789abcdef0123456789abcdef"),
        "ENCRYPTION_MASTER_KEY": base_env.get("ENCRYPTION_MASTER_KEY", ""),
        "DEMO_SEED_ON_START": "false",
        "BOOTSTRAP_ON_START": "false",
        "RATE_LIMIT_ENABLED": "false",
        "APP_ENV": "dev",
        # Seeded accounts get this password, and the staging replicas below boot with the same setting,
        # so neither the seed nor the boot relies on the public demo default.
        "BOOTSTRAP_DEMO_PASSWORD": DEMO_PASSWORD,
    })
    if base_env["ALLOW_SQLITE_FALLBACK"] == "true":
        base_env["TENANT_RLS_ENABLED"] = "false"

    prepare_database(database_url, base_env)

    vendor = Vendor()
    vendor_port = free_port()
    vendor_server, vendor_task = await serve(build_vendor_app(vendor), vendor_port)
    vendor_url = f"http://127.0.0.1:{vendor_port}"

    redis_process = None
    redis_url = args.redis_url
    if not redis_url:
        binary = redis_binary()
        if binary:
            port = free_port()
            # A blocking launch is fine here: it happens before the loop starts serving the replicas,
            # and the process must outlive this coroutine.
            redis_process = subprocess.Popen(  # noqa: ASYNC220
                [binary, "--port", str(port), "--save", "", "--appendonly", "no"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            redis_url = f"redis://127.0.0.1:{port}/0"
            await asyncio.sleep(0.6)
        else:
            print("! no redis-server available: fan-out cannot be proven in this run", flush=True)

    port_a, port_b = free_port(), free_port()
    stack = Stack(database_url=database_url, redis_url=redis_url, vendor_url=vendor_url)
    print(f"• starting two replicas (127.0.0.1:{port_a} and 127.0.0.1:{port_b})", flush=True)
    for port in (port_a, port_b):
        env = replica_env(base_env, vendor_url, redis_url, f"http://127.0.0.1:{port_a}")
        stack.replicas.append((f"replica-{port}", start_replica(env, port)))
    try:
        for port in (port_a, port_b):
            await wait_http(f"http://127.0.0.1:{port}/health")
        print("  both replicas are healthy", flush=True)
        if redis_url:
            say("replicas", f"two API processes share Redis at {redis_url.split('//')[-1]}")

        api_a = Api(f"http://127.0.0.1:{port_a}")
        api_b = Api(f"http://127.0.0.1:{port_b}")
        try:
            buyer = await journey_auth(api_a)
            tokens = await api_a.token("buyer@nile.example.com")
            sockets: dict[str, list[dict]] = {}
            handles = []
            try:
                for label, base in (("replica-a", f"http://127.0.0.1:{port_a}"),
                                    ("replica-b", f"http://127.0.0.1:{port_b}")):
                    bucket: list[dict] = []
                    socket_, pump = await ws_client(base, tokens["authorization"].split(" ", 1)[1], bucket, label)
                    sockets[label] = bucket
                    handles.append((socket_, pump))
                say("websocket", "a live socket is open on each replica")

                admin = await api_a.token("admin@pilot.procureai")
                await journey_integrations(api_a, admin, vendor_url, vendor)
                requester = await api_a.token("requester@nile.example.com")
                rfq_id, supplier_id, rfq_code = await journey_rfq(api_a, requester, buyer)
                await journey_whatsapp_out(api_a, buyer, rfq_id, supplier_id, vendor)
                await journey_whatsapp_in(api_a, rfq_code, sockets)
                await journey_llm(api_a, buyer, rfq_id, supplier_id, vendor)
                await journey_features(api_a, admin, buyer, rfq_id, supplier_id)
                await journey_integration_safety(api_a, admin, buyer, rfq_id, supplier_id, vendor)
                _ = api_b  # replica B served the socket; its health is asserted above
            finally:
                for socket_, pump in handles:
                    pump.cancel()
                    with contextlib.suppress(Exception):
                        await socket_.close()
        finally:
            await api_a.close()
            await api_b.close()
    finally:
        stack.stop()
        vendor_server.should_exit = True
        with contextlib.suppress(Exception):
            await asyncio.wait_for(vendor_task, timeout=10)
        if redis_process:
            redis_process.terminate()
        if args.keep:
            print(f"• database kept at {workdir}", flush=True)
        else:
            shutil.rmtree(workdir, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default="", help="use this database instead of a temp SQLite file")
    parser.add_argument("--redis-url", default="", help="use this Redis instead of the bundled server")
    parser.add_argument("--keep", action="store_true", help="keep the temporary database directory")
    args = parser.parse_args()
    print("ProcureAI end-to-end journeys", flush=True)
    started = time.monotonic()
    try:
        asyncio.run(run(args))
    except SystemExit:
        raise
    except Exception as exc:
        print(f"\n  FAIL  unexpected error: {type(exc).__name__}: {exc}\n", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"\n{len(PASSED)} journeys passed in {time.monotonic() - started:.1f}s", flush=True)


if __name__ == "__main__":
    main()
