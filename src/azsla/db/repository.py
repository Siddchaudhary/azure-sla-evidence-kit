"""Database repository for CRUD operations."""

from datetime import datetime
from typing import Optional, Sequence, List

from sqlalchemy import select, and_, func, delete, Integer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from azsla.db.models import (
    Subscription,
    Resource,
    AvailabilityMetric,
    CollectionRun,
    SLAConfig,
    DashboardCache,
    SLAHistory,
)
from azsla.models import ResourceRecord, AvailabilityResult, ComplianceResult


class SubscriptionRepository:
    """Repository for subscription operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def upsert(self, subscription_id: str, name: Optional[str] = None) -> Subscription:
        """Insert or update a subscription."""
        result = await self.session.execute(
            select(Subscription).where(Subscription.id == subscription_id)
        )
        sub = result.scalar_one_or_none()

        if sub:
            sub.name = name or sub.name
            sub.updated_at = datetime.utcnow()
        else:
            sub = Subscription(id=subscription_id, name=name)
            self.session.add(sub)

        return sub

    async def get_all_active(self) -> Sequence[Subscription]:
        """Get all active subscriptions."""
        result = await self.session.execute(
            select(Subscription).where(Subscription.is_active == True)
        )
        return result.scalars().all()

    async def deactivate(self, subscription_id: str) -> None:
        """Deactivate a subscription."""
        result = await self.session.execute(
            select(Subscription).where(Subscription.id == subscription_id)
        )
        sub = result.scalar_one_or_none()
        if sub:
            sub.is_active = False


class ResourceRepository:
    """Repository for resource operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def upsert_from_record(self, record: ResourceRecord) -> Resource:
        """Insert or update a resource from a ResourceRecord."""
        result = await self.session.execute(
            select(Resource).where(Resource.id == record.id)
        )
        resource = result.scalar_one_or_none()

        now = datetime.utcnow()
        if resource:
            resource.name = record.name
            resource.type = record.type
            resource.location = record.location
            resource.sku = record.sku
            resource.tier = record.tier
            resource.tags = record.tags
            resource.properties = record.properties
            resource.is_active = True
            resource.last_seen = now
        else:
            resource = Resource(
                id=record.id,
                name=record.name,
                type=record.type,
                subscription_id=record.subscription_id,
                resource_group=record.resource_group,
                location=record.location,
                sku=record.sku,
                tier=record.tier,
                tags=record.tags,
                properties=record.properties,
                first_seen=now,
                last_seen=now,
            )
            self.session.add(resource)

        return resource

    async def get_by_subscription(
        self, subscription_id: str, resource_type: Optional[str] = None
    ) -> Sequence[Resource]:
        """Get resources by subscription, optionally filtered by type."""
        query = select(Resource).where(
            and_(
                Resource.subscription_id == subscription_id,
                Resource.is_active == True,
            )
        )
        if resource_type:
            query = query.where(Resource.type == resource_type)
        
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_all_active(
        self,
        resource_type: Optional[str] = None,
        subscription_ids: Optional[List[str]] = None,
    ) -> Sequence[Resource]:
        """Get all active resources, optionally filtered by subscription IDs."""
        query = select(Resource).where(Resource.is_active == True)
        if resource_type:
            query = query.where(Resource.type == resource_type)
        if subscription_ids:
            query = query.where(Resource.subscription_id.in_(subscription_ids))
        
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_resource_types(self) -> Sequence[str]:
        """Get distinct resource types."""
        result = await self.session.execute(
            select(Resource.type).distinct().where(Resource.is_active == True)
        )
        return result.scalars().all()

    async def get_locations(self) -> Sequence[str]:
        """Get distinct locations."""
        result = await self.session.execute(
            select(Resource.location).distinct().where(Resource.is_active == True)
        )
        return result.scalars().all()

    async def to_resource_record(self, resource: Resource) -> ResourceRecord:
        """Convert database Resource to ResourceRecord."""
        return ResourceRecord(
            id=resource.id,
            name=resource.name,
            type=resource.type,
            subscription_id=resource.subscription_id,
            resource_group=resource.resource_group,
            location=resource.location,
            sku=resource.sku,
            tier=resource.tier,
            tags=resource.tags or {},
            properties=resource.properties or {},
        )


