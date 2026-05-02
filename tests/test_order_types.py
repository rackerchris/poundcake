"""Tests for stable order_type classification and filtering."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from api.api.dishes import _filter_dishes, _serialize_dish_status
from api.api.orders import _filter_orders, _serialize_order_status
from api.models.models import Dish, Order
from api.services.order_types import (
    ensure_raw_data_order_type,
    infer_order_type,
    normalize_order_type,
)
from api.types import (
    MANUAL_ORDER_TYPE,
    SCHEDULED_TASK_ORDER_TYPE,
    WEBHOOK_ALERT_ORDER_TYPE,
)


def _order(*, raw_data: dict | None, labels: dict | None = None) -> Order:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return Order(
        id=1,
        req_id="req-1",
        fingerprint="fp-1",
        alert_status="firing",
        processing_status="new",
        is_active=True,
        alert_group_name="ExampleAlert",
        labels=labels or {},
        raw_data=raw_data,
        starts_at=now,
        created_at=now,
        updated_at=now,
        counter=1,
        remediation_outcome="pending",
        auto_close_eligible=False,
    )


def test_order_type_inference_prefers_explicit_raw_data() -> None:
    assert (
        infer_order_type(
            raw_data={"order_type": WEBHOOK_ALERT_ORDER_TYPE},
            labels={"order_type": SCHEDULED_TASK_ORDER_TYPE},
        )
        == WEBHOOK_ALERT_ORDER_TYPE
    )


def test_order_type_inference_supports_scheduled_task_fallbacks() -> None:
    assert (
        infer_order_type(raw_data={"scheduled_task_id": 1}, labels={}) == SCHEDULED_TASK_ORDER_TYPE
    )
    assert (
        infer_order_type(raw_data={}, labels={"scheduled_task_id": "1"})
        == SCHEDULED_TASK_ORDER_TYPE
    )


def test_order_type_inference_supports_legacy_alertmanager_payload() -> None:
    assert (
        infer_order_type(
            raw_data={"status": "firing", "labels": {"alertname": "HostDown"}},
            labels={},
        )
        == WEBHOOK_ALERT_ORDER_TYPE
    )


def test_order_type_inference_defaults_to_manual() -> None:
    assert infer_order_type(raw_data={}, labels={}) == MANUAL_ORDER_TYPE


def test_order_type_normalization_rejects_unknown_values() -> None:
    assert normalize_order_type("scheduled_task") == SCHEDULED_TASK_ORDER_TYPE
    assert normalize_order_type("plugin-health-check") is None
    with pytest.raises(ValueError):
        ensure_raw_data_order_type({"order_type": "plugin-health-check"}, MANUAL_ORDER_TYPE)


def test_order_scope_filters_split_operator_and_system_orders() -> None:
    webhook = _order(raw_data={"order_type": WEBHOOK_ALERT_ORDER_TYPE})
    scheduled = _order(raw_data={"order_type": SCHEDULED_TASK_ORDER_TYPE})
    manual = _order(raw_data={"order_type": MANUAL_ORDER_TYPE})

    assert _filter_orders(
        [webhook, scheduled, manual], order_scope="operator", order_type=None
    ) == [webhook]
    assert _filter_orders([webhook, scheduled, manual], order_scope="system", order_type=None) == [
        scheduled
    ]
    assert _filter_orders(
        [webhook, scheduled, manual], order_scope="all", order_type=MANUAL_ORDER_TYPE
    ) == [manual]


def test_status_serializers_include_order_type() -> None:
    order = _order(raw_data={"order_type": WEBHOOK_ALERT_ORDER_TYPE})
    dish = Dish(
        id=1,
        req_id="req-1",
        order_id=1,
        recipe_id=1,
        run_phase="firing",
        processing_status="new",
        created_at=order.created_at,
        updated_at=order.updated_at,
    )
    dish.order = order

    assert _serialize_order_status(order).order_type == WEBHOOK_ALERT_ORDER_TYPE
    assert _serialize_dish_status(dish).order_type == WEBHOOK_ALERT_ORDER_TYPE


def test_dish_scope_filter_uses_parent_order_type() -> None:
    webhook_order = _order(raw_data={"order_type": WEBHOOK_ALERT_ORDER_TYPE})
    system_order = _order(raw_data={"order_type": SCHEDULED_TASK_ORDER_TYPE})
    webhook_dish = Dish(
        id=1,
        req_id="req-1",
        order_id=1,
        recipe_id=1,
        run_phase="firing",
        processing_status="new",
        created_at=webhook_order.created_at,
        updated_at=webhook_order.updated_at,
    )
    system_dish = Dish(
        id=2,
        req_id="req-2",
        order_id=2,
        recipe_id=1,
        run_phase="firing",
        processing_status="new",
        created_at=system_order.created_at,
        updated_at=system_order.updated_at,
    )
    webhook_dish.order = webhook_order
    system_dish.order = system_order

    assert _filter_dishes([webhook_dish, system_dish], order_scope="operator", order_type=None) == [
        webhook_dish
    ]
    assert _filter_dishes([webhook_dish, system_dish], order_scope="system", order_type=None) == [
        system_dish
    ]
