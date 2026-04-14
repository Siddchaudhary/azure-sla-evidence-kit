"""CLI interface using Typer."""

import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Optional

import typer
from dateutil.relativedelta import relativedelta
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from azsla import __version__
from azsla.calculator import batch_calculate_compliance, detect_outages
from azsla.discover import discover_resources
from azsla.metrics import collect_metrics
from azsla.models import (
    AvailabilityResult,
    ComplianceResult,
    ComplianceStatus,
    OutageRecord,
    ReportMetadata,
    ResourceRecord,
)
from azsla.report import generate_all_reports
from azsla.sla_catalog import get_catalog

app = typer.Typer(
    name="azsla",
    help="Azure SLA Report Generator - Discover resources and generate SLA compliance reports",
    add_completion=False,
)
console = Console()


def setup_logging(verbose: bool = False) -> None:
    """Configure logging with rich output."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


def parse_subscriptions(subs: str | None) -> list[str]:
    """Parse comma-separated subscription IDs."""
    if not subs:
        # Try environment variable
        env_subs = os.getenv("AZURE_SUBSCRIPTION_IDS", "")
        if env_subs:
            return [s.strip() for s in env_subs.split(",") if s.strip()]
        return []
    return [s.strip() for s in subs.split(",") if s.strip()]


def get_last_full_month() -> tuple[datetime, datetime]:
    """Get the start and end of the last full calendar month."""
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    end = today.replace(day=1) - timedelta(days=1)
    end = end.replace(hour=23, minute=59, second=59)
    start = end.replace(day=1, hour=0, minute=0, second=0)
    return start, end


def version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        console.print(f"Azure SLA Report Generator v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        Optional[bool],
        typer.Option("--version", "-v", callback=version_callback, is_eager=True),
    ] = None,
) -> None:
    """Azure SLA Report Generator CLI."""
    pass


@app.command()
def discover(
    subscriptions: Annotated[
        Optional[str],
        typer.Option("--subscriptions", "-s", help="Comma-separated subscription IDs"),
    ] = None,
    out: Annotated[
        Path,
        typer.Option("--out", "-o", help="Output file path for discovered resources"),
    ] = Path("resources.json"),
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Enable verbose logging"),
    ] = False,
) -> None:
    """Discover running Azure resources across subscriptions."""
    setup_logging(verbose)

    sub_ids = parse_subscriptions(subscriptions)
    if not sub_ids:
        console.print("[red]Error: No subscriptions specified. Use --subscriptions or set AZURE_SUBSCRIPTION_IDS[/red]")
        raise typer.Exit(1)

    console.print(f"Discovering resources in {len(sub_ids)} subscription(s)...")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Querying Azure Resource Graph...", total=None)
        resources = discover_resources(sub_ids)

    # Display summary
    table = Table(title=f"Discovered {len(resources)} Resources")
    table.add_column("Type", style="cyan")
    table.add_column("Count", justify="right")

    type_counts: dict[str, int] = {}
    for r in resources:
        type_counts[r.type] = type_counts.get(r.type, 0) + 1

    for rtype, count in sorted(type_counts.items()):
        table.add_row(rtype, str(count))

    console.print(table)

    # Save output
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump([r.model_dump() for r in resources], f, indent=2, default=str)

    console.print(f"[green]Saved to {out}[/green]")


@app.command()
def collect(
    resources_file: Annotated[
        Path,
        typer.Option("--resources", "-r", help="Path to resources JSON file"),
    ],
    start: Annotated[
        Optional[str],
        typer.Option("--start", help="Start date (YYYY-MM-DD)"),
    ] = None,
    end: Annotated[
        Optional[str],
        typer.Option("--end", help="End date (YYYY-MM-DD)"),
    ] = None,
    out: Annotated[
        Path,
        typer.Option("--out", "-o", help="Output file path for metrics"),
    ] = Path("metrics.json"),
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Enable verbose logging"),
    ] = False,
) -> None:
    """Collect availability metrics for discovered resources."""
    setup_logging(verbose)

    # Parse dates
    if start and end:
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end).replace(hour=23, minute=59, second=59)
    else:
        start_dt, end_dt = get_last_full_month()
        console.print(f"Using last full month: {start_dt.date()} to {end_dt.date()}")

    # Load resources
    if not resources_file.exists():
        console.print(f"[red]Error: Resources file not found: {resources_file}[/red]")
        raise typer.Exit(1)

    with open(resources_file) as f:
        resources_data = json.load(f)
    resources = [ResourceRecord(**r) for r in resources_data]

    console.print(f"Collecting metrics for {len(resources)} resources...")
    console.print(f"Time window: {start_dt} to {end_dt}")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Querying Azure Monitor...", total=None)
        results = collect_metrics(resources, start_dt, end_dt)

    # Save output
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump([r.model_dump() for r in results], f, indent=2, default=str)

    console.print(f"[green]Saved metrics to {out}[/green]")


@app.command()
def report(
    metrics_file: Annotated[
        Path,
        typer.Option("--metrics", "-m", help="Path to metrics JSON file"),
    ],
    resources_file: Annotated[
        Optional[Path],
        typer.Option("--resources", "-r", help="Path to resources JSON file"),
    ] = None,
    out: Annotated[
        Path,
        typer.Option("--out", "-o", help="Output directory"),
    ] = Path("outputs"),
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Enable verbose logging"),
    ] = False,
) -> None:
    """Generate reports from collected metrics."""
    setup_logging(verbose)

    # Load metrics
    if not metrics_file.exists():
        console.print(f"[red]Error: Metrics file not found: {metrics_file}[/red]")
        raise typer.Exit(1)

    with open(metrics_file) as f:
        metrics_data = json.load(f)
    availability_results = [AvailabilityResult(**m) for m in metrics_data]

    # Load resources if provided
    resources: list[ResourceRecord] = []
    if resources_file and resources_file.exists():
        with open(resources_file) as f:
            resources_data = json.load(f)
        resources = [ResourceRecord(**r) for r in resources_data]
    else:
        # Create minimal resource records from metrics
        for ar in availability_results:
            resources.append(
                ResourceRecord(
                    id=ar.resource_id,
                    name=ar.resource_name,
                    type=ar.resource_type,
                    subscription_id=ar.resource_id.split("/")[2] if "/" in ar.resource_id else "",
                    resource_group="",
                    location="",
                )
            )

    # Calculate compliance
    catalog = get_catalog()
    compliance_results = batch_calculate_compliance(resources, availability_results, catalog)

    # Detect outages
    all_outages: list[OutageRecord] = []
    for ar in availability_results:
        all_outages.extend(detect_outages(ar))

    # Build metadata
    if availability_results:
        start_time = min(ar.start_time for ar in availability_results)
        end_time = max(ar.end_time for ar in availability_results)
    else:
        start_time = end_time = datetime.utcnow()

    metadata = ReportMetadata(
        start_time=start_time,
        end_time=end_time,
        subscriptions=list(set(r.subscription_id for r in resources if r.subscription_id)),
        total_resources=len(resources),
        compliant_count=sum(1 for c in compliance_results if c.status == ComplianceStatus.COMPLIANT),
        breach_count=sum(1 for c in compliance_results if c.status == ComplianceStatus.BREACH),
        unknown_count=sum(1 for c in compliance_results if c.status == ComplianceStatus.UNKNOWN),
        disclaimers=[
            "This report is for informational purposes only.",
            "Availability data is based on Azure Monitor metrics and may not reflect official SLA measurements.",
            "For SLA credit claims, refer to the Azure Portal SLA dashboard.",
        ],
    )

    # Generate reports
    outputs = generate_all_reports(
        resources=resources,
        compliance_results=compliance_results,
        outages=all_outages,
        metadata=metadata,
        output_dir=out,
    )

    console.print("[green]Reports generated:[/green]")
    for name, path in outputs.items():
        console.print(f"  - {name}: {path}")


@app.command()
def run(
    subscriptions: Annotated[
        Optional[str],
        typer.Option("--subscriptions", "-s", help="Comma-separated subscription IDs"),
    ] = None,
    start: Annotated[
        Optional[str],
        typer.Option("--start", help="Start date (YYYY-MM-DD)"),
    ] = None,
    end: Annotated[
        Optional[str],
        typer.Option("--end", help="End date (YYYY-MM-DD)"),
    ] = None,
    out: Annotated[
        Path,
        typer.Option("--out", "-o", help="Output directory"),
    ] = Path("outputs"),
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Enable verbose logging"),
    ] = False,
) -> None:
    """Run end-to-end: discover, collect, and generate reports."""
    setup_logging(verbose)

    sub_ids = parse_subscriptions(subscriptions)
    if not sub_ids:
        console.print("[red]Error: No subscriptions specified. Use --subscriptions or set AZURE_SUBSCRIPTION_IDS[/red]")
        raise typer.Exit(1)

    # Parse dates
    if start and end:
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end).replace(hour=23, minute=59, second=59)
    else:
        start_dt, end_dt = get_last_full_month()

    console.print(f"[bold]Azure SLA Report Generator[/bold]")
    console.print(f"Subscriptions: {', '.join(sub_ids)}")
    console.print(f"Time window: {start_dt.date()} to {end_dt.date()}")
    console.print()

    # Step 1: Discover
    console.print("[bold cyan]Step 1/3: Discovering resources...[/bold cyan]")
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Querying Azure Resource Graph...", total=None)
        resources = discover_resources(sub_ids)

    console.print(f"  Found {len(resources)} resources")

    if not resources:
        console.print("[yellow]No resources found. Exiting.[/yellow]")
        raise typer.Exit(0)

    # Step 2: Collect metrics
    console.print("[bold cyan]Step 2/3: Collecting metrics...[/bold cyan]")
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Querying Azure Monitor...", total=None)
        availability_results = collect_metrics(resources, start_dt, end_dt)

    console.print(f"  Collected metrics for {len(availability_results)} resources")

    # Step 3: Calculate and report
    console.print("[bold cyan]Step 3/3: Generating reports...[/bold cyan]")

    catalog = get_catalog()
    compliance_results = batch_calculate_compliance(resources, availability_results, catalog)

    all_outages: list[OutageRecord] = []
    for ar in availability_results:
        all_outages.extend(detect_outages(ar))

    metadata = ReportMetadata(
        start_time=start_dt,
        end_time=end_dt,
        subscriptions=sub_ids,
        total_resources=len(resources),
        compliant_count=sum(1 for c in compliance_results if c.status == ComplianceStatus.COMPLIANT),
        breach_count=sum(1 for c in compliance_results if c.status == ComplianceStatus.BREACH),
        unknown_count=sum(1 for c in compliance_results if c.status == ComplianceStatus.UNKNOWN),
        disclaimers=[
            "This report is for informational purposes only.",
            "Availability data is based on Azure Monitor metrics and may not reflect official SLA measurements.",
            "For SLA credit claims, refer to the Azure Portal SLA dashboard.",
        ],
    )

    outputs = generate_all_reports(
        resources=resources,
        compliance_results=compliance_results,
        outages=all_outages,
        metadata=metadata,
        output_dir=out,
    )

    # Summary
    console.print()
    table = Table(title="Compliance Summary")
    table.add_column("Status", style="bold")
    table.add_column("Count", justify="right")

    table.add_row("✅ Compliant", str(metadata.compliant_count), style="green")
    table.add_row("❌ Breach", str(metadata.breach_count), style="red")
    table.add_row("⚠️  Unknown", str(metadata.unknown_count), style="yellow")
    table.add_row("Total", str(metadata.total_resources), style="bold")

    console.print(table)
    console.print()
    console.print("[green]Reports generated:[/green]")
    for name, path in outputs.items():
        console.print(f"  - {name}: {path}")


if __name__ == "__main__":
    app()
