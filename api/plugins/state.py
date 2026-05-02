"""State machine helpers for service plugin runtime execution."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from api.core.statuses import EXECUTION_NON_TERMINAL_STATUSES, EXECUTION_TERMINAL_STATUSES
from api.core.time import utc_runtime_seconds
from api.plugins.contract import expected_outcome_matches
from api.types import CanonicalExecutionStatus, PluginHealthStatus

PLUGIN_RUN_STATE_UNKNOWN: PluginHealthStatus = "unknown"
PLUGIN_RUN_STATE_INITIALIZING: PluginHealthStatus = "initializing"
PLUGIN_RUN_STATE_HEALTHY: PluginHealthStatus = "healthy"
PLUGIN_RUN_STATE_DEGRADED: PluginHealthStatus = "degraded"
PLUGIN_RUN_STATE_FAILED: PluginHealthStatus = "failed"
PLUGIN_RUN_STATE_DISABLED: PluginHealthStatus = "disabled"

EXPEDITER_RUNNER_SERVICE_TYPE = "expediter-runner"
EXPEDITER_RUNNER_RECEIPT_PREFIX = f"{EXPEDITER_RUNNER_SERVICE_TYPE}:"

PLUGIN_RUN_STATES: tuple[PluginHealthStatus, ...] = (
    PLUGIN_RUN_STATE_UNKNOWN,
    PLUGIN_RUN_STATE_INITIALIZING,
    PLUGIN_RUN_STATE_HEALTHY,
    PLUGIN_RUN_STATE_DEGRADED,
    PLUGIN_RUN_STATE_FAILED,
    PLUGIN_RUN_STATE_DISABLED,
)
PLUGIN_CALLABLE_RUN_STATES: frozenset[PluginHealthStatus] = frozenset(
    {
        PLUGIN_RUN_STATE_HEALTHY,
        PLUGIN_RUN_STATE_DEGRADED,
    }
)
PLUGIN_BLOCKED_RUN_STATES: frozenset[PluginHealthStatus] = frozenset(
    {
        PLUGIN_RUN_STATE_UNKNOWN,
        PLUGIN_RUN_STATE_INITIALIZING,
        PLUGIN_RUN_STATE_FAILED,
        PLUGIN_RUN_STATE_DISABLED,
    }
)

NON_TERMINAL_EXECUTION_STATUSES: set[CanonicalExecutionStatus] = set(
    EXECUTION_NON_TERMINAL_STATUSES
)
TERMINAL_EXECUTION_STATUSES: set[CanonicalExecutionStatus] = set(EXECUTION_TERMINAL_STATUSES)
EXECUTION_STATUS_TRANSITIONS: dict[CanonicalExecutionStatus, set[CanonicalExecutionStatus]] = {
    "pending": {
        "pending",
        "dispatched",
        "running",
        "succeeded",
        "failed",
        "errored",
        "timeout",
        "canceled",
    },
    "dispatched": {
        "dispatched",
        "running",
        "succeeded",
        "failed",
        "errored",
        "timeout",
        "canceled",
    },
    "running": {"running", "succeeded", "failed", "errored", "timeout", "canceled"},
    "succeeded": {"succeeded"},
    "failed": {"failed"},
    "errored": {"errored"},
    "timeout": {"timeout"},
    "canceled": {"canceled"},
}


class ServiceExecutionStateError(ValueError):
    """Raised when a service execution transition violates the contract."""


class ServicePluginRunStateError(ValueError):
    """Raised when a service plugin runtime state violates the contract."""


def normalize_plugin_run_state(value: str | None) -> PluginHealthStatus:
    normalized = (value or PLUGIN_RUN_STATE_UNKNOWN).strip().lower()
    if normalized in PLUGIN_RUN_STATES:
        return normalized  # type: ignore[return-value]
    raise ServicePluginRunStateError(f"Invalid service plugin run state: {value}")


def is_plugin_callable_run_state(value: str | None) -> bool:
    return normalize_plugin_run_state(value) in PLUGIN_CALLABLE_RUN_STATES


def plugin_run_state_blocks_dispatch(value: str | None) -> bool:
    return normalize_plugin_run_state(value) in PLUGIN_BLOCKED_RUN_STATES


def normalize_execution_status(value: str | None) -> CanonicalExecutionStatus:
    normalized = (value or "pending").strip().lower()
    if normalized in {
        "pending",
        "dispatched",
        "running",
        "succeeded",
        "failed",
        "errored",
        "timeout",
        "canceled",
    }:
        return normalized  # type: ignore[return-value]
    raise ServiceExecutionStateError(f"Invalid service_exec_status: {value}")


def is_terminal_execution_status(value: str | None) -> bool:
    return normalize_execution_status(value) in TERMINAL_EXECUTION_STATUSES


def validate_execution_transition(
    current: str | None, requested: str | None
) -> CanonicalExecutionStatus:
    current_status = normalize_execution_status(current)
    requested_status = normalize_execution_status(requested)
    if requested_status not in EXECUTION_STATUS_TRANSITIONS[current_status]:
        raise ServiceExecutionStateError(
            f"Invalid service_exec_status transition: {current_status} -> {requested_status}"
        )
    return requested_status


def verdict_status(
    *,
    requested_status: str | None,
    expected_outcome: Any,
    actual_outcome: Any,
) -> CanonicalExecutionStatus:
    status = normalize_execution_status(requested_status)
    if status not in TERMINAL_EXECUTION_STATUSES:
        return status
    if status == "canceled":
        return status
    if expected_outcome is None or actual_outcome is None:
        return status
    return (
        "succeeded"
        if expected_outcome_matches(
            expected=expected_outcome,
            actual=actual_outcome,
            status=status,
        )
        else "failed"
    )


def runtime_seconds(started_at: datetime | None, completed_at: datetime | None) -> int | None:
    return utc_runtime_seconds(started_at, completed_at)


def sla_exceeded(expected_secs: int | None, run_time: int | None) -> bool:
    if expected_secs is None or expected_secs <= 0 or run_time is None:
        return False
    return run_time > expected_secs
