"""Validation helpers for the PoundCake service plugin execution contract."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from jsonschema import Draft202012Validator, SchemaError

from api.types import JSONObject

HEALTH_CHECK_OPERATION = "health_check"


def health_check_operation_parameters() -> JSONObject:
    """Return the standard operation metadata for plugin health-check ingredients."""
    return {
        "operation": HEALTH_CHECK_OPERATION,
        "allowed_operations": [HEALTH_CHECK_OPERATION],
        "operation_metadata": {
            HEALTH_CHECK_OPERATION: {
                "label": "Health check",
                "description": "Check plugin readiness and remote service health.",
                "payload_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
        },
    }


class ServicePluginContractError(ValueError):
    """Raised when a service plugin contract payload is invalid."""


def validate_payload_schema(schema: Any) -> None:
    """Validate a service payload JSON Schema."""
    if not isinstance(schema, dict):
        raise ServicePluginContractError("payload_schema must be an object")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        path = _json_path(["payload_schema", *exc.path])
        raise ServicePluginContractError(f"{path}: {exc.message}") from exc


def validate_service_payload(payload: Any, schema: Any) -> None:
    """Validate a filled service payload against the registered template schema."""
    validate_payload_schema(schema)
    validator = Draft202012Validator(schema)
    errors = sorted(
        validator.iter_errors(payload if payload is not None else {}),
        key=lambda item: list(item.path),
    )
    if errors:
        first = errors[0]
        raise ServicePluginContractError(
            f"{_json_path(['service_payload', *first.path])}: {first.message}"
        )


def validate_service_payload_for_operation(
    payload: Any,
    schema: Any,
    parameters: Any,
) -> None:
    """Validate a filled payload against ingredient and selected-operation schemas."""
    validate_service_payload(payload, schema)
    operation_schema = operation_payload_schema(parameters)
    if operation_schema is not None:
        validate_service_payload(payload, operation_schema)


def operation_payload_schema(parameters: Any) -> JSONObject | None:
    """Return the payload schema for the selected operation, when advertised."""
    if not isinstance(parameters, dict):
        return None
    operation = str(parameters.get("operation") or "").strip()
    if not operation:
        return None
    metadata = parameters.get("operation_metadata")
    if not isinstance(metadata, dict):
        return None
    details = metadata.get(operation)
    if not isinstance(details, dict):
        return None
    schema = details.get("payload_schema")
    return schema if isinstance(schema, dict) else None


def validate_service_operation(parameters: Any) -> None:
    """Validate an operation selected from an ingredient's advertised operation catalog."""
    if parameters is None:
        return
    if not isinstance(parameters, dict):
        raise ServicePluginContractError("service_exec_parameters must be an object")

    raw_allowed = parameters.get("allowed_operations")
    if raw_allowed is None:
        return
    if not isinstance(raw_allowed, list) or not raw_allowed:
        raise ServicePluginContractError("allowed_operations must be a non-empty array")

    allowed = [str(item).strip() for item in raw_allowed if str(item).strip()]
    if not allowed:
        raise ServicePluginContractError("allowed_operations must include at least one operation")

    operation = str(parameters.get("operation") or "").strip()
    if not operation:
        raise ServicePluginContractError("operation is required when allowed_operations is set")
    if operation not in allowed:
        raise ServicePluginContractError("operation must be one of: " + ", ".join(sorted(allowed)))

    metadata = parameters.get("operation_metadata")
    if metadata is None:
        return
    if not isinstance(metadata, dict):
        raise ServicePluginContractError("operation_metadata must be an object")
    for name, details in metadata.items():
        operation_name = str(name or "").strip()
        if operation_name not in allowed:
            raise ServicePluginContractError(
                f"operation_metadata.{operation_name} must reference an allowed operation"
            )
        if not isinstance(details, dict):
            raise ServicePluginContractError(
                f"operation_metadata.{operation_name} must be an object"
            )
        for field in ("label", "description"):
            value = details.get(field)
            if value is not None and not isinstance(value, str):
                raise ServicePluginContractError(
                    f"operation_metadata.{operation_name}.{field} must be a string"
                )
        payload_schema = details.get("payload_schema")
        if payload_schema is not None:
            try:
                validate_payload_schema(payload_schema)
            except ServicePluginContractError as exc:
                raise ServicePluginContractError(
                    f"operation_metadata.{operation_name}.payload_schema invalid: {exc}"
                ) from exc


def expected_outcome_matches(
    *,
    expected: Any,
    actual: Any,
    status: str | None = None,
) -> bool:
    """Compare actual client output to a phase-one expected outcome value."""
    if expected is None:
        return str(status or "").strip().lower() == "succeeded"
    if isinstance(expected, bool):
        return _actual_success(actual=actual, status=status) is expected
    if isinstance(expected, str):
        expected_status = expected.strip().lower()
        return _extract_status(actual=actual, status=status) == expected_status
    if isinstance(expected, dict):
        return _dict_contains(actual, expected, status=status)
    return expected == actual


def _extract_status(*, actual: Any, status: str | None = None) -> str | None:
    if status:
        return status.strip().lower()
    if isinstance(actual, dict):
        for key in ("status", "state", "outcome", "result"):
            value = actual.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
    if isinstance(actual, str) and actual.strip():
        return actual.strip().lower()
    return None


def _actual_success(*, actual: Any, status: str | None = None) -> bool:
    normalized = _extract_status(actual=actual, status=status)
    if normalized is not None:
        return normalized in {"succeeded", "success", "completed", "true", "ok", "passed"}
    if isinstance(actual, bool):
        return actual
    if isinstance(actual, dict):
        for key in ("success", "ok", "passed"):
            if isinstance(actual.get(key), bool):
                return bool(actual[key])
    return False


def _dict_contains(actual: Any, expected: JSONObject, *, status: str | None = None) -> bool:
    if not isinstance(actual, dict):
        return False
    for key, expected_value in expected.items():
        if key in actual:
            actual_value = actual[key]
        elif key == "status" and status is not None:
            actual_value = status
        else:
            return False
        if isinstance(expected_value, dict):
            if not _dict_contains(actual_value, expected_value, status=None):
                return False
            continue
        if actual_value != expected_value:
            return False
    return True


def _json_path(parts: Sequence[object]) -> str:
    rendered = "$"
    for part in parts:
        if isinstance(part, int):
            rendered += f"[{part}]"
        else:
            rendered += f".{part}"
    return rendered
