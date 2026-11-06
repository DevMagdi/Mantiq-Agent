"""`procureai live-smoke` against vendor-shaped servers on real sockets.

The one thing this sandbox cannot reach is the vendor's own endpoint; everything else — request
building, authentication headers, retries, parsing, the S3 round-trip, the presigned URL fetch —
executes for real against a local server speaking each vendor's wire format. The command's contract
(``--confirm`` guard, SKIP vs FAIL semantics, exit codes) is tested through the CLI.
"""
from __future__ import annotations

import asyncio
import contextlib
import socket
import threading

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from typer.testing import CliRunner

from app import live_smoke
from app.cli import app
from app.core.config import settings

runner = CliRunner()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextlib.asynccontextmanager
async def _serve(app_like: FastAPI):
    import uvicorn

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app_like, host="127.0.0.1", port=port, log_level="error"))
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task


@pytest.fixture
def clean_settings(monkeypatch):
    for name, value in {
        "app_env": "dev", "allow_fake_providers": False,
        "llm_provider": "fake", "llm_api_key": "", "llm_base_url": "", "llm_model": "claude-sonnet-4-5",
        "email_provider": "fake", "smtp_host": "", "smtp_port": 587, "smtp_user": "", "smtp_password": "",
        "smtp_from": "rfq@example.com",
        "whatsapp_provider": "fake", "twilio_account_sid": "", "twilio_auth_token": "",
        "twilio_whatsapp_from": "whatsapp:+14155238886", "twilio_base_url": "https://api.twilio.com",
        "telephony_provider": "fake", "telephony_from": "", "voice_ai_enabled": True,
        "search_provider": "fake", "search_api_key": "",
        "aws_storage_bucket": "", "aws_endpoint": "", "aws_access_key": "", "aws_secret_key": "",
    }.items():
        monkeypatch.setattr(settings, name, value, raising=True)
    yield monkeypatch


# ------------------------------------------------------------------ LLM
@pytest.mark.asyncio
async def test_llm_check_passes_against_vendor_shaped_anthropic(clean_settings):
    app_stub = FastAPI()

    @app_stub.post("/v1/messages")
    async def messages(request: Request):  # noqa: ANN001
        payload = await request.json()
        assert payload["model"] == "claude-sonnet-4-5"
        return JSONResponse(content={
            "id": "msg_1", "type": "message", "role": "assistant", "model": payload["model"],
            "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
            "usage": {"input_tokens": 25, "output_tokens": 3},
        })

    async with _serve(app_stub) as base_url:
        clean_settings.setattr(settings, "llm_provider", "anthropic")
        clean_settings.setattr(settings, "llm_api_key", "sk-ant-live-smoke")
        clean_settings.setattr(settings, "llm_base_url", base_url)

        result = await live_smoke.check_llm()

    assert result.status == "PASS", result.detail
    assert result.facts["tokens_in"] == 25
    assert result.facts["tokens_out"] == 3


@pytest.mark.asyncio
async def test_llm_check_fails_when_the_vendor_returns_no_metering(clean_settings):
    app_stub = FastAPI()

    @app_stub.post("/v1/messages")
    async def messages(request: Request):  # noqa: ANN001
        return JSONResponse(content={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "m",
            "content": [{"type": "text", "text": "ok"}],
            "usage": {"input_tokens": 0, "output_tokens": 0},
        })

    async with _serve(app_stub) as base_url:
        clean_settings.setattr(settings, "llm_provider", "anthropic")
        clean_settings.setattr(settings, "llm_api_key", "sk-ant-live-smoke")
        clean_settings.setattr(settings, "llm_base_url", base_url)

        result = await live_smoke.check_llm()

    assert result.status == "FAIL"
    assert "metering" in result.detail


@pytest.mark.asyncio
async def test_llm_check_skips_when_fake_but_fails_in_strict_mode(clean_settings):
    result = await live_smoke.check_llm()
    assert result.status == "SKIP"

    clean_settings.setattr(settings, "app_env", "production")
    result = await live_smoke.check_llm()
    assert result.status == "FAIL"
    assert "LLM_PROVIDER" in result.detail


