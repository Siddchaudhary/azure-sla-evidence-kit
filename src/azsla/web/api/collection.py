"""Collection API endpoints for triggering and monitoring data collection."""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from azsla.db.database import get_db
from azsla.db.repository import CollectionRunRepository, SubscriptionRepository
from azsla.web.scheduler import trigger_collection

router = APIRouter()


class CollectionRunResponse(BaseModel):
    """Collection run response model."""
    id: int
    started_at: datetime
    completed_at: Optional[datetime]
    status: str
    subscription_ids: Optional[str]
    start_time: Optional[datetime]
    end_time: Optional[datetime]
    resources_discovered: int
    metrics_collected: int
    errors: Optional[str]


class TriggerCollectionRequest(BaseModel):
    """Request to trigger a collection run."""
    subscription_ids: Optional[list[str]] = None
    start_date: Optional[str] = None  # YYYY-MM-DD
    end_date: Optional[str] = None    # YYYY-MM-DD
    lookback_days: int = 1


class CollectionStatus(BaseModel):
    """Current collection status."""
    is_running: bool
    current_run: Optional[CollectionRunResponse]
    next_scheduled: Optional[datetime]
    last_completed: Optional[CollectionRunResponse]


@router.get("/status", response_model=CollectionStatus)
async def get_collection_status(
    db: AsyncSession = Depends(get_db),
) -> CollectionStatus:
    """Get current collection status."""
    run_repo = CollectionRunRepository(db)

    running = await run_repo.get_running()
    recent = await run_repo.get_latest(limit=5)
    last_completed = next(
        (r for r in recent if r.status in ("completed", "failed")), None
    )

    return CollectionStatus(
        is_running=running is not None,
        current_run=_to_response(running) if running else None,
        next_scheduled=None,  # TODO: Get from scheduler
        last_completed=_to_response(last_completed) if last_completed else None,
    )


@router.post("/trigger", response_model=dict)
async def trigger_collection_run(
    request: TriggerCollectionRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Trigger a manual collection run."""
    run_repo = CollectionRunRepository(db)

    # Check if already running
    running = await run_repo.get_running()
    if running:
        raise HTTPException(
            status_code=409,
            detail="A collection is already running",
        )

    # Get subscription IDs
    subscription_ids = request.subscription_ids
    if not subscription_ids:
        sub_repo = SubscriptionRepository(db)
        subs = await sub_repo.get_all_active()
        subscription_ids = [s.id for s in subs]

    if not subscription_ids:
        raise HTTPException(
            status_code=400,
            detail="No subscriptions configured. Add subscriptions first.",
        )

    # Parse dates
    if request.start_date and request.end_date:
        start_time = datetime.fromisoformat(request.start_date)
        end_time = datetime.fromisoformat(request.end_date)
    else:
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(days=request.lookback_days)

    # Trigger collection in background
    background_tasks.add_task(
        trigger_collection,
        subscription_ids=subscription_ids,
        start_time=start_time,
        end_time=end_time,
    )

    return {
        "status": "triggered",
        "subscription_ids": subscription_ids,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
    }


@router.get("/runs", response_model=list[CollectionRunResponse])
async def list_collection_runs(
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
) -> list[CollectionRunResponse]:
    """List recent collection runs."""
    run_repo = CollectionRunRepository(db)

    runs = await run_repo.get_latest(limit=limit)
    return [_to_response(r) for r in runs]


def _to_response(run) -> CollectionRunResponse:
    """Convert CollectionRun to response model."""
    return CollectionRunResponse(
        id=run.id,
        started_at=run.started_at,
        completed_at=run.completed_at,
        status=run.status,
        subscription_ids=run.subscription_ids,
        start_time=run.start_time,
        end_time=run.end_time,
        resources_discovered=run.resources_discovered,
        metrics_collected=run.metrics_collected,
        errors=run.errors,
    )
