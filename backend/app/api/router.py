"""API v1 router aggregation.

The accounting router is included **conditionally**: with ``ACCOUNTING_ENABLED=false`` a pilot
deployment must not expose a single accounting path, and FastAPI derives the OpenAPI schema from the
routes it actually has — so the switch has to act at include time, not per request. Every other
router is always present.
"""
from fastapi import APIRouter

from app.api.v1 import accounting, admin, channels, identity, intake, orders, rfq
from app.core.config import settings


def build_api_router(*, accounting_enabled: bool | None = None) -> APIRouter:
    """Assemble the v1 router. ``accounting_enabled`` defaults to the deployment setting."""
    enabled = settings.accounting_enabled if accounting_enabled is None else accounting_enabled
    router = APIRouter()
    router.include_router(identity.router)
    router.include_router(intake.router)
    router.include_router(rfq.router)
    router.include_router(orders.router)
    if enabled:
        router.include_router(accounting.router)
    router.include_router(channels.router)
    router.include_router(admin.router)
    return router


api_router = build_api_router()
