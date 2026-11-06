"""Tracing is off unless OTEL_ENABLED=true — and real when it is.

`otel_enabled` was a dead setting; now it gates a real auto-instrumented pipeline. The test injects
an in-memory span exporter, so no collector is needed: one request through the app must produce a
span carrying the HTTP method, exported synchronously.
"""
from __future__ import annotations

import httpx

import app.core.observability as obs
from app.core.config import settings


async def test_a_request_produces_a_span(monkeypatch) -> None:
    try:
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    except ImportError:  # sdk < 1.23 layout
        from opentelemetry.sdk.trace.export import InMemorySpanExporter  # type: ignore[no-redef]

    previous = settings.otel_enabled
    settings.otel_enabled = False
    try:
        from app.main import create_app

        app = create_app()  # telemetry off inside create_app: no real exporter is ever built
    finally:
        settings.otel_enabled = previous

    monkeypatch.setattr(settings, "otel_enabled", True)
    exporter = InMemorySpanExporter()
    assert obs.setup_observability(app, span_exporter=exporter) is True

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/api/openapi.json")

    spans = exporter.get_finished_spans()
    assert spans, "instrumentation produced no spans"
    methods = {span.attributes.get("http.method") for span in spans if span.attributes}
    assert "GET" in methods, f"no GET span found in {[span.name for span in spans]}"
    services = {span.resource.attributes.get("service.name") for span in spans}
    assert "procureai-api" in services


async def test_tracing_is_a_no_op_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "otel_enabled", False)
    assert obs.setup_observability(None, span_exporter=object()) is False


def test_a_broken_pipeline_never_breaks_boot(monkeypatch) -> None:
    import opentelemetry.sdk.trace as sdk_trace

    def _explode(*args, **kwargs):
        raise RuntimeError("collector gone")

    monkeypatch.setattr(sdk_trace, "TracerProvider", _explode)
    monkeypatch.setattr(settings, "otel_enabled", True)
    assert obs.setup_observability(None) is False  # logged, swallowed, boot proceeds
