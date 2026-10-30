"""LLM provider resolution with per-tenant configuration (FR-ADM-3)."""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm.base import LLMProvider, LLMRequest, LLMResponse, MeteredLLM
from app.agents.llm.fake import FakeLLM
from app.agents.llm.providers import AnthropicProvider, OpenAICompatibleProvider
from app.core.config import settings
from app.core.crypto import decrypt_text
from app.db.base import utcnow
from app.db.models.entities import LlmProviderConfig, Tenant, UsageEvent

log = logging.getLogger("procureai.llm")

_DEFAULT = FakeLLM()
_CACHE: dict[str, Any] = {}


def default_provider() -> Any:
    if settings.llm_provider == "anthropic" and settings.llm_api_key:
        return AnthropicProvider(settings.llm_api_key, settings.llm_model)
    if settings.llm_provider in {"openai", "openai_compatible", "self_hosted"}:
        return OpenAICompatibleProvider(settings.llm_api_key, settings.llm_model, settings.llm_base_url)
    return _DEFAULT


async def provider_for_tenant(session: AsyncSession, tenant: Tenant) -> Any:
    """Tenant override wins; falls back to the platform default; Fake when nothing configured."""
    cache_key = f"tenant:{tenant.id}"
    if cache_key in _CACHE:
        return _CACHE[cache_key]
    config = (await session.execute(
        select(LlmProviderConfig).where(LlmProviderConfig.tenant_id == tenant.id,
                                        LlmProviderConfig.is_default.is_(True))
    )).scalar_one_or_none()
    provider: Any = default_provider()
    if config:
        key = await decrypt_text(session, tenant, config.api_key_enc, config.key_id) if config.api_key_enc else ""
        if config.provider == "anthropic" and key:
            provider = AnthropicProvider(key, config.model)
        elif config.provider in {"openai_compatible", "self_hosted"}:
            provider = OpenAICompatibleProvider(key, config.model, config.base_url)
        elif config.provider == "fake":
            provider = _DEFAULT
    _CACHE[cache_key] = provider
    return provider


def clear_cache() -> None:
    _CACHE.clear()


class MeteringLLM(MeteredLLM):
    """Adds per-tenant usage metering for token billing and budget enforcement."""

    def __init__(self, provider: LLMProvider, session: AsyncSession, tenant_id: str,
                 user_id: str | None = None) -> None:
        self.session = session
        self.tenant_id = tenant_id
        self.user_id = user_id
        super().__init__(provider)

    async def _meter(self, response: LLMResponse, request: LLMRequest) -> None:
        tokens = response.tokens_in + response.tokens_out
        self.session.add(UsageEvent(
            tenant_id=self.tenant_id, kind="llm_token", quantity=tokens, ref_type=f"llm:{request.task}",
            cost_usd=response.cost_usd, occurred_at=utcnow(),
        ))


async def llm_for(session: AsyncSession, tenant: Tenant, user_id: str | None = None) -> MeteringLLM:
    provider = await provider_for_tenant(session, tenant)
    return MeteringLLM(provider, session, tenant.id, user_id)


def content_hash(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]
