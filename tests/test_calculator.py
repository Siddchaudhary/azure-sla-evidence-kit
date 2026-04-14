"""Unit tests for the calculator module."""

import pytest
from datetime import datetime

from azsla.calculator import (
    calculate_availability_percent,
    calculate_downtime_minutes,
    compare_availability,
    calculate_compliance,
    detect_outages,
)
from azsla.models import (
    AvailabilityDataPoint,
    AvailabilityResult,
    ComplianceStatus,
    ResourceRecord,
)
from azsla.sla_catalog import SLACatalog


class TestCalculateAvailabilityPercent:
    """Tests for calculate_availability_percent function."""

    def test_100_percent_availability(self):
        """Full availability should return 100%."""
        result = calculate_availability_percent(43200, 43200)
        assert result == 100.0

    def test_99_percent_availability(self):
        """99% availability calculation."""
        result = calculate_availability_percent(42768, 43200)
        assert result == pytest.approx(99.0, rel=0.01)

    def test_zero_total_minutes(self):
        """Zero total minutes should return -1 (invalid)."""
        result = calculate_availability_percent(100, 0)
        assert result == -1.0

    def test_negative_total_minutes(self):
        """Negative total minutes should return -1."""
        result = calculate_availability_percent(100, -10)
        assert result == -1.0

    def test_negative_available_minutes(self):
        """Negative available minutes should return -1."""
        result = calculate_availability_percent(-100, 1000)
        assert result == -1.0

    def test_available_exceeds_total(self):
        """Available > total should cap at 100%."""
        result = calculate_availability_percent(50000, 43200)
        assert result == 100.0

    def test_precision(self):
        """Verify 4 decimal places precision."""
        result = calculate_availability_percent(43195, 43200)
        assert result == pytest.approx(99.9884, rel=0.0001)

    def test_sla_boundary_99_9(self):
        """Test the common 99.9% SLA boundary."""
        # 43200 minutes in 30 days
        # 99.9% of 43200 = 43156.8 available
        # Downtime allowed: 43.2 minutes
        available = 43200 - 43.2
        result = calculate_availability_percent(available, 43200)
        assert result == pytest.approx(99.9, rel=0.001)

    def test_sla_boundary_99_99(self):
        """Test the 99.99% SLA boundary."""
        # 99.99% allows 4.32 minutes downtime in 30 days
        available = 43200 - 4.32
        result = calculate_availability_percent(available, 43200)
        assert result == pytest.approx(99.99, rel=0.001)


class TestCalculateDowntimeMinutes:
    """Tests for calculate_downtime_minutes function."""

    def test_100_percent_no_downtime(self):
        """100% availability means no downtime."""
        result = calculate_downtime_minutes(100.0, 43200)
        assert result == 0.0

    def test_99_9_percent_downtime(self):
        """99.9% availability downtime calculation."""
        result = calculate_downtime_minutes(99.9, 43200)
        assert result == pytest.approx(43.2, rel=0.1)

    def test_negative_availability(self):
        """Negative availability should return 0."""
        result = calculate_downtime_minutes(-1, 43200)
        assert result == 0.0

    def test_zero_total_minutes(self):
        """Zero total minutes should return 0."""
        result = calculate_downtime_minutes(99.9, 0)
        assert result == 0.0


class TestCompareAvailability:
    """Tests for compare_availability function."""

    def test_compliant_exact_match(self):
        """Exactly meeting SLA is compliant."""
        status, gap = compare_availability(99.9, 99.9)
        assert status == ComplianceStatus.COMPLIANT
        assert gap == 0.0

    def test_compliant_exceeds_sla(self):
        """Exceeding SLA is compliant with positive gap."""
        status, gap = compare_availability(99.95, 99.9)
        assert status == ComplianceStatus.COMPLIANT
        assert gap == pytest.approx(0.05, rel=0.001)

    def test_breach_below_sla(self):
        """Below SLA is a breach with negative gap."""
        status, gap = compare_availability(99.8, 99.9)
        assert status == ComplianceStatus.BREACH
        assert gap == pytest.approx(-0.1, rel=0.001)

    def test_unknown_negative_actual(self):
        """Negative actual availability is unknown."""
        status, gap = compare_availability(-1, 99.9)
        assert status == ComplianceStatus.UNKNOWN
        assert gap == 0.0

    def test_unknown_negative_sla(self):
        """Negative SLA target is unknown."""
        status, gap = compare_availability(99.9, -1)
        assert status == ComplianceStatus.UNKNOWN
        assert gap == 0.0

    def test_barely_compliant(self):
        """Just barely meeting SLA."""
        status, gap = compare_availability(99.9001, 99.9)
        assert status == ComplianceStatus.COMPLIANT
        assert gap > 0

    def test_barely_breach(self):
        """Just barely missing SLA."""
        status, gap = compare_availability(99.8999, 99.9)
        assert status == ComplianceStatus.BREACH
        assert gap < 0


