"""Live-event fan-out across replicas, proven against a real Redis server.

Every test here talks to a genuinely running `redis-server` (started from the binary that ships with
the `redislite` wheel, or the `REDIS_TEST_URL` service in CI). Nothing is stubbed: two `EventBus`
instances behave exactly like two API replicas — they subscribe to the same channel, publish
envelopes and forward each other's events to their own sockets.
"""
from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import time
from pathlib import Path

import pytest

from app.services.events import EventBus


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _redis_binary() -> str | None:
    try:
        import redislite
    except Exception:  # noqa: BLE001 - optional dev dependency
        return None
    candidate = Path(redislite.__file__).parent / "bin" / "redis-server"
    return str(candidate) if candidate.exists() else None


@pytest.fixture(scope="module")
def redis_url():
    configured = os.environ.get("REDIS_TEST_URL", "")
    if configured:
        yield configured
        return
    binary = _redis_binary()
    if not binary:
        pytest.skip("no redis-server available (set REDIS_TEST_URL to run the fan-out proof)")
    port = _free_port()
    process = subprocess.Popen(  # noqa: S603 - a local test server we just located
        [binary, "--port", str(port), "--save", "", "--appendonly", "no"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"redis://127.0.0.1:{port}/0"
    try:
        import redis

        client = redis.Redis.from_url(url)
        for _ in range(100):
            try:
                if client.ping():
                    break
            except Exception:  # noqa: BLE001 - still starting
                time.sleep(0.05)
        else:  # pragma: no cover - a broken binary is a test-environment problem
            pytest.skip("redis-server did not start")
        yield url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover
            process.kill()


class Recorder:
    """Stands in for a replica's WebSocket hub."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.events: list[tuple[str, str, dict]] = []

    async def broadcast_local(self, tenant_id: str, event: str, payload: dict) -> None:
        self.events.append((tenant_id, event, payload))

    async def wait_for(self, count: int, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while len(self.events) < count and time.monotonic() < deadline:
            await asyncio.sleep(0.02)


async def _start(redis_url: str, recorder: Recorder) -> EventBus:
    bus = EventBus()
    bus.attach_local(recorder.broadcast_local)
    started = await bus.start(redis_url)
    assert started is True, "the fan-out must connect to the test Redis"
    return bus


async def test_an_event_published_on_one_replica_reaches_the_other(redis_url):
    replica_a, replica_b = Recorder("a"), Recorder("b")
    bus_a = await _start(redis_url, replica_a)
    bus_b = await _start(redis_url, replica_b)
    try:
        await bus_a.publish("tenant-nile", "notification.created",
                            {"id": "n-1", "title": "Approval required"})
        await replica_b.wait_for(1)

        assert replica_b.events == [("tenant-nile", "notification.created",
                                    {"id": "n-1", "title": "Approval required"})]
        # …and the publishing replica delivered it locally exactly once (no double send).
        assert replica_a.events == replica_b.events
        assert bus_b.received == 1
    finally:
        await bus_a.stop()
        await bus_b.stop()


async def test_a_replica_never_echoes_its_own_event_back_to_itself(redis_url):
    recorder = Recorder("solo")
    bus = await _start(redis_url, recorder)
    try:
        await bus.publish("tenant-nile", "rfq.dispatched", {"rfq_id": "r-1"})
        await asyncio.sleep(0.4)  # give the listener time to (wrongly) echo it
        assert len(recorder.events) == 1
        assert bus.received == 0
    finally:
        await bus.stop()


async def test_events_are_tenant_scoped_and_ordered(redis_url):
    recorder = Recorder("c")
    bus = await _start(redis_url, recorder)
    try:
        await bus.publish("tenant-a", "e.one", {"n": 1})
        await bus.publish("tenant-a", "e.two", {"n": 2})
        await bus.publish("tenant-b", "e.one", {"n": 3})
        await recorder.wait_for(3)
        assert [event for _tenant, event, _payload in recorder.events] == ["e.one", "e.two", "e.one"]
        assert [tenant for tenant, _event, _payload in recorder.events] == \
               ["tenant-a", "tenant-a", "tenant-b"]
    finally:
        await bus.stop()


async def test_without_redis_a_single_replica_still_serves_its_sockets():
    recorder = Recorder("local")
    bus = EventBus()
    bus.attach_local(recorder.broadcast_local)

    started = await bus.start("")

    assert started is False and bus.connected is False
    await bus.publish("tenant-nile", "notification.created", {"id": "n-2"})
    assert recorder.events == [("tenant-nile", "notification.created", {"id": "n-2"})]
    await bus.stop()


async def test_a_multi_replica_deployment_refuses_to_run_without_redis():
    bus = EventBus()
    with pytest.raises(RuntimeError, match="WS_FANOUT_ENABLED"):
        await bus.start("", required=True)
    with pytest.raises(RuntimeError, match="unreachable"):
        await bus.start("redis://127.0.0.1:1/0", required=True, connect_timeout=0.5)
    await bus.stop()


async def test_the_bus_reports_its_health_for_operators(redis_url):
    bus = await _start(redis_url, Recorder("ops"))
    try:
        await bus.publish("tenant-nile", "settings.updated", {"key": "whatsapp"})
        stats = bus.stats()
        assert stats["connected"] is True
        assert stats["published"] == 1
        assert stats["dropped"] == 0
        assert len(stats["origin"]) == 8
    finally:
        await bus.stop()
