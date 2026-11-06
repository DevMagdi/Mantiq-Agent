"""Tracing for ProcureAI — off by default, real when `OTEL_ENABLED=true`.

`otel_enabled` used to be a dead flag. This module makes it honest: when enabled, FastAPI and the
SQLAlchemy engine are auto-instrumented, spans go to the OTLP/HTTP collector named by the standard
`OTEL_EXPORTER_OTLP_ENDPOINT` (or `http://localhost:4318/v1/traces`), and the service is stamped
`procureai-api` with its version. When disabled, or when the exporter cannot be built, startup
proceeds with a log line — observability must never take the API down (AGENT: the request path is
the product; telemetry is a sidecar).

Tests inject their own span exporter via :func:`setup_observability` — no collector needed.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from app.core.config import settings

log = logging.getLogger("procureai.otel")

SERVICE_NAME = "procureai-api"
SERVICE_VERSION = "1.1.0"


def setup_observability(app: Any | None = None, *, span_exporter: Any | None = None) -> bool:
    """Instrument the app. Returns True when tracing is actually wired up. Never raises."""
    if not settings.otel_enabled:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
        if span_exporter is None:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            span_exporter = OTLPSpanExporter(endpoint=endpoint or "http://localhost:4318/v1/traces")
            processor: Any = BatchSpanProcessor(span_exporter)
        else:  # tests: synchronous so assertions can read spans immediately
            processor = SimpleSpanProcessor(span_exporter)

        provider = TracerProvider(resource=Resource.create(
            {"service.name": SERVICE_NAME, "service.version": SERVICE_VERSION}))
        provider.add_span_processor(processor)
        trace.set_tracer_provider(provider)

        if app is not None:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            FastAPIInstrumentor.instrument_app(app, excluded_urls="health,ready,metrics")
        try:
            from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

            SQLAlchemyInstrumentor().instrument()
        except Exception as exc:  # noqa: BLE001 - SQL spans are nice-to-have; HTTP spans still flow
            log.info("SQLAlchemy instrumentation unavailable: %s", exc)
        log.info("tracing enabled (service %s@%s, exporter %s)", SERVICE_NAME, SERVICE_VERSION,
                 endpoint or "default")
        return True
    except Exception as exc:  # noqa: BLE001 - never let telemetry break boot
        log.error("tracing not enabled: %s", exc)
        return False
