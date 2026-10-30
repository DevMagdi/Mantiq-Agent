"""Provider-agnostic LLM contract (Section 6 of the master build prompt).

Nothing in the platform talks to a vendor SDK directly.  Every agent calls
``LLMProvider.complete`` with a *task* tag plus an optional JSON schema and gets
``LLMResponse`` back.  That buys three SRS-level properties:

* model swaps are configuration, not code (NFR-09) — Anthropic is the default,
  any OpenAI-compatible endpoint (including self-hosted vLLM/Ollama) is honoured;
* every call is metered per tenant and priced for billing (FR-BIL-2);
* a deterministic fake provider exercises the exact same pipeline offline, so
  tests and demos never need a network or an API key.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(slots=True)
class LLMMessage:
    """One chat turn. ``role`` first so the call site reads like the transcript."""

    role: Role
    content: str


@dataclass(slots=True)
class LLMRequest:
    task: str
    messages: list[LLMMessage]
    schema: dict[str, Any] | None = None
    tools: list[dict[str, Any]] | None = None
    context: dict[str, Any] = field(default_factory=dict)
    temperature: float = 0.0
    max_tokens: int = 1500
    tenant_id: str | None = None
    user_id: str | None = None


@dataclass(slots=True)
class LLMResponse:
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    model: str = ""
    provider: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    refused: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.tokens_in + self.tokens_out

    def as_summary(self) -> str:
        text = " ".join((self.text or "").split())
        return text[:240] + ("…" if len(text) > 240 else "")


class LLMError(RuntimeError):
    """Provider failure. Callers degrade to the deterministic code path."""


class LLMBudgetExceeded(LLMError):
    """Tenant exhausted its monthly token budget (admin-configured)."""


#: Published USD price per 1M tokens, used for cost accounting. Kept in code so
#: billing never depends on a live pricing endpoint; review when vendors change.
PRICING: dict[str, tuple[float, float]] = {
    "fake-deterministic-1": (0.0, 0.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (0.8, 4.0),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.0),
    "local-llama": (0.0, 0.0),
}


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    price_in, price_out = PRICING.get(model, (0.0, 0.0))
    return round((tokens_in / 1_000_000) * price_in + (tokens_out / 1_000_000) * price_out, 6)


def rough_tokens(text: str) -> int:
    """~4 characters per token; used when a provider reports no usage."""

    return max(1, len(text) // 4)


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.+?)```", re.DOTALL)


def strict_json(text: str) -> dict[str, Any]:
    """Best-effort JSON parse of a model answer.

    Handles fenced blocks and leading prose; returns ``{}`` rather than raising so
    one malformed generation can never crash a request.  Callers must therefore
    treat an empty dict as "no structured output".
    """

    if not text:
        return {}
    for candidate in (text, *[m.group(1) for m in _JSON_BLOCK.finditer(text)]):
        candidate = candidate.strip()
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            continue
        try:
            parsed = json.loads(candidate[start : end + 1])
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


class LLMProvider(Protocol):
    name: str
    model: str

    async def complete(self, request: LLMRequest) -> LLMResponse:  # pragma: no cover - protocol
        ...


class MeteredLLM:
    """Decorator adding latency, cost and usage metering to any provider.

    The concrete subclass in :mod:`app.agents.llm` persists the usage event; this
    layer stays free of database imports so it can wrap pure providers in tests.
    """

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider
        self.name = getattr(provider, "name", "provider")
        self.model = getattr(provider, "model", "model")

    async def complete(self, request: LLMRequest) -> LLMResponse:
        started = time.perf_counter()
        response = await self._provider.complete(request)
        if not response.latency_ms:
            response.latency_ms = int((time.perf_counter() - started) * 1000)
        if not response.model:
            response.model = self.model
        if not response.provider:
            response.provider = self.name
        if not response.cost_usd:
            response.cost_usd = estimate_cost(
                response.model, response.tokens_in, response.tokens_out
            )
        await self._meter(response, request)
        return response

    async def _meter(self, response: LLMResponse, request: LLMRequest) -> None:
        """Hook: subclasses persist a usage event here."""

    def __getattr__(self, item: str) -> Any:  # expose provider extras (e.g. embedding)
        return getattr(self._provider, item)
