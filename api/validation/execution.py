"""Execution payload validation helpers."""

from __future__ import annotations

import re

from api.types import JSONObject

SERVICE_TYPE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,49}$")


def normalize_service_type(service_type: str | None) -> str:
    return (service_type or "").strip().lower()


def validate_service_execution_common(
    *,
    service_type: str | None,
    service_exec: str | None,
    service_payload: JSONObject | None,
    service_exec_parameters: JSONObject | None,
) -> str | None:
    engine = normalize_service_type(service_type)
    if not engine or SERVICE_TYPE_RE.fullmatch(engine) is None:
        return "service_type must start with a lowercase letter and contain only lowercase letters, numbers, underscores, or hyphens"

    if not isinstance(service_exec, str) or not service_exec.strip():
        return "service_exec is required"

    if service_payload is not None and not isinstance(service_payload, dict):
        return "service_payload must be an object when provided"

    if service_exec_parameters is not None and not isinstance(service_exec_parameters, dict):
        return "service_exec_parameters must be an object when provided"

    return None


def validate_service_execution_request(
    *,
    service_type: str | None,
    service_exec: str | None,
    service_payload: JSONObject | None,
    service_exec_parameters: JSONObject | None,
    context: JSONObject | None = None,
) -> str | None:
    error = validate_service_execution_common(
        service_type=service_type,
        service_exec=service_exec,
        service_payload=service_payload,
        service_exec_parameters=service_exec_parameters,
    )
    if error:
        return error

    return None


def validate_runtime_service_payload(
    *,
    service_type: str | None,
    ingredient_purpose: str | None,
    service_exec: str | None,
    service_payload: JSONObject | None,
    service_exec_parameters: JSONObject | None = None,
) -> str | None:
    """Validate engine-aware execution payload contract for runtime orchestration."""
    if service_payload is not None and not isinstance(service_payload, dict):
        return "service_payload must be an object when provided"

    if (ingredient_purpose or "").lower() == "comms" and service_payload is None:
        return "comms ingredient requires service_payload"
    return None
