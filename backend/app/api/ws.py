"""WebSocket hub for live updates (RFQ status, replies, approvals, agent feed)."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.security import decode_token
from app.services.events import bus

log = logging.getLogger("procureai.ws")
router = APIRouter()


class Hub:
    """Per-tenant rooms of WebSockets with a **bounded outbound queue per socket**.

    Broadcasting straight into ``send_text`` lets one stalled reader (laptop asleep, dead TCP through
    a proxy) back-pressure every other subscriber in the room, and buffers an unbounded number of
    frames in this process. Instead each socket drains its own queue in a writer task; a reader that
    falls ``max_pending`` frames behind is disconnected (code 1013) so the browser reconnects and
    refetches, rather than silently pinning memory for the whole room.
    """

    def __init__(self, max_pending: int = 64) -> None:
        self.max_pending = max_pending
        self._rooms: dict[str, set[WebSocket]] = {}
        self._queues: dict[WebSocket, asyncio.Queue[str]] = {}
        self._lock = asyncio.Lock()
        #: sockets dropped for falling behind — surfaced on /metrics so the drain is observable.
        self.slow_disconnects = 0

    async def join(self, tenant_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self._rooms.setdefault(tenant_id, set()).add(ws)
            self._queues[ws] = asyncio.Queue(maxsize=self.max_pending)

    async def leave(self, tenant_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self._rooms.get(tenant_id, set()).discard(ws)
            self._queues.pop(ws, None)

    def queue_for(self, ws: WebSocket) -> asyncio.Queue[str]:
        return self._queues[ws]

    async def enqueue(self, tenant_id: str, ws: WebSocket, message: str) -> None:
        """Best-effort enqueue for one socket; overflow disconnects that socket."""
        queue = self._queues.get(ws)
        if queue is None:
            return
        try:
            queue.put_nowait(message)
            return
        except asyncio.QueueFull:
            pass
        await self._disconnect_slow(tenant_id, ws)

    async def _disconnect_slow(self, tenant_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self._rooms.get(tenant_id, set()).discard(ws)
            self._queues.pop(ws, None)
            self.slow_disconnects += 1
        with contextlib.suppress(Exception):
            await ws.close(code=1013, reason="fell behind")

    async def broadcast_local(self, tenant_id: str, event: str, payload: dict[str, Any]) -> None:
        """Queue the frame for every socket in *this* process (the fan-out calls it per replica)."""
        # `type` is the field every client reads; `event` is kept so older subscribers and the
        # documented payload shape keep working.
        message = json.dumps({"type": event, "event": event, "payload": payload}, default=str)
        async with self._lock:
            targets = list(self._rooms.get(tenant_id, set()))
        overflow: list[WebSocket] = []
        for ws in targets:
            queue = self._queues.get(ws)
            if queue is None:
                continue
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                overflow.append(ws)
        for ws in overflow:
            await self._disconnect_slow(tenant_id, ws)

    async def broadcast(self, tenant_id: str, event: str, payload: dict[str, Any]) -> None:
        """Publish to every replica (Redis) and to this process' sockets."""
        await bus.publish(tenant_id, event, payload)

    def connection_count(self, tenant_id: str | None = None) -> int:
        if tenant_id:
            return len(self._rooms.get(tenant_id, set()))
        return sum(len(v) for v in self._rooms.values())


hub = Hub()
# The bus always knows how to reach this process' sockets; the lifespan adds the Redis transport.
bus.attach_local(hub.broadcast_local)


def publish(tenant_id: str, event: str, payload: dict[str, Any]) -> None:
    """Fire-and-forget publish from sync services (safe if no loop is running)."""
    bus.publish_soon(tenant_id, event, payload)


def _is_ping(raw: str) -> bool:
    """Accept both the browser's bare `ping` and a JSON `{"type": "ping"}` frame."""
    stripped = raw.strip()
    if stripped == "ping":
        return True
    if not stripped.startswith("{"):
        return False
    try:
        data = json.loads(stripped)
    except ValueError:
        return False
    return isinstance(data, dict) and data.get("type") == "ping"


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, token: str = "") -> None:
    """Tenant-scoped live feed.

    The browser cannot set headers on a WebSocket, so the access token travels as a query
    parameter. It is validated *after* the upgrade so the client receives a real close code
    (4401) instead of an opaque handshake failure.
    """
    await ws.accept()
    try:
        payload = decode_token(token, "access")
    except ValueError:
        await ws.close(code=4401, reason="invalid_token")
        return
    tenant_id = payload.get("tenant_id", "")
    if not tenant_id:
        await ws.close(code=4403, reason="no_tenant")
        return
    await hub.join(tenant_id, ws)
    queue = hub.queue_for(ws)

    async def writer() -> None:
        while True:
            message = await queue.get()
            try:
                await ws.send_text(message)
            except Exception:  # pragma: no cover - socket closed underneath us; the reader loop cleans up
                return

    writer_task = asyncio.create_task(writer(), name="ws-writer")
    try:
        await hub.enqueue(tenant_id, ws,
                          json.dumps({"type": "connected", "event": "connected",
                                      "payload": {"tenant_id": tenant_id}}))
        while True:
            raw = await ws.receive_text()
            if _is_ping(raw):
                await hub.enqueue(tenant_id, ws,
                                  json.dumps({"type": "pong", "event": "pong", "payload": {}}))
    except WebSocketDisconnect:
        pass
    except RuntimeError:  # pragma: no cover - the client vanished mid-send
        log.debug("websocket closed while receiving")
    finally:
        writer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await writer_task
        await hub.leave(tenant_id, ws)
