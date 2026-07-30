#  ____                        _  ____      _
# |  _ \ ___  _   _ _ __   __| |/ ___|__ _| | _____
# | |_) / _ \| | | | '_ \ / _` | |   / _` | |/ / _ \
# |  __/ (_) | |_| | | | | (_| | |__| (_| |   <  __/
# |_|   \___/ \__,_|_| |_|\__,_|\____\__,_|_|\_\___|
#
"""Pre-heat service - Creates new orders or increments existing ones."""

import asyncio
import hashlib
import inspect
import random
from datetime import datetime, timezone

from dateutil import parser as dateutil_parser
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError, OperationalError

from api.models.models import Order
from api.services.order_types import ensure_raw_data_order_type
from api.types import WEBHOOK_ALERT_ORDER_TYPE
from api.core.config import get_settings
from api.core.logging import get_logger
from api.plugins.prometheus.watchdog import is_watchdog_alert, record_watchdog_heartbeat
from api.core.metrics import record_order_resolved_before_dish_start
from api.core.statuses import can_transition_to_resolving, is_order_terminal, should_keep_active

logger = get_logger(__name__)
settings = get_settings()

MAX_PRE_HEAT_ATTEMPTS = 3
RETRYABLE_LOCK_ERROR_CODES = {1205, 1213}
RETRY_BACKOFF_SECONDS = (0.05, 0.1, 0.2)


async def _in_transaction(db: AsyncSession) -> bool:
    """Return transaction state for real sessions and async-mocked sessions."""
    in_tx = db.in_transaction()
    if inspect.isawaitable(in_tx):
        in_tx = await in_tx
    return bool(in_tx)


def _db_error_code(exc: OperationalError) -> int | None:
    args = getattr(getattr(exc, "orig", None), "args", ())
    if not args:
        return None
    try:
        return int(args[0])
    except (TypeError, ValueError):
        return None


def _is_retryable_lock_error(exc: OperationalError) -> bool:
    return _db_error_code(exc) in RETRYABLE_LOCK_ERROR_CODES


async def _active_order(db: AsyncSession, fingerprint: str) -> Order | None:
    result = await db.execute(
        select(Order).where(Order.fingerprint_when_active == fingerprint).with_for_update()
    )
    return result.scalars().first()


