"""Resources API endpoints."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from azsla.db.database import get_db
from azsla.db.repository import ResourceRepository, MetricsRepository

router = APIRouter()


class ResourceResponse(BaseModel):
    """Resource response model."""
    id: str
    name: str
    type: str
    type_short: str
    subscription_id: str
    resource_group: str
    location: str
    sku: Optional[str]
    tier: Optional[str]
    is_active: bool
    first_seen: datetime
    last_seen: datetime
    # Latest metrics
    availability_percent: Optional[float] = None
    sla_target: Optional[float] = None
    compliance_status: Optional[str] = None
    gap: Optional[float] = None


class ResourceListResponse(BaseModel):
    """Paginated resource list response."""
    items: list[ResourceResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


@router.get("", response_model=ResourceListResponse)
async def list_resources(
    subscription_id: Optional[str] = Query(None, description="Filter by subscription"),
    resource_type: Optional[str] = Query(None, description="Filter by resource type"),
    location: Optional[str] = Query(None, description="Filter by location"),
    compliance_status: Optional[str] = Query(None, description="Filter by compliance status"),
    search: Optional[str] = Query(None, description="Search by name"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
) -> ResourceListResponse:
    """List resources with filtering and pagination."""
    resource_repo = ResourceRepository(db)
    metrics_repo = MetricsRepository(db)

    # Get all resources (we'll filter in memory for now, can optimize with SQL later)
    resources = await resource_repo.get_all_active(resource_type=resource_type)

    # Get latest metrics
    latest_metrics = await metrics_repo.get_latest_for_all_resources(
        subscription_id=subscription_id
    )
    metrics_by_resource = {m.resource_id: m for m in latest_metrics}

    # Build response items with metrics
    items: list[ResourceResponse] = []
    for r in resources:
        # Apply filters
        if subscription_id and r.subscription_id != subscription_id:
            continue
        if location and r.location != location:
            continue
        if search and search.lower() not in r.name.lower():
            continue

        metric = metrics_by_resource.get(r.id)
        
        # Filter by compliance status
        if compliance_status:
            if not metric or metric.compliance_status != compliance_status:
                continue

        items.append(ResourceResponse(
            id=r.id,
            name=r.name,
            type=r.type,
            type_short=r.type.split("/")[-1],
            subscription_id=r.subscription_id,
            resource_group=r.resource_group,
            location=r.location,
            sku=r.sku,
            tier=r.tier,
            is_active=r.is_active,
            first_seen=r.first_seen,
            last_seen=r.last_seen,
            availability_percent=metric.availability_percent if metric else None,
            sla_target=metric.sla_target if metric else None,
            compliance_status=metric.compliance_status if metric else None,
            gap=metric.gap if metric else None,
        ))

    # Pagination
    total = len(items)
    total_pages = (total + page_size - 1) // page_size
    start = (page - 1) * page_size
    end = start + page_size
    paginated_items = items[start:end]

    return ResourceListResponse(
        items=paginated_items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/types")
async def get_resource_types(
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Get available resource types with counts."""
    resource_repo = ResourceRepository(db)
    resources = await resource_repo.get_all_active()

    type_counts: dict[str, int] = {}
    for r in resources:
        type_counts[r.type] = type_counts.get(r.type, 0) + 1

    return [
        {"type": t, "short_name": t.split("/")[-1], "count": c}
        for t, c in sorted(type_counts.items())
    ]


@router.get("/locations")
async def get_locations(
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Get available locations with counts."""
    resource_repo = ResourceRepository(db)
    resources = await resource_repo.get_all_active()

    location_counts: dict[str, int] = {}
    for r in resources:
        location_counts[r.location] = location_counts.get(r.location, 0) + 1

    return [
        {"location": loc, "count": c}
        for loc, c in sorted(location_counts.items())
    ]


@router.get("/{resource_id:path}", response_model=ResourceResponse)
async def get_resource(
    resource_id: str,
    db: AsyncSession = Depends(get_db),
) -> ResourceResponse:
    """Get a specific resource by ID."""
    resource_repo = ResourceRepository(db)
    metrics_repo = MetricsRepository(db)

    resources = await resource_repo.get_all_active()
    resource = next((r for r in resources if r.id == resource_id), None)

    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    # Get latest metric
    metrics = await metrics_repo.get_for_resource(resource_id)
    metric = metrics[0] if metrics else None

    return ResourceResponse(
        id=resource.id,
        name=resource.name,
        type=resource.type,
        type_short=resource.type.split("/")[-1],
        subscription_id=resource.subscription_id,
        resource_group=resource.resource_group,
        location=resource.location,
        sku=resource.sku,
        tier=resource.tier,
        is_active=resource.is_active,
        first_seen=resource.first_seen,
        last_seen=resource.last_seen,
        availability_percent=metric.availability_percent if metric else None,
        sla_target=metric.sla_target if metric else None,
        compliance_status=metric.compliance_status if metric else None,
        gap=metric.gap if metric else None,
    )