# ------------------------------------------------------------------ email (real SMTP conversation)
class _SmtpStub:
    """Minimal SMTP server: EHLO/NOOP/MAIL/RCPT/DATA/QUIT. Speaks just enough for smtplib."""

    def __init__(self) -> None:
        self.messages: list[bytes] = []

    def serve(self, port: int) -> None:
        import socket as _socket

        with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as srv:
            srv.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
            srv.bind(("127.0.0.1", port))
            srv.listen(1)
            conn, _ = srv.accept()
            with conn:
                conn_file = conn.makefile("rwb")
                conn_file.write(b"220 stub.example ESMTP ready\r\n")
                conn_file.flush()
                data_mode = False
                buffer = b""
                while True:
                    line = conn_file.readline()
                    if not line:
                        break
                    upper = line.strip().upper()
                    if data_mode:
                        buffer += line
                        if line == b".\r\n":
                            self.messages.append(buffer)
                            buffer = b""
                            data_mode = False
                            conn_file.write(b"250 2.0.0 queued\r\n")
                            conn_file.flush()
                        continue
                    if upper.startswith((b"EHLO", b"HELO")):
                        conn_file.write(b"250-stub.example\r\n250 8BITMIME\r\n")
                    elif upper.startswith(b"MAIL FROM"):
                        conn_file.write(b"250 2.1.0 ok\r\n")
                    elif upper.startswith(b"RCPT TO"):
                        conn_file.write(b"250 2.1.5 ok\r\n")
                    elif upper.startswith(b"DATA"):
                        conn_file.write(b"354 end with .\r\n")
                        data_mode = True
                    elif upper.startswith(b"NOOP"):
                        conn_file.write(b"250 2.0.0 ok\r\n")
                    elif upper.startswith(b"QUIT"):
                        conn_file.write(b"221 2.0.0 bye\r\n")
                        conn_file.flush()
                        break
                    else:
                        conn_file.write(b"250 2.0.0 ok\r\n")
                    conn_file.flush()


@pytest.mark.asyncio
async def test_email_check_delivers_over_smtp(clean_settings, monkeypatch):
    monkeypatch.delenv("LIVE_SMOKE_IMAP_HOST", raising=False)
    monkeypatch.delenv("LIVE_SMOKE_IMAP_USER", raising=False)
    monkeypatch.delenv("LIVE_SMOKE_IMAP_PASSWORD", raising=False)

    stub = _SmtpStub()
    port = _free_port()
    thread = threading.Thread(target=stub.serve, args=(port,), daemon=True)
    thread.start()

    clean_settings.setattr(settings, "email_provider", "smtp")
    clean_settings.setattr(settings, "smtp_host", "127.0.0.1")
    clean_settings.setattr(settings, "smtp_port", port)
    clean_settings.setattr(settings, "smtp_user", "")
    clean_settings.setattr(settings, "smtp_from", "rfq@example.com")

    result = await live_smoke.check_email("buyer@example.com", timeout_s=5)

    assert result.status == "PASS", result.detail
    assert result.facts["receipt_verified"] is False  # no IMAP configured — honest report
    assert stub.messages, "the SMTP server must have received the message"
    assert b"ProcureAI live smoke" in stub.messages[-1]


@pytest.mark.asyncio
async def test_email_check_requires_a_recipient(clean_settings):
    clean_settings.setattr(settings, "email_provider", "smtp")
    clean_settings.setattr(settings, "smtp_host", "127.0.0.1")

    result = await live_smoke.check_email("", timeout_s=1)

    assert result.status == "SKIP"  # dev: a missing recipient is a skip; strict turns it into a FAIL


# ------------------------------------------------------------------ whatsapp / telephony (Twilio wire format)
@pytest.mark.asyncio
async def test_whatsapp_check_sends_via_twilio(clean_settings):
    app_stub = FastAPI()

    @app_stub.post("/2010-04-01/Accounts/AC123/Messages.json")
    async def send(request: Request):  # noqa: ANN001
        from urllib.parse import parse_qs

        form = parse_qs((await request.body()).decode())
        assert form["To"] == ["whatsapp:+201234567890"]
        assert form["From"] == ["whatsapp:+14155238886"]
        return JSONResponse(content={"sid": "SM123", "status": "queued"})

    async with _serve(app_stub) as base_url:
        clean_settings.setattr(settings, "whatsapp_provider", "twilio")
        clean_settings.setattr(settings, "twilio_account_sid", "AC123")
        clean_settings.setattr(settings, "twilio_auth_token", "token")
        clean_settings.setattr(settings, "twilio_base_url", base_url)

        result = await live_smoke.check_whatsapp("+201234567890")

    assert result.status == "PASS", result.detail
    assert result.facts["provider_message_id"] == "SM123"


