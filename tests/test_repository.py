"""Unit tests for the database repository module."""

import pytest
from datetime import datetime, timedelta

from azsla.db.repository import (
    ResourceRepository,
    MetricsRepository,
    SubscriptionRepository,
    CollectionRunRepository,
)
from azsla.db.models import Resource, AvailabilityMetric, Subscription


pytestmark = pytest.mark.asyncio


class TestResourceRepository:
    """Tests for ResourceRepository."""

    async def test_create_resource(self, test_session, sample_subscription):
        """Create resource should add to database."""
        repo = ResourceRepository(test_session)
        
        resource = await repo.upsert(
            id="/subscriptions/test-sub/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm1",
            name="vm1",
            resource_type="Microsoft.Compute/virtualMachines",
            subscription_id=sample_subscription.id,
            resource_group="rg",
            location="eastus",
            sku="Standard_D2s_v3",
        )
        
        assert resource.id is not None
        assert resource.name == "vm1"
        assert resource.is_active is True

    async def test_get_all_active(self, test_session, sample_resource):
        """Get all active should return only active resources."""
        repo = ResourceRepository(test_session)
        
        resources = await repo.get_all_active()
        assert len(resources) == 1
        assert resources[0].name == "test-vm"

    async def test_get_all_active_filter_by_type(self, test_session, sample_resource):
        """Get all active should filter by resource type."""
        repo = ResourceRepository(test_session)
        
        # Should find the VM
        resources = await repo.get_all_active(resource_type="Microsoft.Compute/virtualMachines")
        assert len(resources) == 1
        
        # Should not find non-existent type
        resources = await repo.get_all_active(resource_type="Microsoft.Web/sites")
        assert len(resources) == 0

    async def test_get_resource_types(self, test_session, sample_resource):
        """Get resource types should return distinct types."""
        repo = ResourceRepository(test_session)
        
        types = await repo.get_resource_types()
        assert len(types) == 1
        assert "Microsoft.Compute/virtualMachines" in types

    async def test_get_locations(self, test_session, sample_resource):
        """Get locations should return distinct locations."""
        repo = ResourceRepository(test_session)
        
        locations = await repo.get_locations()
        assert len(locations) == 1
        assert "eastus" in locations


class TestMetricsRepository:
    """Tests for MetricsRepository."""

    async def test_create_metric(self, test_session, sample_resource):
        """Create metric should add to database."""
        repo = MetricsRepository(test_session)
        now = datetime.utcnow()
        
        metric = await repo.create(
            resource_id=sample_resource.id,
            start_time=now - timedelta(days=1),
            end_time=now,
            availability_percent=99.95,
            total_minutes=1440,
            available_minutes=1439.28,
            sla_target=99.9,
            compliance_status="COMPLIANT",
            metric_source="test",
        )
        
        assert metric.id is not None
        assert metric.availability_percent == 99.95

    async def test_get_latest_for_all_resources(self, test_session, sample_metric):
        """Get latest should return most recent metric per resource."""
        repo = MetricsRepository(test_session)
        
        metrics = await repo.get_latest_for_all_resources()
        assert len(metrics) == 1
        assert metrics[0].availability_percent == pytest.approx(99.95, rel=0.01)

    async def test_get_compliance_summary(self, test_session, sample_metric):
        """Get compliance summary should return aggregated stats."""
        repo = MetricsRepository(test_session)
        now = datetime.utcnow()
        
        summary = await repo.get_compliance_summary(
            start_time=now - timedelta(days=7),
            end_time=now,
        )
        
        assert summary["total"] > 0
        assert "compliant" in summary
        assert "breach" in summary


class TestSubscriptionRepository:
    """Tests for SubscriptionRepository."""

    async def test_create_subscription(self, test_session):
        """Create subscription should add to database."""
        repo = SubscriptionRepository(test_session)
        
        subscription = await repo.upsert(
            id="new-sub-id",
            name="New Subscription",
        )
        
        assert subscription.id == "new-sub-id"
        assert subscription.name == "New Subscription"
        assert subscription.is_active is True

    async def test_get_all_active(self, test_session, sample_subscription):
        """Get all active should return only active subscriptions."""
        repo = SubscriptionRepository(test_session)
        
        subs = await repo.get_all_active()
        assert len(subs) == 1
        assert subs[0].id == "test-subscription-id"

    async def test_delete_subscription(self, test_session, sample_subscription):
        """Delete should set subscription as inactive."""
        repo = SubscriptionRepository(test_session)
        
        await repo.delete(sample_subscription.id)
        
        subs = await repo.get_all_active()
        assert len(subs) == 0


class TestCollectionRunRepository:
    """Tests for CollectionRunRepository."""

    async def test_create_collection_run(self, test_session):
        """Create collection run should add to database."""
        repo = CollectionRunRepository(test_session)
        now = datetime.utcnow()
        
        run = await repo.create(
            subscription_ids=["sub1", "sub2"],
            start_time=now - timedelta(days=1),
            end_time=now,
        )
        
        assert run.id is not None
        assert run.status == "running"
        assert run.subscription_ids == "sub1,sub2"

    async def test_complete_collection_run(self, test_session):
        """Complete should update run status and counts."""
        repo = CollectionRunRepository(test_session)
        now = datetime.utcnow()
        
        run = await repo.create(
            subscription_ids=["sub1"],
            start_time=now - timedelta(days=1),
            end_time=now,
        )
        
        await repo.complete(
            run_id=run.id,
            resources_discovered=10,
            metrics_collected=10,
        )
        
        # Refresh and check
        latest = await repo.get_latest(limit=1)
        assert len(latest) == 1
        assert latest[0].status == "completed"
        assert latest[0].resources_discovered == 10

    async def test_get_latest_runs(self, test_session, sample_collection_run):
        """Get latest should return recent runs."""
        repo = CollectionRunRepository(test_session)
        
        runs = await repo.get_latest(limit=10)
        assert len(runs) == 1
        assert runs[0].status == "completed"
