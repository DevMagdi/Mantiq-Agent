"""Live-event fan-out across API replicas (Redis pub/sub) with a local fast path.

A browser holds one WebSocket to one replica. The event that interests it may be produced by a
different replica (the request that created the RFQ, the worker that ingested the WhatsApp reply, the
replica that accepted the approval). Without fan-out, a tenant with four replicas sees roughly a
quarter of its own updates — the classic "it only refreshes when I hit F5" bug that looks like a
frontend problem and is not.

The design:

* every process has an **origin id**; publishing writes one envelope to a Redis channel and also
  broadcasts to its own sockets immediately (the local path must not wait for the network);
* every process subscribes to that channel and forwards envelopes **from other origins** to its local
  sockets — own envelopes are skipped, so nobody sees a duplicate;
* a publish that cannot reach Redis is still delivered locally (the socket on this replica works) and
  is logged at error level, because a degraded fan-out is a real incident in a multi-replica
  deployment, not a detail.

`WS_FANOUT_ENABLED=false` keeps the in-process behaviour for a single-replica dev box; production and
staging refuse to start without fan-out (see `app/core/providers.py` and the lifespan check).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger("procureai.events")

CHANNEL = "procureai:events"


class EventBus:
    """Publishes to Redis and to the sockets of this process; receives the other replicas' events."""

    def __init__(self, local: Callable[[str, str, dict[str, Any]], Awaitable[None]] | None = None) -> None:
        self.origin = uuid.uuid4().hex
        self._local = local
        self._redis: Any = None
        self._pubsub: Any = None
        self._listener: asyncio.Task | None = None
        self._stopping = False
        self.connected = False
        self.received = 0
        self.published = 0
        self.dropped = 0

    # ------------------------------------------------------------------ lifecycle
    def attach_local(self, local: Callable[[str, str, dict[str, Any]], Awaitable[None]]) -> None:
        """Wire the local broadcaster (the WebSocket hub). Kept injectable for tests."""
        self._local = local

    async def start(self, url: str, *, required: bool = False, connect_timeout: float = 2.0) -> bool:
        """Connect and subscribe. Returns False when running without Redis (single process)."""
        if not url:
            if required:
                raise RuntimeError("WS_FANOUT_ENABLED=true but REDIS_URL is empty")
            log.warning("live events are in-process only (REDIS_URL empty): one replica only")
            return False
        try:
            import redis.asyncio as aioredis

            client = aioredis.from_url(url, decode_responses=True,
                                       socket_connect_timeout=connect_timeout,
                                       socket_timeout=None, health_check_interval=30)
            await asyncio.wait_for(client.ping(), timeout=connect_timeout)
            self._redis = client
            self._pubsub = client.pubsub(ignore_subscribe_messages=True)
            await self._pubsub.subscribe(CHANNEL)
            self.connected = True
            self._stopping = False
            self._listener = asyncio.create_task(self._listen(), name="ws-event-listener")
            log.info("live event fan-out ready (redis %s, origin %s)", url, self.origin[:8])
            return True
        except Exception as exc:  # noqa: BLE001 - Redis may legitimately be absent in dev
            self.connected = False
            self._redis = None
            if required:
                raise RuntimeError(
                    f"WS_FANOUT_ENABLED=true but Redis at {url!r} is unreachable: {exc}. "
                    "Start Redis (docker compose up redis) or set WS_FANOUT_ENABLED=false for a "
                    "single-replica run."
                ) from exc
            log.warning("live events are in-process only: Redis unreachable (%s)", exc)
            return False

    async def stop(self) -> None:
        self._stopping = True
        if self._listener:
            self._listener.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._listener
            self._listener = None
        if self._pubsub is not None:
            with contextlib.suppress(Exception):
                await self._pubsub.close()
            self._pubsub = None
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.aclose()
            self._redis = None
        self.connected = False

    # ------------------------------------------------------------------ publish / receive
    async def publish(self, tenant_id: str, event: str, payload: dict[str, Any]) -> None:
        """Deliver to this process' sockets now, then hand the envelope to Redis."""
        self.published += 1
        await self._emit_local(tenant_id, event, payload)
        if not self.connected or self._redis is None:
            if self._local is None:
                self.dropped += 1
            return
        envelope = json.dumps({"origin": self.origin, "tenant_id": tenant_id, "event": event,
                               "payload": payload}, default=str)
        try:
            await self._redis.publish(CHANNEL, envelope)
        except Exception:  # noqa: BLE001 - a broken fan-out must not break the business request
            self.dropped += 1
            log.exception("event fan-out failed; the event reached this replica only")

    async def _listen(self) -> None:
        backoff = 0.5
        while not self._stopping:
            try:
                async for message in self._pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    try:
                        envelope = json.loads(message["data"])
                    except (ValueError, TypeError):
                        continue
                    if envelope.get("origin") == self.origin:
                        continue  # already delivered locally by publish()
                    self.received += 1
                    await self._emit_local(envelope.get("tenant_id", ""), envelope.get("event", ""),
                                           envelope.get("payload") or {})
                backoff = 0.5
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect instead of losing the feed
                if self._stopping:
                    return
                log.warning("event listener disconnected (%s); reconnecting in %.1fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10.0)
                with contextlib.suppress(Exception):
                    await self._pubsub.subscribe(CHANNEL)

    async def _emit_local(self, tenant_id: str, event: str, payload: dict[str, Any]) -> None:
        if self._local is None:
            return
        try:
            await self._local(tenant_id, event, payload)
        except Exception:  # noqa: BLE001
            log.exception("local broadcast failed for %s", event)

    def publish_soon(self, tenant_id: str, event: str, payload: dict[str, Any]) -> None:
        """Fire-and-forget from sync service code (no loop → nothing to do)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.publish(tenant_id, event, payload))

    def stats(self) -> dict[str, Any]:
        return {"connected": self.connected, "origin": self.origin[:8], "published": self.published,
                "received": self.received, "dropped": self.dropped,
                "subscribers_here": None if self._local is None else True}


bus = EventBus()
