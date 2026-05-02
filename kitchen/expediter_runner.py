#!/usr/bin/env python3
"""Deployed worker entrypoint for executing runner-dispatched rows.

This file is invoked by Helm and Docker commands rather than imported by
production Python code.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

from api.core.config import get_settings
from api.core.logging import get_logger, setup_logging
from api.plugins.state import TERMINAL_EXECUTION_STATUSES, sla_exceeded
from api.types import JSONObject
from kitchen.service_helpers import (
    get_worker_runtime_config,
    request_control_plane_sync,
    wait_for_api,
)

setup_logging()
logger = get_logger("expediter-runner")

POUNDCAKE_API_URL = os.getenv("POUNDCAKE_API_URL", "http://poundcake:8080").rstrip("/")
API_BASE_URL = f"{POUNDCAKE_API_URL}/api/v1"
EXPEDITER_RUNNER_INTERVAL = int(os.getenv("EXPEDITER_RUNNER_INTERVAL", "2"))
EXPEDITER_RUNNER_LIMIT = int(os.getenv("EXPEDITER_RUNNER_LIMIT", "50"))
SYSTEM_REQ_ID = os.getenv("EXPEDITER_RUNNER_WORKER_ID", f"SYSTEM-EXPEDITER-RUNNER-{os.getpid()}")
POLLER_RETRIES = get_settings().poller_http_retries


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


def _runtime_seconds(row: JSONObject) -> int | None:
    started = _parse_datetime(row.get("service_exec_start_time"))
    if started is None:
        return None
    return max(0, int((datetime.now(timezone.utc) - started).total_seconds()))


def _terminal_payload(row: JSONObject, body: JSONObject, status: str) -> JSONObject:
    outcome = body.get("service_exec_actual_outcome") or body.get("raw")
    context_updates = _merged_context_updates(row, body)
    if isinstance(outcome, dict) and context_updates:
        outcome = {**outcome, "_context_updates": context_updates}
    run_time = _runtime_seconds(row)
    expected_secs = row.get("service_exec_expected_secs")
    expected = expected_secs if isinstance(expected_secs, int) else None
    payload: JSONObject = {
        "service_exec_id": body.get("service_exec_id") or row.get("service_exec_id"),
        "service_exec_status": status,
        "service_exec_completed_time": datetime.now(timezone.utc).isoformat(),
        "service_exec_actual_outcome": outcome if isinstance(outcome, dict) else None,
        "service_exec_error": body.get("service_exec_error"),
        "service_exec_sla_exceeded": sla_exceeded(expected, run_time),
    }
    if run_time is not None:
        payload["service_exec_run_time"] = run_time
    return payload


def _nonterminal_payload(row: JSONObject, body: JSONObject, status: str) -> JSONObject:
    # Once a provider receipt exists, Timer becomes the read-only observer for it.
    payload: JSONObject = {
        "service_exec_id": body.get("service_exec_id") or row.get("service_exec_id"),
        "service_exec_status": "running" if status == "dispatched" else status,
        "service_exec_error": body.get("service_exec_error"),
    }
    context_updates = _merged_context_updates(row, body)
    if context_updates:
        payload["service_exec_actual_outcome"] = {"_context_updates": context_updates}
    return payload


def _merged_context_updates(row: JSONObject, body: JSONObject) -> JSONObject:
    merged: JSONObject = {}
    existing = row.get("service_exec_actual_outcome")
    if isinstance(existing, dict):
        existing_updates = existing.get("_context_updates") or existing.get("context_updates")
        if isinstance(existing_updates, dict):
            merged.update(existing_updates)
    body_updates = body.get("context_updates")
    if isinstance(body_updates, dict):
        merged.update(body_updates)
    return merged


def _error_payload(row: JSONObject, error: str) -> JSONObject:
    run_time = _runtime_seconds(row)
    payload: JSONObject = {
        "service_exec_status": "errored",
        "service_exec_completed_time": datetime.now(timezone.utc).isoformat(),
        "service_exec_actual_outcome": {
            "success": False,
            "status": "errored",
            "reason": "expediter_runner_execute_failed",
            "detail": error,
        },
        "service_exec_error": error,
    }
    if run_time is not None:
        payload["service_exec_run_time"] = run_time
    return payload


def _claim_row(row: JSONObject, req_id: str) -> JSONObject | None:
    row_id = row.get("id")
    response = request_control_plane_sync(
        "POST",
        f"{API_BASE_URL}/dish-ingredients/{row_id}/execution-claim",
        req_id=req_id,
        timeout=10,
        retries=POLLER_RETRIES,
    )
    if response.status_code == 409:
        return None
    if response.status_code >= 400:
        logger.warning(
            "Execution claim failed",
            extra={
                "req_id": req_id,
                "dish_ingredient_id": row_id,
                "status_code": response.status_code,
            },
        )
        return None
    body = response.json()
    return body if isinstance(body, dict) else None


def _release_row(row: JSONObject, req_id: str) -> None:
    row_id = row.get("id")
    response = request_control_plane_sync(
        "POST",
        f"{API_BASE_URL}/dish-ingredients/{row_id}/execution-release",
        req_id=req_id,
        timeout=10,
        retries=POLLER_RETRIES,
    )
    if response.status_code >= 400 and response.status_code != 409:
        logger.warning(
            "Execution release failed",
            extra={
                "req_id": req_id,
                "dish_ingredient_id": row_id,
                "status_code": response.status_code,
            },
        )


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
            "Execution result reconciliation failed",
            extra={
                "req_id": req_id,
                "dish_ingredient_id": row_id,
                "status_code": response.status_code,
            },
        )
        return None
    body = response.json()
    return body if isinstance(body, dict) else None


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
            "Cook advance failed after execution",
            extra={"req_id": req_id, "dish_id": dish_id, "status_code": response.status_code},
        )


def _execute_row(row: JSONObject, req_id: str) -> JSONObject | None:
    claimed = _claim_row(row, req_id)
    if claimed is None:
        return None
    row = claimed
    row_id = row.get("id")
    try:
        response = request_control_plane_sync(
            "POST",
            f"{API_BASE_URL}/expediter/execute/{row_id}",
            req_id=req_id,
            timeout=max(10, int(row.get("service_exec_timeout") or 300)),
            retries=0,
        )
    except Exception as exc:  # noqa: BLE001
        payload = _error_payload(row, f"Expediter runner transport failure: {exc}")
        reconciled = _post_reconcile(row, payload, req_id)
        if reconciled:
            _advance_dish(row, req_id)
        return reconciled

    if response.status_code >= 400:
        payload = _error_payload(
            row,
            f"Expediter execute failed with HTTP {response.status_code}: {response.text}",
        )
        reconciled = _post_reconcile(row, payload, req_id)
        if reconciled:
            _advance_dish(row, req_id)
        return reconciled

    body = response.json()
    if not isinstance(body, dict):
        payload = _error_payload(row, "Expediter execute returned a non-object payload")
        reconciled = _post_reconcile(row, payload, req_id)
        if reconciled:
            _advance_dish(row, req_id)
        return reconciled

    status = str(body.get("status") or "").strip().lower()
    if status in TERMINAL_EXECUTION_STATUSES:
        payload = _terminal_payload(row, body, status)
        reconciled = _post_reconcile(row, payload, req_id)
        if reconciled:
            _advance_dish(row, req_id)
        return reconciled

    payload = _nonterminal_payload(row, body, status or "running")
    reconciled = _post_reconcile(row, payload, req_id)
    if reconciled:
        _release_row(row, req_id)
    return reconciled


def monitor_pending_executions(query_limit: int = EXPEDITER_RUNNER_LIMIT) -> None:
    response = request_control_plane_sync(
        "GET",
        f"{API_BASE_URL}/dish-ingredients/execution-pending",
        params={"limit": query_limit},
        req_id=SYSTEM_REQ_ID,
        timeout=10,
        retries=POLLER_RETRIES,
    )
    if response.status_code >= 400:
        logger.warning(
            "Failed to fetch pending execution rows",
            extra={"req_id": SYSTEM_REQ_ID, "status_code": response.status_code},
        )
        return
    rows = response.json()
    if not isinstance(rows, list):
        return
    for row in rows:
        if isinstance(row, dict):
            _execute_row(row, str(row.get("req_id") or SYSTEM_REQ_ID))


def _run_monitor(name: str, callback) -> None:
    try:
        callback()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Expediter runner monitor pass failed; will retry on next interval",
            extra={"req_id": SYSTEM_REQ_ID, "monitor": name, "error": str(exc)},
        )


if __name__ == "__main__":
    wait_for_api(API_BASE_URL, SYSTEM_REQ_ID, logger)
    logger.info(
        "Expediter Runner started",
        extra={
            "req_id": SYSTEM_REQ_ID,
            "api_base_url": API_BASE_URL,
            "interval_sec": EXPEDITER_RUNNER_INTERVAL,
            "query_limit": EXPEDITER_RUNNER_LIMIT,
            "pending_endpoint": "/dish-ingredients/execution-pending",
            "execute_endpoint": "/expediter/execute/{dish_ingredient_id}",
        },
    )
    while True:
        runtime_config = get_worker_runtime_config(
            api_base_url=API_BASE_URL,
            service_type="expediter-runner",
            req_id=SYSTEM_REQ_ID,
            default_interval=EXPEDITER_RUNNER_INTERVAL,
            default_query_limit=EXPEDITER_RUNNER_LIMIT,
            logger=logger,
        )
        loop_interval = int(runtime_config["run_interval_seconds"])
        loop_limit = int(runtime_config["query_limit"])
        if not runtime_config["enabled"]:
            logger.info(
                "Expediter Runner paused by internal plugin configuration",
                extra={"req_id": SYSTEM_REQ_ID, "interval_sec": loop_interval},
            )
            time.sleep(loop_interval)
            continue
        _run_monitor("pending_executions", lambda: monitor_pending_executions(loop_limit))
        time.sleep(loop_interval)
