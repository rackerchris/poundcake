"""Watchdog heartbeat logic for Prometheus plugin.

Detects always-firing "deadman" watchdog alerts, records each heartbeat tick,
and creates a synthetic "watchdog missing" incident when the heartbeat stops.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import get_settings
from api.core.logging import get_logger
from api.core.time import utc_now_db
from api.models.models import Order, WatchdogHeartbeatState

logger = get_logger(__name__)

WATCHDOG_GROUP_NAMES = {"watchdog", "poundcake_watchdog", "deadman", "dead_mans_switch"}
WATCHDOG_ALERT_NAMES = {"Watchdog", "PoundCakeWatchdog", "Deadman", "DeadMansSwitch"}


def is_watchdog_alert(labels: dict | None) -> bool:
    """Return True if the alert labels indicate a watchdog/deadman alert."""
    if not labels or not isinstance(labels, dict):
        return False
    group_name = str(labels.get("group_name") or labels.get("alertgroup") or "").strip().lower()
    alert_name = str(labels.get("alertname") or labels.get("alert_name") or "").strip().lower()
    return (group_name in WATCHDOG_GROUP_NAMES) or (alert_name in WATCHDOG_ALERT_NAMES)


async def record_watchdog_heartbeat(
    db: AsyncSession,
    alert_data: dict,
    *,
    fingerprint: str,
    alert_name: str,
    req_id: str,
) -> dict:
    """Record a watchdog heartbeat tick and resolve any "missing" synthetic incident."""
    now = utc_now_db()

    status = str(alert_data.get("status") or "firing").strip().lower()

    result = await db.execute(
        select(WatchdogHeartbeatState).where(WatchdogHeartbeatState.heartbeat_key == fingerprint)
    )
    state = result.scalar_one_or_none()

    if state is None:
        state = WatchdogHeartbeatState(
            heartbeat_key=fingerprint,
            alert_name=alert_name,
            alert_fingerprint=fingerprint,
        )
        db.add(state)

    state.alert_name = alert_name
    state.alert_fingerprint = fingerprint
    state.last_status = status
    state.last_seen_at = now
    state.last_received_at = now
    state.missing_since = None
    state.last_payload = alert_data
    state.updated_at = now

    # If there's an existing synthetic "watchdog missing" order, resolve it
    if state.synthetic_order_id:
        order = await db.get(Order, state.synthetic_order_id)
        if order and order.processing_status not in (
            "complete",
            "canceled",
            "failed",
            "errored",
            "timeout",
        ):
            order.processing_status = "resolving"
            order.alert_status = "resolved"
            order.is_active = True
            order.remediation_outcome = "none"
            order.auto_close_eligible = True
            order.updated_at = now
            logger.info(
                "Watchdog heartbeat restored — resolving synthetic missing order",
                extra={
                    "req_id": req_id,
                    "order_id": order.id,
                    "fingerprint": fingerprint,
                },
            )

    await db.flush()
    return {
        "success": True,
        "status": "heartbeat_recorded",
        "fingerprint": fingerprint,
        "last_status": status,
        "last_received_at": now.isoformat(),
    }


async def check_watchdog_heartbeat_once(db: AsyncSession) -> dict:
    """Periodic check: create/open synthetic order if watchdog heartbeat is missing."""
    settings = get_settings()
    now = utc_now_db()
    threshold = timedelta(seconds=settings.watchdog_heartbeat_missing_threshold_seconds)

    result = await db.execute(
        select(WatchdogHeartbeatState).where(WatchdogHeartbeatState.last_received_at.isnot(None))
    )
    states = result.scalars().all()

    created_any = False
    resolved_any = False

    for state in states:
        missing_since = state.missing_since
        last_received = state.last_received_at

        if last_received and (now - last_received) > threshold:
            # Heartbeat is missing
            if not missing_since:
                state.missing_since = now
                state.updated_at = now

            # Create or open a synthetic "watchdog missing" order if not already open
            if not state.synthetic_order_id:
                await _create_synthetic_missing_order(db, state, now)
                created_any = True

        else:
            # Heartbeat is healthy — clear missing_since
            if state.missing_since:
                state.missing_since = None
                state.updated_at = now
                resolved_any = True

    await db.flush()
    return {
        "success": True,
        "status": "check_complete",
        "states_checked": len(states),
        "created_any": created_any,
        "resolved_any": resolved_any,
    }


async def _create_synthetic_missing_order(
    db: AsyncSession, state: WatchdogHeartbeatState, now: datetime
) -> None:
    """Create a synthetic order to represent a missing watchdog heartbeat."""
    fingerprint = state.alert_fingerprint or f"poundcake:watchdog:missing:{state.heartbeat_key}"
    alert_name = state.alert_name or "PoundCakeWatchdogMissing"

    # Check if there's already an active order for this fingerprint
    existing = await db.execute(
        select(Order).where(
            Order.fingerprint == fingerprint,
            Order.processing_status.in_(("new", "processing", "resolving")),
        )
    )
    order = existing.scalar_one_or_none()

    if order is None:
        order = Order(
            req_id=f"watchdog-missing-{now.isoformat()}",
            fingerprint=fingerprint,
            alert_group_name=alert_name,
            alert_status="firing",
            processing_status="new",
            is_active=True,
            labels={
                "alertname": alert_name,
                "group_name": "watchdog",
                "poundcake_synthetic": "watchdog_missing",
                "watchdog_fingerprint": state.heartbeat_key,
            },
            raw_data={
                "alert_status": "firing",
                "alert_name": alert_name,
                "watchdog_missing_since": (state.missing_since or now).isoformat(),
                "watchdog_last_received": (state.last_received_at or now).isoformat(),
            },
        )
        db.add(order)
        await db.flush()
        state.synthetic_order_id = order.id
        state.updated_at = now

        logger.warning(
            "Watchdog heartbeat missing — created synthetic order",
            extra={
                "order_id": order.id,
                "fingerprint": fingerprint,
                "missing_since": state.missing_since.isoformat() if state.missing_since else None,
            },
        )


# =============================================================================
# Background checker task
# =============================================================================

_watchdog_checker_task: asyncio.Task | None = None


def start_watchdog_heartbeat_checker(db_factory: callable) -> None:
    """Start the background watchdog heartbeat checker task."""
    global _watchdog_checker_task
    if _watchdog_checker_task is not None:
        return

    settings = get_settings()
    interval = settings.watchdog_heartbeat_check_interval_seconds

    async def _loop() -> None:
        while True:
            try:
                factory = db_factory()
                async with factory() as session:
                    async with session.begin():
                        result = await check_watchdog_heartbeat_once(session)
                        await session.commit()
                        logger.debug(
                            "Watchdog heartbeat check complete",
                            extra=result,
                        )
            except Exception:
                logger.exception("Watchdog heartbeat check failed")
            await asyncio.sleep(interval)

    _watchdog_checker_task = asyncio.create_task(_loop())
    logger.info(
        "Started watchdog heartbeat checker",
        extra={"interval_seconds": interval},
    )


def stop_watchdog_heartbeat_checker() -> None:
    """Stop the background watchdog heartbeat checker task."""
    global _watchdog_checker_task
    if _watchdog_checker_task is not None:
        _watchdog_checker_task.cancel()
        _watchdog_checker_task = None
        logger.info("Stopped watchdog heartbeat checker")
