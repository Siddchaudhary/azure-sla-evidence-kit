"""Unit tests for the API endpoints."""

import pytest
from httpx import AsyncClient


pytestmark = pytest.mark.asyncio


class TestHealthEndpoints:
    """Tests for health check endpoints."""

    async def test_health_returns_200(self, client: AsyncClient):
        """Health endpoint should return 200 with status."""
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "version" in data

    async def test_ready_returns_200_when_db_connected(self, client: AsyncClient):
        """Ready endpoint should return 200 when database is connected."""
        response = await client.get("/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert data["database"] == "connected"


class TestDashboardAPI:
    """Tests for dashboard API endpoints."""

    async def test_dashboard_stats_empty_db(self, client: AsyncClient):
        """Dashboard stats should return zeros for empty database."""
        response = await client.get("/api/dashboard/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["total_resources"] == 0
        assert data["total_subscriptions"] == 0
        assert "compliance" in data

    async def test_dashboard_stats_with_data(
        self, client: AsyncClient, sample_resource, sample_metric
    ):
        """Dashboard stats should return correct counts with data."""
        response = await client.get("/api/dashboard/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["total_resources"] == 1
        assert data["total_subscriptions"] == 1

    async def test_dashboard_trend(self, client: AsyncClient):
        """Dashboard trend endpoint should return list."""
        response = await client.get("/api/dashboard/trend")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)


class TestResourcesAPI:
    """Tests for resources API endpoints."""

    async def test_list_resources_empty(self, client: AsyncClient):
        """List resources should return empty list for empty database."""
        response = await client.get("/api/resources")
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total"] == 0

    async def test_list_resources_with_data(self, client: AsyncClient, sample_resource, sample_metric):
        """List resources should return resources with metrics."""
        response = await client.get("/api/resources")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["name"] == "test-vm"

    async def test_list_resources_pagination(self, client: AsyncClient, sample_resource):
        """List resources should support pagination."""
        response = await client.get("/api/resources?page=1&page_size=10")
        assert response.status_code == 200
        data = response.json()
        assert "page" in data
        assert "page_size" in data
        assert "total_pages" in data

    async def test_get_resource_types(self, client: AsyncClient, sample_resource):
        """Get resource types should return list of types."""
        response = await client.get("/api/resources/types")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    async def test_get_resource_locations(self, client: AsyncClient, sample_resource):
        """Get resource locations should return list of locations."""
        response = await client.get("/api/resources/locations")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)


class TestSubscriptionsAPI:
    """Tests for subscriptions API endpoints."""

    async def test_list_subscriptions_empty(self, client: AsyncClient):
        """List subscriptions should return empty list for empty database."""
        response = await client.get("/api/subscriptions")
        assert response.status_code == 200
        data = response.json()
        assert data == []

    async def test_list_subscriptions_with_data(self, client: AsyncClient, sample_subscription):
        """List subscriptions should return subscriptions."""
        response = await client.get("/api/subscriptions")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == "test-subscription-id"
        assert data[0]["name"] == "Test Subscription"

    async def test_create_subscription(self, client: AsyncClient):
        """Create subscription should add new subscription."""
        response = await client.post(
            "/api/subscriptions",
            json={"id": "new-sub-id", "name": "New Subscription"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "new-sub-id"

    async def test_delete_subscription(self, client: AsyncClient, sample_subscription):
        """Delete subscription should remove subscription."""
        response = await client.delete(f"/api/subscriptions/{sample_subscription.id}")
        assert response.status_code == 200
        
        # Verify it's deleted
        response = await client.get("/api/subscriptions")
        data = response.json()
        assert len(data) == 0


class TestMetricsAPI:
    """Tests for metrics API endpoints."""

    async def test_get_latest_metrics_empty(self, client: AsyncClient):
        """Get latest metrics should return empty list for empty database."""
        response = await client.get("/api/metrics/latest")
        assert response.status_code == 200
        data = response.json()
        assert data == []

    async def test_get_latest_metrics_with_data(self, client: AsyncClient, sample_metric):
        """Get latest metrics should return metrics."""
        response = await client.get("/api/metrics/latest")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["availability_percent"] == pytest.approx(99.95, rel=0.01)

    async def test_get_breaches_empty(self, client: AsyncClient):
        """Get breaches should return empty list when no breaches."""
        response = await client.get("/api/metrics/breaches")
        assert response.status_code == 200
        data = response.json()
        assert data == []


class TestCollectionAPI:
    """Tests for collection API endpoints."""

    async def test_collection_status(self, client: AsyncClient):
        """Collection status should return current status."""
        response = await client.get("/api/collection/status")
        assert response.status_code == 200
        data = response.json()
        assert "is_running" in data
        assert "last_run" in data

    async def test_collection_runs_empty(self, client: AsyncClient):
        """Collection runs should return empty list for empty database."""
        response = await client.get("/api/collection/runs")
        assert response.status_code == 200
        data = response.json()
        assert data == []

    async def test_collection_runs_with_data(self, client: AsyncClient, sample_collection_run):
        """Collection runs should return run history."""
        response = await client.get("/api/collection/runs")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["status"] == "completed"


class TestExportAPI:
    """Tests for export API endpoints."""

    async def test_export_csv_empty(self, client: AsyncClient):
        """Export CSV should return valid CSV with headers for empty database."""
        response = await client.get("/api/export/csv")
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/csv; charset=utf-8"
        assert "attachment" in response.headers.get("content-disposition", "")
        
        # Check CSV has headers
        content = response.text
        assert "Resource Name" in content

    async def test_export_csv_with_data(self, client: AsyncClient, sample_resource, sample_metric):
        """Export CSV should include resource data."""
        response = await client.get("/api/export/csv")
        assert response.status_code == 200
        
        content = response.text
        assert "test-vm" in content
        assert "COMPLIANT" in content
