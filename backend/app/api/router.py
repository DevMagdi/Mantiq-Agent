"""API v1 router aggregation."""
from fastapi import APIRouter

from app.api.v1 import accounting, admin, channels, identity, intake, orders, rfq

api_router = APIRouter()
api_router.include_router(identity.router)
api_router.include_router(intake.router)
api_router.include_router(rfq.router)
api_router.include_router(orders.router)
api_router.include_router(accounting.router)
api_router.include_router(channels.router)
api_router.include_router(admin.router)