async def _latest_warning_order(db: AsyncSession, fingerprint: str) -> Order | None:
    result = await db.execute(
        select(Order)
        .where(Order.fingerprint == fingerprint, Order.severity == "warning")
        .order_by(Order.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    return result.scalars().first()


def _correlation_key_from_labels(labels: dict) -> str:
    pairs = [
        f"{str(key)}={str(value)}"
        for key, value in sorted(labels.items(), key=lambda item: str(item[0]))
        if str(key).strip().lower() not in {"alertname", "severity"}
    ]
    return hashlib.sha256("\n".join(pairs).encode("utf-8")).hexdigest()


def _parse_alert_time(value: object) -> datetime:
    if value and isinstance(value, str):
        try:
            return dateutil_parser.isoparse(value)
        except (ValueError, TypeError):
            return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value
    return datetime.now(timezone.utc)


def _noop_warning_values(alert_data: dict, labels: dict, group_name: str) -> dict:
    return {
        "alert_group_name": group_name,
        "alert_status": "firing",
        "processing_status": "complete",
        "is_active": False,
        "severity": "warning",
        "instance": labels.get("instance"),
        "labels": labels,
        "annotations": alert_data.get("annotations", {}),
        "raw_data": ensure_raw_data_order_type(alert_data, WEBHOOK_ALERT_ORDER_TYPE),
        "remediation_outcome": "none",
        "clear_timeout_sec": None,
        "clear_deadline_at": None,
        "clear_timed_out_at": None,
        "auto_close_eligible": False,
        "correlation_key": _correlation_key_from_labels(labels),
    }


def _alert_result(
    status: str,
    existing: Order | None,
    fingerprint: str,
    alert_name: str,
    alert_status: str,
) -> dict:
    return {
        "status": status,
        "order_id": existing.id if existing else None,
        "fingerprint": fingerprint,
        "alert_name": alert_name,
        "alert_status": alert_status,
    }


async def _process_warning_alert(
    alert_data: dict,
    db: AsyncSession,
    req_id: str,
    *,
    fingerprint: str,
    alert_name: str,
    group_name: str,
    alert_status: str,
    labels: dict,
) -> dict:
    existing = await _latest_warning_order(db, fingerprint)
    if alert_status == "resolved":
        if not existing:
            return _alert_result("ignored", None, fingerprint, alert_name, alert_status)
        existing.alert_status = "resolved"
        existing.ends_at = _parse_alert_time(alert_data.get("endsAt"))
        existing.processing_status = "complete"
        existing.remediation_outcome = "none"
        existing.is_active = False
        existing.updated_at = datetime.now(timezone.utc)
        return _alert_result("warning_resolved", existing, fingerprint, alert_name, alert_status)

    if alert_status != "firing":
        return _alert_result("ignored", existing, fingerprint, alert_name, alert_status)

    if existing:
        existing.counter += 1
        existing.alert_status = "firing"
        existing.processing_status = "complete"
        existing.remediation_outcome = "none"
        existing.is_active = False
        existing.correlation_key = _correlation_key_from_labels(labels)
        existing.updated_at = datetime.now(timezone.utc)
        return _alert_result(
            "warning_counter_incremented", existing, fingerprint, alert_name, alert_status
        )

    new_order = Order(
        req_id=req_id,
        fingerprint=fingerprint,
        counter=1,
        starts_at=_parse_alert_time(alert_data.get("startsAt")),
        ends_at=None,
        **_noop_warning_values(alert_data, labels, group_name),
    )
    db.add(new_order)
    await db.flush()
    return _alert_result("warning_recorded", new_order, fingerprint, alert_name, alert_status)


async def _increment_existing_order(
    db: AsyncSession,
    existing: Order,
    *,
    reopen_resolving: bool,
) -> None:
    values = {
        "counter": Order.counter + 1,
        "updated_at": datetime.now(timezone.utc),
    }
    if reopen_resolving:
        values.update(
            {
                "alert_status": "firing",
                "processing_status": (
                    "new"
                    if (existing.processing_status or "").lower() == "resolving"
                    else Order.processing_status
                ),
                "ends_at": None,
                "is_active": True,
            }
        )
    await db.execute(update(Order).where(Order.id == existing.id).values(**values))


def _should_reopen_terminal_order_for_resolve(existing: Order, *, prior_alert_status: str) -> bool:
    status = str(existing.processing_status or "").strip().lower()
    if status not in {"complete", "failed", "errored", "timeout", "canceled"}:
        return False
    return str(prior_alert_status or "").strip().lower() != "resolved"


async def _recover_integrity_conflict(
    db: AsyncSession,
    req_id: str,
    fingerprint: str,
    alert_name: str,
    alert_status: str,
) -> dict:
    await db.rollback()
    async with db.begin():
        existing = await _active_order(db, fingerprint)
        if existing:
            await _increment_existing_order(db, existing, reopen_resolving=False)
            logger.info(
                "Order counter incremented after conflict",
                extra={"req_id": req_id, "order_id": existing.id},
            )
            return _alert_result(
                "counter_incremented", existing, fingerprint, alert_name, alert_status
            )

        logger.error(
            "Order conflict without active order",
            extra={"req_id": req_id, "fingerprint": fingerprint},
        )
        return _alert_result("conflict", None, fingerprint, alert_name, alert_status)


async def _process_alert(
    alert_data: dict,
    db: AsyncSession,
    req_id: str,
) -> dict:
    labels = alert_data.get("labels", {})
    if not isinstance(labels, dict):
        labels = {}
    alert_name = labels.get("alertname", "Unknown")
    group_name = labels.get("group_name") or alert_name
    alert_status = str(alert_data.get("status", "firing") or "firing").strip().lower()
    severity = str(labels.get("severity") or "unknown").strip().lower()

    # Prefer Alertmanager fingerprint; fallback to derived value
    fingerprint = (
        alert_data.get("fingerprint") or f"{alert_name}_{labels.get('instance', 'unknown')}"
    )

    logger.info(
        "Processing order",
        extra={
            "req_id": req_id,
            "alert_name": alert_name,
            "alert_status": alert_status,
            "fingerprint": fingerprint,
        },
    )

    # Intercept watchdog alerts before order processing
    if settings.watchdog_heartbeat_enabled and is_watchdog_alert(labels):
        if await _in_transaction(db):
            await db.rollback()
        watchdog_result = await record_watchdog_heartbeat(
            db,
            alert_data,
            fingerprint=fingerprint,
            alert_name=alert_name,
            req_id=req_id,
        )
        return {
            "status": "watchdog_heartbeat",
            "order_id": None,
            "fingerprint": fingerprint,
            "watchdog": watchdog_result,
        }

    if await _in_transaction(db):
        await db.rollback()

    for attempt in range(1, MAX_PRE_HEAT_ATTEMPTS + 1):
        try:
            async with db.begin():
                if severity == "warning":
                    return await _process_warning_alert(
                        alert_data,
                        db,
                        req_id,
                        fingerprint=fingerprint,
                        alert_name=alert_name,
                        group_name=group_name,
                        alert_status=alert_status,
                        labels=labels,
                    )

                existing = await _active_order(db, fingerprint)

                # Resolved notifications can arrive after the order was already
                # made inactive by dish completion. Fall back to the latest
                # unresolved order for this fingerprint so alert_status can be
                # updated correctly.
                if alert_status == "resolved" and not existing:
                    fallback_result = await db.execute(
                        select(Order)
                        .where(
                            Order.fingerprint == fingerprint,
                            func.lower(Order.alert_status) != "resolved",
                        )
                        .order_by(Order.created_at.desc())
                        .with_for_update()
                    )
                    existing = fallback_result.scalars().first()

                if alert_status == "firing":
                    if not existing:
                        # Create fresh record; status 'new' triggers the Dish flow later
                        # Parse startsAt or use current time as default
                        new_order = Order(
                            req_id=req_id,  # Use request ID from webhook
                            fingerprint=fingerprint,
                            alert_group_name=group_name,
                            alert_status="firing",
                            processing_status="new",
                            is_active=True,
                            severity=labels.get("severity", "unknown"),
                            instance=labels.get("instance"),
                            correlation_key=_correlation_key_from_labels(labels),
                            labels=labels,
                            annotations=alert_data.get("annotations", {}),
                            raw_data=ensure_raw_data_order_type(
                                alert_data,
                                WEBHOOK_ALERT_ORDER_TYPE,
                            ),
                            counter=1,
                            starts_at=_parse_alert_time(alert_data.get("startsAt")),
                            remediation_outcome="pending",
                            clear_timeout_sec=None,
                            clear_deadline_at=None,
                            clear_timed_out_at=None,
                            auto_close_eligible=False,
                        )
                        db.add(new_order)
                        await db.flush()

                        logger.info(
                            "New order created",
                            extra={
                                "req_id": req_id,
                                "order_id": new_order.id,
                                "alert_name": alert_name,
                                "group_name": group_name,
                            },
                        )
                        return _alert_result(
                            "created", new_order, fingerprint, alert_name, alert_status
                        )

                    await _increment_existing_order(db, existing, reopen_resolving=True)
                    reopened_from_resolving = (
                        existing.processing_status or ""
                    ).lower() == "resolving"

                    logger.info(
                        "Order counter incremented",
                        extra={
                            "req_id": req_id,
                            "order_id": existing.id,
                            "reopened_from_resolving": reopened_from_resolving,
                        },
                    )
                    return _alert_result(
                        "counter_incremented", existing, fingerprint, alert_name, alert_status
                    )

                if alert_status == "resolved" and existing:
                    resolved_before_dish = existing.processing_status == "new"
                    prior_alert_status = str(existing.alert_status or "")

                    ends_at = alert_data.get("endsAt")
                    if ends_at and isinstance(ends_at, str):
                        try:
                            ends_at = dateutil_parser.isoparse(ends_at)
                        except (ValueError, TypeError):
                            ends_at = datetime.now(timezone.utc)

                    existing.alert_status = "resolved"
                    existing.ends_at = ends_at
                    if can_transition_to_resolving(existing.processing_status, "alert_resolved"):
                        existing.processing_status = "resolving"
                    elif _should_reopen_terminal_order_for_resolve(
                        existing, prior_alert_status=prior_alert_status
                    ):
                        existing.processing_status = "resolving"
                    if is_order_terminal(existing.processing_status):
                        existing.is_active = False
                    else:
                        existing.is_active = should_keep_active(existing.processing_status)
                    existing.updated_at = datetime.now(timezone.utc)

                    if resolved_before_dish:
                        logger.warning(
                            "Order resolved before any dish started",
                            extra={
                                "req_id": req_id,
                                "order_id": existing.id,
                                "alert_name": alert_name,
                                "group_name": group_name,
                                "severity": existing.severity,
                            },
                        )
                        record_order_resolved_before_dish_start(
                            group_name, existing.severity or "unknown"
                        )

                    logger.info("Order resolved", extra={"req_id": req_id, "order_id": existing.id})
                    return _alert_result(
                        "resolved", existing, fingerprint, alert_name, alert_status
                    )

                logger.debug(
                    "Order ignored",
                    extra={
                        "req_id": req_id,
                        "alert_status": alert_status,
                        "existing": existing is not None,
                    },
                )
                return _alert_result("ignored", existing, fingerprint, alert_name, alert_status)
        except IntegrityError:
            return await _recover_integrity_conflict(
                db, req_id, fingerprint, alert_name, alert_status
            )
        except OperationalError as exc:
            if not _is_retryable_lock_error(exc):
                raise
            await db.rollback()
            error_code = _db_error_code(exc)
            if attempt >= MAX_PRE_HEAT_ATTEMPTS:
                logger.error(
                    "Pre-heat retryable database lock error exhausted",
                    extra={
                        "req_id": req_id,
                        "fingerprint": fingerprint,
                        "attempt": attempt,
                        "max_attempts": MAX_PRE_HEAT_ATTEMPTS,
                        "db_error_code": error_code,
                    },
                )
                raise
            logger.warning(
                "Pre-heat retrying database lock error",
                extra={
                    "req_id": req_id,
                    "fingerprint": fingerprint,
                    "attempt": attempt,
                    "max_attempts": MAX_PRE_HEAT_ATTEMPTS,
                    "db_error_code": error_code,
                },
            )
            backoff = RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
            await asyncio.sleep(backoff + random.uniform(0, 0.025))

    raise RuntimeError("pre_heat retry loop exited unexpectedly")


async def pre_heat(payload: dict, db: AsyncSession, req_id: str) -> dict:
    """
    Intake Handler: Solely responsible for Order table management.

    Args:
        payload: Alertmanager webhook payload
        db: Database session
        req_id: Request ID for tracing

    Returns:
        dict: Status and order_id
    """
    alerts = payload.get("alerts", [])

    if not alerts:
        logger.warning("No alerts in payload", extra={"req_id": req_id})
        return {"status": "no_alerts", "results": []}

    results: list[dict] = []

    for alert_data in alerts:
        results.append(await _process_alert(alert_data, db, req_id))

    if len(results) == 1:
        return {
            "status": results[0]["status"],
            "order_id": results[0].get("order_id"),
            "results": results,
        }

    return {"status": "batch", "order_id": None, "results": results}
