"""Helpers for the stable order_type contract."""

from __future__ import annotations

from typing import Any

from api.types import (
    ALL_ORDER_TYPES,
    MANUAL_ORDER_TYPE,
    OPERATOR_ORDER_TYPES,
    SCHEDULED_TASK_ORDER_TYPE,
    SYSTEM_ORDER_TYPES,
    WEBHOOK_ALERT_ORDER_TYPE,
    OrderScope,
    OrderType,
)


def normalize_order_type(value: Any) -> OrderType | None:
    """Return a known order_type value or None."""
    normalized = str(value or "").strip().lower()
    if normalized in ALL_ORDER_TYPES:
        return normalized  # type: ignore[return-value]
    return None


def infer_order_type(*, raw_data: Any, labels: Any) -> OrderType:
    """Infer the order type for old rows that predate explicit order_type stamping."""
    raw = raw_data if isinstance(raw_data, dict) else {}
    label_data = labels if isinstance(labels, dict) else {}

    explicit = normalize_order_type(raw.get("order_type"))
    if explicit:
        return explicit
    explicit = normalize_order_type(label_data.get("order_type"))
    if explicit:
        return explicit

    if raw.get("scheduled_task_id") is not None or label_data.get("scheduled_task_id") is not None:
        return SCHEDULED_TASK_ORDER_TYPE
    if _looks_like_alertmanager_alert(raw):
        return WEBHOOK_ALERT_ORDER_TYPE
    return MANUAL_ORDER_TYPE


def order_scope_types(scope: OrderScope | None) -> frozenset[OrderType]:
    """Return the allowed order types for a reporting scope."""
    if scope == "operator":
        return OPERATOR_ORDER_TYPES
    if scope == "system":
        return SYSTEM_ORDER_TYPES
    return ALL_ORDER_TYPES


def order_matches_filters(
    *,
    raw_data: Any,
    labels: Any,
    order_scope: OrderScope | None = None,
    order_type: OrderType | None = None,
) -> bool:
    """Return whether an order belongs in the requested reporting view."""
    inferred = infer_order_type(raw_data=raw_data, labels=labels)
    if order_type and inferred != order_type:
        return False
    return inferred in order_scope_types(order_scope)


def ensure_raw_data_order_type(raw_data: Any, default: OrderType) -> dict[str, Any]:
    """Copy raw_data and ensure it has a valid order_type."""
    payload = dict(raw_data) if isinstance(raw_data, dict) else {}
    current = payload.get("order_type")
    if current is None or current == "":
        payload["order_type"] = default
        return payload
    normalized = normalize_order_type(current)
    if normalized is None:
        raise ValueError("order_type must be one of: " + ", ".join(sorted(ALL_ORDER_TYPES)))
    payload["order_type"] = normalized
    return payload


def _looks_like_alertmanager_alert(raw: dict[str, Any]) -> bool:
    labels = raw.get("labels")
    if not isinstance(labels, dict):
        return False
    return "alertname" in labels and str(raw.get("status") or "").strip().lower() in {
        "firing",
        "resolved",
    }
