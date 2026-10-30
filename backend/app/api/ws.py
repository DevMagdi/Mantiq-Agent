"""WebSocket hub for live updates (RFQ status, replies, approvals, agent feed)."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.security import decode_token

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

    async def broadcast(self, tenant_id: str, event: str, payload: dict[str, Any]) -> None:
        message = json.dumps({"event": event, "payload": payload}, default=str)
        async with self._lock:
            targets = list(self._rooms.get(tenant_id, set()))
        for ws in targets:
            with contextlib.suppress(Exception):
                await ws.send_text(message)

    def connection_count(self, tenant_id: str | None = None) -> int:
        if tenant_id:
            return len(self._rooms.get(tenant_id, set()))
        return sum(len(v) for v in self._rooms.values())


hub = Hub()


def publish(tenant_id: str, event: str, payload: dict[str, Any]) -> None:
    """Fire-and-forget publish from sync services (safe if no loop is running)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(hub.broadcast(tenant_id, event, payload))


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, token: str = "") -> None:
    try:
        payload = decode_token(token, "access")
    except ValueError:
        await ws.close(code=4401)
        return
    tenant_id = payload.get("tenant_id", "")
    await ws.accept()
    await hub.join(tenant_id, ws)
    await ws.send_text(json.dumps({"event": "connected", "payload": {"tenant_id": tenant_id}}))
    try:
        while True:
            raw = await ws.receive_text()
            if raw == "ping":
                await ws.send_text(json.dumps({"event": "pong", "payload": {}}))
    except WebSocketDisconnect:
        pass
    finally:
        await hub.leave(tenant_id, ws)
