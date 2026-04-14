"""Export API endpoints - CSV and other formats."""

import csv
import io
from datetime import datetime
from typing import Optional, List, AsyncGenerator

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from azsla.db.database import get_db
from azsla.db.repository import ResourceRepository, MetricsRepository, SubscriptionRepository
from azsla.web.rate_limit import rate_limit_export

router = APIRouter()


@router.get("/csv")
@rate_limit_export
async def export_csv(
    request: Request,
    subscription_ids: Optional[List[str]] = Query(None, alias="sub"),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Export SLA compliance data as CSV."""
    resource_repo = ResourceRepository(db)
    metrics_repo = MetricsRepository(db)
    sub_repo = SubscriptionRepository(db)

    # Get resources
    resources = await resource_repo.get_all_active(subscription_ids=subscription_ids)
    
    # Get latest metrics
    latest_metrics = await metrics_repo.get_latest_for_all_resources(
        subscription_ids=subscription_ids
    )
    metrics_by_resource = {m.resource_id: m for m in latest_metrics}
    
    # Get subscriptions for name lookup
    subscriptions = await sub_repo.get_all_active()
    sub_names = {s.id: s.name or s.id for s in subscriptions}

    async def generate_csv() -> AsyncGenerator[str, None]:
        """Stream CSV content row by row for memory efficiency."""
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write header
        writer.writerow([
            "Resource Name",
            "Resource Type",
            "Subscription",
            "Resource Group",
            "Location",
            "Availability %",
            "SLA Target %",
            "Gap %",
            "Compliance Status",
            "Last Measured",
        ])
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)
        
        # Write data rows in batches for streaming
        batch_size = 100
        for i, r in enumerate(resources):
            metric = metrics_by_resource.get(r.id)
            type_short = r.type.split("/")[-1] if r.type else ""
            writer.writerow([
                r.name,
                type_short,
                sub_names.get(r.subscription_id, r.subscription_id[:8]),
                r.resource_group,
                r.location,
                f"{metric.availability_percent:.2f}" if metric and metric.availability_percent else "",
                f"{metric.sla_target:.2f}" if metric and metric.sla_target else "",
                f"{(metric.availability_percent - metric.sla_target):.2f}" if metric and metric.availability_percent and metric.sla_target else "",
                metric.compliance_status if metric else "",
                metric.collected_at.strftime("%Y-%m-%d %H:%M UTC") if metric and metric.collected_at else "",
            ])
            
            # Yield in batches to stream large datasets
            if (i + 1) % batch_size == 0:
                yield output.getvalue()
                output.seek(0)
                output.truncate(0)
        
        # Yield remaining rows
        remaining = output.getvalue()
        if remaining:
            yield remaining

    # Prepare response
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"sla_compliance_report_{timestamp}.csv"
    
    return StreamingResponse(
        generate_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
