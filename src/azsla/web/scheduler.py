"""Background scheduler for periodic data collection."""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from azsla.db.database import AsyncSessionLocal
from azsla.db.repository import (
    SubscriptionRepository,
    ResourceRepository,
    MetricsRepository,
    CollectionRunRepository,
    DashboardCacheRepository,
    SLAHistoryRepository,
)
from azsla.discover import discover_resources
from azsla.metrics import collect_metrics
from azsla.calculator import batch_calculate_compliance
from azsla.sla_catalog import get_catalog
from azsla.web.config import get_settings

logger = logging.getLogger(__name__)

# Global scheduler instance
_scheduler: Optional[AsyncIOScheduler] = None


async def start_scheduler() -> None:
    """Start the background scheduler."""
    global _scheduler

    settings = get_settings()

    _scheduler = AsyncIOScheduler()

    # Add periodic collection job - runs immediately on startup then every N hours
    _scheduler.add_job(
        scheduled_collection,
        trigger=IntervalTrigger(hours=settings.collection_interval_hours),
        id="periodic_collection",
        name="Periodic SLA Data Collection",
        replace_existing=True,
        next_run_time=datetime.utcnow(),  # Run immediately on startup
    )

    _scheduler.start()
    logger.info(
        f"Scheduler started. Collection will run immediately, then every {settings.collection_interval_hours} hours"
    )


