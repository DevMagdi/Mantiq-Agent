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

from app.api.router import build_api_router
from app.api.ws import router as ws_router
from app.core.config import settings
from app.core.errors import RateLimited, install_error_handlers
from app.core.logging import configure_logging
from app.core.providers import assert_ready
from app.core.rate_limit import enforce as rate_enforce
from app.core.rate_limit import retry_after_seconds as rate_retry_after
from app.db.session import SessionLocal, engine
from app.services.bootstrap import bootstrap
from app.services.migrations import run_migrations
from app.services.seed import seed_demo_tenant

configure_logging()
log = logging.getLogger("procureai.main")


@asynccontextmanager
def _assert_supported_database() -> None:
    """PostgreSQL 16 is the supported database.

    SQLite is kept for the test suite and for sandboxes that have no PostgreSQL server. Anywhere
    else it is a hard error rather than a silent downgrade: RLS, pg_trgm search and pgvector
    similarity silently stop protecting data on SQLite.
    """
    url = settings.database_url
    if not url.startswith("sqlite"):
        return
    if settings.app_env == "test" or settings.allow_sqlite_fallback:
        log.warning(
            "running on SQLite (%s); this is a development fallback — RLS, full-text search and "
            "vector ranking are PostgreSQL-only", settings.app_env,
        )
        return
    raise RuntimeError(
        "SQLite is not a supported database for APP_ENV="
        f"{settings.app_env!r}. Point DATABASE_URL at PostgreSQL 16 "
        "(postgresql+asyncpg://user:password@host:5432/procureai). For a sandbox without "
        "PostgreSQL, set ALLOW_SQLITE_FALLBACK=true and accept the reduced guarantees."
    )


async def lifespan(app: FastAPI):
    log.info("starting ProcureAI api env=%s", settings.app_env)
    _assert_supported_database()
    # Provider credentials are part of "can this process serve traffic": in staging/production a
    # missing Twilio/LLM/SMTP credential stops the boot with an actionable message instead of
    # silently falling back to a fake that would never contact a supplier.
    assert_ready(settings)
    from app.core.providers import warnings as provider_warnings

    for warning in provider_warnings(settings):
        log.warning("provider note: %s", warning)
    # Live updates: every replica must see every event, so the bus subscribes before serving.
    from app.api.ws import hub
    from app.services.events import bus

    bus.attach_local(hub.broadcast_local)
    await bus.start(settings.redis_url,
                    required=settings.ws_fanout_enabled and settings.strict_providers)
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
    if settings.demo_seed_on_start and not settings.is_prod:
        # Idempotent (`seed_demo` records `demo_seeded` on the tenant), and never in production.
        try:
            async with SessionLocal() as session:
                result = await seed_demo_tenant(
                    session, demo_password=settings.bootstrap_demo_password
                )
                await session.commit()
            log.info("demo data ready: %s", result)
        except Exception:  # pragma: no cover - demo data must not crash dev servers
            log.exception("demo seed failed at startup")
    yield
    from app.services.events import bus as _bus

    await _bus.stop()
    await engine.dispose()
    log.info("shutdown complete")


def create_app(*, accounting_enabled: bool | None = None) -> FastAPI:
    """Build the application.

    ``accounting_enabled`` defaults to the deployment setting and is resolved **before** the router is
    assembled, so a pilot build has no accounting route and no accounting path in the OpenAPI schema.
    Tests build an isolated instance to prove exactly that.
    """
    application = FastAPI(
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
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["x-request-id"],
    )
    application.middleware("http")(request_context)
    install_error_handlers(application)
    application.include_router(build_api_router(accounting_enabled=accounting_enabled),
                               prefix=settings.api_prefix)
    application.include_router(ws_router)
    _install_system_routes(application)
    return application


def _install_system_routes(application: FastAPI) -> None:
    @application.get("/health", tags=["system"], summary="Liveness probe")
    async def health():
        return {"status": "ok", "app": settings.app_name, "env": settings.app_env, "version": "1.0.0"}

    @application.get("/ready", tags=["system"], summary="Readiness probe (checks the database)")
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
            "llm": settings.llm_provider, "email": settings.email_provider,
            "search": settings.search_provider,
        }}, status_code=200 if status == "ready" else 503)

    @application.get("/metrics", tags=["system"], summary="Prometheus-format metrics (minimal)")
    async def metrics():
        from app.api.ws import hub

        body = (
            "# HELP procureai_up Service liveness\n# TYPE procureai_up gauge\nprocureai_up 1\n"
            "# HELP procureai_ws_connections Active websocket connections\n"
            "# TYPE procureai_ws_connections gauge\n"
            f"procureai_ws_connections {hub.connection_count()}\n"
        )
        return Response(body, media_type="text/plain; version=0.0.4")


async def request_context(request: Request, call_next):  # noqa: ANN001
    request_id = request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex[:16]}"
    request.state.request_id = request_id
    started = time.perf_counter()
    try:
        rate_enforce(request)
    except RateLimited as exc:
        retry_after = str(rate_retry_after(exc))
        return JSONResponse(
            {"type": "https://procureai.mantiq.tech/errors/rate_limited", "title": "Rate Limited",
             "status": 429, "code": "rate_limited", "detail": "Too many requests. Retry shortly.",
             "request_id": request_id},
            status_code=429,
            headers={"retry-after": retry_after, "x-request-id": request_id,
                     "x-ratelimit-scope": "identity"},
        )
    except Exception:  # pragma: no cover - never let the limiter itself take the API down
        log.exception("rate limiter failed; allowing the request")

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


app = create_app()
