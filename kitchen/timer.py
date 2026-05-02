#!/usr/bin/env python3
"""Timer: runtime crawler and reconciler for service plugin executions."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

from api.core.logging import get_logger, setup_logging
from api.core.config import get_settings
from api.plugins.state import TERMINAL_EXECUTION_STATUSES, sla_exceeded
from api.types import JSONObject
from kitchen.service_helpers import (
    get_worker_runtime_config,
    request_control_plane_sync,
    wait_for_api,
)

setup_logging()
logger = get_logger("timer")

POUNDCAKE_API_URL = os.getenv("POUNDCAKE_API_URL", "http://poundcake:8080").rstrip("/")
API_BASE_URL = f"{POUNDCAKE_API_URL}/api/v1"
TIMER_INTERVAL = int(os.getenv("TIMER_INTERVAL", "10"))
POLL_LIMIT = int(os.getenv("TIMER_LIMIT", "100"))
SYSTEM_REQ_ID = os.getenv("TIMER_WORKER_ID", f"SYSTEM-TIMER-{os.getpid()}")
POLLER_RETRIES = get_settings().poller_http_retries
EXPEDITER_STATUS_POLL_TIMEOUT_MIN = 10
EXPEDITER_STATUS_POLL_TIMEOUT_MAX = max(
    EXPEDITER_STATUS_POLL_TIMEOUT_MIN,
    int(os.getenv("TIMER_EXPEDITER_STATUS_TIMEOUT_MAX", "60")),
)


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _runtime_exceeded_timeout(row: JSONObject) -> bool:
    timeout = row.get("service_exec_timeout")
    started = _parse_datetime(row.get("service_exec_start_time"))
    if not isinstance(timeout, int) or timeout <= 0 or started is None:
        return False
    return (datetime.now(timezone.utc) - started).total_seconds() >= timeout


def _expediter_status_poll_timeout(row: JSONObject) -> int:
    timeout = row.get("service_exec_timeout")
    if not isinstance(timeout, int) or timeout <= 0:
        return EXPEDITER_STATUS_POLL_TIMEOUT_MIN
    return min(
        max(EXPEDITER_STATUS_POLL_TIMEOUT_MIN, timeout),
        EXPEDITER_STATUS_POLL_TIMEOUT_MAX,
    )


def _reconcile_payload(
    row: JSONObject,
    *,
    status: str,
    actual_outcome: JSONObject | None,
    error: str | None,
) -> JSONObject:
    actual_outcome = _actual_outcome_with_context_updates(row, actual_outcome)
    now = datetime.now(timezone.utc)
    started = _parse_datetime(row.get("service_exec_start_time"))
    run_time = int((now - started).total_seconds()) if started else None
    expected_secs = row.get("service_exec_expected_secs")
    expected = expected_secs if isinstance(expected_secs, int) else None
    payload: JSONObject = {
        "service_exec_status": status,
        "service_exec_completed_time": now.isoformat(),
        "service_exec_actual_outcome": actual_outcome,
        "service_exec_error": error,
        "service_exec_sla_exceeded": sla_exceeded(expected, run_time),
    }
    if run_time is not None:
        payload["service_exec_run_time"] = max(0, run_time)
    return payload


def _actual_outcome_with_context_updates(
    row: JSONObject,
    actual_outcome: JSONObject | None,
) -> JSONObject | None:
    existing = row.get("service_exec_actual_outcome")
    if not isinstance(existing, dict):
        return actual_outcome
    updates = existing.get("_context_updates") or existing.get("context_updates")
    if not isinstance(updates, dict) or not updates:
        return actual_outcome
    if not isinstance(actual_outcome, dict):
        return {"_context_updates": dict(updates)}
    merged = dict(actual_outcome)
    existing_updates = merged.get("_context_updates")
    if isinstance(existing_updates, dict):
        merged["_context_updates"] = {**updates, **existing_updates}
    else:
        merged["_context_updates"] = dict(updates)
    return merged


def _post_reconcile(row: JSONObject, payload: JSONObject, req_id: str) -> JSONObject | None:
    row_id = row.get("id")
    response = request_control_plane_sync(
        "POST",
        f"{API_BASE_URL}/dish-ingredients/{row_id}/reconcile",
        json=payload,
        req_id=req_id,
        timeout=10,
        retries=POLLER_RETRIES,
    )
    if response.status_code >= 400:
        logger.warning(
            "Runtime reconciliation failed",
            extra={
                "req_id": req_id,
                "dish_ingredient_id": row_id,
                "status_code": response.status_code,
            },
        )
        return None
    logger.info(
        "Runtime row reconciled",
        extra={
            "req_id": req_id,
            "dish_ingredient_id": row_id,
            "dish_id": row.get("dish_id"),
            "service_type": row.get("service_type"),
            "service_exec": row.get("service_exec"),
            "service_exec_status": payload.get("service_exec_status"),
            "service_exec_run_time": payload.get("service_exec_run_time"),
        },
    )
    body = response.json()
    return body if isinstance(body, dict) else None


def _claim_row(row: JSONObject, req_id: str) -> JSONObject | None:
    row_id = row.get("id")
    response = request_control_plane_sync(
        "POST",
        f"{API_BASE_URL}/dish-ingredients/{row_id}/poll-claim",
        req_id=req_id,
        timeout=10,
        retries=POLLER_RETRIES,
    )
    if response.status_code == 409:
        return None
    if response.status_code >= 400:
        logger.warning(
            "Runtime poll claim failed",
            extra={
                "req_id": req_id,
                "dish_ingredient_id": row_id,
                "status_code": response.status_code,
            },
        )
        return None
    claimed = response.json()
    return claimed if isinstance(claimed, dict) else None


def _release_row(row: JSONObject, req_id: str) -> None:
    row_id = row.get("id")
    response = request_control_plane_sync(
        "POST",
        f"{API_BASE_URL}/dish-ingredients/{row_id}/poll-release",
        req_id=req_id,
        timeout=10,
        retries=POLLER_RETRIES,
    )
    if response.status_code >= 400 and response.status_code != 409:
        logger.warning(
            "Runtime poll release failed",
            extra={
                "req_id": req_id,
                "dish_ingredient_id": row_id,
                "status_code": response.status_code,
            },
        )


def _advance_dish(row: JSONObject, req_id: str) -> None:
    dish_id = row.get("dish_id")
    if not isinstance(dish_id, int):
        return
    response = request_control_plane_sync(
        "POST",
        f"{API_BASE_URL}/cook/dishes/{dish_id}/advance",
        req_id=req_id,
        timeout=30,
        retries=POLLER_RETRIES,
    )
    if response.status_code >= 400:
        logger.warning(
            "Cook advance failed after reconciliation",
            extra={"req_id": req_id, "dish_id": dish_id, "status_code": response.status_code},
        )
        return
    logger.info(
        "Cook advance requested after runtime reconciliation",
        extra={"req_id": req_id, "dish_id": dish_id, "status_code": response.status_code},
    )


def _cancel_execution(row: JSONObject, req_id: str) -> JSONObject | None:
    service_type = str(row.get("service_type") or "").strip().lower()
    service_exec_id = str(row.get("service_exec_id") or "").strip()
    if not service_type or not service_exec_id:
        return None
    response = request_control_plane_sync(
        "POST",
        f"{API_BASE_URL}/expediter/cancel/{service_type}/{service_exec_id}",
        req_id=req_id,
        timeout=10,
        retries=POLLER_RETRIES,
    )
    if response.status_code >= 400:
        logger.warning(
            "Expediter cancel failed",
            extra={
                "req_id": req_id,
                "dish_ingredient_id": row.get("id"),
                "status_code": response.status_code,
            },
        )
        return None
    body = response.json()
    logger.info(
        "Expediter cancel completed",
        extra={
            "req_id": req_id,
            "dish_ingredient_id": row.get("id"),
            "dish_id": row.get("dish_id"),
            "service_type": service_type,
            "service_exec_id": service_exec_id,
            "status_code": response.status_code,
        },
    )
    return body if isinstance(body, dict) else None


def _cancel_row_without_advance(row: JSONObject, req_id: str, *, reason: str) -> JSONObject | None:
    claimed = _claim_row(row, req_id)
    if claimed is None:
        return None
    row = claimed
    service_exec_id = str(row.get("service_exec_id") or "").strip()
    cancel_body = _cancel_execution(row, req_id)
    outcome = None
    if cancel_body:
        body_outcome = cancel_body.get("service_exec_actual_outcome") or cancel_body.get("raw")
        outcome = body_outcome if isinstance(body_outcome, dict) else None
    if outcome is None:
        outcome = {
            "status": "canceled",
            "service_exec_id": service_exec_id,
            "reason": reason,
        }
    payload = _reconcile_payload(
        row,
        status="canceled",
        actual_outcome=outcome,
        error=reason,
    )
    payload["service_exec_canceled_time"] = payload["service_exec_completed_time"]
    return row if _post_reconcile(row, payload, req_id) else None


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


def _execution_bucket(row: JSONObject) -> tuple[int, int]:
    depth = _coerce_int(row.get("depth"))
    if depth is None:
        depth = _coerce_int(row.get("step_order")) or 1_000_000
    parallel_group = _coerce_int(row.get("parallel_group")) or 0
    return depth, parallel_group


def _is_blocking_failure(row: JSONObject, reconciled: JSONObject) -> bool:
    if not isinstance(reconciled, dict):
        return False
    status = str(reconciled.get("service_exec_status") or "").strip().lower()
    on_failure = str(row.get("on_failure") or "stop").strip().lower()
    return status in {"failed", "errored", "timeout", "canceled"} and on_failure != "continue"


def _is_blocking_terminal_row(row: JSONObject) -> bool:
    return _is_blocking_failure(row, row)


def _blocking_failure_reason(row: JSONObject) -> str:
    params = row.get("service_exec_parameters")
    outcome = row.get("service_exec_actual_outcome")
    if (
        isinstance(params, dict)
        and params.get("guard_role") == "remediation_precondition"
        and params.get("false_outcome") == "cancel_downstream_no_remediation"
        and isinstance(outcome, dict)
        and outcome.get("is_firing") is False
    ):
        return "Remediation skipped because Alertmanager no longer shows the alert firing"
    return str(row.get("service_exec_error") or "Blocking service execution failed")


def _poll_result(
    row: JSONObject,
    req_id: str,
    *,
    reconciled: JSONObject | None = None,
    terminal: bool = False,
    blocking_failure: bool = False,
    reason: str | None = None,
) -> JSONObject:
    dish_id = row.get("dish_id")
    return {
        "dish_id": dish_id if isinstance(dish_id, int) else None,
        "req_id": req_id,
        "row_id": row.get("id"),
        "row": row,
        "reconciled": reconciled,
        "terminal": terminal,
        "blocking_failure": blocking_failure,
        "reason": reason,
        "bucket": _execution_bucket(row),
    }


def _cancel_blocked_future_rows(row: JSONObject, req_id: str, *, reason: str) -> None:
    dish_id = row.get("dish_id")
    if not isinstance(dish_id, int):
        return
    response = request_control_plane_sync(
        "GET",
        f"{API_BASE_URL}/dishes/{dish_id}/ingredients",
        req_id=req_id,
        timeout=10,
        retries=POLLER_RETRIES,
    )
    if response.status_code >= 400:
        logger.warning(
            "Failed to fetch dish ingredients for blocked-row cancellation",
            extra={"req_id": req_id, "dish_id": dish_id, "status_code": response.status_code},
        )
        return
    rows = response.json()
    if not isinstance(rows, list):
        return
    failed_bucket = _execution_bucket(row)
    canceled_count = 0
    for candidate in rows:
        if not isinstance(candidate, dict):
            continue
        if candidate.get("service_exec_status") != "pending":
            continue
        if _execution_bucket(candidate) <= failed_bucket:
            continue
        payload = _reconcile_payload(
            candidate,
            status="canceled",
            actual_outcome={
                "success": False,
                "status": "canceled",
                "reason": "blocked_by_prior_group_failure",
                "message": reason,
                "blocked_by_dish_ingredient_id": row.get("id"),
            },
            error=reason,
        )
        payload["service_exec_canceled_time"] = payload["service_exec_completed_time"]
        if _post_reconcile(candidate, payload, req_id):
            canceled_count += 1
    if canceled_count:
        logger.info(
            "Timer canceled blocked future runtime rows",
            extra={
                "req_id": req_id,
                "dish_id": dish_id,
                "blocked_by_dish_ingredient_id": row.get("id"),
                "canceled_count": canceled_count,
            },
        )


def _fetch_dish_ingredients(dish_id: int, req_id: str) -> list[JSONObject] | None:
    response = request_control_plane_sync(
        "GET",
        f"{API_BASE_URL}/dishes/{dish_id}/ingredients",
        req_id=req_id,
        timeout=10,
        retries=POLLER_RETRIES,
    )
    if response.status_code >= 400:
        logger.warning(
            "Failed to fetch dish ingredients for group reconciliation",
            extra={"req_id": req_id, "dish_id": dish_id, "status_code": response.status_code},
        )
        return None
    rows = response.json()
    if not isinstance(rows, list):
        return None
    return [row for row in rows if isinstance(row, dict)]


def _group_has_in_flight(rows: list[JSONObject], bucket: tuple[int, int]) -> bool:
    return any(
        _execution_bucket(row) == bucket
        and str(row.get("service_exec_status") or "").strip().lower() in {"dispatched", "running"}
        for row in rows
    )


def _group_rows(rows: list[JSONObject]) -> dict[tuple[int, tuple[int, int]], list[JSONObject]]:
    grouped: dict[tuple[int, tuple[int, int]], list[JSONObject]] = {}
    for row in rows:
        dish_id = row.get("dish_id")
        if not isinstance(dish_id, int):
            continue
        grouped.setdefault((dish_id, _execution_bucket(row)), []).append(row)
    return grouped


def _poll_row(row: JSONObject, req_id: str) -> JSONObject:
    claimed = _claim_row(row, req_id)
    if claimed is None:
        return _poll_result(row, req_id)
    row = claimed
    row_id = row.get("id")
    service_type = str(row.get("service_type") or "").strip().lower()
    service_exec_id = str(row.get("service_exec_id") or "").strip()
    if not service_type or not service_exec_id:
        if not _runtime_exceeded_timeout(row):
            logger.debug(
                "Runtime row is not pollable until service execution identity is recorded",
                extra={
                    "req_id": req_id,
                    "dish_ingredient_id": row_id,
                    "dish_id": row.get("dish_id"),
                    "service_type": service_type,
                    "has_service_exec_id": bool(service_exec_id),
                },
            )
            _release_row(row, req_id)
            return _poll_result(row, req_id)
        payload = _reconcile_payload(
            row,
            status="timeout",
            actual_outcome={
                "success": False,
                "status": "timeout",
                "reason": "missing_service_execution_identity",
            },
            error="Missing service_type or service_exec_id",
        )
        reconciled = _post_reconcile(row, payload, req_id)
        blocking = bool(reconciled and _is_blocking_failure(row, reconciled))
        return _poll_result(
            row,
            req_id,
            reconciled=reconciled,
            terminal=bool(reconciled),
            blocking_failure=blocking,
            reason="Service execution identity was not recorded before timeout",
        )

    try:
        response = request_control_plane_sync(
            "GET",
            f"{API_BASE_URL}/expediter/status/{service_type}/{service_exec_id}",
            req_id=req_id,
            timeout=_expediter_status_poll_timeout(row),
            retries=POLLER_RETRIES,
        )
    except Exception as exc:
        if _runtime_exceeded_timeout(row):
            _cancel_execution(row, req_id)
            payload = _reconcile_payload(
                row,
                status="timeout",
                actual_outcome={
                    "timeout": True,
                    "service_exec_id": service_exec_id,
                    "reason": "expediter_status_poll_transport_failure",
                },
                error=f"Service execution timed out while polling plugin status: {exc}",
            )
            reconciled = _post_reconcile(row, payload, req_id)
            blocking = bool(reconciled and _is_blocking_failure(row, reconciled))
            return _poll_result(
                row,
                req_id,
                reconciled=reconciled,
                terminal=bool(reconciled),
                blocking_failure=blocking,
                reason="Service execution timed out while polling plugin status",
            )
        logger.warning(
            "Expediter status poll transport failure",
            extra={
                "req_id": req_id,
                "dish_ingredient_id": row_id,
                "service_type": service_type,
                "error": str(exc),
            },
        )
        _release_row(row, req_id)
        return _poll_result(row, req_id)
    if response.status_code >= 400:
        detail = response.text
        try:
            body = response.json()
            if isinstance(body, dict):
                detail = str(body.get("detail") or body)
        except ValueError:
            pass
        payload = _reconcile_payload(
            row,
            status="errored",
            actual_outcome={
                "success": False,
                "status": "errored",
                "reason": "expediter_status_poll_failed",
                "http_status": response.status_code,
                "service_type": service_type,
                "service_exec_id": service_exec_id,
                "detail": detail,
            },
            error=f"Expediter status poll failed with HTTP {response.status_code}: {detail}",
        )
        logger.warning(
            "Expediter status poll failed; marking runtime row errored",
            extra={
                "req_id": req_id,
                "dish_ingredient_id": row_id,
                "service_type": service_type,
                "service_exec_id": service_exec_id,
                "status_code": response.status_code,
            },
        )
        reconciled = _post_reconcile(row, payload, req_id)
        blocking = bool(reconciled and _is_blocking_failure(row, reconciled))
        return _poll_result(
            row,
            req_id,
            reconciled=reconciled,
            terminal=bool(reconciled),
            blocking_failure=blocking,
            reason=str(payload["service_exec_error"]),
        )

    body = response.json()
    status = str(body.get("status") or "").strip().lower()
    if status not in TERMINAL_EXECUTION_STATUSES:
        if _runtime_exceeded_timeout(row):
            _cancel_execution(row, req_id)
            payload = _reconcile_payload(
                row,
                status="timeout",
                actual_outcome={"timeout": True, "service_exec_id": service_exec_id},
                error="Service execution timed out",
            )
            reconciled = _post_reconcile(row, payload, req_id)
            blocking = bool(reconciled and _is_blocking_failure(row, reconciled))
            return _poll_result(
                row,
                req_id,
                reconciled=reconciled,
                terminal=bool(reconciled),
                blocking_failure=blocking,
                reason="Service execution timed out before downstream groups ran",
            )
        logger.debug(
            "Runtime row still in flight",
            extra={
                "req_id": req_id,
                "dish_ingredient_id": row_id,
                "dish_id": row.get("dish_id"),
                "service_type": service_type,
                "service_exec": row.get("service_exec"),
                "service_exec_status": status or "unknown",
            },
        )
        _release_row(row, req_id)
        return _poll_result(row, req_id)

    outcome = body.get("service_exec_actual_outcome") or body.get("raw")
    logger.info(
        "Runtime row reached terminal plugin status",
        extra={
            "req_id": req_id,
            "dish_ingredient_id": row_id,
            "dish_id": row.get("dish_id"),
            "service_type": service_type,
            "service_exec": row.get("service_exec"),
            "service_exec_id": service_exec_id,
            "plugin_status": status,
        },
    )
    payload = _reconcile_payload(
        row,
        status=status,
        actual_outcome=outcome if isinstance(outcome, dict) else None,
        error=body.get("service_exec_error"),
    )
    reconciled = _post_reconcile(row, payload, req_id)
    blocking = bool(reconciled and _is_blocking_failure(row, reconciled))
    return _poll_result(
        row,
        req_id,
        reconciled=reconciled,
        terminal=bool(reconciled),
        blocking_failure=blocking,
        reason=body.get("service_exec_error") or f"Blocking service execution ended with {status}",
    )


def monitor_cancel_requested(query_limit: int = POLL_LIMIT) -> None:
    response = request_control_plane_sync(
        "GET",
        f"{API_BASE_URL}/dish-ingredients/cancel-requested",
        params={"limit": query_limit},
        req_id=SYSTEM_REQ_ID,
        timeout=10,
        retries=POLLER_RETRIES,
    )
    if response.status_code >= 400:
        logger.warning(
            "Failed to fetch cancel-requested dish ingredients",
            extra={"req_id": SYSTEM_REQ_ID, "status_code": response.status_code},
        )
        return
    rows = response.json()
    if not isinstance(rows, list):
        return
    if rows:
        logger.debug(
            "Timer found cancel-requested runtime rows",
            extra={"req_id": SYSTEM_REQ_ID, "count": len(rows)},
        )
    advanced_dishes: dict[int, str] = {}
    for row in rows:
        if isinstance(row, dict):
            req_id = str(row.get("req_id") or SYSTEM_REQ_ID)
            canceled = _cancel_row_without_advance(
                row,
                req_id,
                reason="Service execution canceled after alert resolved",
            )
            dish_id = canceled.get("dish_id") if canceled else None
            if isinstance(dish_id, int):
                advanced_dishes.setdefault(dish_id, req_id)
    for dish_id, req_id in sorted(advanced_dishes.items()):
        _advance_dish({"dish_id": dish_id}, req_id)


def monitor_advance_ready(query_limit: int = POLL_LIMIT) -> None:
    response = request_control_plane_sync(
        "GET",
        f"{API_BASE_URL}/dish-ingredients/advance-ready",
        params={"limit": query_limit},
        req_id=SYSTEM_REQ_ID,
        timeout=10,
        retries=POLLER_RETRIES,
    )
    if response.status_code >= 400:
        logger.warning(
            "Failed to fetch advance-ready dish ingredients",
            extra={"req_id": SYSTEM_REQ_ID, "status_code": response.status_code},
        )
        return
    rows = response.json()
    if not isinstance(rows, list):
        return
    if rows:
        logger.debug(
            "Timer found advance-ready runtime rows",
            extra={"req_id": SYSTEM_REQ_ID, "count": len(rows)},
        )
    for row in rows:
        if not isinstance(row, dict):
            continue
        req_id = str(row.get("req_id") or SYSTEM_REQ_ID)
        if _is_blocking_terminal_row(row):
            _cancel_blocked_future_rows(row, req_id, reason=_blocking_failure_reason(row))
        _advance_dish(row, req_id)


def monitor_in_flight(query_limit: int = POLL_LIMIT) -> None:
    response = request_control_plane_sync(
        "GET",
        f"{API_BASE_URL}/dish-ingredients/in-flight",
        params={"limit": query_limit},
        req_id=SYSTEM_REQ_ID,
        timeout=10,
        retries=POLLER_RETRIES,
    )
    if response.status_code >= 400:
        logger.warning(
            "Failed to fetch in-flight dish ingredients",
            extra={"req_id": SYSTEM_REQ_ID, "status_code": response.status_code},
        )
        return
    rows = response.json()
    if not isinstance(rows, list):
        return
    if rows:
        logger.debug(
            "Timer found in-flight runtime rows",
            extra={"req_id": SYSTEM_REQ_ID, "count": len(rows)},
        )
    runtime_rows = [row for row in rows if isinstance(row, dict)]
    for (dish_id, bucket), group_rows in sorted(_group_rows(runtime_rows).items()):
        poll_results: list[JSONObject] = []
        for row in group_rows:
            poll_results.append(_poll_row(row, str(row.get("req_id") or SYSTEM_REQ_ID)))

        reconciled_results = [
            result for result in poll_results if isinstance(result.get("reconciled"), dict)
        ]
        if not reconciled_results:
            continue

        req_id = str(reconciled_results[0].get("req_id") or SYSTEM_REQ_ID)
        current_rows = _fetch_dish_ingredients(dish_id, req_id)
        if current_rows is None:
            continue
        if _group_has_in_flight(current_rows, bucket):
            logger.debug(
                "Timer deferred Cook action until execution group is fully reconciled",
                extra={
                    "req_id": req_id,
                    "dish_id": dish_id,
                    "depth": bucket[0],
                    "parallel_group": bucket[1],
                },
            )
            continue

        blocking = next(
            (
                result
                for result in reconciled_results
                if bool(result.get("blocking_failure")) and isinstance(result.get("row"), dict)
            ),
            None,
        )
        if blocking is not None:
            row = blocking["row"]
            reason = str(
                blocking.get("reason")
                or row.get("service_exec_error")
                or "Blocking service execution failed"
            )
            _cancel_blocked_future_rows(row, req_id, reason=reason)
        _advance_dish({"dish_id": dish_id}, req_id)


def _run_monitor(name: str, callback) -> None:
    try:
        callback()
    except Exception as exc:
        logger.warning(
            "Timer monitor pass failed; will retry on next interval",
            extra={"req_id": SYSTEM_REQ_ID, "monitor": name, "error": str(exc)},
        )


if __name__ == "__main__":
    wait_for_api(API_BASE_URL, SYSTEM_REQ_ID, logger)
    logger.info(
        "Timer runtime crawler started",
        extra={
            "req_id": SYSTEM_REQ_ID,
            "api_base_url": API_BASE_URL,
            "interval_sec": TIMER_INTERVAL,
            "poll_limit": POLL_LIMIT,
            "http_retries": POLLER_RETRIES,
            "monitors": "cancel_requested,in_flight,advance_ready",
            "in_flight_endpoint": "/dish-ingredients/in-flight",
            "cancel_endpoint": "/dish-ingredients/cancel-requested",
            "advance_ready_endpoint": "/dish-ingredients/advance-ready",
            "reconcile_endpoint": "/dish-ingredients/{id}/reconcile",
            "advance_endpoint": "/cook/dishes/{id}/advance",
        },
    )
    while True:
        runtime_config = get_worker_runtime_config(
            api_base_url=API_BASE_URL,
            service_type="timer",
            req_id=SYSTEM_REQ_ID,
            default_interval=TIMER_INTERVAL,
            default_query_limit=POLL_LIMIT,
            logger=logger,
        )
        loop_interval = int(runtime_config["run_interval_seconds"])
        loop_limit = int(runtime_config["query_limit"])
        if not runtime_config["enabled"]:
            logger.info(
                "Timer paused by internal plugin configuration",
                extra={"req_id": SYSTEM_REQ_ID, "interval_sec": loop_interval},
            )
            time.sleep(loop_interval)
            continue
        _run_monitor("cancel_requested", lambda: monitor_cancel_requested(loop_limit))
        _run_monitor("in_flight", lambda: monitor_in_flight(loop_limit))
        _run_monitor("advance_ready", lambda: monitor_advance_ready(loop_limit))
        time.sleep(loop_interval)
