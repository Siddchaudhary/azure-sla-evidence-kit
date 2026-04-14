"""Data models for Azure SLA Report Generator."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ComplianceStatus(str, Enum):
    """SLA compliance status."""

    COMPLIANT = "COMPLIANT"
    BREACH = "BREACH"
    UNKNOWN = "UNKNOWN"


class ResourceRecord(BaseModel):
    """Normalized Azure resource record."""

    id: str = Field(..., description="Full Azure resource ID")
    name: str = Field(..., description="Resource name")
    type: str = Field(..., description="Resource type (e.g., Microsoft.Compute/virtualMachines)")
    subscription_id: str = Field(..., description="Subscription ID")
    resource_group: str = Field(..., description="Resource group name")
    location: str = Field(..., description="Azure region")
    tags: dict[str, str] = Field(default_factory=dict, description="Resource tags")
    sku: str | None = Field(default=None, description="SKU name if available")
    tier: str | None = Field(default=None, description="SKU tier if available")
    properties: dict[str, Any] = Field(
        default_factory=dict, description="Additional properties"
    )


class AvailabilityDataPoint(BaseModel):
    """Single availability data point."""

    timestamp: datetime
    available: bool
    value: float | None = None  # Raw metric value if applicable


class AvailabilityResult(BaseModel):
    """Availability metrics result for a resource."""

    resource_id: str
    resource_name: str
    resource_type: str
    start_time: datetime
    end_time: datetime
    total_minutes: float
    available_minutes: float
    down_minutes: float
    availability_percent: float
    data_points: list[AvailabilityDataPoint] = Field(default_factory=list)
    metric_source: str = Field(
        default="unknown", description="Source of availability data"
    )
    notes: list[str] = Field(default_factory=list, description="Collection notes/warnings")


class SLATarget(BaseModel):
    """SLA target for a resource type."""

    resource_type: str
    sla_percent: float
    conditions: dict[str, Any] = Field(default_factory=dict)
    source: str = Field(default="catalog", description="Where this SLA came from")


class ComplianceResult(BaseModel):
    """Compliance calculation result for a resource."""

    resource_id: str
    resource_name: str
    resource_type: str
    location: str
    subscription_id: str
    actual_availability: float
    sla_target: float
    status: ComplianceStatus
    gap: float = Field(description="Difference: actual - target (negative = breach)")
    availability_result: AvailabilityResult | None = None
    notes: list[str] = Field(default_factory=list)


class OutageRecord(BaseModel):
    """Record of a detected outage period."""

    resource_id: str
    resource_name: str
    start_time: datetime
    end_time: datetime
    duration_minutes: float
    severity: str = "unknown"


class ReportMetadata(BaseModel):
    """Metadata for generated reports."""

    generated_at: datetime = Field(default_factory=datetime.utcnow)
    start_time: datetime
    end_time: datetime
    subscriptions: list[str]
    total_resources: int
    compliant_count: int
    breach_count: int
    unknown_count: int
    tool_version: str = "0.1.0"
    disclaimers: list[str] = Field(default_factory=list)
