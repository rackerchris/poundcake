"""Shared planning helpers for phase-scoped dish dispatch."""

from __future__ import annotations

from api.types import JSONObject

import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.models import DishIngredient, Ingredient, Order, Recipe, RecipeIngredient
from api.plugins.contract import (
    validate_service_operation,
    validate_service_payload_for_operation,
)
from api.services.communications import normalize_run_condition, normalize_run_phase
from api.services.communications_policy import is_communication_step, should_seed_route_step

TEMPLATE_RE = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")


def is_phase_eligible(step_phase: str | None, target_phase: str) -> bool:
    normalized = normalize_run_phase(step_phase)
    target = normalize_run_phase(target_phase)
    if normalized == "both":
        return target in {"firing", "resolving"}
    return normalized == target


def is_run_condition_eligible(ri: RecipeIngredient, *, phase: str, order: Order | None) -> bool:
    condition = normalize_run_condition(getattr(ri, "run_condition", "always"))
    if condition == "always":
        return True
    if order is None:
        return False

    remediation_outcome = str(getattr(order, "remediation_outcome", "") or "").lower()
    timed_out = getattr(order, "clear_timed_out_at", None) is not None
    target_phase = normalize_run_phase(phase)

    if target_phase == "resolving":
        if condition == "resolved_after_success":
            return remediation_outcome == "succeeded" and not timed_out
        if condition == "resolved_after_failure":
            return remediation_outcome == "failed"
        if condition == "resolved_after_no_remediation":
            return remediation_outcome == "none"
        if condition == "resolved_after_timeout":
            return timed_out
        return False

    return condition == "always"


def build_step_task_key(ri: RecipeIngredient) -> str:
    task_suffix = ((ri.ingredient.task_key_template if ri.ingredient else None) or "task").replace(
        ".", "_"
    )
    return f"step_{ri.step_order}_{task_suffix}"


def build_step_parameters(ri: RecipeIngredient) -> JSONObject | None:
    base = dict((ri.ingredient.service_exec_parameters if ri.ingredient else None) or {})
    overrides = getattr(ri, "service_exec_parameters_override", None)
    if overrides:
        base.update(overrides)
    return base or None


def validate_step_operation(ri: RecipeIngredient) -> None:
    validate_service_operation(build_step_parameters(ri))


def _runtime_row_depth(row: DishIngredient) -> int:
    depth = getattr(row, "depth", None)
    if depth is not None:
        return int(depth)
    step_order = getattr(row, "step_order", None)
    return int(step_order) if step_order is not None else 0


def _runtime_row_step_order(row: DishIngredient) -> int:
    step_order = getattr(row, "step_order", None)
    if step_order is not None:
        return int(step_order)
    return _runtime_row_depth(row)


def _candidate_row_depth(ri: RecipeIngredient) -> int:
    depth = getattr(ri, "depth", None)
    if depth is not None:
        return int(depth)
    return int(getattr(ri, "step_order", None) or 0)


def _candidate_row_step_order(ri: RecipeIngredient) -> int:
    return int(getattr(ri, "step_order", None) or _candidate_row_depth(ri))


def _finalizer_base_bucket(
    *,
    recipe_ingredients: list[RecipeIngredient],
    existing_rows: list[DishIngredient],
    phase: str,
) -> tuple[int, int]:
    max_depth = 0
    max_step_order = 0
    for row in existing_rows:
        max_depth = max(max_depth, _runtime_row_depth(row))
        max_step_order = max(max_step_order, _runtime_row_step_order(row))
    for ri in recipe_ingredients:
        if ri.ingredient is None:
            continue
        if not is_phase_eligible(ri.run_phase, phase):
            continue
        max_depth = max(max_depth, _candidate_row_depth(ri))
        max_step_order = max(max_step_order, _candidate_row_step_order(ri))
    return max_depth, max_step_order


def _order_hydration_context(order: Order | None) -> JSONObject:
    if order is None:
        return {"order": {"labels": {}, "annotations": {}}}
    return {
        "order": {
            "id": order.id,
            "req_id": order.req_id,
            "alert_group_name": order.alert_group_name,
            "alert_status": order.alert_status,
            "labels": order.labels or {},
            "annotations": order.annotations or {},
            "raw_data": order.raw_data or {},
        }
    }


def _lookup_path(context: JSONObject, path: str) -> Any:
    current: Any = context
    for part in path.split("."):
        key = part.strip()
        if not key:
            return None
        if isinstance(current, dict) and key in current:
            current = current[key]
            continue
        return None
    return current


