"""Metrics collection from Azure Monitor."""

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Any

from azure.identity import DefaultAzureCredential
from azure.mgmt.monitor import MonitorManagementClient

from azsla.models import AvailabilityDataPoint, AvailabilityResult, ResourceRecord

logger = logging.getLogger(__name__)


class MetricsCollector(ABC):
    """Abstract base class for resource-specific metrics collectors."""

    @property
    @abstractmethod
    def supported_types(self) -> list[str]:
        """Resource types this collector handles."""
        pass

    @abstractmethod
    def collect(
        self,
        resource: ResourceRecord,
        start_time: datetime,
        end_time: datetime,
        subscription_id: str,
        credential: DefaultAzureCredential,
    ) -> AvailabilityResult:
        """Collect availability metrics for a resource."""
        pass


class VMMetricsCollector(MetricsCollector):
    """Metrics collector for Virtual Machines."""

    @property
    def supported_types(self) -> list[str]:
        return ["microsoft.compute/virtualmachines"]

    def collect(
        self,
        resource: ResourceRecord,
        start_time: datetime,
        end_time: datetime,
        subscription_id: str,
        credential: DefaultAzureCredential,
    ) -> AvailabilityResult:
        """
        Collect VM availability using VmAvailabilityMetric.

        Falls back to estimation if metric unavailable.
        """
        total_minutes = (end_time - start_time).total_seconds() / 60
        data_points: list[AvailabilityDataPoint] = []
        notes: list[str] = []

        try:
            client = MonitorManagementClient(credential, subscription_id)
            # Format timespan as ISO8601 interval
            timespan = f"{start_time.isoformat()}/{end_time.isoformat()}"
            
            # Try VmAvailabilityMetric (available for most VM SKUs)
            response = client.metrics.list(
                resource_uri=resource.id,
                metricnames="VmAvailabilityMetric",
                timespan=timespan,
                interval="PT5M",  # ISO8601 duration: 5 minutes
                aggregation="Average",
            )

            available_minutes = 0.0
            metric_found = False

            for metric in response.value:
                if metric.name.value.lower() == "vmavailabilitymetric":
                    metric_found = True
                    for ts in metric.timeseries:
                        for dp in ts.data:
                            if dp.average is not None:
                                # VmAvailabilityMetric: 1 = available, 0 = unavailable
                                is_available = dp.average >= 0.5
                                data_points.append(
                                    AvailabilityDataPoint(
                                        timestamp=dp.time_stamp,
                                        available=is_available,
                                        value=dp.average,
                                    )
                                )
                                if is_available:
                                    available_minutes += 5  # 5-minute granularity

            if not metric_found:
                notes.append("VmAvailabilityMetric not found; using estimation")
                # Fallback: assume 100% if no data (conservative estimate for running VMs)
                available_minutes = total_minutes
                notes.append("Assumed 100% availability (no metric data)")

        except Exception as e:
            logger.warning(f"Failed to query VM metrics for {resource.name}: {e}")
            notes.append(f"Metric query failed: {e}")
            available_minutes = total_minutes  # Conservative fallback
            notes.append("Assumed 100% availability (query failed)")

        down_minutes = max(0, total_minutes - available_minutes)
        availability_percent = (available_minutes / total_minutes * 100) if total_minutes > 0 else 0

        return AvailabilityResult(
            resource_id=resource.id,
            resource_name=resource.name,
            resource_type=resource.type,
            start_time=start_time,
            end_time=end_time,
            total_minutes=total_minutes,
            available_minutes=available_minutes,
            down_minutes=down_minutes,
            availability_percent=round(availability_percent, 4),
            data_points=data_points,
            metric_source="VmAvailabilityMetric" if not notes else "estimated",
            notes=notes,
        )


