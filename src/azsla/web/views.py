"""HTML views using Jinja2 templates."""

from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import APIRouter, Request, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from azsla.db.database import get_db
from azsla.db.repository import (
    ResourceRepository,
    MetricsRepository,
    SubscriptionRepository,
    CollectionRunRepository,
    DashboardCacheRepository,
    SLAHistoryRepository,
)
from azsla.web.app import get_templates, APP_VERSION
from azsla.web.app import get_templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    subscription_ids: Optional[List[str]] = Query(None, alias="sub"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Main dashboard view - uses cached data for instant loading."""
    templates = get_templates()
    sub_repo = SubscriptionRepository(db)
    cache_repo = DashboardCacheRepository(db)

    # Get all subscriptions for filter dropdown
    subscriptions = await sub_repo.get_all_active()
    subscriptions_json = [
        {"id": s.id, "name": s.name or f"Subscription {s.id[:8]}..."}
        for s in subscriptions
    ]
    
    # Filter subscription IDs (empty list or None means all subscriptions)
    selected_sub_ids = subscription_ids if subscription_ids else None
    
    # Parse dates
    if not end_date:
        end_dt = datetime.utcnow()
        end_date = end_dt.strftime("%Y-%m-%d")
    else:
        end_dt = datetime.fromisoformat(end_date)

    if not start_date:
        start_dt = end_dt - timedelta(days=30)
        start_date = start_dt.strftime("%Y-%m-%d")
    else:
        start_dt = datetime.fromisoformat(start_date)

    # Check if we can use cached data (no subscription filters = use cache)
    # Date filtering always uses last 30 days by default, so cache is valid
    use_cache = selected_sub_ids is None
    cached = await cache_repo.get("global") if use_cache else None
    
    if cached and cached.trend_data is not None:
        # Use pre-computed cached data - instant load!
        summary = await cache_repo.to_summary_dict(cached)
        breaches = cached.top_breaches or []
        trend_data = cached.trend_data or []
        total_resources = cached.total_resources
        resource_types = cached.resource_types or []
        locations = cached.locations or []
    else:
        # Compute on-the-fly (for filtered queries or no cache yet)
        resource_repo = ResourceRepository(db)
        metrics_repo = MetricsRepository(db)
        
        resources = await resource_repo.get_all_active(subscription_ids=selected_sub_ids)
        resource_types = await resource_repo.get_resource_types()
        locations = await resource_repo.get_locations()
        total_resources = len(resources)

        summary = await metrics_repo.get_compliance_summary(
            start_time=start_dt,
            end_time=end_dt,
            subscription_ids=selected_sub_ids,
        )

        latest_metrics = await metrics_repo.get_latest_for_all_resources(
            subscription_ids=selected_sub_ids
        )
        breaches = [m for m in latest_metrics if m.compliance_status == "BREACH"]
        
        # Get trend data from history repository
        history_repo = SLAHistoryRepository(db)
        trend_data = await history_repo.get_trend(days=30)

    # Get last refresh timestamp
    run_repo = CollectionRunRepository(db)
    latest_runs = await run_repo.get_latest(limit=1)
    last_refresh = latest_runs[0].completed_at if latest_runs and latest_runs[0].completed_at else None

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "subscriptions": subscriptions,
            "subscriptions_json": subscriptions_json,
            "selected_subscription_ids": selected_sub_ids or [],
            "start_date": start_date,
            "end_date": end_date,
            "total_resources": total_resources,
            "total_subscriptions": len(subscriptions),
            "resource_types": resource_types,
            "locations": locations,
            "summary": summary,
            "breaches": breaches,
            "trend_data": trend_data,
            "cached": cached is not None,  # Flag to show cache status
            "last_refresh": last_refresh,
            "version": APP_VERSION,
        },
    )


@router.get("/resources", response_class=HTMLResponse)
async def resources_view(
    request: Request,
    subscription_id: Optional[str] = Query(None),
    resource_type: Optional[str] = Query(None),
    location: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Resources list view."""
    templates = get_templates()
    resource_repo = ResourceRepository(db)
    metrics_repo = MetricsRepository(db)
    sub_repo = SubscriptionRepository(db)

    # Get filter options
    subscriptions = await sub_repo.get_all_active()
    resource_types = await resource_repo.get_resource_types()
    locations = await resource_repo.get_locations()

    # Get resources
    resources = await resource_repo.get_all_active(resource_type=resource_type)
    latest_metrics = await metrics_repo.get_latest_for_all_resources(
        subscription_id=subscription_id
    )
    metrics_map = {m.resource_id: m for m in latest_metrics}

    # Build resource list with metrics
    resource_list = []
    for r in resources:
        if subscription_id and r.subscription_id != subscription_id:
            continue
        if location and r.location != location:
            continue
        if search and search.lower() not in r.name.lower():
            continue

        metric = metrics_map.get(r.id)
        if status and metric and metric.compliance_status != status:
            continue
        if status and not metric:
            continue

        resource_list.append({
            "resource": r,
            "metric": metric,
        })

    # Pagination
    page_size = 20
    total = len(resource_list)
    total_pages = (total + page_size - 1) // page_size
    start = (page - 1) * page_size
    end = start + page_size
    paginated = resource_list[start:end]

    return templates.TemplateResponse(
        request,
        "resources.html",
        {
            "resources": paginated,
            "subscriptions": subscriptions,
            "resource_types": resource_types,
            "locations": locations,
            "selected_subscription": subscription_id,
            "selected_type": resource_type,
            "selected_location": location,
            "selected_status": status,
            "search": search,
            "page": page,
            "total_pages": total_pages,
            "total": total,
        },
    )


@router.get("/resource/{resource_id:path}", response_class=HTMLResponse)
async def resource_detail_view(
    request: Request,
    resource_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Resource detail view with metrics history."""
    templates = get_templates()
    resource_repo = ResourceRepository(db)
    metrics_repo = MetricsRepository(db)

    # Get resource
    resources = await resource_repo.get_all_active()
    resource = next((r for r in resources if r.id == resource_id), None)

    if not resource:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"error": "Resource not found"},
            status_code=404,
        )

    # Get metrics history
    metrics = await metrics_repo.get_for_resource(resource_id)

    return templates.TemplateResponse(
        request,
        "resource_detail.html",
        {
            "resource": resource,
            "metrics": metrics,
        },
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_view(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Settings view for managing subscriptions and collection."""
    templates = get_templates()
    sub_repo = SubscriptionRepository(db)
    run_repo = CollectionRunRepository(db)

    subscriptions = await sub_repo.get_all_active()
    recent_runs = await run_repo.get_latest(limit=10)

    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "subscriptions": subscriptions,
            "recent_runs": recent_runs,
        },
    )


@router.get("/api/resource/{resource_id:path}")
async def get_resource_api(
    resource_id: str,
    db: AsyncSession = Depends(get_db),
):
    """API endpoint for resource details (used by modal)."""
    resource_repo = ResourceRepository(db)
    metrics_repo = MetricsRepository(db)

    # Get resource
    resources = await resource_repo.get_all_active()
    resource = next((r for r in resources if r.id == resource_id), None)

    if not resource:
        return {"error": "Resource not found"}

    # Get metrics history
    metrics = await metrics_repo.get_for_resource(resource_id)

    return {
        "resource": {
            "id": resource.id,
            "name": resource.name,
            "type": resource.type,
            "location": resource.location,
            "resource_group": resource.resource_group,
            "subscription_id": resource.subscription_id,
        },
        "metrics": [
            {
                "id": m.id,
                "period_start": m.period_start.isoformat() if m.period_start else None,
                "period_end": m.period_end.isoformat() if m.period_end else None,
                "availability_percent": m.availability_percent,
                "sla_target": m.sla_target,
                "compliance_status": m.compliance_status,
            }
            for m in metrics
        ],
    }
