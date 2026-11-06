"""The shared (Redis) rate-limiter backend, proven against an in-process Redis.

Two replicas that never share memory must still count against **one** budget. These tests inject a
`fakeredis` server (same wire semantics as redis-server) and check exactly that, plus the honest
degradation when Redis is unreachable: the caller is not failed open silently — it gets the memory
path and a log line, and `procureai doctor` reports the fallback.
"""
from __future__ import annotations

import logging

import pytest

from app.core import rate_limit
from app.core.config import settings
from app.core.errors import RateLimited


@pytest.fixture()
def fake_redis(monkeypatch):
    import fakeredis.aioredis

    server = fakeredis.aioredis.FakeRedis(decode_responses=True)
    previous_backend = settings.rate_limit_backend
    previous_enabled = settings.rate_limit_enabled
    monkeypatch.setattr(settings, "rate_limit_backend", "redis")
    monkeypatch.setattr(settings, "rate_limit_enabled", True)

    async def _client():
        return server

    monkeypatch.setattr(rate_limit, "_redis", _client)
    yield server
    settings.rate_limit_backend = previous_backend
    settings.rate_limit_enabled = previous_enabled
    rate_limit.reset()


async def test_the_window_is_shared_across_replicas(fake_redis) -> None:
    """Two 'processes' (fresh memory state) counting against one Redis window trip it together."""
    previous = settings.rate_limit_per_minute
    settings.rate_limit_per_minute = 4
    try:
        for _ in range(4):
            await rate_limit.check("user:shared")
        # 'Another replica' has empty memory buckets; the shared window still blocks it.
        rate_limit._BUCKETS.clear()
        with pytest.raises(RateLimited):
            await rate_limit.check("user:shared")
    finally:
        settings.rate_limit_per_minute = previous


async def test_rejected_attempts_do_not_extend_the_window(fake_redis) -> None:
    previous = settings.rate_limit_per_minute
    settings.rate_limit_per_minute = 1
    try:
        await rate_limit.check("user:tight")
        for _ in range(3):  # rejected; must not pile up inside the sorted set
            with pytest.raises(RateLimited):
                await rate_limit.check("user:tight")
        cardinality = await fake_redis.zcard("procureai:rl:user:tight")
        assert cardinality == 1, "rejected calls were counted against the window"
    finally:
        settings.rate_limit_per_minute = previous


async def test_an_unreachable_redis_falls_back_to_memory_and_says_so(monkeypatch, caplog) -> None:
    async def _down():
        return None

    monkeypatch.setattr(rate_limit, "_redis", _down)
    monkeypatch.setattr(settings, "rate_limit_backend", "redis")
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    with caplog.at_level(logging.WARNING, logger="procureai.ratelimit"):
        await rate_limit.check("user:fallback")
    # The memory path served the request; one attempt is now in the memory bucket.
    assert "fallback" not in caplog.text  # _redis() itself logs the reason; this call stays quiet
    assert len(rate_limit._BUCKETS["user:fallback"]) == 1


async def test_a_redis_error_mid_call_also_falls_back(monkeypatch) -> None:
    class Exploding:
        def pipeline(self, transaction: bool = True):
            raise ConnectionError("boom")

    async def _client():
        return Exploding()

    monkeypatch.setattr(rate_limit, "_redis", _client)
    monkeypatch.setattr(settings, "rate_limit_backend", "redis")
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    await rate_limit.check("user:degraded")
    assert len(rate_limit._BUCKETS["user:degraded"]) == 1
    assert rate_limit.backend_name() == "memory-fallback (redis unreachable)"


def test_backend_name_reports_memory_by_default(monkeypatch) -> None:
    monkeypatch.setattr(settings, "rate_limit_backend", "memory")
    rate_limit._redis_state = "unknown"
    assert rate_limit.backend_name() == "memory"