def _hydrate_value(value: Any, context: JSONObject) -> Any:
    if isinstance(value, dict):
        return {key: _hydrate_value(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [_hydrate_value(item, context) for item in value]
    if not isinstance(value, str):
        return value

    full_match = TEMPLATE_RE.fullmatch(value.strip())
    if full_match:
        resolved = _lookup_path(context, full_match.group(1).strip())
        return resolved if resolved is not None else value

    def replace(match: re.Match[str]) -> str:
        resolved = _lookup_path(context, match.group(1).strip())
        return "" if resolved is None else str(resolved)

    return TEMPLATE_RE.sub(replace, value)


def build_step_payload(ri: RecipeIngredient, *, order: Order | None = None) -> JSONObject | None:
    base = dict((ri.ingredient.service_payload_template if ri.ingredient else None) or {})
    overrides = getattr(ri, "service_payload", None)
    if overrides:
        base.update(overrides)
    base = _hydrate_value(base, _order_hydration_context(order))
    return base or None


def resolved_expected_run_secs(ri: RecipeIngredient) -> int | None:
    override = getattr(ri, "service_exec_expected_secs", None)
    if override is not None:
        return int(override)
    if ri.ingredient is None or getattr(ri.ingredient, "default_expected_secs", None) is None:
        return None
    return int(ri.ingredient.default_expected_secs)


def resolved_timeout_duration_sec(ri: RecipeIngredient) -> int | None:
    override = getattr(ri, "service_exec_timeout", None)
    if override is not None:
        return int(override)
    if ri.ingredient is None or getattr(ri.ingredient, "default_timeout", None) is None:
        return None
    return int(ri.ingredient.default_timeout)


async def expected_run_secs_for_phase(
    db: AsyncSession,
    *,
    recipe_id: int,
    phase: str,
    extra_recipe_ingredients: list[RecipeIngredient] | None = None,
) -> int:
    normalized_phase = normalize_run_phase(phase)
    allowed_phases = (normalized_phase, "both")
    query = (
        select(
            func.coalesce(
                func.sum(
                    func.coalesce(
                        RecipeIngredient.service_exec_expected_secs,
                        Ingredient.default_expected_secs,
                    )
                ),
                0,
            )
        )
        .select_from(RecipeIngredient)
        .join(Ingredient, RecipeIngredient.ingredient_id == Ingredient.id)
        .where(
            RecipeIngredient.recipe_id == recipe_id,
            RecipeIngredient.run_phase.in_(allowed_phases),
        )
    )
    result = await db.execute(query)
    total = int(result.scalar() or 0)
    if extra_recipe_ingredients:
        for ri in extra_recipe_ingredients:
            if ri.ingredient is None:
                continue
            if not is_phase_eligible(ri.run_phase, phase):
                continue
            total += int(resolved_expected_run_secs(ri) or 0)
    return total


def expected_run_secs_from_recipe_snapshot(
    *,
    recipe: Recipe,
    phase: str,
    extra_recipe_ingredients: list[RecipeIngredient] | None = None,
) -> int:
    total = 0
    for ri in list(recipe.recipe_ingredients) + list(extra_recipe_ingredients or []):
        if ri.ingredient is None:
            continue
        if not is_phase_eligible(ri.run_phase, phase):
            continue
        total += int(resolved_expected_run_secs(ri) or 0)
    return total


def seed_dish_ingredients_for_phase(
    *,
    dish_id: int,
    recipe: Recipe,
    phase: str,
    order: Order | None = None,
    existing_by_recipe_ingredient_id: dict[int, DishIngredient] | None = None,
    extra_recipe_ingredients: list[RecipeIngredient] | None = None,
) -> list[DishIngredient]:
    existing = existing_by_recipe_ingredient_id or {}
    existing_rows = list(existing.values())
    recipe_steps = list(recipe.recipe_ingredients)
    extra_steps = list(extra_recipe_ingredients or [])
    recipe_has_communication_step = any(is_communication_step(ri) for ri in recipe_steps)
    finalizer_depth, finalizer_step_order = _finalizer_base_bucket(
        recipe_ingredients=recipe_steps,
        existing_rows=existing_rows,
        phase=phase,
    )
    seeded: list[DishIngredient] = []
    seeded_communication_count = 0
    candidates = [(ri, False) for ri in recipe_steps] + [(ri, True) for ri in extra_steps]
    for ri, inherited_policy_step in candidates:
        if ri.ingredient is None:
            continue
        is_comm_step = is_communication_step(ri)
        if not is_phase_eligible(ri.run_phase, phase):
            continue
        if not is_run_condition_eligible(ri, phase=phase, order=order):
            continue
        if not should_seed_route_step(recipe_ingredient=ri, order=order):
            continue
        if ri.id in existing:
            continue
        if inherited_policy_step and is_comm_step and recipe_has_communication_step:
            continue

        service_payload = build_step_payload(ri, order=order)
        if ri.ingredient is not None:
            service_exec_parameters = build_step_parameters(ri)
            validate_service_operation(service_exec_parameters)
            validate_service_payload_for_operation(
                service_payload or {},
                ri.ingredient.payload_schema,
                service_exec_parameters,
            )
        step_order = ri.step_order
        depth = ri.depth
        parallel_group = ri.parallel_group
        if is_comm_step and inherited_policy_step:
            seeded_communication_count += 1
            offset = seeded_communication_count
            step_order = finalizer_step_order + offset
            depth = finalizer_depth + offset
            parallel_group = 0

        seeded.append(
            DishIngredient(
                req_id=getattr(order, "req_id", "") or "",
                dish_id=dish_id,
                recipe_ingredient_id=ri.id,
                task_key=f"step_{step_order}_{ri.ingredient.task_key_template.replace('.', '_')}",
                step_order=step_order,
                parallel_group=parallel_group,
                depth=depth,
                service_type=ri.ingredient.service_type,
                service_exec=ri.ingredient.service_exec,
                destination_target=getattr(ri.ingredient, "destination_target", "") or "",
                service_payload=service_payload,
                service_exec_parameters=build_step_parameters(ri),
                service_exec_expected_secs=resolved_expected_run_secs(ri),
                service_exec_timeout=resolved_timeout_duration_sec(ri),
                service_exec_expected_outcome=(
                    ri.service_exec_expected_outcome
                    if ri.service_exec_expected_outcome is not None
                    else getattr(ri.ingredient, "service_exec_expected_outcome_default", None)
                ),
                retry_count=getattr(ri.ingredient, "retry_count", None),
                retry_delay=getattr(ri.ingredient, "retry_delay", None),
                on_failure=getattr(ri.ingredient, "on_failure", None),
                service_exec_status="pending",
            )
        )
    return seeded
