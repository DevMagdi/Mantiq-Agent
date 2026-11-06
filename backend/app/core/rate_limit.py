"""Rate limiting and abuse control.

Bucketing is by **identity, not by connection**: an authenticated request is counted against the
signed-in user, and only anonymous traffic falls back to the client IP. Keying everything on the IP
looks safe but breaks the common enterprise case — fifty people behind one office NAT, or every
request arriving through a load balancer — where a single bucket throttles the whole company.

Sign-in is bucketed twice: per account (credential stuffing against one user, on top of the
password lockout in `services/auth.py`) and per IP (mass attempts from one host).

Two backends, chosen by `RATE_LIMIT_BACKEND`:

* ``memory`` (default) — exact for a single replica, per-process across several;
* ``redis`` — a shared sliding window (sorted set, atomic pipeline) so every replica counts
  against the same budget. If Redis is unreachable the limiter falls back to memory for that call
  and says so in the logs rather than failing open silently or taking the API down.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict, deque

from fastapi import Request

from app.core.config import settings
from app.core.errors import RateLimited

_BUCKETS: dict[str, deque[float]] = defaultdict(deque)
_MAX_KEYS = 50_000

_redis_client = None
_redis_state = "unknown"  # unknown | up | down (last real call's outcome; drives honest doctor output)


def backend_name() -> str:
    """Which backend the limiter is actually using, for `procureai doctor`."""
    if settings.rate_limit_backend != "redis":
        return "memory"
    return "redis" if _redis_state != "down" else "memory-fallback (redis unreachable)"


async def _redis():
    """Lazily connect; cache the client. Returns None when unavailable."""
    global _redis_client, _redis_state
    if _redis_client is not None:
        return _redis_client
    if settings.rate_limit_backend != "redis":
        return None
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.redis_url, decode_responses=True,
                                   socket_connect_timeout=0.5, socket_timeout=0.5)
        await asyncio.wait_for(client.ping(), timeout=1.0)
        _redis_client = client
        _redis_state = "up"
        return _redis_client
    except Exception as exc:  # noqa: BLE001 - Redis absence must degrade, not 500 the API
        _redis_state = "down"
        import logging

        logging.getLogger("procureai.ratelimit").warning(
            "rate limit backend redis unavailable (%s); falling back to per-process memory buckets", exc)
        return None


def _check_memory(key: str, limit: int) -> int | None:
    """Append to the in-process window. Returns the retry-after in seconds when over budget."""
    now = time.monotonic()
    window_start = now - 60.0
    bucket = _BUCKETS[key]
    while bucket and bucket[0] < window_start:
        bucket.popleft()
    if len(bucket) >= limit:
        return max(1, int(60 - (now - bucket[0])))
    bucket.append(now)
    if len(_BUCKETS) > _MAX_KEYS:  # cheap eviction bound
        for k in list(_BUCKETS)[: _MAX_KEYS // 10]:
            _BUCKETS.pop(k, None)
    return None


async def _check_redis(key: str, limit: int) -> int | None:
    """Sliding window over a Redis sorted set. Returns the retry-after in seconds when over budget."""
    global _redis_state
    client = await _redis()
    if client is None:
        return _check_memory(key, limit)
    now = time.time()
    member = f"{now:.6f}:{uuid.uuid4().hex[:12]}"
    rkey = f"procureai:rl:{key}"
    try:
        pipe = client.pipeline(transaction=True)
        pipe.zremrangebyscore(rkey, 0, now - 60.0)
        pipe.zadd(rkey, {member: now})
        pipe.zcard(rkey)
        pipe.expire(rkey, 61)
        _, _, count, _ = await pipe.execute()
        _redis_state = "up"
        if count <= limit:
            return None
        # Over budget: remove the attempt we just recorded so rejected calls do not extend the window.
        await client.zrem(rkey, member)
        oldest = await client.zrange(rkey, 0, 0, withscores=True)
        if oldest:
            return max(1, int(60 - (now - oldest[0][1])) + 1)
        return 60
    except Exception as exc:  # noqa: BLE001 - Redis hiccup: degrade to memory, never fail the request open silently
        _redis_state = "down"
        import logging

        logging.getLogger("procureai.ratelimit").warning(
            "rate limit redis call failed (%s); using per-process memory for this call", exc)
        return _check_memory(key, limit)


async def check(key: str, limit_per_minute: int | None = None) -> None:
    if not settings.rate_limit_enabled:
        return
    limit = limit_per_minute or settings.rate_limit_per_minute
    retry_after = (await _check_redis(key, limit)) if settings.rate_limit_backend == "redis" else _check_memory(key, limit)
    if retry_after is not None:
        raise RateLimited(
            f"Too many requests. Retry in {retry_after}s.",
            extra={"retry_after_seconds": retry_after},
        )


def reset() -> None:
    """Clear the in-process buckets (tests); Redis keys carry a 61 s TTL and expire on their own."""
    global _redis_client, _redis_state
    _BUCKETS.clear()
    _redis_client = None
    _redis_state = "unknown"


def identity(request: Request) -> str:
    """`user:<id>` when a valid access token is present, otherwise `ip:<host>`.

    Verification is a local HMAC check (no database), so this costs microseconds per request. An
    unsigned or expired token is not trusted for bucketing — it falls back to the IP, which is the
    strictest interpretation.
    """
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        from app.core.security import decode_token

        try:
            payload = decode_token(header[7:].strip())
        except Exception:  # noqa: BLE001 - any decode problem means "treat as anonymous"
            payload = None
        if payload and payload.get("sub"):
            return f"user:{payload['sub']}"
    host = request.client.host if request.client else "unknown"
    return f"ip:{host}"


async def enforce(request: Request) -> None:
    """Apply the per-identity limit to one request, with the anonymous limit for guest traffic."""
    key = identity(request)
    if key.startswith("user:"):
        await check(key)
    else:
        await check(key, settings.rate_limit_anonymous_per_minute)


async def enforce_login(ip: str, email: str) -> None:
    """Two buckets for sign-in: the account being attacked, and the host doing the guessing."""
    normalized = (email or "").strip().lower()
    if normalized:
        await check(f"login:account:{normalized}", settings.rate_limit_login_per_account)
    await check(f"login:ip:{ip or 'unknown'}", settings.rate_limit_login_per_ip)


def retry_after_seconds(error: RateLimited) -> int:
    extra = getattr(error, "extra", None) or {}
    try:
        return max(1, int(extra.get("retry_after_seconds", 60)))
    except (TypeError, ValueError):
        return 60
