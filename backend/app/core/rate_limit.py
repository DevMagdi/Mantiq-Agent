"""Rate limiting and abuse control.

Bucketing is by **identity, not by connection**: an authenticated request is counted against the
signed-in user, and only anonymous traffic falls back to the client IP. Keying everything on the IP
looks safe but breaks the common enterprise case — fifty people behind one office NAT, or every
request arriving through a load balancer — where a single bucket throttles the whole company.

Sign-in is bucketed twice: per account (credential stuffing against one user, on top of the
password lockout in `services/auth.py`) and per IP (mass attempts from one host).

The counter lives in process memory, which is exact for a single worker and best-effort across
replicas; a Redis backend slots in behind `check()` for multi-replica deployments (documented in
`docs/RUNBOOK.md`).
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Request

from app.core.config import settings
from app.core.errors import RateLimited

_BUCKETS: dict[str, deque[float]] = defaultdict(deque)
_MAX_KEYS = 50_000


def check(key: str, limit_per_minute: int | None = None) -> None:
    if not settings.rate_limit_enabled:
        return
    limit = limit_per_minute or settings.rate_limit_per_minute
    now = time.monotonic()
    window_start = now - 60.0
    bucket = _BUCKETS[key]
    while bucket and bucket[0] < window_start:
        bucket.popleft()
    if len(bucket) >= limit:
        retry_after = max(1, int(60 - (now - bucket[0])))
        raise RateLimited(
            f"Too many requests. Retry in {retry_after}s.",
            extra={"retry_after_seconds": retry_after},
        )
    bucket.append(now)
    if len(_BUCKETS) > _MAX_KEYS:  # cheap eviction bound
        for k in list(_BUCKETS)[: _MAX_KEYS // 10]:
            _BUCKETS.pop(k, None)


def reset() -> None:
    _BUCKETS.clear()


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


def enforce(request: Request) -> None:
    """Apply the per-identity limit to one request, with the anonymous limit for guest traffic."""
    key = identity(request)
    if key.startswith("user:"):
        check(key)
    else:
        check(key, settings.rate_limit_anonymous_per_minute)


def enforce_login(ip: str, email: str) -> None:
    """Two buckets for sign-in: the account being attacked, and the host doing the guessing."""
    normalized = (email or "").strip().lower()
    if normalized:
        check(f"login:account:{normalized}", settings.rate_limit_login_per_account)
    check(f"login:ip:{ip or 'unknown'}", settings.rate_limit_login_per_ip)


def retry_after_seconds(error: RateLimited) -> int:
    extra = getattr(error, "extra", None) or {}
    try:
        return max(1, int(extra.get("retry_after_seconds", 60)))
    except (TypeError, ValueError):
        return 60
