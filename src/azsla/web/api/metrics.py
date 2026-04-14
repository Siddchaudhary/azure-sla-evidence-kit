"""Metrics API endpoints."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from azsla.db.database import get_db
from azsla.db.repository import MetricsRepository

router = APIRouter()


class MetricResponse(BaseModel):
    """Metric response model."""
    id: int
    resource_id: str
    start_time: datetime
    end_time: datetime
    total_minutes: float
    available_minutes: float
    down_minutes: float
    availability_percent: float
    sla_target: Optional[float]
    compliance_status: Optional[str]
    gap: Optional[float]
    metric_source: str
    notes: Optional[str]
    collected_at: datetime


class MetricDetailResponse(MetricResponse):
    """Detailed metric response with data points."""
    data_points: Optional[list[dict]]


@router.get("/resource/{resource_id:path}", response_model=list[MetricResponse])
async def get_metrics_for_resource(
    resource_id: str,
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum results"),
    db: AsyncSession = Depends(get_db),
) -> list[MetricResponse]:
    """Get metrics history for a specific resource."""
    metrics_repo = MetricsRepository(db)

    start_time = datetime.fromisoformat(start_date) if start_date else None
    end_time = datetime.fromisoformat(end_date) if end_date else None

    metrics = await metrics_repo.get_for_resource(
        resource_id=resource_id,
        start_time=start_time,
        end_time=end_time,
    )

    return [
        MetricResponse(
            id=m.id,
            resource_id=m.resource_id,
            start_time=m.start_time,
            end_time=m.end_time,
            total_minutes=m.total_minutes,
            available_minutes=m.available_minutes,
            down_minutes=m.down_minutes,
            availability_percent=m.availability_percent,
            sla_target=m.sla_target,
            compliance_status=m.compliance_status,
            gap=m.gap,
            metric_source=m.metric_source,
            notes=m.notes,
            collected_at=m.collected_at,
        )
        for m in metrics[:limit]
    ]


@router.get("/latest", response_model=list[MetricResponse])
async def get_latest_metrics(
    subscription_id: Optional[str] = Query(None, description="Filter by subscription"),
    compliance_status: Optional[str] = Query(None, description="Filter by status"),
    db: AsyncSession = Depends(get_db),
) -> list[MetricResponse]:
    """Get the latest metrics for all resources."""
    metrics_repo = MetricsRepository(db)

    metrics = await metrics_repo.get_latest_for_all_resources(
        subscription_id=subscription_id
    )

    results = []
    for m in metrics:
        if compliance_status and m.compliance_status != compliance_status:
            continue
        results.append(MetricResponse(
            id=m.id,
            resource_id=m.resource_id,
            start_time=m.start_time,
            end_time=m.end_time,
            total_minutes=m.total_minutes,
            available_minutes=m.available_minutes,
            down_minutes=m.down_minutes,
            availability_percent=m.availability_percent,
            sla_target=m.sla_target,
            compliance_status=m.compliance_status,
            gap=m.gap,
            metric_source=m.metric_source,
            notes=m.notes,
            collected_at=m.collected_at,
        ))

    return results


@router.get("/breaches", response_model=list[MetricResponse])
async def get_sla_breaches(
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db),
) -> list[MetricResponse]:
    """Get all SLA breaches."""
    metrics_repo = MetricsRepository(db)

    metrics = await metrics_repo.get_latest_for_all_resources()

    return [
        MetricResponse(
            id=m.id,
            resource_id=m.resource_id,
            start_time=m.start_time,
            end_time=m.end_time,
            total_minutes=m.total_minutes,
            available_minutes=m.available_minutes,
            down_minutes=m.down_minutes,
            availability_percent=m.availability_percent,
            sla_target=m.sla_target,
            compliance_status=m.compliance_status,
            gap=m.gap,
            metric_source=m.metric_source,
            notes=m.notes,
            collected_at=m.collected_at,
        )
        for m in metrics
        if m.compliance_status == "BREACH"
    ]


@router.get("/summary")
async def get_metrics_summary(
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    subscription_id: Optional[str] = Query(None, description="Filter by subscription"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get aggregated metrics summary."""
    metrics_repo = MetricsRepository(db)

    start_time = datetime.fromisoformat(start_date) if start_date else None
    end_time = datetime.fromisoformat(end_date) if end_date else None

    summary = await metrics_repo.get_compliance_summary(
        start_time=start_time,
        end_time=end_time,
        subscription_id=subscription_id,
    )

    # Calculate additional stats
    total = summary["total"] or 1
    return {
        **summary,
        "compliance_rate": round((summary["compliant"] / total) * 100, 2),
        "breach_rate": round((summary["breach"] / total) * 100, 2),
        "unknown_rate": round((summary["unknown"] / total) * 100, 2),
    }
