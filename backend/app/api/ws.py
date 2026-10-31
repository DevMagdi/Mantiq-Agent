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
    def __init__(self) -> None:
        self._rooms: dict[str, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def join(self, tenant_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self._rooms.setdefault(tenant_id, set()).add(ws)

    async def leave(self, tenant_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self._rooms.get(tenant_id, set()).discard(ws)

    async def broadcast_local(self, tenant_id: str, event: str, payload: dict[str, Any]) -> None:
        """Send to the sockets that live in *this* process (the fan-out calls it per replica)."""
        # `type` is the field every client reads; `event` is kept so older subscribers and the
        # documented payload shape keep working.
        message = json.dumps({"type": event, "event": event, "payload": payload}, default=str)
        async with self._lock:
            targets = list(self._rooms.get(tenant_id, set()))
        for ws in targets:
            with contextlib.suppress(Exception):
                await ws.send_text(message)

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
    await ws.send_text(json.dumps({"type": "connected", "event": "connected",
                                   "payload": {"tenant_id": tenant_id}}))
    try:
        while True:
            raw = await ws.receive_text()
            if _is_ping(raw):
                await ws.send_text(json.dumps({"type": "pong", "event": "pong", "payload": {}}))
    except WebSocketDisconnect:
        pass
    except RuntimeError:  # pragma: no cover - the client vanished mid-send
        log.debug("websocket closed while receiving")
    finally:
        await hub.leave(tenant_id, ws)
