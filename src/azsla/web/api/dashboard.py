"""Dashboard API endpoints."""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from azsla.db.database import get_db
from azsla.db.repository import MetricsRepository, ResourceRepository, SLAHistoryRepository

router = APIRouter()


class ComplianceSummary(BaseModel):
    """Compliance summary response."""
    total: int
    compliant: int
    breach: int
    unknown: int
    compliant_percent: float
    breach_percent: float
    unknown_percent: float
    avg_availability: Optional[float]


class DashboardStats(BaseModel):
    """Dashboard statistics response."""
    total_resources: int
    total_subscriptions: int
    resource_types: list[str]
    locations: list[str]
    compliance: ComplianceSummary
    last_collection: Optional[datetime]


class TrendDataPoint(BaseModel):
    """Single trend data point."""
    date: str
    avg_availability: Optional[float]
    min_availability: Optional[float] = None
    max_availability: Optional[float] = None
    compliance_rate: Optional[float] = None
    compliant: Optional[int] = None
    breach: Optional[int] = None
    total_resources: Optional[int] = None


@router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats(
    subscription_id: Optional[str] = Query(None, description="Filter by subscription"),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db),
) -> DashboardStats:
    """Get dashboard statistics."""
    resource_repo = ResourceRepository(db)
    metrics_repo = MetricsRepository(db)

    # Parse dates
    start_time = datetime.fromisoformat(start_date) if start_date else None
    end_time = datetime.fromisoformat(end_date) if end_date else None

    # Get resources
    resources = await resource_repo.get_all_active()
    resource_types = await resource_repo.get_resource_types()
    locations = await resource_repo.get_locations()

    # Get unique subscriptions
    subscriptions = set(r.subscription_id for r in resources)

    # Get compliance summary
    summary = await metrics_repo.get_compliance_summary(
        start_time=start_time,
        end_time=end_time,
        subscription_id=subscription_id,
    )

    total = summary["total"] or 1  # Avoid division by zero
    compliance_summary = ComplianceSummary(
        total=summary["total"],
        compliant=summary["compliant"],
        breach=summary["breach"],
        unknown=summary["unknown"],
        compliant_percent=round((summary["compliant"] / total) * 100, 1),
        breach_percent=round((summary["breach"] / total) * 100, 1),
        unknown_percent=round((summary["unknown"] / total) * 100, 1),
        avg_availability=summary["avg_availability"],
    )

    return DashboardStats(
        total_resources=len(resources),
        total_subscriptions=len(subscriptions),
        resource_types=list(resource_types),
        locations=list(locations),
        compliance=compliance_summary,
        last_collection=None,  # TODO: Add last collection time
    )


@router.get("/trend", response_model=list[TrendDataPoint])
async def get_availability_trend(
    days: int = Query(30, ge=1, le=365, description="Number of days"),
    subscription_id: Optional[str] = Query(None, description="Filter by subscription"),
    db: AsyncSession = Depends(get_db),
) -> list[TrendDataPoint]:
    """Get SLA trend data for charts from historical snapshots."""
    history_repo = SLAHistoryRepository(db)

    trend_data = await history_repo.get_trend(
        days=days,
        subscription_id=subscription_id,
    )

    return [TrendDataPoint(**d) for d in trend_data]


@router.get("/compliance-by-type")
async def get_compliance_by_type(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Get compliance breakdown by resource type."""
    metrics_repo = MetricsRepository(db)
    latest_metrics = await metrics_repo.get_latest_for_all_resources()

    # Group by resource type
    by_type: dict[str, dict] = {}
    for metric in latest_metrics:
        if not metric.resource:
            continue
        rtype = metric.resource.type.split("/")[-1]
        if rtype not in by_type:
            by_type[rtype] = {"type": rtype, "total": 0, "compliant": 0, "breach": 0, "unknown": 0}
        by_type[rtype]["total"] += 1
        status = (metric.compliance_status or "UNKNOWN").lower()
        if status in by_type[rtype]:
            by_type[rtype][status] += 1

    return list(by_type.values())


@router.get("/compliance-by-location")
async def get_compliance_by_location(
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Get compliance breakdown by Azure region."""
    metrics_repo = MetricsRepository(db)
    latest_metrics = await metrics_repo.get_latest_for_all_resources()

    # Group by location
    by_location: dict[str, dict] = {}
    for metric in latest_metrics:
        if not metric.resource:
            continue
        location = metric.resource.location
        if location not in by_location:
            by_location[location] = {"location": location, "total": 0, "compliant": 0, "breach": 0, "unknown": 0}
        by_location[location]["total"] += 1
        status = (metric.compliance_status or "UNKNOWN").lower()
        if status in by_location[location]:
            by_location[location][status] += 1

    return list(by_location.values())
