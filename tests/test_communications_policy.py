"""Tests for provider-neutral PoundCake communications policy."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from api.plugins.contract import (
    ServicePluginContractError,
    validate_service_payload,
    validate_service_payload_for_operation,
)
from api.plugins.dummy.templates import DUMMY_INGREDIENT_TEMPLATES
from api.schemas.schemas import CommunicationPolicyUpdate
from api.services.capability_resolution import ResolvedCapabilityIngredient
from api.services.communications_policy import (
    _apply_step_spec,
    _group_routes_from_steps,
    _managed_step_key_from_recipe_ingredient,
    _step_matches_spec,
    build_recipe_local_policy_step_specs,
    replace_recipe_communication_steps,
)
from kitchen.execution_segments import next_pending_execution_segment


def _dummy_template(service_exec: str) -> dict[object, object]:
    for template in DUMMY_INGREDIENT_TEMPLATES:
        if template["service_exec"] == service_exec and template["ingredient_purpose"] == "comms":
            return template
    raise AssertionError(f"missing dummy comms template: {service_exec}")


def test_comms_policy_accepts_service_plugin_route() -> None:
    payload = CommunicationPolicyUpdate.model_validate(
        {
            "routes": [
                {
                    "label": "Dummy comms",
                    "service_type": "dummy",
                    "destination_target": "dummy",
                    "provider_config": {},
                    "enabled": True,
                    "position": 1,
                }
            ]
        }
    )

    routes, specs = build_recipe_local_policy_step_specs(
        recipe_id=1,
        routes=[item.model_dump() for item in payload.routes],
    )

    assert [route.service_type for route in routes] == ["dummy"]
    assert {spec["service_type"] for spec in specs} == {"dummy"}
    assert {"open", "notify", "close"} <= {spec["service_exec"] for spec in specs}


def test_managed_comms_steps_are_late_isolated_execution_buckets() -> None:
    _routes, specs = build_recipe_local_policy_step_specs(
        recipe_id=1,
        routes=[
            {
                "label": "Dummy comms",
                "service_type": "dummy",
                "destination_target": "dummy",
                "provider_config": {},
                "enabled": True,
                "position": 0,
            }
        ],
    )

    assert all(int(spec["step_order"]) >= 1000 for spec in specs)
    assert all(spec["depth"] == spec["step_order"] for spec in specs)
    assert all(spec["parallel_group"] == 0 for spec in specs)


def test_group_routes_reads_managed_metadata_from_recipe_step_payload() -> None:
    _routes, specs = build_recipe_local_policy_step_specs(
        recipe_id=1,
        routes=[
            {
                "id": "dummy-default",
                "label": "Dummy comms",
                "service_type": "dummy",
                "destination_target": "dummy",
                "provider_config": {},
                "enabled": True,
                "position": 1,
            }
        ],
    )
    step = SimpleNamespace(
        service_payload=specs[0]["service_payload"],
        ingredient=SimpleNamespace(
            ingredient_purpose="comms",
            service_payload_template={},
        ),
    )

    grouped = _group_routes_from_steps([step])

    assert [route.id for route in grouped] == ["dummy-default"]


def test_managed_comms_step_identity_is_stable_for_in_place_reconcile() -> None:
    _routes, specs = build_recipe_local_policy_step_specs(
        recipe_id=1,
        routes=[
            {
                "id": "dummy-default",
                "label": "Dummy comms",
                "service_type": "dummy",
                "destination_target": "dummy",
                "provider_config": {},
                "enabled": True,
                "position": 1,
            }
        ],
    )
    spec = dict(specs[0])
    spec.update(
        {
            "ingredient_id": 10,
            "service_exec_expected_secs": 5,
            "service_exec_timeout": 30,
        }
    )
    step = SimpleNamespace(
        ingredient_id=10,
        step_order=1,
        on_success="stop",
        parallel_group=9,
        depth=9,
        service_payload=spec["service_payload"],
        service_exec_parameters_override={},
        service_exec_expected_secs=1,
        service_exec_timeout=2,
        service_exec_expected_outcome={},
        run_phase="both",
        run_condition="always",
        ingredient=SimpleNamespace(
            ingredient_purpose="comms",
            service_payload_template={},
        ),
    )

    assert _managed_step_key_from_recipe_ingredient(step) == spec["task_key_template"]
    assert not _step_matches_spec(step, spec)

    _apply_step_spec(step, spec)

    assert _step_matches_spec(step, spec)


@pytest.mark.asyncio
async def test_replace_recipe_communication_steps_reuses_existing_managed_step() -> None:
    _routes, specs = build_recipe_local_policy_step_specs(
        recipe_id=1,
        routes=[
            {
                "id": "dummy-default",
                "label": "Dummy comms",
                "service_type": "dummy",
                "destination_target": "dummy",
                "provider_config": {},
                "enabled": True,
                "position": 1,
            }
        ],
    )
    ingredient = SimpleNamespace(
        id=10,
        service_type="dummy",
        service_exec="communication",
        ingredient_purpose="comms",
        is_active=True,
        deleted=False,
        payload_schema=_dummy_template("communication")["payload_schema"],
        service_exec_parameters={
            "operation": "open",
            "allowed_operations": ["open", "notify", "update", "close"],
        },
        default_expected_secs=5,
        default_timeout=30,
    )
    existing_step = SimpleNamespace(
        id=99,
        ingredient_id=10,
        step_order=1,
        on_success="stop",
        parallel_group=9,
        depth=9,
        service_payload=specs[0]["service_payload"],
        service_exec_parameters_override={},
        service_exec_expected_secs=1,
        service_exec_timeout=2,
        service_exec_expected_outcome={},
        run_phase="both",
        run_condition="always",
        ingredient=ingredient,
    )
    recipe = SimpleNamespace(id=1, recipe_ingredients=[existing_step])

    class _ScalarResult:
        def __init__(self, rows: list[SimpleNamespace]) -> None:
            self._rows = rows

        def all(self) -> list[SimpleNamespace]:
            return list(self._rows)

    class _ExecuteResult:
        def __init__(self, rows: list[SimpleNamespace]) -> None:
            self._rows = rows

        def scalars(self) -> _ScalarResult:
            return _ScalarResult(self._rows)

        def unique(self) -> "_ExecuteResult":
            return self

    class _Session:
        def __init__(self) -> None:
            self.added: list[object] = []
            self.calls = 0

        async def execute(self, _statement: object) -> _ExecuteResult:
            self.calls += 1
            if self.calls == 1:
                return _ExecuteResult([existing_step])
            return _ExecuteResult([ingredient])

        def add(self, item: object) -> None:
            self.added.append(item)

    session = _Session()

    monkeypatch = pytest.MonkeyPatch()
    async def fake_enabled_plugin_configs(_db: object) -> dict[str, object]:
        return {}

    async def fake_resolve_active_capability_ingredient(
        _db: object, *, capability: dict[str, object]
    ) -> ResolvedCapabilityIngredient:
        return ResolvedCapabilityIngredient(
            capability_id="dummy.communication.open.default",
            service_type="dummy",
            mode="communication",
            operation=str(capability["operation"]),
            defaults={},
            priority=100,
            ingredient=ingredient,
        )

    monkeypatch.setattr(
        "api.services.communications_policy._enabled_plugin_configs",
        fake_enabled_plugin_configs,
    )
    monkeypatch.setattr(
        "api.services.communications_policy.build_enabled_plugin_capability_catalog",
        lambda _configs=None: [
            {
                "capability_id": "dummy.communication.open.default",
                "service_type": "dummy",
                "mode": "communication",
                "operation": "open",
                "ingredient_ref": {
                    "service_exec": "communication",
                    "destination_target": "dummy",
                    "task_key_template": "dummy-comms",
                },
                "defaults": {},
                "priority": 100,
            }
        ],
    )
    monkeypatch.setattr(
        "api.services.communications_policy.resolve_active_capability_ingredient",
        fake_resolve_active_capability_ingredient,
    )
    try:
        await replace_recipe_communication_steps(session, recipe=recipe, step_specs=specs)
    finally:
        monkeypatch.undo()

    assert len(session.added) == len(specs) - 1
    assert existing_step.id == 99
    assert existing_step.step_order == specs[0]["step_order"]
    assert existing_step.on_success == specs[0]["on_success"]


@pytest.mark.asyncio
async def test_replace_recipe_communication_steps_loads_existing_steps_via_session() -> None:
    _routes, specs = build_recipe_local_policy_step_specs(
        recipe_id=7,
        routes=[
            {
                "id": "dummy-default",
                "label": "Dummy comms",
                "service_type": "dummy",
                "destination_target": "dummy",
                "provider_config": {},
                "enabled": True,
                "position": 1,
            }
        ],
    )
    ingredient = SimpleNamespace(
        id=321,
        service_type="dummy",
        service_exec="communication",
        ingredient_purpose="comms",
        is_active=True,
        deleted=False,
        payload_schema=_dummy_template("communication")["payload_schema"],
        service_exec_parameters={
            "operation": "open",
            "allowed_operations": ["open", "notify", "update", "close"],
        },
        service_payload_template={},
        default_expected_secs=5,
        default_timeout=30,
    )
    existing_step = SimpleNamespace(
        id=88,
        recipe_id=7,
        ingredient_id=321,
        step_order=999,
        on_success="stop",
        parallel_group=9,
        depth=9,
        service_payload=specs[0]["service_payload"],
        service_exec_parameters_override={},
        service_exec_expected_secs=1,
        service_exec_timeout=2,
        service_exec_expected_outcome={},
        run_phase="both",
        run_condition="always",
        ingredient=ingredient,
    )
    recipe = SimpleNamespace(id=7)

    class _ScalarResult:
        def __init__(self, rows: list[object]) -> None:
            self._rows = rows

        def all(self) -> list[object]:
            return list(self._rows)

    class _ExecuteResult:
        def __init__(self, rows: list[object]) -> None:
            self._rows = rows

        def scalars(self) -> _ScalarResult:
            return _ScalarResult(self._rows)

        def unique(self) -> "_ExecuteResult":
            return self

    class _Session:
        def __init__(self) -> None:
            self.added: list[object] = []
            self.calls = 0

        async def execute(self, _statement: object) -> _ExecuteResult:
            self.calls += 1
            if self.calls == 1:
                return _ExecuteResult([existing_step])
            return _ExecuteResult([ingredient])

        def add(self, item: object) -> None:
            self.added.append(item)

    session = _Session()

    monkeypatch = pytest.MonkeyPatch()
    async def fake_enabled_plugin_configs(_db: object) -> dict[str, object]:
        return {}

    async def fake_resolve_active_capability_ingredient(
        _db: object, *, capability: dict[str, object]
    ) -> ResolvedCapabilityIngredient:
        return ResolvedCapabilityIngredient(
            capability_id="dummy.communication.open.default",
            service_type="dummy",
            mode="communication",
            operation=str(capability["operation"]),
            defaults={},
            priority=100,
            ingredient=ingredient,
        )

    monkeypatch.setattr(
        "api.services.communications_policy._enabled_plugin_configs",
        fake_enabled_plugin_configs,
    )
    monkeypatch.setattr(
        "api.services.communications_policy.build_enabled_plugin_capability_catalog",
        lambda _configs=None: [
            {
                "capability_id": "dummy.communication.open.default",
                "service_type": "dummy",
                "mode": "communication",
                "operation": "open",
                "ingredient_ref": {
                    "service_exec": "communication",
                    "destination_target": "dummy",
                    "task_key_template": "dummy-comms",
                },
                "defaults": {},
                "priority": 100,
            }
        ],
    )
    monkeypatch.setattr(
        "api.services.communications_policy.resolve_active_capability_ingredient",
        fake_resolve_active_capability_ingredient,
    )
    try:
        await replace_recipe_communication_steps(session, recipe=recipe, step_specs=specs)
    finally:
        monkeypatch.undo()

    assert session.calls == 1
    assert len(session.added) == len(specs) - 1
    assert existing_step.id == 88
    assert existing_step.step_order == specs[0]["step_order"]
    assert existing_step.on_success == specs[0]["on_success"]


def test_comms_depth_keeps_policy_step_after_remediation_bucket() -> None:
    rows = [
        {
            "id": 1,
            "service_type": "dummy",
            "service_exec": "positive_result",
            "service_exec_status": "pending",
            "step_order": 1,
            "depth": 0,
            "parallel_group": 0,
        },
        {
            "id": 2,
            "service_type": "dummy",
            "service_exec": "communication",
            "service_exec_status": "pending",
            "step_order": 1000,
            "depth": 1000,
            "parallel_group": 0,
        },
    ]

    segment = next_pending_execution_segment({"id": 99, "recipe": {}}, rows)

    assert segment is not None
    assert [row["id"] for row in segment.rows] == [1]


def test_dummy_comms_template_rejects_invalid_filled_payload() -> None:
    template = _dummy_template("communication")
    with pytest.raises(ServicePluginContractError):
        validate_service_payload(
            {"message": "missing title and description"}, template["payload_schema"]
        )


def test_bakery_comms_template_accepts_managed_policy_context() -> None:
    from api.plugins.bakery.templates import ingredient_templates

    template = next(
        item
        for item in ingredient_templates()
        if item["service_exec"] == "communication"
    )
    parameters = dict(template["service_exec_parameters"])
    parameters["operation"] = "open"
    validate_service_payload_for_operation(
        {
            "title": "Alert requires attention",
            "description": "PoundCake opened a communication.",
            "source": "poundcake",
            "context": {
                "source": "poundcake",
                "route_label": "Bakery Rackspace Core",
                "destination_target": "rackspace_core",
                "provider_config": {"assignment_group": "Cloud"},
                "semantic_text": {"headline": "Alert requires attention"},
                "poundcake_policy": {"route_id": "bakery-rackspace-core-1"},
            },
        },
        template["payload_schema"],
        parameters,
    )


def test_bakery_comms_template_accepts_planned_alert_context() -> None:
    from api.plugins.bakery.templates import ingredient_templates

    template = next(
        item
        for item in ingredient_templates()
        if item["service_exec"] == "communication"
    )
    parameters = dict(template["service_exec_parameters"])
    parameters["operation"] = "open"
    validate_service_payload_for_operation(
        {
            "title": "PoundCake alert update: kube-pod-crash-looping-critical",
            "description": "PoundCake completed the managed critical-alert recipe.",
            "message": "PoundCake completed alert validation, evidence gathering, and action routing.",
            "source": "genestack_monitoring",
            "severity": "critical",
            "category": "alert_remediation",
            "state": "updated",
            "context": {
                "alert_name": "kube-pod-crash-looping-critical",
                "alert_group_name": "kube-pod-crash-looping-critical",
                "labels": {"severity": "critical", "namespace": "poundcake"},
                "annotations": {"summary": "Pod is crash looping"},
                "order_id": 75,
                "req_id": "E2E-PROM-RULE-RELOAD-123",
                "source_path": "alerts/kubernetes/pods.yaml",
                "operator_review_required": True,
            },
        },
        template["payload_schema"],
        parameters,
    )


@pytest.mark.asyncio
async def test_replace_recipe_communication_steps_requires_enabled_comms_capability() -> None:
    _routes, specs = build_recipe_local_policy_step_specs(
        recipe_id=1,
        routes=[
            {
                "id": "dummy-default",
                "label": "Dummy comms",
                "service_type": "dummy",
                "destination_target": "dummy",
                "provider_config": {},
                "enabled": True,
                "position": 1,
            }
        ],
    )
    recipe = SimpleNamespace(id=1, recipe_ingredients=[])

    class _ScalarResult:
        def all(self) -> list[object]:
            return []

    class _ExecuteResult:
        def scalars(self) -> _ScalarResult:
            return _ScalarResult()

        def unique(self) -> "_ExecuteResult":
            return self

    class _Session:
        async def execute(self, _statement: object) -> _ExecuteResult:
            return _ExecuteResult()

        def add(self, _item: object) -> None:
            raise AssertionError("no steps should be added when capability resolution fails")

    session = _Session()
    monkeypatch = pytest.MonkeyPatch()
    async def fake_enabled_plugin_configs(_db: object) -> dict[str, object]:
        return {}

    monkeypatch.setattr(
        "api.services.communications_policy._enabled_plugin_configs",
        fake_enabled_plugin_configs,
    )
    monkeypatch.setattr(
        "api.services.communications_policy.build_enabled_plugin_capability_catalog",
        lambda _configs=None: [],
    )
    try:
        with pytest.raises(ValueError, match="No enabled communication capability registered"):
            await replace_recipe_communication_steps(session, recipe=recipe, step_specs=specs)
    finally:
        monkeypatch.undo()
