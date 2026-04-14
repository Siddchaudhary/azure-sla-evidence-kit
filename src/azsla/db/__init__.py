"""Database module for Azure SLA Report Generator."""

from azsla.db.database import get_db, init_db, AsyncSessionLocal
from azsla.db.models import (
    Base,
    Subscription,
    Resource,
    AvailabilityMetric,
    CollectionRun,
    SLAConfig,
    DashboardCache,
    SLAHistory,
)

__all__ = [
    "get_db",
    "init_db",
    "AsyncSessionLocal",
    "Base",
    "Subscription",
    "Resource",
    "AvailabilityMetric",
    "CollectionRun",
    "SLAConfig",
    "DashboardCache",
    "SLAHistory",
]
