"""Rate limiting and abuse control (in-memory dev; Redis-backed in compose)."""
from __future__ import annotations

import time
from collections import defaultdict, deque

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
