"""SLA compliance calculator."""

import logging
from datetime import datetime

from azsla.models import (
    AvailabilityResult,
    ComplianceResult,
    ComplianceStatus,
    OutageRecord,
    ResourceRecord,
)
from azsla.sla_catalog import SLACatalog, get_catalog

logger = logging.getLogger(__name__)


def calculate_availability_percent(
    available_minutes: float,
    total_minutes: float,
) -> float:
    """
    Calculate availability percentage.

    Args:
        available_minutes: Minutes the resource was available
        total_minutes: Total minutes in the time window

    Returns:
        Availability percentage (0-100), or -1 if invalid

    Examples:
        >>> calculate_availability_percent(43200, 43200)
        100.0
        >>> calculate_availability_percent(43000, 43200)
        99.5370...
        >>> calculate_availability_percent(0, 0)
        -1
    """
    if total_minutes <= 0:
        return -1.0

    if available_minutes < 0:
        return -1.0

    if available_minutes > total_minutes:
        # Cap at 100% (data anomaly)
        available_minutes = total_minutes

    return round((available_minutes / total_minutes) * 100, 4)


def calculate_downtime_minutes(
    availability_percent: float,
    total_minutes: float,
) -> float:
    """
    Calculate downtime in minutes from availability percentage.

    Args:
        availability_percent: Availability as percentage (0-100)
        total_minutes: Total minutes in the time window

    Returns:
        Downtime in minutes
    """
    if availability_percent < 0 or total_minutes <= 0:
        return 0.0

    uptime_fraction = availability_percent / 100
    return round(total_minutes * (1 - uptime_fraction), 2)


def compare_availability(
    actual_percent: float,
    sla_percent: float,
) -> tuple[ComplianceStatus, float]:
    """
    Compare actual availability against SLA target.

    Args:
        actual_percent: Measured availability percentage
        sla_percent: SLA target percentage

    Returns:
        Tuple of (ComplianceStatus, gap)
        Gap is positive if exceeding SLA, negative if breaching
    """
    if actual_percent < 0 or sla_percent < 0:
        return ComplianceStatus.UNKNOWN, 0.0

    gap = round(actual_percent - sla_percent, 4)

    if actual_percent >= sla_percent:
        return ComplianceStatus.COMPLIANT, gap
    else:
        return ComplianceStatus.BREACH, gap


def calculate_compliance(
    resource: ResourceRecord,
    availability: AvailabilityResult,
    catalog: SLACatalog | None = None,
) -> ComplianceResult:
    """
    Calculate compliance status for a resource.

    Args:
        resource: The resource record
        availability: Availability metrics result
        catalog: SLA catalog (uses default if not provided)

    Returns:
        ComplianceResult with status and details
    """
    if catalog is None:
        catalog = get_catalog()

    sla_target = catalog.get_sla(resource)
    notes: list[str] = []

    # Handle unknown availability
    if availability.availability_percent < 0:
        return ComplianceResult(
            resource_id=resource.id,
            resource_name=resource.name,
            resource_type=resource.type,
            location=resource.location,
            subscription_id=resource.subscription_id,
            actual_availability=-1,
            sla_target=sla_target.sla_percent,
            status=ComplianceStatus.UNKNOWN,
            gap=0,
            availability_result=availability,
            notes=availability.notes + [f"SLA source: {sla_target.source}"],
        )

    # Handle unknown SLA
    if sla_target.sla_percent < 0:
        notes.append("No SLA defined for this resource type")
        return ComplianceResult(
            resource_id=resource.id,
            resource_name=resource.name,
            resource_type=resource.type,
            location=resource.location,
            subscription_id=resource.subscription_id,
            actual_availability=availability.availability_percent,
            sla_target=-1,
            status=ComplianceStatus.UNKNOWN,
            gap=0,
            availability_result=availability,
            notes=notes,
        )

    # Compare availability vs SLA
    status, gap = compare_availability(
        availability.availability_percent,
        sla_target.sla_percent,
    )

    notes.append(f"SLA source: {sla_target.source}")
    if sla_target.conditions:
        notes.append(f"SLA conditions: {sla_target.conditions}")

    return ComplianceResult(
        resource_id=resource.id,
        resource_name=resource.name,
        resource_type=resource.type,
        location=resource.location,
        subscription_id=resource.subscription_id,
        actual_availability=availability.availability_percent,
        sla_target=sla_target.sla_percent,
        status=status,
        gap=gap,
        availability_result=availability,
        notes=notes,
    )


def detect_outages(
    availability: AvailabilityResult,
    threshold: float = 0.5,
) -> list[OutageRecord]:
    """
    Detect outage periods from availability data points.

    Args:
        availability: Availability result with data points
        threshold: Availability value below which is considered "down"

    Returns:
        List of OutageRecord for detected outages
    """
    if not availability.data_points:
        return []

    outages: list[OutageRecord] = []
    current_outage_start: datetime | None = None

    sorted_points = sorted(availability.data_points, key=lambda dp: dp.timestamp)

    for i, dp in enumerate(sorted_points):
        if not dp.available or (dp.value is not None and dp.value < threshold):
            if current_outage_start is None:
                current_outage_start = dp.timestamp
        else:
            if current_outage_start is not None:
                # End of outage
                outage_end = dp.timestamp
                duration = (outage_end - current_outage_start).total_seconds() / 60
                outages.append(
                    OutageRecord(
                        resource_id=availability.resource_id,
                        resource_name=availability.resource_name,
                        start_time=current_outage_start,
                        end_time=outage_end,
                        duration_minutes=duration,
                    )
                )
                current_outage_start = None

    # Handle ongoing outage at end of data
    if current_outage_start is not None and sorted_points:
        outages.append(
            OutageRecord(
                resource_id=availability.resource_id,
                resource_name=availability.resource_name,
                start_time=current_outage_start,
                end_time=sorted_points[-1].timestamp,
                duration_minutes=(
                    sorted_points[-1].timestamp - current_outage_start
                ).total_seconds() / 60,
                severity="ongoing",
            )
        )

    return outages


def batch_calculate_compliance(
    resources: list[ResourceRecord],
    availability_results: list[AvailabilityResult],
    catalog: SLACatalog | None = None,
) -> list[ComplianceResult]:
    """
    Calculate compliance for multiple resources.

    Args:
        resources: List of resources
        availability_results: Availability results (matched by resource_id)
        catalog: SLA catalog

    Returns:
        List of ComplianceResult
    """
    if catalog is None:
        catalog = get_catalog()

    # Create lookup by resource ID
    availability_map = {ar.resource_id: ar for ar in availability_results}

    results: list[ComplianceResult] = []
    for resource in resources:
        availability = availability_map.get(resource.id)
        if availability is None:
            logger.warning(f"No availability data for resource: {resource.id}")
            # Create unknown result
            results.append(
                ComplianceResult(
                    resource_id=resource.id,
                    resource_name=resource.name,
                    resource_type=resource.type,
                    location=resource.location,
                    subscription_id=resource.subscription_id,
                    actual_availability=-1,
                    sla_target=catalog.get_sla(resource).sla_percent,
                    status=ComplianceStatus.UNKNOWN,
                    gap=0,
                    notes=["No availability data collected"],
                )
            )
        else:
            results.append(calculate_compliance(resource, availability, catalog))

    return results
