"""Release update notification delivery logic."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.core.logging import get_logger
from api.models.models import (
    ReleaseUpdateNotification,
    ReleaseUpdateNotificationDelivery,
)

logger = get_logger(__name__)

SYSTEM_SOURCE = "poundcake_system"
NOTIFIED_STATE = "notified"
SUCCEEDED_DELIVERY_STATE = "succeeded"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def _load_notification(
    db: AsyncSession,
    *,
    oci_repository: str,
    available_app_version: str,
    available_chart_version: str,
    for_update: bool = False,
) -> ReleaseUpdateNotification | None:
    query = (
        select(ReleaseUpdateNotification)
        .options(selectinload(ReleaseUpdateNotification.deliveries))
        .where(
            ReleaseUpdateNotification.oci_repository == oci_repository,
            ReleaseUpdateNotification.available_app_version == available_app_version,
            ReleaseUpdateNotification.available_chart_version == available_chart_version,
        )
    )
    if for_update:
        query = query.with_for_update()
    result = await db.execute(query)
    return result.unique().scalars().first()


async def _get_or_create_notification(
    db: AsyncSession,
    *,
    oci_repository: str,
    current_app_version: str,
    current_chart_version: str,
    available_app_version: str,
    available_chart_version: str,
    available_created_at: datetime | None = None,
) -> ReleaseUpdateNotification:
    existing = await _load_notification(
        db,
        oci_repository=oci_repository,
        available_app_version=available_app_version,
        available_chart_version=available_chart_version,
        for_update=True,
    )
    if existing is not None:
        return existing

    notification = ReleaseUpdateNotification(
        oci_repository=oci_repository,
        current_app_version=current_app_version,
        current_chart_version=current_chart_version,
        available_app_version=available_app_version,
        available_chart_version=available_chart_version,
        available_created_at=available_created_at,
        state="pending",
    )
    db.add(notification)
    await db.flush()
    return notification


async def _snapshot_routes_if_needed(
    db: AsyncSession,
    notification: ReleaseUpdateNotification,
) -> list[ReleaseUpdateNotificationDelivery]:
    count = await db.scalar(
        select(func.count(ReleaseUpdateNotificationDelivery.id)).where(
            ReleaseUpdateNotificationDelivery.notification_id == notification.id
        )
    )
    if int(count or 0) > 0:
        return list(notification.deliveries or [])

    from api.services.communications_policy import get_global_policy_routes

    routes = [route for route in await get_global_policy_routes(db) if route.enabled]
    if not routes:
        notification.state = "blocked"
        notification.latest_error = "No enabled global communications routes are configured."
        notification.updated_at = _utc_now()
        return []

    deliveries: list[ReleaseUpdateNotificationDelivery] = []
    for route in routes:
        delivery = ReleaseUpdateNotificationDelivery(
            notification_id=notification.id,
            route_id=route.id,
            route_label=route.label,
            execution_target=route.execution_target,
            destination_target=route.destination_target or "",
            provider_config=route.provider_config or {},
            state="pending",
        )
        db.add(delivery)
        deliveries.append(delivery)
    notification.deliveries = deliveries
    notification.state = "notifying"
    notification.latest_error = None
    notification.updated_at = _utc_now()
    await db.flush()
    return deliveries


def build_advisory_payload(
    notification: ReleaseUpdateNotification,
    delivery: ReleaseUpdateNotificationDelivery,
) -> dict[str, Any]:
    current_chart = notification.current_chart_version or "unknown"
    available_created = (
        notification.available_created_at.isoformat()
        if notification.available_created_at
        else "unknown"
    )
    description = (
        f"A newer PoundCake release is available.\n\n"
        f"Current app version: {notification.current_app_version}\n"
        f"Current chart version: {current_chart}\n"
        f"Available app version: {notification.available_app_version}\n"
        f"Available chart version: {notification.available_chart_version}\n"
        f"OCI repository: {notification.oci_repository}\n"
        f"Release published: {available_created}\n\n"
        "PoundCake did not perform an automatic upgrade. Review the release and run the "
        "normal install/upgrade process when ready."
    )
    route_metadata = {
        "scope": "global",
        "owner_key": "global",
        "route_id": delivery.route_id,
        "label": delivery.route_label,
        "execution_target": delivery.execution_target,
        "destination_target": delivery.destination_target or "",
        "provider_config": delivery.provider_config or {},
        "enabled": True,
        "outage_enabled": False,
        "position": 0,
    }
    context = {
        "source": SYSTEM_SOURCE,
        "category": "release_update",
        "oci_repository": notification.oci_repository,
        "current_app_version": notification.current_app_version,
        "current_chart_version": notification.current_chart_version,
        "available_app_version": notification.available_app_version,
        "available_chart_version": notification.available_chart_version,
        "release_update_notification_id": notification.id,
        "provider_type": delivery.execution_target,
        "execution_target": delivery.execution_target,
        "destination_target": delivery.destination_target or "",
        "provider_config": delivery.provider_config or {},
        "scope": "global",
        "owner_key": "global",
        "route_id": delivery.route_id,
        "route_label": delivery.route_label,
        "route_metadata": route_metadata,
    }
    return {
        "title": f"[PoundCake Update Available] {notification.available_app_version} available",
        "description": description,
        "message": description,
        "severity": "info",
        "category": "release_update",
        "source": SYSTEM_SOURCE,
        "context": context,
    }


def _idempotency_key(
    notification: ReleaseUpdateNotification,
    delivery: ReleaseUpdateNotificationDelivery,
) -> str:
    seed = (
        "release-update:"
        f"{notification.oci_repository}:"
        f"{notification.available_app_version}:"
        f"{notification.available_chart_version}:"
        f"{delivery.route_id}"
    )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


async def process_release_notification(
    db: AsyncSession,
    *,
    oci_repository: str,
    current_app_version: str,
    current_chart_version: str,
    available_app_version: str,
    available_chart_version: str,
    available_created_at: datetime | None = None,
    req_id: str,
) -> dict:
    notification = await _get_or_create_notification(
        db,
        oci_repository=oci_repository,
        current_app_version=current_app_version,
        current_chart_version=current_chart_version,
        available_app_version=available_app_version,
        available_chart_version=available_chart_version,
        available_created_at=available_created_at,
    )

    if notification.state == NOTIFIED_STATE:
        return {
            "status": "already_notified",
            "notification_id": notification.id,
            "message": "This release has already been notified.",
        }

    deliveries = await _snapshot_routes_if_needed(db, notification)

    if not deliveries:
        return {
            "status": "blocked",
            "notification_id": notification.id,
            "message": "No enabled global communications routes are configured.",
        }

    return {
        "status": "dispatched",
        "notification_id": notification.id,
        "delivery_count": len(deliveries),
        "deliveries": [
            {
                "delivery_id": d.id,
                "route_id": d.route_id,
                "route_label": d.route_label,
                "state": d.state,
            }
            for d in deliveries
        ],
    }
