"""Pytest configuration and shared fixtures."""

import asyncio
from datetime import datetime, timedelta
from typing import AsyncGenerator, Generator
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from azsla.db.database import Base, get_db
from azsla.db.models import Resource, AvailabilityMetric, Subscription, CollectionRun
from azsla.web.app import create_app


# Use in-memory SQLite for tests
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create an event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def test_engine():
    """Create a test database engine."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def test_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create a test database session."""
    async_session = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session() as session:
        yield session


@pytest_asyncio.fixture
async def app(test_session: AsyncSession):
    """Create a test FastAPI app with test database."""
    app = create_app()
    
    async def override_get_db():
        yield test_session
    
    app.dependency_overrides[get_db] = override_get_db
    return app


@pytest_asyncio.fixture
async def client(app) -> AsyncGenerator[AsyncClient, None]:
    """Create an async HTTP client for testing."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def sync_client(app) -> Generator[TestClient, None, None]:
    """Create a sync HTTP client for testing."""
    with TestClient(app) as client:
        yield client


@pytest_asyncio.fixture
async def sample_subscription(test_session: AsyncSession) -> Subscription:
    """Create a sample subscription for testing."""
    subscription = Subscription(
        id="test-subscription-id",
        name="Test Subscription",
        is_active=True,
    )
    test_session.add(subscription)
    await test_session.commit()
    await test_session.refresh(subscription)
    return subscription


@pytest_asyncio.fixture
async def sample_resource(test_session: AsyncSession, sample_subscription: Subscription) -> Resource:
    """Create a sample resource for testing."""
    resource = Resource(
        id="/subscriptions/test-subscription-id/resourceGroups/test-rg/providers/Microsoft.Compute/virtualMachines/test-vm",
        name="test-vm",
        type="Microsoft.Compute/virtualMachines",
        subscription_id=sample_subscription.id,
        resource_group="test-rg",
        location="eastus",
        sku="Standard_D2s_v3",
        is_active=True,
    )
    test_session.add(resource)
    await test_session.commit()
    await test_session.refresh(resource)
    return resource


@pytest_asyncio.fixture
async def sample_metric(test_session: AsyncSession, sample_resource: Resource) -> AvailabilityMetric:
    """Create a sample metric for testing."""
    now = datetime.utcnow()
    metric = AvailabilityMetric(
        resource_id=sample_resource.id,
        start_time=now - timedelta(days=1),
        end_time=now,
        availability_percent=99.95,
        total_minutes=1440,
        available_minutes=1439.28,
        sla_target=99.9,
        compliance_status="COMPLIANT",
        metric_source="VmAvailabilityMetric",
    )
    test_session.add(metric)
    await test_session.commit()
    await test_session.refresh(metric)
    return metric


@pytest_asyncio.fixture
async def sample_collection_run(test_session: AsyncSession, sample_subscription: Subscription) -> CollectionRun:
    """Create a sample collection run for testing."""
    now = datetime.utcnow()
    run = CollectionRun(
        subscription_ids=sample_subscription.id,
        start_time=now - timedelta(days=1),
        end_time=now,
        status="completed",
        resources_discovered=10,
        metrics_collected=10,
        started_at=now - timedelta(minutes=5),
        completed_at=now,
    )
    test_session.add(run)
    await test_session.commit()
    await test_session.refresh(run)
    return run


@pytest.fixture
def mock_azure_credential():
    """Mock Azure DefaultAzureCredential."""
    with patch("azure.identity.DefaultAzureCredential") as mock:
        mock.return_value = MagicMock()
        yield mock


@pytest.fixture
def sample_resource_record():
    """Create a sample ResourceRecord for testing."""
    from azsla.models import ResourceRecord
    return ResourceRecord(
        id="/subscriptions/test-sub/resourceGroups/test-rg/providers/Microsoft.Compute/virtualMachines/test-vm",
        name="test-vm",
        type="Microsoft.Compute/virtualMachines",
        subscription_id="test-sub",
        resource_group="test-rg",
        location="eastus",
        tags={"environment": "test"},
        sku="Standard_D2s_v3",
        tier="Standard",
        properties={},
    )
