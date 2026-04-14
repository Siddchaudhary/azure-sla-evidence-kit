"""SQLAlchemy database models."""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    JSON,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all models."""
    pass


class Subscription(Base):
    """Azure subscription being monitored."""

    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    resources: Mapped[list["Resource"]] = relationship(
        "Resource", back_populates="subscription", cascade="all, delete-orphan"
    )


class Resource(Base):
    """Discovered Azure resource."""

    __tablename__ = "resources"

    id: Mapped[str] = mapped_column(String(500), primary_key=True)  # Full Azure resource ID
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    subscription_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("subscriptions.id"), nullable=False
    )
    resource_group: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    sku: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    tier: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    tags: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    properties: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    subscription: Mapped["Subscription"] = relationship(
        "Subscription", back_populates="resources"
    )
    metrics: Mapped[list["AvailabilityMetric"]] = relationship(
        "AvailabilityMetric", back_populates="resource", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_resources_sub_type", "subscription_id", "type"),
    )


class AvailabilityMetric(Base):
    """Collected availability metrics for a resource."""

    __tablename__ = "availability_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    resource_id: Mapped[str] = mapped_column(
        String(500), ForeignKey("resources.id"), nullable=False
    )
    collection_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("collection_runs.id"), nullable=False
    )

    # Time window for this metric
    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # Availability data
    total_minutes: Mapped[float] = mapped_column(Float, nullable=False)
    available_minutes: Mapped[float] = mapped_column(Float, nullable=False)
    down_minutes: Mapped[float] = mapped_column(Float, nullable=False)
    availability_percent: Mapped[float] = mapped_column(Float, nullable=False, index=True)

    # SLA comparison
    sla_target: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    compliance_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    gap: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Metadata
    metric_source: Mapped[str] = mapped_column(String(50), default="unknown")
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    data_points: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    resource: Mapped["Resource"] = relationship("Resource", back_populates="metrics")
    collection_run: Mapped["CollectionRun"] = relationship(
        "CollectionRun", back_populates="metrics"
    )

    __table_args__ = (
        Index("ix_metrics_resource_time", "resource_id", "start_time", "end_time"),
        Index("ix_metrics_time_range", "start_time", "end_time"),
    )


class CollectionRun(Base):
    """Record of a data collection run."""

    __tablename__ = "collection_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running, completed, failed
    
    # What was collected
    subscription_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # comma-separated
    start_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # Results summary
    resources_discovered: Mapped[int] = mapped_column(Integer, default=0)
    metrics_collected: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    metrics: Mapped[list["AvailabilityMetric"]] = relationship(
        "AvailabilityMetric", back_populates="collection_run"
    )


class SLAConfig(Base):
    """Custom SLA configuration overrides."""

    __tablename__ = "sla_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    resource_type: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    resource_id: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # For resource-specific overrides
    sla_percent: Mapped[float] = mapped_column(Float, nullable=False)
    conditions: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        Index("ix_sla_config_type_resource", "resource_type", "resource_id"),
    )


class DashboardCache(Base):
    """Pre-computed dashboard statistics for instant loading."""

    __tablename__ = "dashboard_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cache_key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    
    # Cached statistics
    total_resources: Mapped[int] = mapped_column(Integer, default=0)
    total_subscriptions: Mapped[int] = mapped_column(Integer, default=0)
    compliant_count: Mapped[int] = mapped_column(Integer, default=0)
    breach_count: Mapped[int] = mapped_column(Integer, default=0)
    unknown_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_availability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    
    # Resource breakdown (JSON for flexibility)
    resource_types: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    locations: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    subscription_breakdown: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    trend_data: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    
    # Breach details for quick display
    top_breaches: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    
    # Metadata
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    collection_run_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class SLAHistory(Base):
    """Historical SLA compliance snapshots for trend analysis."""

    __tablename__ = "sla_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_date: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    
    # Scope (optional - for per-subscription tracking)
    subscription_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    
    # Compliance counts
    total_resources: Mapped[int] = mapped_column(Integer, default=0)
    compliant_count: Mapped[int] = mapped_column(Integer, default=0)
    breach_count: Mapped[int] = mapped_column(Integer, default=0)
    unknown_count: Mapped[int] = mapped_column(Integer, default=0)
    
    # Availability metrics
    avg_availability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    min_availability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_availability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    
    # Compliance rate as percentage
    compliance_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    
    # Linked collection run
    collection_run_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    # Metadata
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_sla_history_date_sub", "snapshot_date", "subscription_id"),
    )