class AppServiceMetricsCollector(MetricsCollector):
    """Metrics collector for App Services (Web Apps)."""

    @property
    def supported_types(self) -> list[str]:
        return ["microsoft.web/sites"]

    def collect(
        self,
        resource: ResourceRecord,
        start_time: datetime,
        end_time: datetime,
        subscription_id: str,
        credential: DefaultAzureCredential,
    ) -> AvailabilityResult:
        """
        Collect App Service availability using HTTP success rate.

        Strategy:
        - Query Requests (total HTTP requests) and Http5xx (server errors)
        - Availability = (Requests - Http5xx) / Requests * 100
        - If no requests, assume 100% (service is up but idle)
        """
        total_minutes = (end_time - start_time).total_seconds() / 60
        data_points: list[AvailabilityDataPoint] = []
        notes: list[str] = []

        try:
            client = MonitorManagementClient(credential, subscription_id)
            timespan = f"{start_time.isoformat()}/{end_time.isoformat()}"
            
            # Query both Requests and Http5xx metrics together
            response = client.metrics.list(
                resource_uri=resource.id,
                metricnames="Requests,Http5xx,Http4xx,HealthCheckStatus",
                timespan=timespan,
                interval="PT1H",  # ISO8601 duration: 1 hour
                aggregation="Total,Average",
            )

            total_requests = 0.0
            total_5xx_errors = 0.0
            total_4xx_errors = 0.0
            health_check_avg = None
            metrics_found = set()

            for metric in response.value:
                metric_name = metric.name.value.lower()
                metrics_found.add(metric_name)
                
                for ts in metric.timeseries:
                    for dp in ts.data:
                        if metric_name == "requests" and dp.total is not None:
                            total_requests += dp.total
                            # Track hourly data points
                            if dp.time_stamp:
                                data_points.append(
                                    AvailabilityDataPoint(
                                        timestamp=dp.time_stamp,
                                        available=True,  # Will adjust below
                                        value=dp.total,
                                    )
                                )
                        elif metric_name == "http5xx" and dp.total is not None:
                            total_5xx_errors += dp.total
                        elif metric_name == "http4xx" and dp.total is not None:
                            total_4xx_errors += dp.total
                        elif metric_name == "healthcheckstatus" and dp.average is not None:
                            if health_check_avg is None:
                                health_check_avg = dp.average
                            else:
                                health_check_avg = (health_check_avg + dp.average) / 2

            # Calculate availability
            if total_requests > 0:
                # Server errors (5xx) indicate unavailability
                successful_requests = total_requests - total_5xx_errors
                availability_percent = (successful_requests / total_requests) * 100
                notes.append(f"Based on {int(total_requests)} requests, {int(total_5xx_errors)} 5xx errors")
                if total_4xx_errors > 0:
                    notes.append(f"{int(total_4xx_errors)} 4xx errors (client errors, not counted)")
                metric_source = "Http5xx/Requests"
            elif health_check_avg is not None:
                # Use health check if no traffic
                availability_percent = health_check_avg
                notes.append(f"No HTTP traffic; using HealthCheckStatus: {health_check_avg:.2f}%")
                metric_source = "HealthCheckStatus"
            else:
                # No data - assume service is available but idle
                availability_percent = 100.0
                notes.append("No HTTP requests or health check data; assuming 100% (idle)")
                metric_source = "assumed"

            if not metrics_found:
                notes.append("No metrics found for this App Service")
                metric_source = "no_data"

            # Calculate minutes
            available_minutes = (availability_percent / 100) * total_minutes
            down_minutes = total_minutes - available_minutes

            # Update data points with availability status
            if total_requests > 0:
                error_rate = total_5xx_errors / total_requests if total_requests > 0 else 0
                for dp in data_points:
                    # Mark as unavailable if error rate > 5%
                    dp.available = error_rate < 0.05

        except Exception as e:
            logger.warning(f"Failed to query App Service metrics for {resource.name}: {e}")
            notes.append(f"Metric query failed: {e}")
            # Return unknown on error
            return AvailabilityResult(
                resource_id=resource.id,
                resource_name=resource.name,
                resource_type=resource.type,
                start_time=start_time,
                end_time=end_time,
                total_minutes=total_minutes,
                available_minutes=0,
                down_minutes=0,
                availability_percent=-1,
                data_points=[],
                metric_source="error",
                notes=notes,
            )

        return AvailabilityResult(
            resource_id=resource.id,
            resource_name=resource.name,
            resource_type=resource.type,
            start_time=start_time,
            end_time=end_time,
            total_minutes=total_minutes,
            available_minutes=round(available_minutes, 2),
            down_minutes=round(down_minutes, 2),
            availability_percent=round(availability_percent, 4),
            data_points=data_points,
            metric_source=metric_source,
            notes=notes,
        )


class AKSMetricsCollector(MetricsCollector):
    """Metrics collector for AKS clusters."""

    @property
    def supported_types(self) -> list[str]:
        return ["microsoft.containerservice/managedclusters"]

    def collect(
        self,
        resource: ResourceRecord,
        start_time: datetime,
        end_time: datetime,
        subscription_id: str,
        credential: DefaultAzureCredential,
    ) -> AvailabilityResult:
        """
        Collect AKS cluster availability.

        TODO: Implement using kube-apiserver availability or node readiness.
        For now, returns UNKNOWN status.
        """
        total_minutes = (end_time - start_time).total_seconds() / 60

        notes = [
            "TODO: AKS metrics collection not implemented",
            "AKS availability requires kube-apiserver/node analysis",
        ]

        return AvailabilityResult(
            resource_id=resource.id,
            resource_name=resource.name,
            resource_type=resource.type,
            start_time=start_time,
            end_time=end_time,
            total_minutes=total_minutes,
            available_minutes=0,
            down_minutes=0,
            availability_percent=-1,
            data_points=[],
            metric_source="stub",
            notes=notes,
        )


class SQLMetricsCollector(MetricsCollector):
    """Metrics collector for SQL Databases."""

    @property
    def supported_types(self) -> list[str]:
        return ["microsoft.sql/servers/databases"]

    def collect(
        self,
        resource: ResourceRecord,
        start_time: datetime,
        end_time: datetime,
        subscription_id: str,
        credential: DefaultAzureCredential,
    ) -> AvailabilityResult:
        """
        Collect SQL Database availability using connection_successful metric.
        """
        total_minutes = (end_time - start_time).total_seconds() / 60
        data_points: list[AvailabilityDataPoint] = []
        notes: list[str] = []

        try:
            client = MonitorManagementClient(credential, subscription_id)
            timespan = f"{start_time.isoformat()}/{end_time.isoformat()}"
            
            response = client.metrics.list(
                resource_uri=resource.id,
                metricnames="connection_successful,connection_failed",
                timespan=timespan,
                interval="PT1H",
                aggregation="Total",
            )

            successful = 0.0
            failed = 0.0
            metrics_found = False

            for metric in response.value:
                metric_name = metric.name.value.lower()
                for ts in metric.timeseries:
                    for dp in ts.data:
                        if dp.total is not None:
                            metrics_found = True
                            if "successful" in metric_name:
                                successful += dp.total
                            elif "failed" in metric_name:
                                failed += dp.total

            if metrics_found and (successful + failed) > 0:
                availability_percent = (successful / (successful + failed)) * 100
                notes.append(f"Based on {int(successful)} successful, {int(failed)} failed connections")
                metric_source = "connection_metrics"
            else:
                # No connection activity - assume available
                availability_percent = 100.0
                notes.append("No connection metrics; assuming 100% (idle database)")
                metric_source = "assumed"

        except Exception as e:
            logger.warning(f"Failed to query SQL metrics for {resource.name}: {e}")
            notes.append(f"Metric query failed: {e}")
            availability_percent = 100.0
            metric_source = "assumed"
            notes.append("Assumed 100% availability (query failed)")

        available_minutes = (availability_percent / 100) * total_minutes
        down_minutes = total_minutes - available_minutes

        return AvailabilityResult(
            resource_id=resource.id,
            resource_name=resource.name,
            resource_type=resource.type,
            start_time=start_time,
            end_time=end_time,
            total_minutes=total_minutes,
            available_minutes=round(available_minutes, 2),
            down_minutes=round(down_minutes, 2),
            availability_percent=round(availability_percent, 4),
            data_points=data_points,
            metric_source=metric_source,
            notes=notes,
        )


