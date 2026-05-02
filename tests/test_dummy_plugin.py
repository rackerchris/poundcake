"""Unit tests for the dummy service plugin reference implementation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from api.plugins.catalog import (
    get_enabled_plugin_helper,
    get_enabled_plugin_helper_capabilities,
    get_enabled_plugins,
    validate_enabled_plugin_helper_dependencies,
)
from api.plugins.contract import (
    ServicePluginContractError,
    validate_payload_schema,
    validate_service_operation,
)
from api.plugins.dummy.adapter import DummyExecutionAdapter
from api.plugins.dummy.bootstrap import bootstrap_dummy_helper_validation
from api.plugins.dummy.helper import DummyPluginHelper
from api.plugins.dummy.templates import (
    DUMMY_INGREDIENT_TEMPLATES,
    DUMMY_RECIPE_TEMPLATES,
    DUMMY_SCHEDULED_TASKS,
)
from api.plugins.manifest import validate_service_plugin
from api.plugins.manifest import ServicePlugin, ServicePluginManifestError
from api.plugins.types import ExecutionContext
from api.services.dish_planner import build_step_parameters, validate_step_operation


def _ctx(service_exec: str) -> ExecutionContext:
    return ExecutionContext(
        service_type="dummy",
        service_exec=service_exec,
        req_id="unit-test",
        service_payload={"message": "hello"},
    )


def _communication_ctx(operation: str) -> ExecutionContext:
    return ExecutionContext(
        service_type="dummy",
        service_exec="communication",
        req_id="unit-test",
        service_payload={
            "title": "hello",
            "description": "world",
            "message": "message",
            "source": "unit-test",
            "context": {},
        },
        service_exec_parameters={
            "operation": operation,
            "allowed_operations": ["open", "notify", "update", "close"],
        },
    )


def test_catalog_defaults_to_dummy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POUNDCAKE_ENABLED_PLUGINS", raising=False)
    plugins = get_enabled_plugins()
    assert [plugin.service_type for plugin in plugins] == ["dummy"]


def test_dummy_manifest_validates() -> None:
    plugin = get_enabled_plugins()[0]
    assert validate_service_plugin(plugin, directory_name="dummy") is plugin
    assert plugin.plugin_tier == "supported"
    assert plugin.plugin_log_key == "dummy"
    assert plugin.helper_factory is not None
    assert plugin.helper_capabilities == ("dummy.echo",)
    assert plugin.required_helper_capabilities == {"dummy": ("dummy.echo",)}
    assert plugin.bootstrap_factory is not None


def test_dummy_helper_is_registered() -> None:
    helper = get_enabled_plugin_helper("dummy")
    assert helper is not None
    assert helper.echo({"message": "hello"}) == {  # type: ignore[attr-defined]
        "success": True,
        "service_type": "dummy",
        "payload": {"message": "hello"},
    }
    assert get_enabled_plugin_helper_capabilities()["dummy"] == ["dummy.echo"]
    validate_enabled_plugin_helper_dependencies()


@pytest.mark.asyncio
async def test_dummy_bootstrap_consumes_registered_helper() -> None:
    stats = await bootstrap_dummy_helper_validation(
        None,  # type: ignore[arg-type]
        {"dummy": DummyPluginHelper()},
    )

    assert stats == {
        "processed": 1,
        "errors": 0,
        "helper": {
            "success": True,
            "service_type": "dummy",
            "payload": {"phase": "bootstrap"},
        },
    }


def test_dummy_health_check_reports_healthy() -> None:
    health = DummyExecutionAdapter().health_check()

    assert health.service_type == "dummy"
    assert health.status == "healthy"


def test_manifest_rejects_invalid_helper_capability() -> None:
    plugin = ServicePlugin(
        service_type="dummy",
        adapter_factory=lambda: DummyExecutionAdapter(),
        ingredient_templates=DUMMY_INGREDIENT_TEMPLATES,
        scheduled_tasks=(
            {
                "task_key": "plugin-health-check:dummy",
                "task_type": "plugin_health_check",
                "service_type": "dummy",
                "service_exec": "health_check",
            },
        ),
        helper_factory=lambda: object(),
        helper_capabilities=("RepoRead",),
    )
    with pytest.raises(ServicePluginManifestError, match="lowercase dotted token"):
        validate_service_plugin(plugin, directory_name="dummy")


def test_manifest_rejects_helper_capabilities_without_helper_factory() -> None:
    plugin = ServicePlugin(
        service_type="dummy",
        adapter_factory=lambda: DummyExecutionAdapter(),
        ingredient_templates=DUMMY_INGREDIENT_TEMPLATES,
        scheduled_tasks=(
            {
                "task_key": "plugin-health-check:dummy",
                "task_type": "plugin_health_check",
                "service_type": "dummy",
                "service_exec": "health_check",
            },
        ),
        helper_capabilities=("dummy.echo",),
    )
    with pytest.raises(ServicePluginManifestError, match="require helper_factory"):
        validate_service_plugin(plugin, directory_name="dummy")


def test_unapproved_plugin_cannot_register_log_key() -> None:
    plugin = ServicePlugin(
        service_type="custom",
        adapter_factory=lambda: DummyExecutionAdapter(),
        ingredient_templates=DUMMY_INGREDIENT_TEMPLATES,
        scheduled_tasks=(
            {
                "task_key": "plugin-health-check:custom",
                "task_type": "plugin_health_check",
                "service_type": "custom",
                "service_exec": "health_check",
            },
        ),
        plugin_tier="community",
        plugin_log_key="custom",
    )
    with pytest.raises(ServicePluginManifestError, match="plugin_log_key"):
        validate_service_plugin(plugin, directory_name="custom")


def test_unapproved_plugin_cannot_claim_supported_tier() -> None:
    plugin = ServicePlugin(
        service_type="custom",
        adapter_factory=lambda: DummyExecutionAdapter(),
        ingredient_templates=DUMMY_INGREDIENT_TEMPLATES,
        scheduled_tasks=(
            {
                "task_key": "plugin-health-check:custom",
                "task_type": "plugin_health_check",
                "service_type": "custom",
                "service_exec": "health_check",
            },
        ),
        plugin_tier="supported",
        plugin_log_key="custom",
    )
    with pytest.raises(ServicePluginManifestError, match="not approved"):
        validate_service_plugin(plugin, directory_name="custom")


def test_dummy_templates_are_strict_service_plugin_templates() -> None:
    assert {template["service_exec"] for template in DUMMY_INGREDIENT_TEMPLATES} == {
        "health_check",
        "positive_result",
        "negative_result",
        "slow_result",
        "sleep_10",
        "communication",
    }
    comms_template = next(
        template
        for template in DUMMY_INGREDIENT_TEMPLATES
        if template["ingredient_purpose"] == "comms"
    )
    assert comms_template["service_exec"] == "communication"
    assert comms_template["service_exec_parameters"] == {
        "operation": "open",
        "allowed_operations": ["open", "notify", "update", "close"],
        "operation_metadata": {
            "open": {"label": "Open", "description": "Create a communication thread."},
            "notify": {"label": "Notify", "description": "Add a notification."},
            "update": {"label": "Update", "description": "Update an existing thread."},
            "close": {"label": "Close", "description": "Close an existing thread."},
        },
    }
    assert {recipe["name"] for recipe in DUMMY_RECIPE_TEMPLATES} >= {
        "dummy-positive-result",
        "dummy-negative-result",
        "dummy-expected-negative-result",
        "dummy-parallel-slow-cancel-result",
        "dummy-prior-blocking-sleep10-result",
    }
    assert {task["task_key"] for task in DUMMY_SCHEDULED_TASKS} == {
        "plugin-health-check:dummy",
        "dummy-scheduled-positive-result",
    }
    health_task = next(
        task for task in DUMMY_SCHEDULED_TASKS if task["task_type"] == "plugin_health_check"
    )
    assert health_task["service_type"] == "dummy"
    service_task = next(
        task for task in DUMMY_SCHEDULED_TASKS if task["task_type"] == "service_execution"
    )
    assert service_task["service_type"] == "dummy"
    assert service_task["service_exec"] == "positive_result"
    for template in DUMMY_INGREDIENT_TEMPLATES:
        assert template["service_type"] == "dummy"
        assert template["service_exec"]
        assert template["default_expected_secs"] > 0
        assert template["default_timeout"] > 0
        validate_payload_schema(template["payload_schema"])
    sleep_template = next(
        template
        for template in DUMMY_INGREDIENT_TEMPLATES
        if template["service_exec"] == "sleep_10"
    )
    assert sleep_template["default_expected_secs"] == 10
    assert sleep_template["default_timeout"] == 60
    stress_recipe = next(
        recipe
        for recipe in DUMMY_RECIPE_TEMPLATES
        if recipe["name"] == "dummy-prior-blocking-sleep10-result"
    )
    sleep_steps = [
        step for step in stress_recipe["recipe_ingredients"] if step["service_exec"] == "sleep_10"
    ]
    assert len(sleep_steps) == 3
    assert len(stress_recipe["recipe_ingredients"]) == 3
    assert all(step["service_exec_expected_secs"] == 30 for step in sleep_steps)
    assert all(step["service_exec_timeout"] == 180 for step in sleep_steps)


def test_manifest_requires_plugin_health_scheduled_task() -> None:
    plugin = ServicePlugin(
        service_type="dummy",
        adapter_factory=lambda: DummyExecutionAdapter(),
        ingredient_templates=DUMMY_INGREDIENT_TEMPLATES,
        scheduled_tasks=(
            {
                "task_key": "dummy-scheduled-positive-result",
                "task_type": "service_execution",
                "service_type": "dummy",
                "service_exec": "positive_result",
            },
        ),
    )
    with pytest.raises(ServicePluginManifestError, match="plugin_health_check"):
        validate_service_plugin(plugin, directory_name="dummy")


def test_manifest_rejects_bootstrap_owned_scheduled_next_run_at() -> None:
    plugin = ServicePlugin(
        service_type="dummy",
        adapter_factory=lambda: DummyExecutionAdapter(),
        ingredient_templates=DUMMY_INGREDIENT_TEMPLATES,
        scheduled_tasks=(
            DUMMY_SCHEDULED_TASKS[0],
            {
                "task_key": "dummy-scheduled-positive-result",
                "task_type": "service_execution",
                "service_type": "dummy",
                "service_exec": "positive_result",
                "next_run_at": "2026-05-03T17:45:00Z",
            },
        ),
    )
    with pytest.raises(ServicePluginManifestError, match="next_run_at"):
        validate_service_plugin(plugin, directory_name="dummy")


def test_manifest_rejects_ingredient_lifecycle_fields() -> None:
    template = dict(DUMMY_INGREDIENT_TEMPLATES[0])
    template["is_active"] = True
    plugin = ServicePlugin(
        service_type="dummy",
        adapter_factory=lambda: DummyExecutionAdapter(),
        ingredient_templates=(template,),
        scheduled_tasks=DUMMY_SCHEDULED_TASKS,
    )

    with pytest.raises(ServicePluginManifestError, match=r"ingredient_templates\[0\]\.is_active"):
        validate_service_plugin(plugin, directory_name="dummy")


def test_manifest_rejects_recipe_step_database_identity_fields() -> None:
    recipe = {
        "name": "dummy-invalid-step-id",
        "recipe_ingredients": [
            {
                "service_type": "dummy",
                "service_exec": "positive_result",
                "destination_target": "dummy",
                "task_key_template": "dummy-positive-result",
                "ingredient_id": 11,
            }
        ],
    }
    plugin = ServicePlugin(
        service_type="dummy",
        adapter_factory=lambda: DummyExecutionAdapter(),
        ingredient_templates=DUMMY_INGREDIENT_TEMPLATES,
        recipe_templates=(recipe,),
        scheduled_tasks=DUMMY_SCHEDULED_TASKS,
    )

    with pytest.raises(
        ServicePluginManifestError,
        match=r"recipe_templates\[0\]\.recipe_ingredients\[0\]\.ingredient_id",
    ):
        validate_service_plugin(plugin, directory_name="dummy")


@pytest.mark.asyncio
async def test_dummy_dispatch_returns_running_receipt() -> None:
    result = await DummyExecutionAdapter().dispatch(_ctx("positive_result"))
    assert result.status == "running"
    assert result.service_exec_id is not None
    assert result.service_exec_id.startswith("dummy:positive_result:")


@pytest.mark.asyncio
async def test_dummy_communication_dispatch_uses_operation_from_parameters() -> None:
    result = await DummyExecutionAdapter().dispatch(_communication_ctx("close"))

    assert result.status == "running"
    assert result.service_exec_id is not None
    assert result.service_exec_id.startswith("dummy:communication:close:")
    assert result.result is not None
    assert result.result["operation"] == "close"


@pytest.mark.asyncio
async def test_dummy_sleep_10_dispatch_returns_accepted_receipt_without_pausing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("api.plugins.dummy.adapter.time.time", lambda: 1000)

    result = await DummyExecutionAdapter().dispatch(_ctx("sleep_10"))

    assert result.status == "running"
    assert result.service_exec_id is not None
    assert result.service_exec_id.startswith("dummy:sleep_10:")
    assert result.result is not None
    assert result.result["accepted"] is True
    assert result.result["status_code"] == 202
    assert result.result["work_execution_id"] == result.service_exec_id
    assert result.result["ready_at"] == 1010


@pytest.mark.asyncio
async def test_dummy_sleep_10_poll_runs_until_receipt_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("api.plugins.dummy.adapter.time.time", lambda: 1000)
    dispatch_result = await DummyExecutionAdapter().dispatch(_ctx("sleep_10"))
    receipt = dispatch_result.service_exec_id

    monkeypatch.setattr("api.plugins.dummy.adapter.time.time", lambda: 1005)
    running = await DummyExecutionAdapter().poll(_ctx("sleep_10"), receipt)

    monkeypatch.setattr("api.plugins.dummy.adapter.time.time", lambda: 1011)
    succeeded = await DummyExecutionAdapter().poll(_ctx("sleep_10"), receipt)

    assert running.status == "running"
    assert succeeded.status == "succeeded"


def test_dummy_communication_rejects_operation_outside_template_allow_list() -> None:
    error = DummyExecutionAdapter().validate(_communication_ctx("delete"))

    assert error == "dummy communication operation must be one of: close, notify, open, update"


def test_dummy_communication_override_keeps_template_allow_list() -> None:
    template = next(
        template
        for template in DUMMY_INGREDIENT_TEMPLATES
        if template["service_exec"] == "communication"
    )
    recipe_ingredient = SimpleNamespace(
        ingredient=SimpleNamespace(service_exec_parameters=template["service_exec_parameters"]),
        service_exec_parameters_override={"operation": "close"},
    )

    assert build_step_parameters(recipe_ingredient) == {
        "operation": "close",
        "allowed_operations": ["open", "notify", "update", "close"],
        "operation_metadata": {
            "open": {"label": "Open", "description": "Create a communication thread."},
            "notify": {"label": "Notify", "description": "Add a notification."},
            "update": {"label": "Update", "description": "Update an existing thread."},
            "close": {"label": "Close", "description": "Close an existing thread."},
        },
    }


def test_service_operation_validator_rejects_disallowed_operation() -> None:
    with pytest.raises(ServicePluginContractError, match="operation must be one of"):
        validate_service_operation(
            {"operation": "delete", "allowed_operations": ["open", "notify"]}
        )


def test_dish_planner_rejects_disallowed_recipe_operation_before_dispatch() -> None:
    template = next(
        template
        for template in DUMMY_INGREDIENT_TEMPLATES
        if template["service_exec"] == "communication"
    )
    recipe_ingredient = SimpleNamespace(
        ingredient=SimpleNamespace(service_exec_parameters=template["service_exec_parameters"]),
        service_exec_parameters_override={"operation": "delete"},
    )

    with pytest.raises(ServicePluginContractError, match="operation must be one of"):
        validate_step_operation(recipe_ingredient)  # type: ignore[arg-type]


def test_dummy_communication_rejects_disallowed_recipe_ingredient_override() -> None:
    template = next(
        template
        for template in DUMMY_INGREDIENT_TEMPLATES
        if template["service_exec"] == "communication"
    )
    recipe_ingredient = SimpleNamespace(
        ingredient=SimpleNamespace(service_exec_parameters=template["service_exec_parameters"]),
        service_exec_parameters_override={"operation": "delete"},
    )
    resolved_parameters = build_step_parameters(recipe_ingredient)

    ctx = _communication_ctx("open").model_copy(
        update={"service_exec_parameters": resolved_parameters}
    )
    error = DummyExecutionAdapter().validate(ctx)

    assert resolved_parameters == {
        "operation": "delete",
        "allowed_operations": ["open", "notify", "update", "close"],
        "operation_metadata": {
            "open": {"label": "Open", "description": "Create a communication thread."},
            "notify": {"label": "Notify", "description": "Add a notification."},
            "update": {"label": "Update", "description": "Update an existing thread."},
            "close": {"label": "Close", "description": "Close an existing thread."},
        },
    }
    assert error == "dummy communication operation must be one of: close, notify, open, update"


@pytest.mark.asyncio
async def test_dummy_poll_returns_positive_terminal_outcome() -> None:
    result = await DummyExecutionAdapter().poll(
        _ctx("positive_result"),
        "dummy:positive_result:receipt",
    )
    assert result.status == "succeeded"
    assert result.result == {
        "success": True,
        "status": "succeeded",
        "message": "dummy succeeded poll result",
        "service_exec": "positive_result",
        "service_exec_id": "dummy:positive_result:receipt",
    }


@pytest.mark.asyncio
async def test_dummy_poll_returns_negative_terminal_outcome() -> None:
    result = await DummyExecutionAdapter().poll(
        _ctx("negative_result"),
        "dummy:negative_result:receipt",
    )
    assert result.status == "failed"
    assert result.result is not None
    assert result.result["success"] is False


@pytest.mark.asyncio
async def test_dummy_poll_returns_sleep_10_terminal_outcome() -> None:
    result = await DummyExecutionAdapter().poll(
        _ctx("sleep_10"),
        "dummy:sleep_10:receipt",
    )
    assert result.status == "succeeded"
    assert result.result is not None
    assert result.result["success"] is True
    assert result.result["service_exec"] == "sleep_10"


@pytest.mark.asyncio
async def test_dummy_cancel_returns_canonical_canceled() -> None:
    adapter = DummyExecutionAdapter()
    result = await adapter.dispatch(_ctx("slow_result"))
    cancel_result = await adapter.cancel(
        _ctx("slow_result"),
        result.service_exec_id,
    )
    assert cancel_result.status == "canceled"
    assert cancel_result.result is not None
    assert cancel_result.result["status"] == "canceled"