class MetricsRepository:
    """Repository for metrics operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_availability(
        self,
        result: AvailabilityResult,
        compliance: ComplianceResult,
        collection_run_id: int,
    ) -> AvailabilityMetric:
        """Save availability result to database."""
        metric = AvailabilityMetric(
            resource_id=result.resource_id,
            collection_run_id=collection_run_id,
            start_time=result.start_time,
            end_time=result.end_time,
            total_minutes=result.total_minutes,
            available_minutes=result.available_minutes,
            down_minutes=result.down_minutes,
            availability_percent=result.availability_percent,
            sla_target=compliance.sla_target,
            compliance_status=compliance.status.value,
            gap=compliance.gap,
            metric_source=result.metric_source,
            notes="; ".join(result.notes) if result.notes else None,
            data_points=[dp.model_dump(mode="json") for dp in result.data_points] if result.data_points else None,
        )
        self.session.add(metric)
        return metric

    async def get_for_resource(
        self,
        resource_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Sequence[AvailabilityMetric]:
        """Get metrics for a resource within a time range."""
        query = select(AvailabilityMetric).where(
            AvailabilityMetric.resource_id == resource_id
        )
        if start_time:
            query = query.where(AvailabilityMetric.start_time >= start_time)
        if end_time:
            query = query.where(AvailabilityMetric.end_time <= end_time)
        
        query = query.order_by(AvailabilityMetric.start_time.desc())
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_latest_for_all_resources(
        self,
        subscription_ids: Optional[List[str]] = None,
        subscription_id: Optional[str] = None,  # Backward compatibility
    ) -> Sequence[AvailabilityMetric]:
        """Get the most recent metric for each resource."""
        # Handle backward compatibility - single subscription_id converts to list
        if subscription_id and not subscription_ids:
            subscription_ids = [subscription_id]
        
        # Subquery to get max collection_run_id per resource
        subquery = (
            select(
                AvailabilityMetric.resource_id,
                func.max(AvailabilityMetric.collection_run_id).label("max_run_id"),
            )
            .group_by(AvailabilityMetric.resource_id)
            .subquery()
        )

        query = (
            select(AvailabilityMetric)
            .join(
                subquery,
                and_(
                    AvailabilityMetric.resource_id == subquery.c.resource_id,
                    AvailabilityMetric.collection_run_id == subquery.c.max_run_id,
                ),
            )
            .options(selectinload(AvailabilityMetric.resource))
        )

        if subscription_ids:
            query = query.join(Resource).where(Resource.subscription_id.in_(subscription_ids))

        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_compliance_summary(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        subscription_ids: Optional[List[str]] = None,
        subscription_id: Optional[str] = None,  # Backward compatibility
    ) -> dict:
        """Get compliance summary statistics."""
        # Handle backward compatibility
        if subscription_id and not subscription_ids:
            subscription_ids = [subscription_id]
        
        query = select(
            func.count(AvailabilityMetric.id).label("total"),
            func.sum(
                func.cast(AvailabilityMetric.compliance_status == "COMPLIANT", Integer)
            ).label("compliant"),
            func.sum(
                func.cast(AvailabilityMetric.compliance_status == "BREACH", Integer)
            ).label("breach"),
            func.sum(
                func.cast(AvailabilityMetric.compliance_status == "UNKNOWN", Integer)
            ).label("unknown"),
            func.avg(AvailabilityMetric.availability_percent).label("avg_availability"),
        )

        if start_time:
            query = query.where(AvailabilityMetric.start_time >= start_time)
        if end_time:
            query = query.where(AvailabilityMetric.end_time <= end_time)
        if subscription_ids:
            query = query.join(Resource).where(Resource.subscription_id.in_(subscription_ids))

        result = await self.session.execute(query)
        row = result.one()

        return {
            "total": row.total or 0,
            "compliant": row.compliant or 0,
            "breach": row.breach or 0,
            "unknown": row.unknown or 0,
            "avg_availability": round(row.avg_availability, 4) if row.avg_availability else None,
        }

    async def get_availability_trend(
        self,
        resource_id: Optional[str] = None,
        days: int = 30,
        subscription_ids: Optional[List[str]] = None,
        subscription_id: Optional[str] = None,  # Backward compatibility
    ) -> Sequence[dict]:
        """Get daily availability trend."""
        # Handle backward compatibility
        if subscription_id and not subscription_ids:
            subscription_ids = [subscription_id]
        
        query = select(
            func.date(AvailabilityMetric.start_time).label("date"),
            func.avg(AvailabilityMetric.availability_percent).label("avg_availability"),
            func.count(AvailabilityMetric.id).label("resource_count"),
        ).group_by(func.date(AvailabilityMetric.start_time)).order_by(
            func.date(AvailabilityMetric.start_time)
        )

        if resource_id:
            query = query.where(AvailabilityMetric.resource_id == resource_id)
        
        if subscription_ids:
            query = query.join(Resource).where(Resource.subscription_id.in_(subscription_ids))

        result = await self.session.execute(query)
        return [
            {
                "date": str(row.date),
                "avg_availability": round(row.avg_availability, 4) if row.avg_availability else None,
                "resource_count": row.resource_count,
            }
            for row in result.all()
        ]


class CollectionRunRepository:
    """Repository for collection run operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        subscription_ids: list[str],
        start_time: datetime,
        end_time: datetime,
    ) -> CollectionRun:
        """Create a new collection run."""
        run = CollectionRun(
            subscription_ids=",".join(subscription_ids),
            start_time=start_time,
            end_time=end_time,
            status="running",
        )
        self.session.add(run)
        await self.session.flush()  # Get the ID
        return run

    async def complete(
        self,
        run_id: int,
        resources_discovered: int,
        metrics_collected: int,
        errors: Optional[str] = None,
    ) -> None:
        """Mark a collection run as completed."""
        result = await self.session.execute(
            select(CollectionRun).where(CollectionRun.id == run_id)
        )
        run = result.scalar_one_or_none()
        if run:
            run.status = "completed" if not errors else "failed"
            run.completed_at = datetime.utcnow()
            run.resources_discovered = resources_discovered
            run.metrics_collected = metrics_collected
            run.errors = errors

    async def get_latest(self, limit: int = 10) -> Sequence[CollectionRun]:
        """Get latest collection runs."""
        result = await self.session.execute(
            select(CollectionRun)
            .order_by(CollectionRun.started_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def get_running(self) -> Optional[CollectionRun]:
        """Get any currently running collection."""
        result = await self.session.execute(
            select(CollectionRun).where(CollectionRun.status == "running")
        )
        return result.scalar_one_or_none()


class DashboardCacheRepository:
    """Repository for pre-computed dashboard statistics."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, cache_key: str = "global") -> Optional[DashboardCache]:
        """Get cached dashboard stats."""
        result = await self.session.execute(
            select(DashboardCache).where(DashboardCache.cache_key == cache_key)
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        cache_key: str,
        total_resources: int,
        total_subscriptions: int,
        compliant_count: int,
        breach_count: int,
        unknown_count: int,
        avg_availability: Optional[float],
        resource_types: list[str],
        locations: list[str],
        subscription_breakdown: Optional[dict] = None,
        trend_data: Optional[list] = None,
        top_breaches: Optional[list] = None,
        collection_run_id: Optional[int] = None,
    ) -> DashboardCache:
        """Insert or update cached dashboard stats."""
        result = await self.session.execute(
            select(DashboardCache).where(DashboardCache.cache_key == cache_key)
        )
        cache = result.scalar_one_or_none()

        now = datetime.utcnow()
        if cache:
            cache.total_resources = total_resources
            cache.total_subscriptions = total_subscriptions
            cache.compliant_count = compliant_count
            cache.breach_count = breach_count
            cache.unknown_count = unknown_count
            cache.avg_availability = avg_availability
            cache.resource_types = resource_types
            cache.locations = locations
            cache.subscription_breakdown = subscription_breakdown
            cache.trend_data = trend_data
            cache.top_breaches = top_breaches
            cache.computed_at = now
            cache.collection_run_id = collection_run_id
        else:
            cache = DashboardCache(
                cache_key=cache_key,
                total_resources=total_resources,
                total_subscriptions=total_subscriptions,
                compliant_count=compliant_count,
                breach_count=breach_count,
                unknown_count=unknown_count,
                avg_availability=avg_availability,
                resource_types=resource_types,
                locations=locations,
                subscription_breakdown=subscription_breakdown,
                trend_data=trend_data,
                top_breaches=top_breaches,
                computed_at=now,
                collection_run_id=collection_run_id,
            )
            self.session.add(cache)

        return cache

    async def to_summary_dict(self, cache: DashboardCache) -> dict:
        """Convert cache to summary dictionary for template."""
        return {
            "total": cache.compliant_count + cache.breach_count + cache.unknown_count,
            "compliant": cache.compliant_count,
            "breach": cache.breach_count,
            "unknown": cache.unknown_count,
            "avg_availability": cache.avg_availability,
        }


class SLAHistoryRepository:
    """Repository for SLA history / trend operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_snapshot(
        self,
        snapshot_date: datetime,
        total_resources: int,
        compliant_count: int,
        breach_count: int,
        unknown_count: int,
        avg_availability: Optional[float],
        min_availability: Optional[float] = None,
        max_availability: Optional[float] = None,
        subscription_id: Optional[str] = None,
        collection_run_id: Optional[int] = None,
    ) -> "SLAHistory":
        """Create a new SLA history snapshot."""
        # Calculate compliance rate
        total_measured = compliant_count + breach_count
        compliance_rate = (compliant_count / total_measured * 100) if total_measured > 0 else None
        
        snapshot = SLAHistory(
            snapshot_date=snapshot_date,
            subscription_id=subscription_id,
            total_resources=total_resources,
            compliant_count=compliant_count,
            breach_count=breach_count,
            unknown_count=unknown_count,
            avg_availability=avg_availability,
            min_availability=min_availability,
            max_availability=max_availability,
            compliance_rate=compliance_rate,
            collection_run_id=collection_run_id,
        )
        self.session.add(snapshot)
        await self.session.flush()
        return snapshot

    async def get_trend(
        self,
        days: int = 30,
        subscription_id: Optional[str] = None,
    ) -> Sequence[dict]:
        """Get SLA trend data for the specified period."""
        cutoff = datetime.utcnow() - timedelta(days=days)
        
        query = (
            select(SLAHistory)
            .where(SLAHistory.snapshot_date >= cutoff)
            .order_by(SLAHistory.snapshot_date)
        )
        
        if subscription_id:
            query = query.where(SLAHistory.subscription_id == subscription_id)
        else:
            # Global snapshots (no subscription_id)
            query = query.where(SLAHistory.subscription_id.is_(None))
        
        result = await self.session.execute(query)
        snapshots = result.scalars().all()
        
        return [
            {
                "date": s.snapshot_date.strftime("%Y-%m-%d"),
                "total_resources": s.total_resources,
                "compliant": s.compliant_count,
                "breach": s.breach_count,
                "unknown": s.unknown_count,
                "avg_availability": round(s.avg_availability, 4) if s.avg_availability else None,
                "min_availability": round(s.min_availability, 4) if s.min_availability else None,
                "max_availability": round(s.max_availability, 4) if s.max_availability else None,
                "compliance_rate": round(s.compliance_rate, 2) if s.compliance_rate else None,
            }
            for s in snapshots
        ]

    async def get_latest(self, subscription_id: Optional[str] = None) -> Optional["SLAHistory"]:
        """Get the most recent snapshot."""
        query = (
            select(SLAHistory)
            .order_by(SLAHistory.snapshot_date.desc())
            .limit(1)
        )
        
        if subscription_id:
            query = query.where(SLAHistory.subscription_id == subscription_id)
        else:
            query = query.where(SLAHistory.subscription_id.is_(None))
        
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def cleanup_old(self, days_to_keep: int = 90) -> int:
        """Delete snapshots older than specified days."""
        cutoff = datetime.utcnow() - timedelta(days=days_to_keep)
        result = await self.session.execute(
            delete(SLAHistory).where(SLAHistory.snapshot_date < cutoff)
        )
        return result.rowcount