class StorageAccountMetricsCollector(MetricsCollector):
    """Metrics collector for Storage Accounts."""

    @property
    def supported_types(self) -> list[str]:
        return ["microsoft.storage/storageaccounts"]

    def collect(
        self,
        resource: ResourceRecord,
        start_time: datetime,
        end_time: datetime,
        subscription_id: str,
        credential: DefaultAzureCredential,
    ) -> AvailabilityResult:
        """
        Collect Storage Account availability using Availability metric.
        """
        total_minutes = (end_time - start_time).total_seconds() / 60
        data_points: list[AvailabilityDataPoint] = []
        notes: list[str] = []

        try:
            client = MonitorManagementClient(credential, subscription_id)
            timespan = f"{start_time.isoformat()}/{end_time.isoformat()}"
            
            response = client.metrics.list(
                resource_uri=resource.id,
                metricnames="Availability",
                timespan=timespan,
                interval="PT1H",
                aggregation="Average",
            )

            availability_sum = 0.0
            count = 0

            for metric in response.value:
                for ts in metric.timeseries:
                    for dp in ts.data:
                        if dp.average is not None:
                            availability_sum += dp.average
                            count += 1
                            data_points.append(
                                AvailabilityDataPoint(
                                    timestamp=dp.time_stamp,
                                    available=dp.average >= 99.0,
                                    value=dp.average,
                                )
                            )

            if count > 0:
                availability_percent = availability_sum / count
                notes.append(f"Based on {count} hourly Availability metric samples")
                metric_source = "Availability"
            else:
                availability_percent = 100.0
                notes.append("No availability metrics; assuming 100%")
                metric_source = "assumed"

        except Exception as e:
            logger.warning(f"Failed to query Storage metrics for {resource.name}: {e}")
            notes.append(f"Metric query failed: {e}")
            availability_percent = 100.0
            metric_source = "assumed"

        available_minutes = (availability_percent / 100) * total_minutes
        down_minutes = total_minutes - available_minutes

        return AvailabilityResult(
            resource_id=resource.id,
            resource_name=resource.name,
            resource_type=resource.type,
            start_time=start_time,
            end_time=end_time,
            total_minutes=total_minutes,
            available_minutes=round(available_minutes, 2),
            down_minutes=round(down_minutes, 2),
            availability_percent=round(availability_percent, 4),
            data_points=data_points,
            metric_source=metric_source,
            notes=notes,
        )


class PostgreSQLFlexibleMetricsCollector(MetricsCollector):
    """Metrics collector for PostgreSQL Flexible Server."""

    @property
    def supported_types(self) -> list[str]:
        return ["microsoft.dbforpostgresql/flexibleservers"]

    def collect(
        self,
        resource: ResourceRecord,
        start_time: datetime,
        end_time: datetime,
        subscription_id: str,
        credential: DefaultAzureCredential,
    ) -> AvailabilityResult:
        """
        Collect PostgreSQL Flexible Server availability.
        Uses is_db_alive metric or active_connections as proxy.
        """
        total_minutes = (end_time - start_time).total_seconds() / 60
        data_points: list[AvailabilityDataPoint] = []
        notes: list[str] = []

        try:
            client = MonitorManagementClient(credential, subscription_id)
            timespan = f"{start_time.isoformat()}/{end_time.isoformat()}"
            
            response = client.metrics.list(
                resource_uri=resource.id,
                metricnames="is_db_alive,active_connections,cpu_percent",
                timespan=timespan,
                interval="PT5M",
                aggregation="Average,Maximum",
            )

            alive_count = 0
            dead_count = 0
            has_metrics = False

            for metric in response.value:
                metric_name = metric.name.value.lower()
                for ts in metric.timeseries:
                    for dp in ts.data:
                        if metric_name == "is_db_alive" and dp.average is not None:
                            has_metrics = True
                            if dp.average >= 1:
                                alive_count += 1
                            else:
                                dead_count += 1
                            data_points.append(
                                AvailabilityDataPoint(
                                    timestamp=dp.time_stamp,
                                    available=dp.average >= 1,
                                    value=dp.average,
                                )
                            )
                        elif metric_name == "cpu_percent" and dp.average is not None:
                            # CPU activity indicates database is running
                            has_metrics = True

            if alive_count + dead_count > 0:
                availability_percent = (alive_count / (alive_count + dead_count)) * 100
                notes.append(f"Based on is_db_alive: {alive_count} up, {dead_count} down samples")
                metric_source = "is_db_alive"
            elif has_metrics:
                availability_percent = 100.0
                notes.append("Database showing activity; assuming 100%")
                metric_source = "activity"
            else:
                availability_percent = 100.0
                notes.append("No availability metrics; assuming 100%")
                metric_source = "assumed"

        except Exception as e:
            logger.warning(f"Failed to query PostgreSQL metrics for {resource.name}: {e}")
            notes.append(f"Metric query failed: {e}")
            availability_percent = 100.0
            metric_source = "assumed"

        available_minutes = (availability_percent / 100) * total_minutes
        down_minutes = total_minutes - available_minutes

        return AvailabilityResult(
            resource_id=resource.id,
            resource_name=resource.name,
            resource_type=resource.type,
            start_time=start_time,
            end_time=end_time,
            total_minutes=total_minutes,
            available_minutes=round(available_minutes, 2),
            down_minutes=round(down_minutes, 2),
            availability_percent=round(availability_percent, 4),
            data_points=data_points,
            metric_source=metric_source,
            notes=notes,
        )


