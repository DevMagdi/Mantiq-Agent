"""ProcureAI API application."""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.router import api_router
from app.api.ws import router as ws_router
from app.core.config import settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.core.rate_limit import check as rate_check
from app.db.session import SessionLocal, engine
from app.services.bootstrap import bootstrap
from app.services.migrations import run_migrations

configure_logging()
log = logging.getLogger("procureai.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("starting ProcureAI api env=%s", settings.app_env)
    try:
        await run_migrations()
    except Exception:  # pragma: no cover - migration tooling optional in tests
        log.exception("migration run failed at startup")
    if settings.bootstrap_on_start:
        try:
            async with SessionLocal() as session:
                await bootstrap(session)
        except Exception:  # pragma: no cover - bootstrap must not crash dev servers
            log.exception("bootstrap failed at startup")
    yield
    await engine.dispose()
    log.info("shutdown complete")


app = FastAPI(
    title="ProcureAI API",
    version="1.0.0",
    description=(
        "AI procurement agent platform by Mantiq Technologies. The system never commits the buyer "
        "to a purchase without explicit human approval."
    ),
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["x-request-id"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):  # noqa: ANN001
    request_id = request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex[:16]}"
    request.state.request_id = request_id
    started = time.perf_counter()
    try:
        rate_check(f"ip:{request.client.host if request.client else 'unknown'}")
    except Exception:
        retry_after = "60"
        return JSONResponse(
            {"type": "https://procureai.mantiq.tech/errors/rate_limited", "title": "Rate Limited",
             "status": 429, "code": "rate_limited", "detail": "Too many requests. Retry shortly.",
             "request_id": request_id},
            status_code=429, headers={"retry-after": retry_after, "x-request-id": request_id},
        )
    response = await call_next(request)
    latency_ms = int((time.perf_counter() - started) * 1000)
    response.headers["x-request-id"] = request_id
    response.headers["x-process-time-ms"] = str(latency_ms)
    response.headers["x-content-type-options"] = "nosniff"
    response.headers["referrer-policy"] = "strict-origin-when-cross-origin"
    response.headers["x-frame-options"] = "DENY"
    response.headers["permissions-policy"] = "geolocation=(), microphone=(), camera=()"
    if settings.is_prod:
        response.headers["strict-transport-security"] = "max-age=63072000; includeSubDomains"
        response.headers["content-security-policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self' ws: wss:; frame-ancestors 'none'"
        )
    log.info("request", extra={"request_id": request_id, "route": request.url.path,
                               "status_code": response.status_code, "latency_ms": latency_ms})
    return response


install_error_handlers(app)
app.include_router(api_router, prefix=settings.api_prefix)
app.include_router(ws_router)


@app.get("/health", tags=["system"], summary="Liveness probe")
async def health():
    return {"status": "ok", "app": settings.app_name, "env": settings.app_env, "version": "1.0.0"}


@app.get("/ready", tags=["system"], summary="Readiness probe (checks the database)")
async def ready():
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        database = "ok"
    except Exception as exc:  # pragma: no cover
        log.warning("readiness failed: %s", exc)
        database = "unavailable"
    status = "ready" if database == "ok" else "degraded"
    return JSONResponse({"status": status, "database": database, "adapters": {
        "llm": settings.llm_provider, "email": settings.email_provider, "search": settings.search_provider,
    }}, status_code=200 if status == "ready" else 503)


@app.get("/metrics", tags=["system"], summary="Prometheus-format metrics (minimal)")
async def metrics():
    from app.api.ws import hub

    body = (
        "# HELP procureai_up Service liveness\n# TYPE procureai_up gauge\nprocureai_up 1\n"
        "# HELP procureai_ws_connections Active websocket connections\n"
        "# TYPE procureai_ws_connections gauge\n"
        f"procureai_ws_connections {hub.connection_count()}\n"
    )
    return Response(body, media_type="text/plain; version=0.0.4")