async def stop_scheduler() -> None:
    """Stop the background scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler stopped")


async def scheduled_collection() -> None:
    """Scheduled collection job - runs periodically."""
    settings = get_settings()

    logger.info("Starting scheduled collection...")

    async with AsyncSessionLocal() as session:
        sub_repo = SubscriptionRepository(session)
        subscriptions = await sub_repo.get_all_active()

        if not subscriptions:
            logger.warning("No active subscriptions configured. Skipping collection.")
            return

        subscription_ids = [s.id for s in subscriptions]
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(days=settings.collection_lookback_days)

        await session.commit()

    await trigger_collection(
        subscription_ids=subscription_ids,
        start_time=start_time,
        end_time=end_time,
    )


async def trigger_collection(
    subscription_ids: list[str],
    start_time: datetime,
    end_time: datetime,
) -> None:
    """
    Trigger a data collection run.

    This can be called from the scheduler or manually via API.
    """
    logger.info(
        f"Starting collection for {len(subscription_ids)} subscriptions "
        f"from {start_time} to {end_time}"
    )

    async with AsyncSessionLocal() as session:
        run_repo = CollectionRunRepository(session)
        sub_repo = SubscriptionRepository(session)
        resource_repo = ResourceRepository(session)
        metrics_repo = MetricsRepository(session)

        # Create collection run record
        run = await run_repo.create(subscription_ids, start_time, end_time)
        await session.commit()
        run_id = run.id

        errors: list[str] = []
        resources_discovered = 0
        metrics_collected = 0

        try:
            # Ensure subscriptions exist in DB
            for sub_id in subscription_ids:
                await sub_repo.upsert(sub_id)
            await session.commit()

            # Discover resources
            logger.info("Discovering resources...")
            try:
                discovered = discover_resources(subscription_ids)
                resources_discovered = len(discovered)
                logger.info(f"Discovered {resources_discovered} resources")

                # Save resources to database
                for record in discovered:
                    await resource_repo.upsert_from_record(record)
                await session.commit()

            except Exception as e:
                logger.error(f"Discovery failed: {e}")
                errors.append(f"Discovery error: {e}")

            # Collect metrics
            if resources_discovered > 0:
                logger.info("Collecting metrics...")
                try:
                    # Get resources from DB to ensure we have the latest
                    db_resources = await resource_repo.get_all_active()
                    resource_records = [
                        await resource_repo.to_resource_record(r) for r in db_resources
                    ]

                    availability_results = collect_metrics(
                        resource_records, start_time, end_time
                    )
                    logger.info(f"Collected metrics for {len(availability_results)} resources")

                    # Calculate compliance
                    catalog = get_catalog()
                    compliance_results = batch_calculate_compliance(
                        resource_records, availability_results, catalog
                    )

                    # Save metrics to database
                    compliance_map = {c.resource_id: c for c in compliance_results}
                    for result in availability_results:
                        compliance = compliance_map.get(result.resource_id)
                        if compliance:
                            await metrics_repo.save_availability(result, compliance, run_id)
                            metrics_collected += 1

                    await session.commit()
                    logger.info(f"Saved {metrics_collected} metrics")

                except Exception as e:
                    logger.error(f"Metrics collection failed: {e}")
                    errors.append(f"Metrics error: {e}")

            # Complete the run
            await run_repo.complete(
                run_id=run_id,
                resources_discovered=resources_discovered,
                metrics_collected=metrics_collected,
                errors="; ".join(errors) if errors else None,
            )
            await session.commit()

            logger.info(
                f"Collection completed: {resources_discovered} resources, "
                f"{metrics_collected} metrics, {len(errors)} errors"
            )

            # Pre-compute dashboard cache for instant loading
            try:
                await compute_and_cache_dashboard(session, run_id)
                await session.commit()
                logger.info("Dashboard cache updated successfully")
            except Exception as cache_err:
                logger.warning(f"Dashboard cache update failed: {cache_err}")

            # Backup database to blob storage for persistence
            try:
                from azsla.web.db_backup import backup_to_blob
                await backup_to_blob()
            except Exception as backup_err:
                logger.warning(f"Blob backup skipped: {backup_err}")

        except Exception as e:
            logger.error(f"Collection run failed: {e}")
            await run_repo.complete(
                run_id=run_id,
                resources_discovered=resources_discovered,
                metrics_collected=metrics_collected,
                errors=str(e),
            )
            await session.commit()
            raise


async def compute_and_cache_dashboard(session, collection_run_id: int) -> None:
    """
    Compute dashboard statistics and cache them for instant loading.
    
    Called after each collection run to pre-compute all dashboard data.
    Also creates a historical snapshot for trend analysis.
    """
    resource_repo = ResourceRepository(session)
    metrics_repo = MetricsRepository(session)
    sub_repo = SubscriptionRepository(session)
    cache_repo = DashboardCacheRepository(session)
    history_repo = SLAHistoryRepository(session)
    
    logger.info("Computing dashboard cache...")
    
    # Get all resources and subscriptions
    resources = await resource_repo.get_all_active()
    subscriptions = await sub_repo.get_all_active()
    resource_types = await resource_repo.get_resource_types()
    locations = await resource_repo.get_locations()
    
    # Get compliance summary (last 30 days)
    from datetime import timedelta
    end_dt = datetime.utcnow()
    start_dt = end_dt - timedelta(days=30)
    
    summary = await metrics_repo.get_compliance_summary(
        start_time=start_dt,
        end_time=end_dt,
    )
    
    # Get trend data from history
    trend_data = await history_repo.get_trend(days=30)
    
    # Get latest breaches for quick display
    latest_metrics = await metrics_repo.get_latest_for_all_resources()
    breaches = [m for m in latest_metrics if m.compliance_status == "BREACH"]
    
    # Calculate min/max availability for history snapshot
    availability_values = [m.availability_percent for m in latest_metrics if m.availability_percent is not None]
    min_availability = min(availability_values) if availability_values else None
    max_availability = max(availability_values) if availability_values else None
    
    # Create history snapshot for trend tracking
    try:
        await history_repo.create_snapshot(
            snapshot_date=end_dt,
            total_resources=len(resources),
            compliant_count=summary["compliant"],
            breach_count=summary["breach"],
            unknown_count=summary["unknown"],
            avg_availability=summary["avg_availability"],
            min_availability=min_availability,
            max_availability=max_availability,
            subscription_id=None,  # Global snapshot
            collection_run_id=collection_run_id,
        )
        logger.info("SLA history snapshot created")
    except Exception as hist_err:
        logger.warning(f"Failed to create history snapshot: {hist_err}")
    
    # Serialize breaches for JSON storage
    top_breaches = []
    for m in breaches[:10]:  # Top 10 breaches
        resource = await session.get(Resource, m.resource_id)
        top_breaches.append({
            "resource_id": m.resource_id,
            "resource_name": resource.name if resource else m.resource_id.split("/")[-1],
            "resource_type": resource.type.split("/")[-1] if resource else "Unknown",
            "availability_percent": round(m.availability_percent, 2),
            "sla_target": round(m.sla_target, 2) if m.sla_target else 99.9,
            "gap": round(m.gap, 4) if m.gap else 0,
        })
    
    # Get updated trend data (including the new snapshot)
    trend_data = await history_repo.get_trend(days=30)
    
    # Store in cache
    await cache_repo.upsert(
        cache_key="global",  # Global dashboard cache
        total_resources=len(resources),
        total_subscriptions=len(subscriptions),
        compliant_count=summary["compliant"],
        breach_count=summary["breach"],
        unknown_count=summary["unknown"],
        avg_availability=summary["avg_availability"],
        resource_types=list(resource_types),
        locations=list(locations),
        subscription_breakdown=None,  # Could add per-subscription stats later
        trend_data=trend_data,
        top_breaches=top_breaches,
        collection_run_id=collection_run_id,
    )
    
    logger.info(
        f"Dashboard cache computed: {len(resources)} resources, "
        f"{summary['compliant']} compliant, {summary['breach']} breaches"
    )


# Need Resource import for breach serialization
from azsla.db.models import Resource