class ContainerAppsMetricsCollector(MetricsCollector):
    """Metrics collector for Container Apps."""

    @property
    def supported_types(self) -> list[str]:
        return ["microsoft.app/containerapps"]

    def collect(
        self,
        resource: ResourceRecord,
        start_time: datetime,
        end_time: datetime,
        subscription_id: str,
        credential: DefaultAzureCredential,
    ) -> AvailabilityResult:
        """
        Collect Container Apps availability using Requests and Failed Requests.
        """
        total_minutes = (end_time - start_time).total_seconds() / 60
        data_points: list[AvailabilityDataPoint] = []
        notes: list[str] = []

        try:
            client = MonitorManagementClient(credential, subscription_id)
            timespan = f"{start_time.isoformat()}/{end_time.isoformat()}"
            
            response = client.metrics.list(
                resource_uri=resource.id,
                metricnames="Requests,FailedRequests,Replicas",
                timespan=timespan,
                interval="PT1H",
                aggregation="Total,Average",
            )

            total_requests = 0.0
            failed_requests = 0.0
            has_replicas = False

            for metric in response.value:
                metric_name = metric.name.value.lower()
                for ts in metric.timeseries:
                    for dp in ts.data:
                        if metric_name == "requests" and dp.total is not None:
                            total_requests += dp.total
                        elif metric_name == "failedrequests" and dp.total is not None:
                            failed_requests += dp.total
                        elif metric_name == "replicas" and dp.average is not None and dp.average > 0:
                            has_replicas = True

            if total_requests > 0:
                successful = total_requests - failed_requests
                availability_percent = (successful / total_requests) * 100
                notes.append(f"Based on {int(total_requests)} requests, {int(failed_requests)} failed")
                metric_source = "Requests"
            elif has_replicas:
                availability_percent = 100.0
                notes.append("Container has replicas running; assuming 100%")
                metric_source = "replicas"
            else:
                availability_percent = 100.0
                notes.append("No traffic data; assuming 100%")
                metric_source = "assumed"

        except Exception as e:
            logger.warning(f"Failed to query Container Apps metrics for {resource.name}: {e}")
            notes.append(f"Metric query failed: {e}")
            availability_percent = 100.0
            metric_source = "assumed"

        available_minutes = (availability_percent / 100) * total_minutes
        down_minutes = total_minutes - available_minutes

        return AvailabilityResult(
            resource_id=resource.id,
            resource_name=resource.name,
            resource_type=resource.type,
            start_time=start_time,
            end_time=end_time,
            total_minutes=total_minutes,
            available_minutes=round(available_minutes, 2),
            down_minutes=round(down_minutes, 2),
            availability_percent=round(availability_percent, 4),
            data_points=data_points,
            metric_source=metric_source,
            notes=notes,
        )


class LoadBalancerMetricsCollector(MetricsCollector):
    """Metrics collector for Azure Load Balancer."""

    @property
    def supported_types(self) -> list[str]:
        return ["microsoft.network/loadbalancers"]

    def collect(
        self,
        resource: ResourceRecord,
        start_time: datetime,
        end_time: datetime,
        subscription_id: str,
        credential: DefaultAzureCredential,
    ) -> AvailabilityResult:
        """
        Collect Load Balancer availability using VipAvailability and HealthProbeStatus.
        """
        total_minutes = (end_time - start_time).total_seconds() / 60
        data_points: list[AvailabilityDataPoint] = []
        notes: list[str] = []

        try:
            client = MonitorManagementClient(credential, subscription_id)
            timespan = f"{start_time.isoformat()}/{end_time.isoformat()}"
            
            response = client.metrics.list(
                resource_uri=resource.id,
                metricnames="VipAvailability,DipAvailability",
                timespan=timespan,
                interval="PT5M",
                aggregation="Average",
            )

            availability_sum = 0.0
            count = 0

            for metric in response.value:
                metric_name = metric.name.value.lower()
                if metric_name == "vipavailability":
                    for ts in metric.timeseries:
                        for dp in ts.data:
                            if dp.average is not None:
                                availability_sum += dp.average
                                count += 1
                                data_points.append(
                                    AvailabilityDataPoint(
                                        timestamp=dp.time_stamp,
                                        available=dp.average >= 99.0,
                                        value=dp.average,
                                    )
                                )

            if count > 0:
                availability_percent = availability_sum / count
                notes.append(f"Based on {count} VipAvailability samples")
                metric_source = "VipAvailability"
            else:
                availability_percent = 100.0
                notes.append("No VipAvailability data; assuming 100%")
                metric_source = "assumed"

        except Exception as e:
            logger.warning(f"Failed to query Load Balancer metrics for {resource.name}: {e}")
            notes.append(f"Metric query failed: {e}")
            availability_percent = 100.0
            metric_source = "assumed"

        available_minutes = (availability_percent / 100) * total_minutes
        down_minutes = total_minutes - available_minutes

        return AvailabilityResult(
            resource_id=resource.id,
            resource_name=resource.name,
            resource_type=resource.type,
            start_time=start_time,
            end_time=end_time,
            total_minutes=total_minutes,
            available_minutes=round(available_minutes, 2),
            down_minutes=round(down_minutes, 2),
            availability_percent=round(availability_percent, 4),
            data_points=data_points,
            metric_source=metric_source,
            notes=notes,
        )