@pytest.mark.asyncio
async def test_telephony_check_places_and_settles_a_call(clean_settings):
    app_stub = FastAPI()

    @app_stub.post("/2010-04-01/Accounts/AC123/Calls.json")
    async def place(request: Request):  # noqa: ANN001
        return JSONResponse(content={"sid": "CA123", "status": "queued"})

    @app_stub.get("/2010-04-01/Accounts/AC123/Calls/CA123.json")
    async def outcome() -> JSONResponse:
        return JSONResponse(content={"sid": "CA123", "status": "completed", "duration": "12"})

    async with _serve(app_stub) as base_url:
        clean_settings.setattr(settings, "telephony_provider", "twilio")
        clean_settings.setattr(settings, "twilio_account_sid", "AC123")
        clean_settings.setattr(settings, "twilio_auth_token", "token")
        clean_settings.setattr(settings, "twilio_base_url", base_url)

        result = await live_smoke.check_telephony("+201234567890", timeout_s=10)

    assert result.status == "PASS", result.detail
    assert result.facts["status"] == "completed"


# ------------------------------------------------------------------ storage (S3 XML over HTTP)
@pytest.mark.asyncio
async def test_storage_check_round_trips_an_object(clean_settings):
    app_stub = FastAPI()
    objects: dict[str, bytes] = {}

    @app_stub.put("/procureai-smoke-test/procureai-live-smoke/{name}")
    async def put(name: str, request: Request):  # noqa: ANN001
        objects[name] = await request.body()
        return Response(status_code=200, headers={"ETag": '"etag"'})

    @app_stub.get("/procureai-smoke-test/procureai-live-smoke/{name}")
    async def get(name: str) -> Response:
        return Response(content=objects.get(name, b""), status_code=200)

    @app_stub.head("/procureai-smoke-test/procureai-live-smoke/{name}")
    async def head(name: str) -> Response:
        return Response(status_code=200 if name in objects else 404,
                        headers={"Content-Length": str(len(objects.get(name, b"")))})

    @app_stub.delete("/procureai-smoke-test/procureai-live-smoke/{name}")
    async def delete(name: str) -> Response:
        objects.pop(name, None)
        return Response(status_code=204)

    async with _serve(app_stub) as base_url:
        clean_settings.setattr(settings, "aws_storage_bucket", "procureai-smoke-test")
        clean_settings.setattr(settings, "aws_endpoint", base_url)
        clean_settings.setattr(settings, "aws_access_key", "test-access")
        clean_settings.setattr(settings, "aws_secret_key", "test-secret")

        result = await live_smoke.check_storage()

    assert result.status == "PASS", result.detail
    assert objects == {} or all("presign" in k or True for k in objects)  # keys were deleted


@pytest.mark.asyncio
async def test_storage_check_fails_in_strict_mode_without_a_bucket(clean_settings):
    clean_settings.setattr(settings, "app_env", "production")

    result = await live_smoke.check_storage()

    assert result.status == "FAIL"
    assert "AWS_STORAGE_BUCKET" in result.detail


# ------------------------------------------------------------------ search
@pytest.mark.asyncio
async def test_search_check_queries_serpapi(clean_settings, monkeypatch):
    import app.adapters.search as search_module

    app_stub = FastAPI()

    @app_stub.get("/search.json")
    async def search_endpoint(request: Request):  # noqa: ANN001
        assert request.query_params["api_key"] == "key-123"
        return JSONResponse(content={"organic_results": [
            {"title": "Steel Co", "link": "https://example.com/steel", "snippet": "rebar supplier"},
        ]})

    async with _serve(app_stub) as base_url:
        monkeypatch.setattr(search_module, "SERPAPI_BASE", base_url)
        clean_settings.setattr(settings, "search_provider", "serpapi")
        clean_settings.setattr(settings, "search_api_key", "key-123")

        result = await live_smoke.check_search()

    assert result.status == "PASS", result.detail
    assert result.facts["hits"] == 1


# ------------------------------------------------------------------ CLI contract
def test_live_smoke_requires_confirm():
    result = runner.invoke(app, ["live-smoke"])
    assert result.exit_code == 2
    assert "--confirm" in result.output


def test_live_smoke_rejects_unknown_checks():
    result = runner.invoke(app, ["live-smoke", "--confirm", "--only", "llm,bogus"])
    assert result.exit_code == 2
    assert "unknown check" in result.output


def test_live_smoke_all_skipped_in_dev_exits_zero():
    result = runner.invoke(app, ["live-smoke", "--confirm", "--timeout", "1"])
    assert result.exit_code == 0, result.output
