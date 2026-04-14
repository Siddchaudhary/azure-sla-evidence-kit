"""Subscriptions API endpoints."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Body
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from azsla.db.database import get_db
from azsla.db.repository import SubscriptionRepository, ResourceRepository

router = APIRouter()


class SubscriptionResponse(BaseModel):
    """Subscription response model."""
    id: str
    name: Optional[str]
    is_active: bool
    resource_count: int
    created_at: datetime
    updated_at: datetime


class SubscriptionCreate(BaseModel):
    """Subscription creation request."""
    id: str
    name: Optional[str] = None


@router.get("", response_model=list[SubscriptionResponse])
async def list_subscriptions(
    db: AsyncSession = Depends(get_db),
) -> list[SubscriptionResponse]:
    """List all monitored subscriptions."""
    sub_repo = SubscriptionRepository(db)
    resource_repo = ResourceRepository(db)

    subscriptions = await sub_repo.get_all_active()
    
    results = []
    for sub in subscriptions:
        resources = await resource_repo.get_by_subscription(sub.id)
        results.append(SubscriptionResponse(
            id=sub.id,
            name=sub.name,
            is_active=sub.is_active,
            resource_count=len(resources),
            created_at=sub.created_at,
            updated_at=sub.updated_at,
        ))

    return results


@router.post("", response_model=SubscriptionResponse)
async def add_subscription(
    subscription: SubscriptionCreate,
    db: AsyncSession = Depends(get_db),
) -> SubscriptionResponse:
    """Add a subscription to monitor."""
    sub_repo = SubscriptionRepository(db)

    sub = await sub_repo.upsert(subscription.id, subscription.name)
    await db.commit()

    return SubscriptionResponse(
        id=sub.id,
        name=sub.name,
        is_active=sub.is_active,
        resource_count=0,
        created_at=sub.created_at,
        updated_at=sub.updated_at,
    )


@router.delete("/{subscription_id}")
async def remove_subscription(
    subscription_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Remove a subscription from monitoring."""
    sub_repo = SubscriptionRepository(db)

    await sub_repo.deactivate(subscription_id)
    await db.commit()

    return {"status": "deactivated", "subscription_id": subscription_id}


@router.get("/{subscription_id}", response_model=SubscriptionResponse)
async def get_subscription(
    subscription_id: str,
    db: AsyncSession = Depends(get_db),
) -> SubscriptionResponse:
    """Get a specific subscription."""
    sub_repo = SubscriptionRepository(db)
    resource_repo = ResourceRepository(db)

    subscriptions = await sub_repo.get_all_active()
    sub = next((s for s in subscriptions if s.id == subscription_id), None)

    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")

    resources = await resource_repo.get_by_subscription(sub.id)

    return SubscriptionResponse(
        id=sub.id,
        name=sub.name,
        is_active=sub.is_active,
        resource_count=len(resources),
        created_at=sub.created_at,
        updated_at=sub.updated_at,
    )