class ApplicationGatewayMetricsCollector(MetricsCollector):
    """Metrics collector for Application Gateway."""

    @property
    def supported_types(self) -> list[str]:
        return ["microsoft.network/applicationgateways"]

    def collect(
        self,
        resource: ResourceRecord,
        start_time: datetime,
        end_time: datetime,
        subscription_id: str,
        credential: DefaultAzureCredential,
    ) -> AvailabilityResult:
        """
        Collect Application Gateway availability.
        """
        total_minutes = (end_time - start_time).total_seconds() / 60
        data_points: list[AvailabilityDataPoint] = []
        notes: list[str] = []

        try:
            client = MonitorManagementClient(credential, subscription_id)
            timespan = f"{start_time.isoformat()}/{end_time.isoformat()}"
            
            response = client.metrics.list(
                resource_uri=resource.id,
                metricnames="TotalRequests,FailedRequests,UnhealthyHostCount,HealthyHostCount",
                timespan=timespan,
                interval="PT1H",
                aggregation="Total,Average",
            )

            total_requests = 0.0
            failed_requests = 0.0
            has_healthy_hosts = False

            for metric in response.value:
                metric_name = metric.name.value.lower()
                for ts in metric.timeseries:
                    for dp in ts.data:
                        if metric_name == "totalrequests" and dp.total is not None:
                            total_requests += dp.total
                        elif metric_name == "failedrequests" and dp.total is not None:
                            failed_requests += dp.total
                        elif metric_name == "healthyhostcount" and dp.average is not None and dp.average > 0:
                            has_healthy_hosts = True

            if total_requests > 0:
                successful = total_requests - failed_requests
                availability_percent = (successful / total_requests) * 100
                notes.append(f"Based on {int(total_requests)} requests, {int(failed_requests)} failed")
                metric_source = "Requests"
            elif has_healthy_hosts:
                availability_percent = 100.0
                notes.append("Healthy hosts detected; assuming 100%")
                metric_source = "HealthyHosts"
            else:
                availability_percent = 100.0
                notes.append("No traffic data; assuming 100%")
                metric_source = "assumed"

        except Exception as e:
            logger.warning(f"Failed to query App Gateway metrics for {resource.name}: {e}")
            notes.append(f"Metric query failed: {e}")
            availability_percent = 100.0
            metric_source = "assumed"

        available_minutes = (availability_percent / 100) * total_minutes
        down_minutes = total_minutes - available_minutes

        return AvailabilityResult(
            resource_id=resource.id,
            resource_name=resource.name,
            resource_type=resource.type,
            start_time=start_time,
            end_time=end_time,
            total_minutes=total_minutes,
            available_minutes=round(available_minutes, 2),
            down_minutes=round(down_minutes, 2),
            availability_percent=round(availability_percent, 4),
            data_points=data_points,
            metric_source=metric_source,
            notes=notes,
        )


class KeyVaultMetricsCollector(MetricsCollector):
    """Metrics collector for Azure Key Vault."""

    @property
    def supported_types(self) -> list[str]:
        return ["microsoft.keyvault/vaults"]

    def collect(
        self,
        resource: ResourceRecord,
        start_time: datetime,
        end_time: datetime,
        subscription_id: str,
        credential: DefaultAzureCredential,
    ) -> AvailabilityResult:
        """
        Collect Key Vault availability using Availability and ServiceApiLatency.
        """
        total_minutes = (end_time - start_time).total_seconds() / 60
        data_points: list[AvailabilityDataPoint] = []
        notes: list[str] = []

        try:
            client = MonitorManagementClient(credential, subscription_id)
            timespan = f"{start_time.isoformat()}/{end_time.isoformat()}"
            
            response = client.metrics.list(
                resource_uri=resource.id,
                metricnames="Availability,ServiceApiHit,ServiceApiResult",
                timespan=timespan,
                interval="PT1H",
                aggregation="Average,Total",
            )

            availability_sum = 0.0
            count = 0

            for metric in response.value:
                metric_name = metric.name.value.lower()
                if metric_name == "availability":
                    for ts in metric.timeseries:
                        for dp in ts.data:
                            if dp.average is not None:
                                availability_sum += dp.average
                                count += 1
                                data_points.append(
                                    AvailabilityDataPoint(
                                        timestamp=dp.time_stamp,
                                        available=dp.average >= 99.0,
                                        value=dp.average,
                                    )
                                )

            if count > 0:
                availability_percent = availability_sum / count
                notes.append(f"Based on {count} Availability samples")
                metric_source = "Availability"
            else:
                availability_percent = 100.0
                notes.append("No availability metrics; assuming 100%")
                metric_source = "assumed"

        except Exception as e:
            logger.warning(f"Failed to query Key Vault metrics for {resource.name}: {e}")
            notes.append(f"Metric query failed: {e}")
            availability_percent = 100.0
            metric_source = "assumed"

        available_minutes = (availability_percent / 100) * total_minutes
        down_minutes = total_minutes - available_minutes

        return AvailabilityResult(
            resource_id=resource.id,
            resource_name=resource.name,
            resource_type=resource.type,
            start_time=start_time,
            end_time=end_time,
            total_minutes=total_minutes,
            available_minutes=round(available_minutes, 2),
            down_minutes=round(down_minutes, 2),
            availability_percent=round(availability_percent, 4),
            data_points=data_points,
            metric_source=metric_source,
            notes=notes,
        )


