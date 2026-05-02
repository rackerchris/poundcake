"""Helpers for ordered runtime execution segments within a dish."""

from __future__ import annotations

from api.types import JSONObject

from typing import Any
from typing import NamedTuple

PENDING_EXECUTION_STATUSES = {"pending", None}
IN_FLIGHT_EXECUTION_STATUSES = {"dispatched", "running"}
SUCCESS_EXECUTION_STATUSES = {"succeeded"}
FAILURE_EXECUTION_STATUSES = {"failed", "errored", "timeout", "canceled"}


class ExecutionSegment(NamedTuple):
    depth: int
    parallel_group: int
    service_types: tuple[str, ...]
    rows: list[JSONObject]


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _parse_step_order_from_task_key(task_key: str | None) -> int | None:
    if not isinstance(task_key, str):
        return None
    if not task_key.startswith("step_"):
        return None
    remainder = task_key[len("step_") :]
    prefix = remainder.split("_", 1)[0]
    if prefix.isdigit():
        return int(prefix)
    return None


def _runtime_int(item: JSONObject, key: str) -> int | None:
    return _coerce_int(item.get(key))


def build_recipe_step_order_map(dish: JSONObject) -> dict[int, int]:
    raw_recipe = dish.get("recipe")
    recipe = raw_recipe if isinstance(raw_recipe, dict) else {}
    raw_recipe_ingredients = recipe.get("recipe_ingredients")
    recipe_ingredients = raw_recipe_ingredients if isinstance(raw_recipe_ingredients, list) else []
    step_orders: dict[int, int] = {}
    for item in recipe_ingredients:
        if not isinstance(item, dict):
            continue
        ri_id = _coerce_int(item.get("id"))
        step_order = _coerce_int(item.get("step_order"))
        if ri_id is None or step_order is None:
            continue
        step_orders[ri_id] = step_order
    return step_orders


def sort_ingredients_for_execution(
    dish: JSONObject, ingredients: list[JSONObject]
) -> list[JSONObject]:
    step_orders = build_recipe_step_order_map(dish)

    def _sort_key(item: JSONObject) -> tuple[int, int, int, str, int]:
        recipe_ingredient_id = _coerce_int(item.get("recipe_ingredient_id"))
        task_key = str(item.get("task_key") or "")
        step_order = _runtime_int(item, "step_order")
        if step_order is None and recipe_ingredient_id is not None:
            step_order = step_orders.get(recipe_ingredient_id)
        if step_order is None:
            step_order = _parse_step_order_from_task_key(task_key)
        if step_order is None:
            step_order = 1_000_000
        depth = _runtime_int(item, "depth")
        if depth is None:
            depth = step_order
        parallel_group = _runtime_int(item, "parallel_group")
        if parallel_group is None:
            parallel_group = 0
        item_id = _coerce_int(item.get("id")) or 0
        return (depth, parallel_group, step_order, task_key, item_id)

    return sorted(
        [item for item in ingredients if isinstance(item, dict)],
        key=_sort_key,
    )


def _execution_bucket(item: JSONObject) -> tuple[int, int]:
    depth = _runtime_int(item, "depth")
    if depth is None:
        depth = _runtime_int(item, "step_order")
    if depth is None:
        depth = 1_000_000
    parallel_group = _runtime_int(item, "parallel_group")
    if parallel_group is None:
        parallel_group = 0
    return depth, parallel_group


def next_pending_execution_segment(
    dish: JSONObject, ingredients: list[JSONObject]
) -> ExecutionSegment | None:
    ordered = sort_ingredients_for_execution(dish, ingredients)
    ready_bucket: tuple[int, int] | None = None

    for item in ordered:
        status = item.get("service_exec_status")
        if status in SUCCESS_EXECUTION_STATUSES:
            continue
        if status in FAILURE_EXECUTION_STATUSES:
            if str(item.get("on_failure") or "stop").strip().lower() == "continue":
                continue
            return None
        if status in IN_FLIGHT_EXECUTION_STATUSES:
            return None
        if status not in PENDING_EXECUTION_STATUSES:
            continue
        ready_bucket = _execution_bucket(item)
        break

    if ready_bucket is None:
        return None

    segment: list[JSONObject] = []
    service_types: set[str] = set()
    for item in ordered:
        if _execution_bucket(item) != ready_bucket:
            continue
        if item.get("service_exec_status") not in PENDING_EXECUTION_STATUSES:
            continue
        service_type = str(item.get("service_type") or "").strip().lower()
        if not service_type:
            return None
        service_types.add(service_type)
        segment.append(item)

    if not segment:
        return None
    depth, parallel_group = ready_bucket
    return ExecutionSegment(
        depth=depth,
        parallel_group=parallel_group,
        service_types=tuple(sorted(service_types)),
        rows=segment,
    )


def has_pending_execution(dish: JSONObject, ingredients: list[JSONObject]) -> bool:
    return next_pending_execution_segment(dish, ingredients) is not None


def has_in_flight_execution(ingredients: list[JSONObject]) -> bool:
    return any(
        item.get("service_exec_status") in IN_FLIGHT_EXECUTION_STATUSES
        for item in ingredients
        if isinstance(item, dict)
    )
