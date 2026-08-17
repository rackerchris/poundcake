"""Tests for recipe-to-runtime dish planning contracts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from api.models.models import DishIngredient, Ingredient, Recipe, RecipeIngredient
from api.plugins.contract import ServicePluginContractError
import api.services.dish_planner as dish_planner
from api.services.dish_planner import (
    build_dish_plan,
    build_step_payload,
    expected_run_secs_from_recipe_snapshot,
    seed_dish_ingredients_for_phase,
)


def _ingredient() -> Ingredient:
    return Ingredient(
        service_type="dummy",
        service_exec="sleep_10",
        destination_target="dummy",
        task_key_template="dummy-sleep-10",
        service_payload_template={"message": "default message"},
        payload_schema={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
            "additionalProperties": False,
        },
        default_expected_secs=10,
        default_timeout=60,
        service_exec_expected_outcome_default={"success": True},
        ingredient_purpose="utility",
        is_blocking=True,
        retry_count=0,
        retry_delay=0,
        on_failure="stop",
    )


def _operation_ingredient() -> Ingredient:
    ingredient = _ingredient()
    ingredient.service_exec = "operation_result"
    ingredient.task_key_template = "dummy-operation-result"
    ingredient.service_payload_template = {}
    ingredient.payload_schema = {
        "type": "object",
        "properties": {
            "message": {"type": "string"},
            "target": {"type": "string", "minLength": 1},
        },
        "additionalProperties": False,
    }
    ingredient.service_exec_parameters = {
        "operation": "run",
        "allowed_operations": ["run"],
        "operation_metadata": {
            "run": {
                "payload_schema": {
                    "type": "object",
                    "properties": {"target": {"type": "string", "minLength": 1}},
                    "required": ["target"],
                    "additionalProperties": False,
                }
            }
        },
    }
    return ingredient


def _comms_ingredient() -> Ingredient:
    return Ingredient(
        service_type="dummy",
        service_exec="communication",
        destination_target="dummy",
        task_key_template="dummy-communication",
        service_payload_template={},
        payload_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        },
        service_exec_parameters={
            "operation": "notify",
            "allowed_operations": ["open", "notify", "close"],
        },
        default_expected_secs=2,
        default_timeout=30,
        service_exec_expected_outcome_default={"success": True},
        ingredient_purpose="comms",
        is_blocking=True,
        retry_count=0,
        retry_delay=0,
        on_failure="continue",
    )


def _route_payload(route_id: str) -> dict:
    return {
        "context": {
            "poundcake_policy": {
                "route_id": route_id,
                "service_type": "dummy",
                "destination_target": "dummy",
                "provider_config": {},
                "enabled": True,
                "position": 1,
            }
        }
    }


def test_seed_dish_ingredients_copies_ingredient_slas_when_recipe_has_no_override() -> None:
    ingredient = _ingredient()
    recipe_ingredient = RecipeIngredient(
        id=101,
        ingredient=ingredient,
        step_order=1,
        parallel_group=1,
        depth=1,
        run_phase="firing",
        run_condition="always",
        service_payload={"message": "runtime message"},
    )
    recipe = Recipe(
        id=501,
        name="sla-defaults",
        enabled=True,
        recipe_ingredients=[recipe_ingredient],
    )

    rows = seed_dish_ingredients_for_phase(dish_id=9001, recipe=recipe, phase="firing")

    assert len(rows) == 1
    assert rows[0].service_exec_expected_secs == 10
    assert rows[0].service_exec_timeout == 60


def test_seed_dish_ingredients_prefers_recipe_ingredient_sla_overrides() -> None:
    ingredient = _ingredient()
    recipe_ingredient = RecipeIngredient(
        id=102,
        ingredient=ingredient,
        step_order=1,
        parallel_group=1,
        depth=1,
        run_phase="firing",
        run_condition="always",
        service_payload={"message": "runtime message"},
        service_exec_expected_secs=30,
        service_exec_timeout=180,
    )
    recipe = Recipe(
        id=502,
        name="sla-overrides",
        enabled=True,
        recipe_ingredients=[recipe_ingredient],
    )

    rows = seed_dish_ingredients_for_phase(dish_id=9002, recipe=recipe, phase="firing")

    assert len(rows) == 1
    assert rows[0].service_exec_expected_secs == 30
    assert rows[0].service_exec_timeout == 180


def test_seed_dish_ingredients_hydrates_payload_from_order_context() -> None:
    ingredient = _ingredient()
    ingredient.service_payload_template = {
        "message": "alert on {{ order.labels.instance }}",
        "labels": "{{ order.labels }}",
    }
    ingredient.payload_schema = {
        "type": "object",
        "properties": {
            "message": {"type": "string"},
            "labels": {"type": "object"},
        },
        "required": ["message"],
        "additionalProperties": False,
    }
    recipe_ingredient = RecipeIngredient(
        id=103,
        ingredient=ingredient,
        step_order=1,
        parallel_group=1,
        depth=1,
        run_phase="firing",
        run_condition="always",
    )
    recipe = Recipe(
        id=504,
        name="payload-hydration",
        enabled=True,
        recipe_ingredients=[recipe_ingredient],
    )
    order = SimpleNamespace(
        id=601,
        req_id="req-hydrate",
        alert_group_name="host-down",
        alert_status="firing",
        labels={"instance": "compute-1", "severity": "critical"},
        annotations={},
        raw_data={},
    )

    rows = seed_dish_ingredients_for_phase(
        dish_id=9003,
        recipe=recipe,
        phase="firing",
        order=order,
    )

    assert len(rows) == 1
    assert rows[0].service_payload == {
        "message": "alert on compute-1",
        "labels": {"instance": "compute-1", "severity": "critical"},
    }
    assert rows[0].service_exec_expected_outcome == {"success": True}


def test_build_step_payload_enriches_managed_comms_context_with_order_alert_data() -> None:
    ingredient = _comms_ingredient()
    recipe_ingredient = RecipeIngredient(
        id=121,
        ingredient=ingredient,
        step_order=1000,
        parallel_group=0,
        depth=1000,
        run_phase="firing",
        run_condition="always",
        service_payload={
            "title": "Alert requires attention",
            "description": "No matching workflow is configured for this alert.",
            "source": "poundcake",
            "context": {
                "source": "poundcake",
                "route_label": "Bakery Rackspace Core",
                "destination_target": "rackspace_core",
                "poundcake_policy": {
                    "managed": True,
                    "route_id": "bakery-rackspace-core-1",
                    "event": "fallback_open",
                },
            },
        },
    )
    order = SimpleNamespace(
        id=611,
        req_id="req-managed",
        alert_group_name="host-down",
        alert_status="firing",
        severity="critical",
        labels={"alertname": "HostDown", "instance": "compute-1"},
        annotations={"summary": "compute-1 is unreachable"},
        raw_data={},
    )

    payload = build_step_payload(recipe_ingredient, order=order)

    assert payload is not None
    context = payload["context"]
    assert context["labels"] == {"alertname": "HostDown", "instance": "compute-1"}
    assert context["annotations"] == {"summary": "compute-1 is unreachable"}
    assert context["alert_name"] == "HostDown"
    assert context["alert_group_name"] == "host-down"
    assert context["order_id"] == 611
    assert context["req_id"] == "req-managed"
    assert payload["severity"] == "critical"
    assert context["poundcake_policy"]["route_id"] == "bakery-rackspace-core-1"


def test_build_step_payload_leaves_non_managed_comms_context_unchanged() -> None:
    ingredient = _comms_ingredient()
    recipe_ingredient = RecipeIngredient(
        id=122,
        ingredient=ingredient,
        step_order=1,
        parallel_group=0,
        depth=0,
        run_phase="firing",
        run_condition="always",
        service_payload={
            "title": "Manual comms",
            "source": "poundcake",
            "context": {"route_label": "Bakery Rackspace Core"},
        },
    )
    order = SimpleNamespace(
        id=612,
        req_id="req-nonmanaged",
        alert_group_name="host-down",
        alert_status="firing",
        severity="critical",
        labels={"alertname": "HostDown"},
        annotations={},
        raw_data={},
    )

    payload = build_step_payload(recipe_ingredient, order=order)

    assert payload is not None
    assert "labels" not in payload["context"]
    assert "order_id" not in payload["context"]
    assert "severity" not in payload


def test_build_step_payload_managed_comms_without_order_is_unchanged() -> None:
    ingredient = _comms_ingredient()
    recipe_ingredient = RecipeIngredient(
        id=123,
        ingredient=ingredient,
        step_order=1000,
        parallel_group=0,
        depth=1000,
        run_phase="firing",
        run_condition="always",
        service_payload={
            "title": "Alert requires attention",
            "source": "poundcake",
            "context": {
                "source": "poundcake",
                "poundcake_policy": {"managed": True, "route_id": "bakery-rackspace-core-1"},
            },
        },
    )

    payload = build_step_payload(recipe_ingredient, order=None)

    assert payload is not None
    assert "labels" not in payload["context"]
    assert "order_id" not in payload["context"]
    assert "severity" not in payload


def test_seed_dish_ingredients_uses_operator_action_order_payload() -> None:
    ingredient = _operation_ingredient()
    recipe_ingredient = RecipeIngredient(
        id=113,
        ingredient=ingredient,
        step_order=1,
        parallel_group=1,
        depth=1,
        run_phase="firing",
        run_condition="always",
        service_payload={},
    )
    recipe = Recipe(
        id=514,
        name="operator-action:dummy:operation",
        enabled=True,
        recipe_ingredients=[recipe_ingredient],
    )
    order = SimpleNamespace(
        id=611,
        req_id="req-operator-action",
        alert_group_name="operator-action:dummy:operation",
        alert_status="firing",
        labels={},
        annotations={},
        raw_data={
            "operator_action": True,
            "service_type": "dummy",
            "service_exec": "operation_result",
            "task_key_template": "dummy-operation-result",
            "service_payload": {"target": "api"},
        },
    )

    rows = seed_dish_ingredients_for_phase(
        dish_id=9013,
        recipe=recipe,
        phase="firing",
        order=order,
    )

    assert len(rows) == 1
    assert rows[0].service_payload == {"target": "api"}


def test_seed_dish_ingredients_applies_resolving_run_conditions() -> None:
    ingredient = _ingredient()
    recipe_ingredient = RecipeIngredient(
        id=104,
        ingredient=ingredient,
        step_order=1,
        parallel_group=1,
        depth=1,
        run_phase="resolving",
        run_condition="resolved_after_success",
        service_payload={"message": "resolved"},
    )
    recipe = Recipe(
        id=505,
        name="resolving-condition",
        enabled=True,
        recipe_ingredients=[recipe_ingredient],
    )
    failed_order = SimpleNamespace(
        id=602,
        req_id="req-failed",
        alert_group_name="host-down",
        alert_status="resolved",
        remediation_outcome="failed",
        clear_timed_out_at=None,
        labels={},
        annotations={},
        raw_data={},
    )
    succeeded_order = SimpleNamespace(
        id=603,
        req_id="req-succeeded",
        alert_group_name="host-down",
        alert_status="resolved",
        remediation_outcome="succeeded",
        clear_timed_out_at=None,
        labels={},
        annotations={},
        raw_data={},
    )

    assert (
        seed_dish_ingredients_for_phase(
            dish_id=9004,
            recipe=recipe,
            phase="resolving",
            order=failed_order,
        )
        == []
    )
    rows = seed_dish_ingredients_for_phase(
        dish_id=9005,
        recipe=recipe,
        phase="resolving",
        order=succeeded_order,
    )

    assert len(rows) == 1
    assert rows[0].service_payload == {"message": "resolved"}


def test_seed_dish_ingredients_validates_payload_before_dispatch() -> None:
    recipe_ingredient = RecipeIngredient(
        id=105,
        ingredient=_ingredient(),
        step_order=1,
        parallel_group=1,
        depth=1,
        run_phase="firing",
        run_condition="always",
        service_payload={"message": 123},
    )
    recipe = Recipe(
        id=506,
        name="invalid-payload",
        enabled=True,
        recipe_ingredients=[recipe_ingredient],
    )

    with pytest.raises(ServicePluginContractError, match="service_payload.message"):
        seed_dish_ingredients_for_phase(dish_id=9006, recipe=recipe, phase="firing")


def test_seed_dish_ingredients_uses_operation_payload_schema_before_dispatch() -> None:
    recipe_ingredient = RecipeIngredient(
        id=106,
        ingredient=_operation_ingredient(),
        step_order=1,
        parallel_group=1,
        depth=1,
        run_phase="firing",
        run_condition="always",
        service_payload={"message": "top-level-only"},
    )
    recipe = Recipe(
        id=507,
        name="invalid-operation-payload",
        enabled=True,
        recipe_ingredients=[recipe_ingredient],
    )

    with pytest.raises(ServicePluginContractError, match="target"):
        seed_dish_ingredients_for_phase(dish_id=9007, recipe=recipe, phase="firing")


def test_seed_dish_ingredients_places_comms_after_current_dish_shape() -> None:
    early = RecipeIngredient(
        id=106,
        ingredient=_ingredient(),
        step_order=1,
        parallel_group=0,
        depth=0,
        run_phase="firing",
        run_condition="always",
        service_payload={"message": "early"},
    )
    late = RecipeIngredient(
        id=107,
        ingredient=_ingredient(),
        step_order=10,
        parallel_group=0,
        depth=5000,
        run_phase="firing",
        run_condition="always",
        service_payload={"message": "late"},
    )
    comms = RecipeIngredient(
        id=108,
        ingredient=_comms_ingredient(),
        step_order=1000,
        parallel_group=0,
        depth=1000,
        run_phase="firing",
        run_condition="always",
        service_payload={"message": "notify"},
    )
    recipe = Recipe(
        id=507,
        name="comms-finalizer",
        enabled=True,
        recipe_ingredients=[early, late],
    )

    rows = seed_dish_ingredients_for_phase(
        dish_id=9007,
        recipe=recipe,
        phase="firing",
        extra_recipe_ingredients=[comms],
    )

    comms_row = next(row for row in rows if row.recipe_ingredient_id == 108)
    assert comms_row.step_order == 11
    assert comms_row.depth == 5001
    assert comms_row.parallel_group == 0


def test_seed_dish_ingredients_skips_global_comms_when_recipe_has_local_comms() -> None:
    remediation = RecipeIngredient(
        id=109,
        ingredient=_ingredient(),
        step_order=1,
        parallel_group=0,
        depth=0,
        run_phase="firing",
        run_condition="always",
        service_payload={"message": "work"},
    )
    local_comms = RecipeIngredient(
        id=110,
        ingredient=_comms_ingredient(),
        step_order=2,
        parallel_group=0,
        depth=1,
        run_phase="firing",
        run_condition="always",
        service_payload={"message": "local starting"},
    )
    global_comms = RecipeIngredient(
        id=111,
        ingredient=_comms_ingredient(),
        step_order=1000,
        parallel_group=0,
        depth=1000,
        run_phase="firing",
        run_condition="always",
        service_payload={"message": "global finalizer"},
    )
    recipe = Recipe(
        id=508,
        name="local-plus-global-comms",
        enabled=True,
        recipe_ingredients=[remediation, local_comms],
    )

    rows = seed_dish_ingredients_for_phase(
        dish_id=9008,
        recipe=recipe,
        phase="firing",
        extra_recipe_ingredients=[global_comms],
    )

    local_row = next(row for row in rows if row.recipe_ingredient_id == 110)
    assert local_row.step_order == 2
    assert local_row.depth == 1
    assert {row.recipe_ingredient_id for row in rows} == {109, 110}


def test_seed_dish_ingredients_does_not_duplicate_existing_global_comms_row() -> None:
    remediation = RecipeIngredient(
        id=112,
        ingredient=_ingredient(),
        step_order=1,
        parallel_group=0,
        depth=0,
        run_phase="firing",
        run_condition="always",
        service_payload={"message": "work"},
    )
    global_comms = RecipeIngredient(
        id=113,
        ingredient=_comms_ingredient(),
        step_order=1000,
        parallel_group=0,
        depth=1000,
        run_phase="firing",
        run_condition="always",
        service_payload={"message": "notify"},
    )
    existing_global_comms = DishIngredient(
        id=999,
        dish_id=9009,
        recipe_ingredient_id=113,
        task_key="step_2_dummy-communication",
        service_type="dummy",
        service_exec="communication",
        service_payload={"context": {"poundcake_policy": {"route_id": "default"}}},
        service_exec_status="pending",
    )
    recipe = Recipe(
        id=509,
        name="existing-global-comms",
        enabled=True,
        recipe_ingredients=[remediation],
    )

    rows = seed_dish_ingredients_for_phase(
        dish_id=9009,
        recipe=recipe,
        phase="firing",
        existing_by_recipe_ingredient_id={113: existing_global_comms},
        extra_recipe_ingredients=[global_comms],
    )

    assert [row.recipe_ingredient_id for row in rows] == [112]


@pytest.mark.asyncio
async def test_build_dish_plan_inherits_global_comms_when_recipe_has_no_local_comms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remediation = RecipeIngredient(
        id=114,
        ingredient=_ingredient(),
        step_order=1,
        parallel_group=0,
        depth=0,
        run_phase="firing",
        run_condition="always",
    )
    global_comms = RecipeIngredient(
        id=115,
        ingredient=_comms_ingredient(),
        step_order=1000,
        parallel_group=0,
        depth=1000,
        run_phase="firing",
        run_condition="always",
        service_payload=_route_payload("global-route"),
    )
    recipe = Recipe(
        id=510,
        name="inherits-global-comms",
        enabled=True,
        recipe_ingredients=[remediation],
    )
    global_recipe = Recipe(
        id=511,
        name="pcm-policy-global",
        enabled=True,
        recipe_ingredients=[global_comms],
    )

    async def _global_policy_recipe(_db: object) -> Recipe:
        return global_recipe

    monkeypatch.setattr(
        dish_planner,
        "get_global_policy_recipe_for_planning",
        _global_policy_recipe,
    )

    plan = await build_dish_plan(object(), recipe=recipe, order=SimpleNamespace(raw_data={}))

    assert plan.recipe is recipe
    assert plan.inherited_recipe_ingredients == [global_comms]


@pytest.mark.asyncio
async def test_build_dish_plan_keeps_local_comms_as_the_effective_comms_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_comms = RecipeIngredient(
        id=116,
        ingredient=_comms_ingredient(),
        step_order=2,
        parallel_group=0,
        depth=1,
        run_phase="firing",
        run_condition="always",
        service_payload=_route_payload("local-route"),
    )
    recipe = Recipe(
        id=512,
        name="local-comms",
        enabled=True,
        recipe_ingredients=[local_comms],
    )

    async def _unexpected_global_policy_recipe(_db: object) -> Recipe:
        raise AssertionError("local comms recipes must not load global policy")

    monkeypatch.setattr(
        dish_planner,
        "get_global_policy_recipe_for_planning",
        _unexpected_global_policy_recipe,
    )

    plan = await build_dish_plan(object(), recipe=recipe, order=SimpleNamespace(raw_data={}))

    assert plan.recipe is recipe
    assert plan.inherited_recipe_ingredients == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw_data",
    [
        {"operator_action": True},
        {"order_type": "scheduled_task"},
    ],
)
async def test_build_dish_plan_keeps_predeclared_orders_exact(
    monkeypatch: pytest.MonkeyPatch,
    raw_data: dict,
) -> None:
    recipe = Recipe(
        id=513,
        name="predeclared",
        enabled=True,
        recipe_ingredients=[],
    )

    async def _unexpected_global_policy_recipe(_db: object) -> Recipe:
        raise AssertionError("predeclared order plans must not load inherited policy")

    monkeypatch.setattr(
        dish_planner,
        "get_global_policy_recipe_for_planning",
        _unexpected_global_policy_recipe,
    )

    plan = await build_dish_plan(object(), recipe=recipe, order=SimpleNamespace(raw_data=raw_data))

    assert plan.recipe is recipe
    assert plan.inherited_recipe_ingredients == []


def test_expected_run_secs_from_recipe_snapshot_matches_phase_eligible_steps() -> None:
    first = RecipeIngredient(
        id=201,
        ingredient=_ingredient(),
        step_order=1,
        parallel_group=0,
        depth=0,
        run_phase="firing",
        run_condition="always",
        service_exec_expected_secs=12,
    )
    second = RecipeIngredient(
        id=202,
        ingredient=_ingredient(),
        step_order=2,
        parallel_group=0,
        depth=1,
        run_phase="both",
        run_condition="always",
    )
    resolving_only = RecipeIngredient(
        id=203,
        ingredient=_ingredient(),
        step_order=3,
        parallel_group=0,
        depth=2,
        run_phase="resolving",
        run_condition="always",
        service_exec_expected_secs=99,
    )
    extra_policy = RecipeIngredient(
        id=204,
        ingredient=_ingredient(),
        step_order=4,
        parallel_group=0,
        depth=3,
        run_phase="firing",
        run_condition="always",
        service_exec_expected_secs=5,
    )
    recipe = Recipe(
        id=503,
        name="duration-snapshot",
        enabled=True,
        recipe_ingredients=[first, second, resolving_only],
    )

    assert (
        expected_run_secs_from_recipe_snapshot(
            recipe=recipe,
            phase="firing",
            extra_recipe_ingredients=[extra_policy],
        )
        == 27
    )