class CosmosDBMetricsCollector(MetricsCollector):
    """Metrics collector for Cosmos DB."""

    @property
    def supported_types(self) -> list[str]:
        return ["microsoft.documentdb/databaseaccounts"]

    def collect(
        self,
        resource: ResourceRecord,
        start_time: datetime,
        end_time: datetime,
        subscription_id: str,
        credential: DefaultAzureCredential,
    ) -> AvailabilityResult:
        """
        Collect Cosmos DB availability using ServiceAvailability.
        """
        total_minutes = (end_time - start_time).total_seconds() / 60
        data_points: list[AvailabilityDataPoint] = []
        notes: list[str] = []

        try:
            client = MonitorManagementClient(credential, subscription_id)
            timespan = f"{start_time.isoformat()}/{end_time.isoformat()}"
            
            response = client.metrics.list(
                resource_uri=resource.id,
                metricnames="ServiceAvailability,TotalRequests,TotalRequestUnits",
                timespan=timespan,
                interval="PT1H",
                aggregation="Average,Total",
            )

            availability_sum = 0.0
            count = 0
            has_requests = False

            for metric in response.value:
                metric_name = metric.name.value.lower()
                if metric_name == "serviceavailability":
                    for ts in metric.timeseries:
                        for dp in ts.data:
                            if dp.average is not None:
                                availability_sum += dp.average
                                count += 1
                                data_points.append(
                                    AvailabilityDataPoint(
                                        timestamp=dp.time_stamp,
                                        available=dp.average >= 99.0,
                                        value=dp.average,
                                    )
                                )
                elif metric_name == "totalrequests":
                    for ts in metric.timeseries:
                        for dp in ts.data:
                            if dp.total is not None and dp.total > 0:
                                has_requests = True

            if count > 0:
                availability_percent = availability_sum / count
                notes.append(f"Based on {count} ServiceAvailability samples")
                metric_source = "ServiceAvailability"
            elif has_requests:
                availability_percent = 100.0
                notes.append("Has request activity; assuming 100%")
                metric_source = "activity"
            else:
                availability_percent = 100.0
                notes.append("No availability metrics; assuming 100%")
                metric_source = "assumed"

        except Exception as e:
            logger.warning(f"Failed to query Cosmos DB metrics for {resource.name}: {e}")
            notes.append(f"Metric query failed: {e}")
            availability_percent = 100.0
            metric_source = "assumed"

        available_minutes = (availability_percent / 100) * total_minutes
        down_minutes = total_minutes - available_minutes

        return AvailabilityResult(
            resource_id=resource.id,
            resource_name=resource.name,
            resource_type=resource.type,
            start_time=start_time,
            end_time=end_time,
            total_minutes=total_minutes,
            available_minutes=round(available_minutes, 2),
            down_minutes=round(down_minutes, 2),
            availability_percent=round(availability_percent, 4),
            data_points=data_points,
            metric_source=metric_source,
            notes=notes,
        )


class RedisCacheMetricsCollector(MetricsCollector):
    """Metrics collector for Azure Cache for Redis."""

    @property
    def supported_types(self) -> list[str]:
        return ["microsoft.cache/redis"]

    def collect(
        self,
        resource: ResourceRecord,
        start_time: datetime,
        end_time: datetime,
        subscription_id: str,
        credential: DefaultAzureCredential,
    ) -> AvailabilityResult:
        """
        Collect Redis Cache availability using connected clients and errors.
        """
        total_minutes = (end_time - start_time).total_seconds() / 60
        data_points: list[AvailabilityDataPoint] = []
        notes: list[str] = []

        try:
            client = MonitorManagementClient(credential, subscription_id)
            timespan = f"{start_time.isoformat()}/{end_time.isoformat()}"
            
            response = client.metrics.list(
                resource_uri=resource.id,
                metricnames="connectedclients,errors,serverLoad",
                timespan=timespan,
                interval="PT1H",
                aggregation="Average,Total",
            )

            has_clients = False
            total_errors = 0.0
            total_samples = 0

            for metric in response.value:
                metric_name = metric.name.value.lower()
                for ts in metric.timeseries:
                    for dp in ts.data:
                        if metric_name == "connectedclients" and dp.average is not None and dp.average > 0:
                            has_clients = True
                            total_samples += 1
                        elif metric_name == "errors" and dp.total is not None:
                            total_errors += dp.total

            if has_clients:
                # Redis is available if clients can connect
                availability_percent = 100.0
                notes.append(f"Redis has connected clients; {int(total_errors)} errors recorded")
                metric_source = "connectedclients"
            else:
                availability_percent = 100.0
                notes.append("No client connections; assuming 100% (idle)")
                metric_source = "assumed"

        except Exception as e:
            logger.warning(f"Failed to query Redis metrics for {resource.name}: {e}")
            notes.append(f"Metric query failed: {e}")
            availability_percent = 100.0
            metric_source = "assumed"

        available_minutes = (availability_percent / 100) * total_minutes
        down_minutes = total_minutes - available_minutes

        return AvailabilityResult(
            resource_id=resource.id,
            resource_name=resource.name,
            resource_type=resource.type,
            start_time=start_time,
            end_time=end_time,
            total_minutes=total_minutes,
            available_minutes=round(available_minutes, 2),
            down_minutes=round(down_minutes, 2),
            availability_percent=round(availability_percent, 4),
            data_points=data_points,
            metric_source=metric_source,
            notes=notes,
        )


