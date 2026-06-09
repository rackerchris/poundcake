"""Shared provider-neutral communication routing helpers."""

from __future__ import annotations

from api.types import JSONObject

from typing import Any

CANONICAL_COMMUNICATION_OPERATIONS = {
    "open",
    "notify",
    "update",
    "close",
}

RUN_CONDITIONS = {
    "always",
    "remediation_failed",
    "clear_timeout_expired",
    "resolved_after_success",
    "resolved_after_failure",
    "resolved_after_no_remediation",
    "resolved_after_timeout",
}

RUN_PHASES = {
    "firing",
    "resolving",
    "both",
}

ALERTMANAGER_REQUIRED_LABEL_FIELDS = {
    "alertname",
    "group_name",
    "severity",
}

ALERTMANAGER_REQUIRED_ANNOTATION_FIELDS = {
    "summary",
    "description",
}

def normalize_destination_type(value: str | None) -> str:
    return str(value or "").strip().lower()


def normalize_destination_target(value: Any) -> str:
    return str(value or "").strip()


def normalize_route_provider_config(
    service_type: str | None,
    provider_config: JSONObject | None,
    *,
    require_required: bool = True,
) -> JSONObject:
    raw = provider_config if isinstance(provider_config, dict) else {}
    return {str(key): value for key, value in raw.items()}


def normalize_communication_operation(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in CANONICAL_COMMUNICATION_OPERATIONS:
        return raw
    return raw


def normalize_run_phase(value: str | None) -> str:
    return str(value or "both").strip().lower() or "both"


def normalize_run_condition(value: str | None) -> str:
    return str(value or "always").strip().lower() or "always"
