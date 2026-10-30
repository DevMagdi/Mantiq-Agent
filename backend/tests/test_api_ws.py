"""WebSocket hub: handshake, auth, frames and broadcast envelope.

The ASGI test client cannot upgrade a socket, so the endpoint is driven directly through a
duck-typed WebSocket. That still exercises the real handler: accept/close codes, the tenant room,
the ping/pong path and the `type` envelope every client reads.
"""
from __future__ import annotations

import json

from starlette.websockets import WebSocketDisconnect

from app.api.ws import hub, websocket_endpoint
from app.core.security import create_access_token


class FakeWebSocket:
    def __init__(self, incoming: list[str] | None = None) -> None:
        self.accepted = False
        self.close_code: int | None = None
        self.close_reason = ""
        self.sent: list[str] = []
        self._incoming = list(incoming or [])

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000, reason: str = "") -> None:  # noqa: ASYNC109
        self.close_code = code
        self.close_reason = reason

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    async def receive_text(self) -> str:
        if self._incoming:
            return self._incoming.pop(0)
        raise WebSocketDisconnect(code=1000)


async def test_valid_token_joins_the_tenant_room_and_answers_ping():
    token = create_access_token(user_id="u1", tenant_id="tenant-ws", roles=["buyer"])
    ws = FakeWebSocket(incoming=["ping"])
    await websocket_endpoint(ws, token=token)

    assert ws.accepted is True
    frames = [json.loads(item) for item in ws.sent]
    assert frames[0]["type"] == "connected"
    assert frames[0]["payload"]["tenant_id"] == "tenant-ws"
    assert frames[-1]["type"] == "pong"
    # The room is cleaned up when the socket goes away.
    assert hub.connection_count("tenant-ws") == 0


async def test_json_ping_also_gets_a_pong():
    token = create_access_token(user_id="u2", tenant_id="tenant-ws2", roles=["buyer"])
    ws = FakeWebSocket(incoming=[json.dumps({"type": "ping"})])
    await websocket_endpoint(ws, token=token)
    assert json.loads(ws.sent[-1])["type"] == "pong"


async def test_invalid_token_is_closed_after_the_upgrade_with_4401():
    ws = FakeWebSocket()
    await websocket_endpoint(ws, token="not-a-token")

    # Accept first: the browser then receives a real close code instead of an opaque handshake
    # failure it cannot distinguish from a network error.
    assert ws.accepted is True
    assert ws.close_code == 4401
    assert ws.close_reason == "invalid_token"
    assert ws.sent == []


async def test_broadcast_envelope_carries_type_and_event():
    ws = FakeWebSocket()
    await hub.join("tenant-b", ws)
    await hub.broadcast("tenant-b", "notification.created", {"id": "n1"})
    frame = json.loads(ws.sent[0])
    assert frame["type"] == "notification.created"
    assert frame["event"] == "notification.created"
    assert frame["payload"] == {"id": "n1"}
    await hub.leave("tenant-b", ws)
    assert hub.connection_count("tenant-b") == 0