class ServiceBusMetricsCollector(MetricsCollector):
    """Metrics collector for Azure Service Bus."""

    @property
    def supported_types(self) -> list[str]:
        return ["microsoft.servicebus/namespaces"]

    def collect(
        self,
        resource: ResourceRecord,
        start_time: datetime,
        end_time: datetime,
        subscription_id: str,
        credential: DefaultAzureCredential,
    ) -> AvailabilityResult:
        """
        Collect Service Bus availability using success/error rates.
        """
        total_minutes = (end_time - start_time).total_seconds() / 60
        data_points: list[AvailabilityDataPoint] = []
        notes: list[str] = []

        try:
            client = MonitorManagementClient(credential, subscription_id)
            timespan = f"{start_time.isoformat()}/{end_time.isoformat()}"
            
            response = client.metrics.list(
                resource_uri=resource.id,
                metricnames="SuccessfulRequests,ServerErrors,IncomingMessages",
                timespan=timespan,
                interval="PT1H",
                aggregation="Total",
            )

            successful = 0.0
            server_errors = 0.0
            has_messages = False

            for metric in response.value:
                metric_name = metric.name.value.lower()
                for ts in metric.timeseries:
                    for dp in ts.data:
                        if dp.total is not None:
                            if "successful" in metric_name:
                                successful += dp.total
                            elif "servererrors" in metric_name:
                                server_errors += dp.total
                            elif "incoming" in metric_name and dp.total > 0:
                                has_messages = True

            total_ops = successful + server_errors
            if total_ops > 0:
                availability_percent = (successful / total_ops) * 100
                notes.append(f"Based on {int(successful)} successful, {int(server_errors)} errors")
                metric_source = "SuccessfulRequests"
            elif has_messages:
                availability_percent = 100.0
                notes.append("Has message activity; assuming 100%")
                metric_source = "activity"
            else:
                availability_percent = 100.0
                notes.append("No activity data; assuming 100%")
                metric_source = "assumed"

        except Exception as e:
            logger.warning(f"Failed to query Service Bus metrics for {resource.name}: {e}")
            notes.append(f"Metric query failed: {e}")
            availability_percent = 100.0
            metric_source = "assumed"

        available_minutes = (availability_percent / 100) * total_minutes
        down_minutes = total_minutes - available_minutes

        return AvailabilityResult(
            resource_id=resource.id,
            resource_name=resource.name,
            resource_type=resource.type,
            start_time=start_time,
            end_time=end_time,
            total_minutes=total_minutes,
            available_minutes=round(available_minutes, 2),
            down_minutes=round(down_minutes, 2),
            availability_percent=round(availability_percent, 4),
            data_points=data_points,
            metric_source=metric_source,
            notes=notes,
        )


class EventHubsMetricsCollector(MetricsCollector):
    """Metrics collector for Azure Event Hubs."""

    @property
    def supported_types(self) -> list[str]:
        return ["microsoft.eventhub/namespaces"]

    def collect(
        self,
        resource: ResourceRecord,
        start_time: datetime,
        end_time: datetime,
        subscription_id: str,
        credential: DefaultAzureCredential,
    ) -> AvailabilityResult:
        """
        Collect Event Hubs availability using success/error rates.
        """
        total_minutes = (end_time - start_time).total_seconds() / 60
        data_points: list[AvailabilityDataPoint] = []
        notes: list[str] = []

        try:
            client = MonitorManagementClient(credential, subscription_id)
            timespan = f"{start_time.isoformat()}/{end_time.isoformat()}"
            
            response = client.metrics.list(
                resource_uri=resource.id,
                metricnames="SuccessfulRequests,ServerErrors,IncomingMessages,OutgoingMessages",
                timespan=timespan,
                interval="PT1H",
                aggregation="Total",
            )

            successful = 0.0
            server_errors = 0.0
            has_messages = False

            for metric in response.value:
                metric_name = metric.name.value.lower()
                for ts in metric.timeseries:
                    for dp in ts.data:
                        if dp.total is not None:
                            if "successful" in metric_name:
                                successful += dp.total
                            elif "servererrors" in metric_name:
                                server_errors += dp.total
                            elif "messages" in metric_name and dp.total > 0:
                                has_messages = True

            total_ops = successful + server_errors
            if total_ops > 0:
                availability_percent = (successful / total_ops) * 100
                notes.append(f"Based on {int(successful)} successful, {int(server_errors)} errors")
                metric_source = "SuccessfulRequests"
            elif has_messages:
                availability_percent = 100.0
                notes.append("Has message activity; assuming 100%")
                metric_source = "activity"
            else:
                availability_percent = 100.0
                notes.append("No activity data; assuming 100%")
                metric_source = "assumed"

        except Exception as e:
            logger.warning(f"Failed to query Event Hubs metrics for {resource.name}: {e}")
            notes.append(f"Metric query failed: {e}")
            availability_percent = 100.0
            metric_source = "assumed"

        available_minutes = (availability_percent / 100) * total_minutes
        down_minutes = total_minutes - available_minutes

        return AvailabilityResult(
            resource_id=resource.id,
            resource_name=resource.name,
            resource_type=resource.type,
            start_time=start_time,
            end_time=end_time,
            total_minutes=total_minutes,
            available_minutes=round(available_minutes, 2),
            down_minutes=round(down_minutes, 2),
            availability_percent=round(availability_percent, 4),
            data_points=data_points,
            metric_source=metric_source,
            notes=notes,
        )


