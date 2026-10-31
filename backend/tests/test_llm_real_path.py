"""The real LLM providers, exercised over HTTP and through the supplier-reply pipeline.

Nothing here is stubbed inside the application: `AnthropicProvider` and `OpenAICompatibleProvider`
open a socket to a local server that speaks the vendor wire format, and the end-to-end tests push a
supplier reply through `extraction.ingest_inbound` with `LLM_PROVIDER` set to a real provider. The
stub server plays the vendor's role (it is the one thing we cannot reach from CI); every line of our
own request building, authentication, parsing, retrying and persistence executes for real.
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

from app.adapters.base import InboundMessage
from app.agents.llm import clear_cache
from app.agents.llm.base import LLMMessage, LLMRequest
from app.agents.llm.providers import AnthropicProvider, OpenAICompatibleProvider
from app.core.config import settings
from app.db.models.entities import RFQ, ExtractionResult, Supplier, Tenant
from app.db.session import SessionLocal
from app.services import extraction
from app.services.suppliers import contact_pii, primary_contact

MODEL = "claude-sonnet-4-5-20250929"

OFFER_JSON = {
    "fields": {
        "unit_price": {"value": "20500"},
        "currency": {"value": "EGP"},
        "quantity": {"value": "40"},
        "uom": {"value": "t"},
        "lead_time_days": {"value": "21"},
        "validity_days": {"value": "14"},
        "payment_terms": {"value": "Net 30"},
    },
    "lines": [],
}


# ------------------------------------------------------------------ the vendor-side server
class Vendor:
    """Records what our client sent and answers in the vendor's format."""

    def __init__(self) -> None:
        self.anthropic_calls: list[dict] = []
        self.openai_calls: list[dict] = []
        self.headers: list[dict] = []
        self.anthropic_status: list[int] = []  # scripted failures, consumed in order
        self.openai_status: list[int] = []
        self.structured = OFFER_JSON
        self.free_text = "The offer is within target. I recommend negotiating lead time."
        self.calls = 0

    def _next(self, scripted: list[int]) -> int:
        return scripted.pop(0) if scripted else 200


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextlib.asynccontextmanager
async def vendor_server(vendor: Vendor):
    import uvicorn

    app = FastAPI()

    @app.post("/v1/messages")
    async def messages(request: Request):  # noqa: ANN001
        payload = await request.json()
        vendor.anthropic_calls.append(payload)
        vendor.headers.append(dict(request.headers))
        vendor.calls += 1
        status = vendor._next(vendor.anthropic_status)
        if status >= 400:
            return JSONResponse(status_code=status,
                                content={"type": "error", "error": {"message": "overloaded_error"}})
        content: list[dict] = [{"type": "text", "text": vendor.free_text}]
        if payload.get("tools"):
            content = [{"type": "tool_use", "id": "toolu_1", "name": payload["tools"][0]["name"],
                        "input": vendor.structured}]
        return JSONResponse(content={
            "id": "msg_1", "type": "message", "role": "assistant", "model": payload["model"],
            "content": content, "stop_reason": "tool_use" if payload.get("tools") else "end_turn",
            "usage": {"input_tokens": 812, "output_tokens": 143},
        })

    @app.post("/v1/chat/completions")
    async def chat(request: Request):  # noqa: ANN001
        payload = await request.json()
        vendor.openai_calls.append(payload)
        vendor.headers.append(dict(request.headers))
        vendor.calls += 1
        status = vendor._next(vendor.openai_status)
        if status >= 400:
            return JSONResponse(status_code=status,
                                content={"error": {"message": "rate limit exceeded"}})
        text = json.dumps(vendor.structured) if payload.get("response_format") else vendor.free_text
        return JSONResponse(content={
            "id": "chatcmpl-1", "object": "chat.completion", "model": payload["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 901, "completion_tokens": 87},
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


@pytest.fixture
async def vendor():
    instance = Vendor()
    async with vendor_server(instance) as base_url:
        yield base_url, instance


# ------------------------------------------------------------------ wire format: Anthropic
async def test_anthropic_request_carries_the_key_the_version_and_a_system_prompt(vendor):
    base_url, spy = vendor
    provider = AnthropicProvider("sk-ant-api03-test", MODEL, base_url=base_url)

    response = await provider.complete(LLMRequest(
        task="summary",
        messages=[LLMMessage("system", "You are a procurement analyst."),
                  LLMMessage("user", "Summarise the offer.")],
        max_tokens=512, temperature=0.2,
    ))

    sent = spy.anthropic_calls[0]
    assert spy.headers[0]["x-api-key"] == "sk-ant-api03-test"
    assert spy.headers[0]["anthropic-version"] == "2023-06-01"
    assert sent["system"] == "You are a procurement analyst."
    assert sent["messages"] == [{"role": "user", "content": "Summarise the offer."}]
    assert sent["max_tokens"] == 512 and sent["temperature"] == 0.2
    assert response.provider == "anthropic"
    assert response.text == spy.free_text
    assert (response.tokens_in, response.tokens_out) == (812, 143)
    assert response.latency_ms >= 0


async def test_anthropic_structured_output_uses_a_forced_tool(vendor):
    base_url, spy = vendor
    provider = AnthropicProvider("sk-ant-test", MODEL, base_url=base_url)

    response = await provider.complete(LLMRequest(
        task="extract_offer", messages=[LLMMessage("user", "quote")], schema={"type": "object"},
    ))

    sent = spy.anthropic_calls[0]
    assert sent["tool_choice"] == {"type": "tool", "name": "emit_structured_output"}
    assert sent["tools"][0]["input_schema"] == {"type": "object"}
    assert response.data == OFFER_JSON, "the tool payload is the structured answer"
    assert response.tool_calls[0]["name"] == "emit_structured_output"


async def test_an_overloaded_endpoint_is_retried_and_then_succeeds(vendor):
    base_url, spy = vendor
    spy.anthropic_status = [529, 200]
    provider = AnthropicProvider("sk-ant-test", MODEL, base_url=base_url, max_retries=2)

    response = await provider.complete(LLMRequest(task="summary",
                                                  messages=[LLMMessage("user", "ping")]))

    assert response.text == spy.free_text
    assert len(spy.anthropic_calls) == 2


async def test_a_rejected_key_is_not_retried(vendor):
    base_url, spy = vendor
    spy.anthropic_status = [401]
    provider = AnthropicProvider("bad-key", MODEL, base_url=base_url, max_retries=3)

    with pytest.raises(RuntimeError, match="Anthropic error 401"):
        await provider.complete(LLMRequest(task="summary", messages=[LLMMessage("user", "ping")]))

    assert len(spy.anthropic_calls) == 1


async def test_an_unreachable_endpoint_fails_with_a_clear_message():
    provider = AnthropicProvider("sk-ant-test", MODEL, base_url=f"http://127.0.0.1:{_free_port()}",
                                 max_retries=1)

    with pytest.raises(RuntimeError, match="unreachable"):
        await provider.complete(LLMRequest(task="summary", messages=[LLMMessage("user", "ping")]))


async def test_an_anthropic_provider_without_a_key_refuses_before_the_network():
    provider = AnthropicProvider("", MODEL)

    with pytest.raises(RuntimeError, match="LLM_API_KEY is not configured"):
        await provider.complete(LLMRequest(task="summary", messages=[LLMMessage("user", "ping")]))


# ------------------------------------------------------------------ wire format: OpenAI-compatible
async def test_openai_compatible_request_uses_bearer_auth_and_json_mode(vendor):
    base_url, spy = vendor
    # LLM_BASE_URL for an OpenAI-compatible endpoint includes the version segment, exactly like the
    # vendor's own documented base URL (https://api.openai.com/v1).
    provider = OpenAICompatibleProvider("sk-openai-test", "gpt-4.1", base_url=f"{base_url}/v1")

    response = await provider.complete(LLMRequest(
        task="extract_offer", messages=[LLMMessage("user", "quote")], schema={"type": "object"},
    ))

    sent = spy.openai_calls[0]
    assert spy.headers[0]["authorization"] == "Bearer sk-openai-test"
    assert sent["response_format"] == {"type": "json_object"}
    assert sent["messages"][-1]["role"] == "system"
    assert "Return ONLY JSON" in sent["messages"][-1]["content"]
    assert response.data == OFFER_JSON
    assert (response.tokens_in, response.tokens_out) == (901, 87)


async def test_a_self_hosted_endpoint_needs_no_key(vendor):
    base_url, spy = vendor
    provider = OpenAICompatibleProvider("", "qwen2.5-72b-instruct", base_url=f"{base_url}/v1")

    response = await provider.complete(LLMRequest(task="summary",
                                                  messages=[LLMMessage("user", "ping")]))

    assert "authorization" not in spy.headers[0]
    assert response.provider == "openai_compatible"
    assert response.text == spy.free_text


async def test_openai_rate_limiting_is_retried(vendor):
    base_url, spy = vendor
    spy.openai_status = [429, 200]
    provider = OpenAICompatibleProvider("sk-test", "gpt-4.1", base_url=f"{base_url}/v1", max_retries=2)

    response = await provider.complete(LLMRequest(task="summary",
                                                  messages=[LLMMessage("user", "ping")]))

    assert response.text == spy.free_text
    assert len(spy.openai_calls) == 2


# ------------------------------------------------------------------ the production path
async def _open_rfq(client, buyer, requester):
    pr = await client.post("/api/v1/intake/text", headers=requester["headers"], json={
        "text": "Need 40 t rebar 16mm, target 21000 EGP per ton.", "title": "Live LLM fixture",
    })
    pr_id = pr.json()["id"]
    detail = (await client.get(f"/api/v1/requests/{pr_id}", headers=requester["headers"])).json()
    for question in detail.get("clarifications", []):
        if question["status"] == "open":
            await client.post(f"/api/v1/requests/{pr_id}/clarifications/{question['id']}/answer",
                              headers=requester["headers"], json={"answer": "ASTM A615"})
    await client.post(f"/api/v1/requests/{pr_id}/submit", headers=requester["headers"])
    suppliers = (await client.get("/api/v1/suppliers", headers=buyer["headers"],
                                  params={"page_size": 1})).json()["items"]
    rfq = await client.post("/api/v1/rfqs", headers=buyer["headers"], json={
        "pr_id": pr_id, "supplier_ids": [suppliers[0]["id"]], "deadline_days": 7,
    })
    rfq_id = rfq.json()["id"]
    await client.post(f"/api/v1/rfqs/{rfq_id}/dispatch", headers=buyer["headers"], json={})
    detail = (await client.get(f"/api/v1/rfqs/{rfq_id}", headers=buyer["headers"])).json()
    return rfq_id, detail["suppliers"][0]["supplier_id"]


@pytest.mark.parametrize("provider_name", ["anthropic", "openai_compatible"])
async def test_a_supplier_reply_is_extracted_by_a_real_llm_over_http(
    client, buyer, requester, vendor, monkeypatch, provider_name
):
    """The whole chain: inbound mail → matching → real HTTP call → persisted extraction + offer."""
    base_url, spy = vendor
    monkeypatch.setattr(settings, "llm_provider", provider_name)
    monkeypatch.setattr(settings, "llm_api_key", "sk-live-test")
    monkeypatch.setattr(settings, "llm_model", MODEL if provider_name == "anthropic" else "gpt-4.1")
    monkeypatch.setattr(settings, "llm_base_url",
                        base_url if provider_name == "anthropic" else f"{base_url}/v1")
    clear_cache()

    try:
        rfq_id, supplier_id = await _open_rfq(client, buyer, requester)
        async with SessionLocal() as session:
            tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
            # A tenant-level override (set by another test or by an admin) outranks the platform
            # default by design; this test is about the platform default.
            from sqlalchemy import delete

            from app.db.models.entities import LlmProviderConfig

            await session.execute(delete(LlmProviderConfig).where(
                LlmProviderConfig.tenant_id == tenant.id))
            await session.flush()
            clear_cache()
            rfq = (await session.execute(select(RFQ).where(RFQ.id == rfq_id))).scalar_one()
            supplier = (await session.execute(
                select(Supplier).where(Supplier.id == supplier_id))).scalar_one()
            contact = await primary_contact(session, tenant, supplier.id)
            sender = (await contact_pii(session, tenant, contact))[0] if contact else ""

            result = await extraction.ingest_inbound(session, tenant, InboundMessage(
                channel="email", external_id=f"live-{provider_name}", sender=sender,
                subject=f"Re: {rfq.code}",
                body="Unit price: 20,500 EGP per ton. Lead time: 21 days. Validity: 14 days.",
                attachments=[], in_reply_to="", received_at="",
            ))
            await session.commit()

            assert result["matched"] is True
            assert result["extractions"], "the pipeline must have produced an extraction"
            row = (await session.execute(
                select(ExtractionResult).where(
                    ExtractionResult.id == result["extractions"][0]))).scalar_one()
            assert row.model.startswith(f"{provider_name}:"), row.model
    finally:
        clear_cache()

    calls = spy.anthropic_calls if provider_name == "anthropic" else spy.openai_calls
    assert len(calls) == 1, "exactly one model call for one supplier reply"
    sent = calls[0]
    assert sent["model"] in {MODEL, "gpt-4.1"}
    joined = json.dumps(sent["messages"] if provider_name == "openai_compatible" else
                        sent["messages"] + [{"content": sent.get("system", "")}], ensure_ascii=False)
    assert "20,500" in joined, "the supplier text must reach the model verbatim"
    assert "untrusted" in joined.lower(), "supplier content must be wrapped as untrusted data"