class TestDetectOutages:
    """Tests for detect_outages function."""

    def test_no_data_points(self):
        """No data points means no outages."""
        availability = AvailabilityResult(
            resource_id="test",
            resource_name="test",
            resource_type="test",
            start_time=datetime(2026, 3, 1),
            end_time=datetime(2026, 3, 2),
            total_minutes=1440,
            available_minutes=1440,
            down_minutes=0,
            availability_percent=100,
            data_points=[],
        )
        outages = detect_outages(availability)
        assert len(outages) == 0

    def test_single_outage(self):
        """Detect a single outage period."""
        data_points = [
            AvailabilityDataPoint(timestamp=datetime(2026, 3, 1, 0, 0), available=True, value=1.0),
            AvailabilityDataPoint(timestamp=datetime(2026, 3, 1, 0, 5), available=True, value=1.0),
            AvailabilityDataPoint(timestamp=datetime(2026, 3, 1, 0, 10), available=False, value=0.0),
            AvailabilityDataPoint(timestamp=datetime(2026, 3, 1, 0, 15), available=False, value=0.0),
            AvailabilityDataPoint(timestamp=datetime(2026, 3, 1, 0, 20), available=True, value=1.0),
        ]
        availability = AvailabilityResult(
            resource_id="test",
            resource_name="test",
            resource_type="test",
            start_time=datetime(2026, 3, 1),
            end_time=datetime(2026, 3, 2),
            total_minutes=1440,
            available_minutes=1430,
            down_minutes=10,
            availability_percent=99.31,
            data_points=data_points,
        )
        outages = detect_outages(availability)
        assert len(outages) == 1
        assert outages[0].duration_minutes == 10.0

    def test_multiple_outages(self):
        """Detect multiple outage periods."""
        data_points = [
            AvailabilityDataPoint(timestamp=datetime(2026, 3, 1, 0, 0), available=False, value=0.0),
            AvailabilityDataPoint(timestamp=datetime(2026, 3, 1, 0, 5), available=True, value=1.0),
            AvailabilityDataPoint(timestamp=datetime(2026, 3, 1, 0, 10), available=True, value=1.0),
            AvailabilityDataPoint(timestamp=datetime(2026, 3, 1, 0, 15), available=False, value=0.0),
            AvailabilityDataPoint(timestamp=datetime(2026, 3, 1, 0, 20), available=True, value=1.0),
        ]
        availability = AvailabilityResult(
            resource_id="test",
            resource_name="test",
            resource_type="test",
            start_time=datetime(2026, 3, 1),
            end_time=datetime(2026, 3, 2),
            total_minutes=1440,
            available_minutes=1425,
            down_minutes=15,
            availability_percent=98.96,
            data_points=data_points,
        )
        outages = detect_outages(availability)
        assert len(outages) == 2


class TestCalculateCompliance:
    """Integration tests for calculate_compliance."""

    @pytest.fixture
    def sample_resource(self) -> ResourceRecord:
        """Create a sample VM resource."""
        return ResourceRecord(
            id="/subscriptions/sub1/resourceGroups/rg1/providers/Microsoft.Compute/virtualMachines/vm1",
            name="vm1",
            type="Microsoft.Compute/virtualMachines",
            subscription_id="sub1",
            resource_group="rg1",
            location="eastus",
            sku="Standard_D2s_v3",
        )

    @pytest.fixture
    def sample_availability(self, sample_resource: ResourceRecord) -> AvailabilityResult:
        """Create sample availability result."""
        return AvailabilityResult(
            resource_id=sample_resource.id,
            resource_name=sample_resource.name,
            resource_type=sample_resource.type,
            start_time=datetime(2026, 3, 1),
            end_time=datetime(2026, 4, 1),
            total_minutes=44640,  # 31 days
            available_minutes=44600,
            down_minutes=40,
            availability_percent=99.91,
        )

    def test_compliant_vm(self, sample_resource: ResourceRecord, sample_availability: AvailabilityResult):
        """Test compliant VM against 99.9% SLA."""
        result = calculate_compliance(sample_resource, sample_availability)

        assert result.status == ComplianceStatus.COMPLIANT
        assert result.actual_availability == 99.91
        assert result.gap > 0

    def test_breach_vm(self, sample_resource: ResourceRecord):
        """Test VM breaching 99.9% SLA."""
        availability = AvailabilityResult(
            resource_id=sample_resource.id,
            resource_name=sample_resource.name,
            resource_type=sample_resource.type,
            start_time=datetime(2026, 3, 1),
            end_time=datetime(2026, 4, 1),
            total_minutes=44640,
            available_minutes=44500,
            down_minutes=140,
            availability_percent=99.69,
        )
        result = calculate_compliance(sample_resource, availability)

        assert result.status == ComplianceStatus.BREACH
        assert result.actual_availability == 99.69
        assert result.gap < 0

    def test_unknown_availability(self, sample_resource: ResourceRecord):
        """Test with unknown availability (-1)."""
        availability = AvailabilityResult(
            resource_id=sample_resource.id,
            resource_name=sample_resource.name,
            resource_type=sample_resource.type,
            start_time=datetime(2026, 3, 1),
            end_time=datetime(2026, 4, 1),
            total_minutes=44640,
            available_minutes=0,
            down_minutes=0,
            availability_percent=-1,
            notes=["Metrics unavailable"],
        )
        result = calculate_compliance(sample_resource, availability)

        assert result.status == ComplianceStatus.UNKNOWN
        assert "Metrics unavailable" in result.notes


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