class GenericAvailabilityCollector(MetricsCollector):
    """
    Generic metrics collector for resources that support standard Availability metric.
    Used for: CDN, Bastion, VNet Gateway, Azure Firewall, Cognitive Services, etc.
    """

    def __init__(self, resource_types: list[str]):
        self._supported_types = resource_types

    @property
    def supported_types(self) -> list[str]:
        return self._supported_types

    def collect(
        self,
        resource: ResourceRecord,
        start_time: datetime,
        end_time: datetime,
        subscription_id: str,
        credential: DefaultAzureCredential,
    ) -> AvailabilityResult:
        """
        Collect availability using generic Availability or Throughput metrics.
        Falls back to 100% if no metrics available (resource is provisioned).
        """
        total_minutes = (end_time - start_time).total_seconds() / 60
        data_points: list[AvailabilityDataPoint] = []
        notes: list[str] = []

        # Try common availability metrics by resource type
        metrics_to_try = self._get_metrics_for_type(resource.type.lower())

        try:
            client = MonitorManagementClient(credential, subscription_id)
            timespan = f"{start_time.isoformat()}/{end_time.isoformat()}"
            
            response = client.metrics.list(
                resource_uri=resource.id,
                metricnames=",".join(metrics_to_try),
                timespan=timespan,
                interval="PT1H",
                aggregation="Average,Total",
            )

            availability_sum = 0.0
            count = 0
            has_activity = False

            for metric in response.value:
                metric_name = metric.name.value.lower()
                for ts in metric.timeseries:
                    for dp in ts.data:
                        # Look for availability-like metrics (0-100 scale)
                        if dp.average is not None:
                            if "availability" in metric_name or "uptime" in metric_name:
                                availability_sum += dp.average
                                count += 1
                            else:
                                # Any activity indicates resource is running
                                has_activity = True
                        if dp.total is not None and dp.total > 0:
                            has_activity = True

            if count > 0:
                availability_percent = availability_sum / count
                notes.append(f"Based on {count} availability samples")
                metric_source = "Availability"
            elif has_activity:
                availability_percent = 100.0
                notes.append("Resource shows activity; assuming 100%")
                metric_source = "activity"
            else:
                availability_percent = 100.0
                notes.append("No metrics data; assuming 100% (resource provisioned)")
                metric_source = "assumed"

        except Exception as e:
            logger.warning(f"Failed to query metrics for {resource.name}: {e}")
            notes.append(f"Metric query failed: {e}")
            availability_percent = 100.0
            metric_source = "assumed"

        available_minutes = (availability_percent / 100) * total_minutes
        down_minutes = total_minutes - available_minutes

        return AvailabilityResult(
            resource_id=resource.id,
            resource_name=resource.name,
            resource_type=resource.type,
            start_time=start_time,
            end_time=end_time,
            total_minutes=total_minutes,
            available_minutes=round(available_minutes, 2),
            down_minutes=round(down_minutes, 2),
            availability_percent=round(availability_percent, 4),
            data_points=data_points,
            metric_source=metric_source,
            notes=notes,
        )

    def _get_metrics_for_type(self, resource_type: str) -> list[str]:
        """Get relevant metrics based on resource type."""
        metrics_map = {
            "microsoft.network/bastionhosts": ["TotalSessions", "SessionCount"],
            "microsoft.network/virtualnetworkgateways": ["TunnelAverageBandwidth", "P2SBandwidth", "TunnelIngressBytes"],
            "microsoft.network/azurefirewalls": ["Throughput", "FirewallHealth", "DataProcessed"],
            "microsoft.cdn/profiles": ["RequestCount", "ByteHitRatio", "OriginHealthPercentage"],
            "microsoft.cognitiveservices/accounts": ["TotalCalls", "SuccessfulCalls", "TotalErrors"],
            "microsoft.network/expressroutecircuits": ["BitsInPerSecond", "BitsOutPerSecond"],
            "microsoft.network/publicipaddresses": ["ByteCount", "PacketCount"],
        }
        return metrics_map.get(resource_type, ["Availability", "Throughput"])


# Registry of all collectors
COLLECTORS: list[MetricsCollector] = [
    VMMetricsCollector(),
    AppServiceMetricsCollector(),
    AKSMetricsCollector(),
    SQLMetricsCollector(),
    StorageAccountMetricsCollector(),
    PostgreSQLFlexibleMetricsCollector(),
    ContainerAppsMetricsCollector(),
    LoadBalancerMetricsCollector(),
    ApplicationGatewayMetricsCollector(),
    KeyVaultMetricsCollector(),
    CosmosDBMetricsCollector(),
    RedisCacheMetricsCollector(),
    ServiceBusMetricsCollector(),
    EventHubsMetricsCollector(),
    # Generic collector for network infrastructure
    GenericAvailabilityCollector([
        "microsoft.network/bastionhosts",
        "microsoft.network/virtualnetworkgateways",
        "microsoft.network/azurefirewalls",
        "microsoft.cdn/profiles",
        "microsoft.cognitiveservices/accounts",
        "microsoft.network/expressroutecircuits",
        "microsoft.network/publicipaddresses",
    ]),
]


def get_collector(resource_type: str) -> MetricsCollector | None:
    """Get the appropriate collector for a resource type."""
    normalized_type = resource_type.lower()
    for collector in COLLECTORS:
        if normalized_type in collector.supported_types:
            return collector
    return None


def collect_metrics(
    resources: list[ResourceRecord],
    start_time: datetime,
    end_time: datetime,
    credential: DefaultAzureCredential | None = None,
) -> list[AvailabilityResult]:
    """
    Collect availability metrics for a list of resources.

    Args:
        resources: List of resources to collect metrics for
        start_time: Start of the time window
        end_time: End of the time window
        credential: Azure credential

    Returns:
        List of AvailabilityResult objects
    """
    if credential is None:
        credential = DefaultAzureCredential()

    results: list[AvailabilityResult] = []

    for resource in resources:
        collector = get_collector(resource.type)
        
        # Extract subscription_id from resource id
        # Format: /subscriptions/{sub}/resourceGroups/...
        parts = resource.id.split("/")
        subscription_id = parts[2] if len(parts) > 2 else ""

        if collector is None:
            logger.warning(f"No collector for resource type: {resource.type}")
            # Create unknown result
            total_minutes = (end_time - start_time).total_seconds() / 60
            results.append(
                AvailabilityResult(
                    resource_id=resource.id,
                    resource_name=resource.name,
                    resource_type=resource.type,
                    start_time=start_time,
                    end_time=end_time,
                    total_minutes=total_minutes,
                    available_minutes=0,
                    down_minutes=0,
                    availability_percent=-1,
                    metric_source="unsupported",
                    notes=[f"No metrics collector for type: {resource.type}"],
                )
            )
            continue

        logger.info(f"Collecting metrics for {resource.name} ({resource.type})")
        try:
            result = collector.collect(resource, start_time, end_time, subscription_id, credential)
            results.append(result)
        except Exception as e:
            logger.error(f"Failed to collect metrics for {resource.name}: {e}")
            total_minutes = (end_time - start_time).total_seconds() / 60
            results.append(
                AvailabilityResult(
                    resource_id=resource.id,
                    resource_name=resource.name,
                    resource_type=resource.type,
                    start_time=start_time,
                    end_time=end_time,
                    total_minutes=total_minutes,
                    available_minutes=0,
                    down_minutes=0,
                    availability_percent=-1,
                    metric_source="error",
                    notes=[f"Collection error: {e}"],
                )
            )

    return results
