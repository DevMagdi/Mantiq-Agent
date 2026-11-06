"""The WebSocket hub's back-pressure contract.

A broadcast must never be hostage to one stalled reader: frames queue per socket, and a socket that
falls `max_pending` frames behind is disconnected with code 1013 (the browser reconnects and
refetches) instead of pinning unbounded memory or stalling the rest of the room.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.api.ws import Hub


class FakeWebSocket:
    """Minimal stand-in for starlette's WebSocket: records what would be sent (or refuses to send)."""

    def __init__(self, *, send_blocks: bool = False) -> None:
        self.sent: list[str] = []
        self.closed_with: tuple[int, str] | None = None
        self._send_blocks = send_blocks

    async def send_text(self, message: str) -> None:
        if self._send_blocks:
            await asyncio.Event().wait()  # never completes: the reader is asleep
        self.sent.append(message)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed_with = (code, reason)


async def test_a_slow_reader_is_disconnected_without_stalling_the_room() -> None:
    hub = Hub(max_pending=4)
    fast, slow = FakeWebSocket(), FakeWebSocket(send_blocks=True)
    await hub.join("tenant-1", fast)
    await hub.join("tenant-1", slow)

    # Drain `fast`'s queue continuously; `slow` never drains.
    async def drain_fast() -> None:
        queue = hub.queue_for(fast)
        while True:
            message = await queue.get()
            await fast.send_text(message)

    drainer = asyncio.create_task(drain_fast())
    try:
        for i in range(6):  # max_pending is 4 — the 5th frame overflows the sleeping socket
            await hub.broadcast_local("tenant-1", "po.updated", {"n": i})
            for _ in range(3):  # let the healthy reader's drainer run between frames
                await asyncio.sleep(0)
        assert slow.closed_with is not None, "the stalled socket was never dropped"
        assert slow.closed_with[0] == 1013
        assert hub.connection_count("tenant-1") == 1
        assert hub.slow_disconnects == 1
        assert slow not in hub._rooms.get("tenant-1", set())
        # Every frame reached the healthy reader.
        assert len(fast.sent) == 6
        assert json.loads(fast.sent[-1])["payload"]["n"] == 5
    finally:
        drainer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await drainer


async def test_the_queue_bound_holds_under_burst() -> None:
    hub = Hub(max_pending=8)
    slow = FakeWebSocket(send_blocks=True)  # no drainer task at all
    await hub.join("tenant-1", slow)
    for i in range(20):
        await hub.broadcast_local("tenant-1", "agent.feed", {"n": i})
    assert hub.connection_count("tenant-1") == 0, "overflowing socket stayed registered"
    assert hub.slow_disconnects == 1


async def test_leaving_a_room_forgets_the_queue() -> None:
    hub = Hub(max_pending=2)
    ws = FakeWebSocket()
    await hub.join("tenant-1", ws)
    assert hub.queue_for(ws).maxsize == 2
    await hub.leave("tenant-1", ws)
    assert hub.connection_count("tenant-1") == 0
    with pytest.raises(KeyError):
        hub.queue_for(ws)
