"""API routes package."""

from fastapi import APIRouter

from azsla.web.api.dashboard import router as dashboard_router
from azsla.web.api.resources import router as resources_router
from azsla.web.api.metrics import router as metrics_router
from azsla.web.api.subscriptions import router as subscriptions_router
from azsla.web.api.collection import router as collection_router
from azsla.web.api.export import router as export_router

router = APIRouter()

router.include_router(dashboard_router, prefix="/dashboard", tags=["Dashboard"])
router.include_router(resources_router, prefix="/resources", tags=["Resources"])
router.include_router(metrics_router, prefix="/metrics", tags=["Metrics"])
router.include_router(subscriptions_router, prefix="/subscriptions", tags=["Subscriptions"])
router.include_router(collection_router, prefix="/collection", tags=["Collection"])
router.include_router(export_router, prefix="/export", tags=["Export"])
