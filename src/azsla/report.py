"""Report generation using Jinja2 templates."""

import csv
import logging
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from azsla.models import (
    ComplianceResult,
    ComplianceStatus,
    OutageRecord,
    ReportMetadata,
    ResourceRecord,
)

logger = logging.getLogger(__name__)

# Default templates directory
TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"


class ReportGenerator:
    """Generates SLA compliance reports."""

    def __init__(self, templates_dir: Path | str | None = None):
        """
        Initialize the report generator.

        Args:
            templates_dir: Path to Jinja2 templates directory
        """
        self.templates_dir = Path(templates_dir) if templates_dir else TEMPLATES_DIR

        if not self.templates_dir.exists():
            logger.warning(f"Templates directory not found: {self.templates_dir}")

        self.env = Environment(
            loader=FileSystemLoader(str(self.templates_dir)),
            autoescape=select_autoescape(["html", "xml"]),
        )

        # Add custom filters
        self.env.filters["format_percent"] = self._format_percent
        self.env.filters["format_datetime"] = self._format_datetime
        self.env.filters["status_emoji"] = self._status_emoji
        self.env.filters["status_class"] = self._status_class

    @staticmethod
    def _format_percent(value: float) -> str:
        """Format a percentage value."""
        if value < 0:
            return "N/A"
        return f"{value:.4f}%"

    @staticmethod
    def _format_datetime(dt: datetime) -> str:
        """Format a datetime."""
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")

    @staticmethod
    def _status_emoji(status: ComplianceStatus) -> str:
        """Get emoji for compliance status."""
        return {
            ComplianceStatus.COMPLIANT: "✅",
            ComplianceStatus.BREACH: "❌",
            ComplianceStatus.UNKNOWN: "⚠️",
        }.get(status, "❓")

    @staticmethod
    def _status_class(status: ComplianceStatus) -> str:
        """Get CSS class for compliance status."""
        return {
            ComplianceStatus.COMPLIANT: "status-compliant",
            ComplianceStatus.BREACH: "status-breach",
            ComplianceStatus.UNKNOWN: "status-unknown",
        }.get(status, "status-unknown")

    def generate_executive_summary(
        self,
        compliance_results: list[ComplianceResult],
        metadata: ReportMetadata,
        output_path: Path,
    ) -> None:
        """
        Generate executive summary Markdown report.

        Args:
            compliance_results: List of compliance results
            metadata: Report metadata
            output_path: Path to write the report
        """
        template = self.env.get_template("executive.md.j2")

        # Compute summary stats
        compliant = [r for r in compliance_results if r.status == ComplianceStatus.COMPLIANT]
        breaches = [r for r in compliance_results if r.status == ComplianceStatus.BREACH]
        unknown = [r for r in compliance_results if r.status == ComplianceStatus.UNKNOWN]

        # Group by subscription
        by_subscription: dict[str, list[ComplianceResult]] = {}
        for result in compliance_results:
            sub_id = result.subscription_id
            if sub_id not in by_subscription:
                by_subscription[sub_id] = []
            by_subscription[sub_id].append(result)

        content = template.render(
            metadata=metadata,
            results=compliance_results,
            compliant=compliant,
            breaches=breaches,
            unknown=unknown,
            by_subscription=by_subscription,
            total=len(compliance_results),
        )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content)
        logger.info(f"Generated executive summary: {output_path}")

    def generate_detailed_report(
        self,
        compliance_results: list[ComplianceResult],
        metadata: ReportMetadata,
        output_path: Path,
    ) -> None:
        """
        Generate detailed HTML report.

        Args:
            compliance_results: List of compliance results
            metadata: Report metadata
            output_path: Path to write the report
        """
        template = self.env.get_template("detailed.html.j2")

        # Sort by status (breaches first, then unknown, then compliant)
        status_order = {
            ComplianceStatus.BREACH: 0,
            ComplianceStatus.UNKNOWN: 1,
            ComplianceStatus.COMPLIANT: 2,
        }
        sorted_results = sorted(
            compliance_results,
            key=lambda r: (status_order.get(r.status, 3), r.resource_name),
        )

        content = template.render(
            metadata=metadata,
            results=sorted_results,
        )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content)
        logger.info(f"Generated detailed report: {output_path}")

    def export_resources_csv(
        self,
        resources: list[ResourceRecord],
        compliance_results: list[ComplianceResult],
        output_path: Path,
    ) -> None:
        """
        Export resources and compliance data to CSV.

        Args:
            resources: List of resources
            compliance_results: Compliance results
            output_path: Path to write CSV
        """
        # Create lookup
        compliance_map = {r.resource_id: r for r in compliance_results}

        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "resource_id",
                "name",
                "type",
                "subscription_id",
                "resource_group",
                "location",
                "sku",
                "tier",
                "availability_percent",
                "sla_target",
                "status",
                "gap",
            ])

            for resource in resources:
                compliance = compliance_map.get(resource.id)
                writer.writerow([
                    resource.id,
                    resource.name,
                    resource.type,
                    resource.subscription_id,
                    resource.resource_group,
                    resource.location,
                    resource.sku or "",
                    resource.tier or "",
                    compliance.actual_availability if compliance else "",
                    compliance.sla_target if compliance else "",
                    compliance.status.value if compliance else "UNKNOWN",
                    compliance.gap if compliance else "",
                ])

        logger.info(f"Exported resources CSV: {output_path}")

    def export_outages_csv(
        self,
        outages: list[OutageRecord],
        output_path: Path,
    ) -> None:
        """
        Export outage records to CSV.

        Args:
            outages: List of outage records
            output_path: Path to write CSV
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "resource_id",
                "resource_name",
                "start_time",
                "end_time",
                "duration_minutes",
                "severity",
            ])

            for outage in outages:
                writer.writerow([
                    outage.resource_id,
                    outage.resource_name,
                    outage.start_time.isoformat(),
                    outage.end_time.isoformat() if outage.end_time else "",
                    outage.duration_minutes,
                    outage.severity,
                ])

        logger.info(f"Exported outages CSV: {output_path}")


def generate_all_reports(
    resources: list[ResourceRecord],
    compliance_results: list[ComplianceResult],
    outages: list[OutageRecord],
    metadata: ReportMetadata,
    output_dir: Path,
    templates_dir: Path | str | None = None,
) -> dict[str, Path]:
    """
    Generate all report outputs.

    Args:
        resources: Discovered resources
        compliance_results: Compliance calculations
        outages: Detected outages
        metadata: Report metadata
        output_dir: Output directory
        templates_dir: Optional custom templates directory

    Returns:
        Dict mapping report type to output path
    """
    generator = ReportGenerator(templates_dir)
    output_dir = Path(output_dir)
    exports_dir = output_dir / "exports"

    outputs: dict[str, Path] = {}

    # Executive summary
    exec_path = output_dir / "executive_summary.md"
    generator.generate_executive_summary(compliance_results, metadata, exec_path)
    outputs["executive_summary"] = exec_path

    # Detailed report
    detail_path = output_dir / "detailed_report.html"
    generator.generate_detailed_report(compliance_results, metadata, detail_path)
    outputs["detailed_report"] = detail_path

    # CSV exports
    resources_csv = exports_dir / "resources.csv"
    generator.export_resources_csv(resources, compliance_results, resources_csv)
    outputs["resources_csv"] = resources_csv

    outages_csv = exports_dir / "outages.csv"
    generator.export_outages_csv(outages, outages_csv)
    outputs["outages_csv"] = outages_csv

    return outputs
