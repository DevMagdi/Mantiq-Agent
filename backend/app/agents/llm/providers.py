"""Anthropic Claude and OpenAI-compatible providers (covers self-hosted vLLM/Ollama)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

import httpx

from app.agents.llm.base import LLMRequest, LLMResponse, strict_json

log = logging.getLogger("procureai.llm.providers")

ANTHROPIC_DEFAULT_BASE = "https://api.anthropic.com"
OPENAI_DEFAULT_BASE = "https://api.openai.com/v1"

#: A model endpoint that is momentarily overloaded must not turn into a failed RFQ. Only the
#: statuses that mean "try again" are retried; a 400/401 is a configuration bug and fails at once.
RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504, 529})


async def _post_with_retries(client: httpx.AsyncClient, url: str, *, headers: dict[str, str],
                             payload: dict[str, Any], max_retries: int,
                             label: str) -> httpx.Response:
    """POST a completion, retrying transport errors and overload/rate-limit answers only."""
    attempt = 0
    while True:
        try:
            response = await client.post(url, headers=headers, json=payload)
        except httpx.TransportError as exc:
            if attempt >= max_retries:
                raise RuntimeError(f"{label} is unreachable: {exc}") from exc
            delay = 0.5 * (2**attempt)
            log.warning("%s transport error (%s); retrying in %.1fs", label, exc, delay)
            await asyncio.sleep(delay)
            attempt += 1
            continue
        if response.status_code in RETRY_STATUSES and attempt < max_retries:
            delay = 0.5 * (2**attempt)
            log.warning("%s returned %s; retrying in %.1fs", label, response.status_code, delay)
            await asyncio.sleep(delay)
            attempt += 1
            continue
        return response


class AnthropicProvider:
    """Claude via the Messages API. Model name always comes from config, never hard-coded."""

    name = "anthropic"

    def __init__(self, api_key: str, model: str, base_url: str = ANTHROPIC_DEFAULT_BASE,
                 max_retries: int = 2) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries

    async def complete(self, request: LLMRequest) -> LLMResponse:
        if not self.api_key:
            raise RuntimeError("ANTHROPIC/LLM_API_KEY is not configured")
        system = "\n\n".join(m.content for m in request.messages if m.role == "system")
        messages = [{"role": m.role, "content": m.content} for m in request.messages if m.role != "system"]
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "system": system,
            "messages": messages or [{"role": "user", "content": "Continue."}],
        }
        if request.schema:
            payload["tools"] = [{
                "name": "emit_structured_output",
                "description": "Return the structured result for this task.",
                "input_schema": request.schema,
            }]
            payload["tool_choice"] = {"type": "tool", "name": "emit_structured_output"}
        if request.tools:
            payload.setdefault("tools", []).extend(request.tools)

        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await _post_with_retries(
                client,
                f"{self.base_url}/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                payload=payload,
                max_retries=self.max_retries,
                label="Anthropic",
            )
        if resp.status_code >= 400:
            raise RuntimeError(f"Anthropic error {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append({"name": block.get("name"), "input": block.get("input", {})})
        text = "\n".join(text_parts)
        usage = data.get("usage", {})
        structured = tool_calls[0]["input"] if tool_calls else strict_json(text)
        return LLMResponse(
            text=text,
            data=structured,
            tool_calls=tool_calls,
            tokens_in=int(usage.get("input_tokens", 0)),
            tokens_out=int(usage.get("output_tokens", 0)),
            model=self.model,
            provider=self.name,
            latency_ms=int((time.perf_counter() - started) * 1000),
            raw={"stop_reason": data.get("stop_reason")},
        )


class OpenAICompatibleProvider:
    """Any /v1/chat/completions endpoint: OpenAI, vLLM, Ollama, LM Studio (self-hosted option)."""

    name = "openai_compatible"

    def __init__(self, api_key: str, model: str, base_url: str = OPENAI_DEFAULT_BASE,
                 max_retries: int = 2) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries

    async def complete(self, request: LLMRequest) -> LLMResponse:
        if not self.base_url:
            raise RuntimeError("LLM_BASE_URL is not configured")
        if not self.model:
            raise RuntimeError("LLM_MODEL is not configured")
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.schema:
            payload["response_format"] = {"type": "json_object"}
            payload["messages"].append({
                "role": "system",
                "content": "Return ONLY JSON matching this schema: " + json.dumps(request.schema),
            })
        if request.tools:
            payload["tools"] = request.tools
        started = time.perf_counter()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await _post_with_retries(
                client, f"{self.base_url}/chat/completions", headers=headers, payload=payload,
                max_retries=self.max_retries, label="LLM endpoint",
            )
        if resp.status_code >= 400:
            raise RuntimeError(f"LLM error {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        usage = data.get("usage", {})
        return LLMResponse(
            text=text,
            data=strict_json(text),
            tokens_in=int(usage.get("prompt_tokens", 0)),
            tokens_out=int(usage.get("completion_tokens", 0)),
            model=self.model,
            provider=self.name,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


def env_configured_provider(provider: str, model: str, api_key: str, base_url: str):
    """Build a real provider from environment values; None when the selection is not real."""
    if provider == "anthropic" and api_key:
        # A gateway in front of Anthropic (LiteLLM, Azure-style proxy) is expressed with LLM_BASE_URL.
        return AnthropicProvider(api_key, model, base_url or ANTHROPIC_DEFAULT_BASE)
    if provider in {"openai", "openai_compatible", "self_hosted"}:
        return OpenAICompatibleProvider(api_key, model, base_url or OPENAI_DEFAULT_BASE)
    return None


def provider_from_env() -> Any:
    """Compatibility entry point that reads the process environment directly (CLI scripts)."""
    provider = os.getenv("LLM_PROVIDER", "fake")
    return env_configured_provider(provider, os.getenv("LLM_MODEL", "claude-sonnet-4-5"),
                                   os.getenv("LLM_API_KEY", ""), os.getenv("LLM_BASE_URL", ""))
