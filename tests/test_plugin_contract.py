"""Unit tests for the service plugin payload and outcome contract."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from api.plugins.contract import (
    ServicePluginContractError,
    expected_outcome_matches,
    health_check_operation_parameters,
    validate_payload_schema,
    validate_service_operation,
    validate_service_payload,
    validate_service_payload_for_operation,
)
from api.plugins.base import ExecutionAdapter
from api.plugins.catalog import (
    _discover_plugin_modules,
    _load_plugin,
    build_enabled_plugin_registry,
)
from api.plugins.alertmanager.templates import ALERTMANAGER_INGREDIENT_TEMPLATES
from api.plugins.bakery.templates import ingredient_templates as bakery_ingredient_templates
from api.plugins.dummy.templates import DUMMY_INGREDIENT_TEMPLATES
from api.plugins.genestack_monitoring.templates import GENESTACK_MONITORING_INGREDIENT_TEMPLATES
from api.plugins.git.templates import GIT_INGREDIENT_TEMPLATES
from api.plugins.github.templates import GITHUB_INGREDIENT_TEMPLATES
from api.plugins.k8s.templates import K8S_INGREDIENT_TEMPLATES
from api.plugins.prometheus.templates import PROMETHEUS_INGREDIENT_TEMPLATES
from api.plugins.release.templates import RELEASE_INGREDIENT_TEMPLATES
from api.plugins.stackstorm.templates import STACKSTORM_INGREDIENT_TEMPLATES
from api.plugins.manifest import (
    ServicePlugin as ServicePluginManifest,
    ServicePluginManifestError,
    validate_service_plugin,
)
from api.plugins.internal_services import INTERNAL_SERVICE_TYPES
from api.plugins.registry import ExecutionAdapterRegistry
from api.plugins.state import (
    PLUGIN_BLOCKED_RUN_STATES,
    PLUGIN_CALLABLE_RUN_STATES,
    PLUGIN_RUN_STATE_DEGRADED,
    PLUGIN_RUN_STATE_DISABLED,
    PLUGIN_RUN_STATE_FAILED,
    PLUGIN_RUN_STATE_HEALTHY,
    PLUGIN_RUN_STATE_INITIALIZING,
    PLUGIN_RUN_STATE_UNKNOWN,
    PLUGIN_RUN_STATES,
    normalize_plugin_run_state,
    plugin_run_state_blocks_dispatch,
)
from api.api.expediter import _plugin_health_block
from api.models.models import ServicePlugin
from api.types import PluginHealthStatus
from api.plugins.types import (
    ExecutionContext,
    ExecutionResult,
    PluginBootstrapResult,
    PluginHealthResult,
)
from api.services.plugin_orchestrator import ExecutionOrchestrator

STRICT_SCHEMA = {
    "type": "object",
    "properties": {
        "message": {"type": "string", "minLength": 1},
        "success": {"type": "boolean"},
    },
    "required": ["message"],
    "additionalProperties": False,
}

BUILTIN_PLUGIN_TEMPLATE_SETS = (
    ("alertmanager", ALERTMANAGER_INGREDIENT_TEMPLATES),
    ("bakery", bakery_ingredient_templates()),
    ("dummy", DUMMY_INGREDIENT_TEMPLATES),
    ("genestack_monitoring", GENESTACK_MONITORING_INGREDIENT_TEMPLATES),
    ("git", GIT_INGREDIENT_TEMPLATES),
    ("github", GITHUB_INGREDIENT_TEMPLATES),
    ("k8s", K8S_INGREDIENT_TEMPLATES),
    ("prometheus", PROMETHEUS_INGREDIENT_TEMPLATES),
    ("release", RELEASE_INGREDIENT_TEMPLATES),
    ("stackstorm", STACKSTORM_INGREDIENT_TEMPLATES),
)


def _called_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def test_plugin_run_state_constants_match_contract_literal() -> None:
    assert PLUGIN_RUN_STATES == (
        PLUGIN_RUN_STATE_UNKNOWN,
        PLUGIN_RUN_STATE_INITIALIZING,
        PLUGIN_RUN_STATE_HEALTHY,
        PLUGIN_RUN_STATE_DEGRADED,
        PLUGIN_RUN_STATE_FAILED,
        PLUGIN_RUN_STATE_DISABLED,
    )
    assert set(PLUGIN_RUN_STATES) == set(get_args(PluginHealthStatus))
    assert PLUGIN_CALLABLE_RUN_STATES == {
        PLUGIN_RUN_STATE_HEALTHY,
        PLUGIN_RUN_STATE_DEGRADED,
    }
    assert PLUGIN_BLOCKED_RUN_STATES == {
        PLUGIN_RUN_STATE_UNKNOWN,
        PLUGIN_RUN_STATE_INITIALIZING,
        PLUGIN_RUN_STATE_FAILED,
        PLUGIN_RUN_STATE_DISABLED,
    }
    assert plugin_run_state_blocks_dispatch(PLUGIN_RUN_STATE_FAILED) is True
    assert plugin_run_state_blocks_dispatch(PLUGIN_RUN_STATE_DEGRADED) is False


def test_plugin_run_state_rejects_legacy_unhealthy() -> None:
    with pytest.raises(ValueError, match="Invalid service plugin run state"):
        normalize_plugin_run_state("unhealthy")

    with pytest.raises(ValidationError):
        PluginHealthResult.model_validate(
            {
                "service_type": "dummy",
                "status": "unhealthy",
            }
        )


def test_execution_adapter_poll_contract_is_read_only() -> None:
    doc = ExecutionAdapter.poll.__doc__ or ""

    assert "Read-only observation" in doc
    assert "must not start or retry" in doc
    assert "PoundCake-owned runtime state" in doc
    assert "provider write operations" in doc


def test_adapter_poll_methods_do_not_call_known_workload_operations() -> None:
    adapter_paths = sorted(Path("api/plugins").glob("*/adapter.py"))
    forbidden_call_names = {
        "_execute",
        "_execute_health_check",
        "_execute_inspect",
        "_execute_list_alerts",
        "_execute_list_groups",
        "_execute_sync_silences",
        "commit_and_pr",
        "commit_files",
        "create_or_update_rule",
        "create_pull_request",
        "delete_rule",
        "reload_config",
        "sync_genestack_monitoring_content",
        "sync_stackstorm_content",
    }
    violations: list[str] = []

    for path in adapter_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef) or node.name != "poll":
                continue
            for child in ast.walk(node):
                if not isinstance(child, ast.Call):
                    continue
                name = _called_name(child.func)
                if name in forbidden_call_names:
                    violations.append(f"{path}:{child.lineno}:{name}")

    assert violations == []


def test_validate_payload_schema_accepts_json_schema() -> None:
    validate_payload_schema(STRICT_SCHEMA)


def test_validate_payload_schema_rejects_invalid_schema() -> None:
    with pytest.raises(ServicePluginContractError, match="payload_schema"):
        validate_payload_schema({"type": "not-a-json-schema-type"})


def test_validate_service_payload_rejects_invalid_filled_form() -> None:
    with pytest.raises(ServicePluginContractError, match=r"\$.service_payload"):
        validate_service_payload({"message": "", "extra": True}, STRICT_SCHEMA)


def test_validate_service_payload_accepts_valid_filled_form() -> None:
    validate_service_payload({"message": "hello", "success": True}, STRICT_SCHEMA)


def test_validate_service_operation_accepts_minimal_health_metadata() -> None:
    validate_service_operation(health_check_operation_parameters())


def test_validate_service_operation_rejects_invalid_metadata_operation() -> None:
    with pytest.raises(ServicePluginContractError, match="must reference an allowed operation"):
        validate_service_operation(
            {
                "operation": "run",
                "allowed_operations": ["run"],
                "operation_metadata": {"delete": {"label": "Delete"}},
            }
        )


def test_validate_service_operation_accepts_operation_payload_schema() -> None:
    validate_service_operation(
        {
            "operation": "run",
            "allowed_operations": ["run"],
            "operation_metadata": {
                "run": {
                    "label": "Run",
                    "payload_schema": {
                        "type": "object",
                        "properties": {"target": {"type": "string", "minLength": 1}},
                        "required": ["target"],
                        "additionalProperties": False,
                    },
                }
            },
        }
    )


def test_validate_service_operation_rejects_invalid_operation_payload_schema() -> None:
    with pytest.raises(ServicePluginContractError, match="payload_schema invalid"):
        validate_service_operation(
            {
                "operation": "run",
                "allowed_operations": ["run"],
                "operation_metadata": {
                    "run": {
                        "payload_schema": {"type": "not-a-json-schema-type"},
                    }
                },
            }
        )


def test_validate_service_payload_for_operation_applies_selected_schema() -> None:
    base_schema = {
        "type": "object",
        "properties": {
            "namespace": {"type": "string", "minLength": 1},
            "pod_name": {"type": "string", "minLength": 1},
            "label_selector": {"type": "string", "minLength": 1},
        },
        "additionalProperties": False,
    }
    parameters = {
        "operation": "delete",
        "allowed_operations": ["logs", "delete"],
        "operation_metadata": {
            "delete": {
                "payload_schema": {
                    "type": "object",
                    "properties": {
                        "namespace": {"type": "string", "minLength": 1},
                        "pod_name": {"type": "string", "minLength": 1},
                    },
                    "required": ["namespace", "pod_name"],
                    "additionalProperties": False,
                }
            }
        },
    }

    with pytest.raises(ServicePluginContractError, match="pod_name"):
        validate_service_payload_for_operation(
            {"namespace": "poundcake", "label_selector": "app=api"},
            base_schema,
            parameters,
        )

    validate_service_payload_for_operation(
        {"namespace": "poundcake", "pod_name": "api-123"},
        base_schema,
        parameters,
    )


def test_builtin_plugin_operations_have_authoritative_fail_closed_payload_schemas() -> None:
    violations: list[str] = []
    for service_type, templates in BUILTIN_PLUGIN_TEMPLATE_SETS:
        for template in templates:
            label = (
                f"{service_type}:{template.get('service_exec')}:"
                f"{template.get('task_key_template')}"
            )
            schema = template.get("payload_schema")
            if not isinstance(schema, dict):
                violations.append(f"{label}: missing payload_schema")
            else:
                if schema.get("type") != "object":
                    violations.append(f"{label}: payload_schema must be an object schema")
                if schema.get("additionalProperties") is not False:
                    violations.append(f"{label}: payload_schema must fail closed")
                try:
                    validate_payload_schema(schema)
                except ServicePluginContractError as exc:
                    violations.append(f"{label}: payload_schema invalid: {exc}")

            parameters = template.get("service_exec_parameters") or {}
            if not isinstance(parameters, dict):
                violations.append(f"{label}: service_exec_parameters must be an object")
                continue
            operations = parameters.get("allowed_operations") or []
            if not operations:
                continue
            if not isinstance(operations, list):
                violations.append(f"{label}: allowed_operations must be a list")
                continue
            metadata = parameters.get("operation_metadata") or {}
            if not isinstance(metadata, dict):
                violations.append(f"{label}: operation_metadata must be an object")
                continue
            for operation in operations:
                operation_label = f"{label}:{operation}"
                op_meta = metadata.get(operation)
                if not isinstance(op_meta, dict):
                    violations.append(f"{operation_label}: missing operation_metadata")
                    continue
                op_schema = op_meta.get("payload_schema")
                if not isinstance(op_schema, dict):
                    violations.append(f"{operation_label}: missing operation payload_schema")
                    continue
                if op_schema.get("type") != "object":
                    violations.append(
                        f"{operation_label}: operation payload_schema must be an object schema"
                    )
                if op_schema.get("additionalProperties") is not False:
                    violations.append(
                        f"{operation_label}: operation payload_schema must fail closed"
                    )
                try:
                    validate_payload_schema(op_schema)
                except ServicePluginContractError as exc:
                    violations.append(f"{operation_label}: operation payload_schema invalid: {exc}")

    assert violations == []


def test_execution_context_requires_json_object_payloads() -> None:
    with pytest.raises(ValidationError):
        ExecutionContext.model_validate(
            {
                "service_type": "dummy",
                "service_exec": "positive_result",
                "req_id": "unit-test",
                "service_payload": ["not", "an", "object"],
            }
        )


def test_execution_result_rejects_noncanonical_status() -> None:
    with pytest.raises(ValidationError):
        ExecutionResult.model_validate(
            {
                "service_type": "dummy",
                "status": "almost_done",
            }
        )


def test_plugin_bootstrap_result_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError):
        PluginBootstrapResult.model_validate(
            {
                "service_type": "bakery",
                "status": "half-ready",
            }
        )


class _PluginRowResult:
    def __init__(self, row: ServicePlugin | None) -> None:
        self.row = row

    def scalar_one_or_none(self) -> ServicePlugin | None:
        return self.row


class _PluginHealthDb:
    def __init__(self, row: ServicePlugin | None) -> None:
        self.row = row

    async def execute(self, _statement: object) -> _PluginRowResult:
        return _PluginRowResult(self.row)


@pytest.mark.asyncio
async def test_expediter_blocks_non_health_while_plugin_initializing() -> None:
    row = ServicePlugin(
        service_type="bakery",
        plugin_short_id="bky7x2p9",
        enabled=True,
        health_status="initializing",
    )
    db = _PluginHealthDb(row)

    blocked = await _plugin_health_block(
        db=db, service_type="bakery", service_exec="open", action="dispatch"
    )
    allowed = await _plugin_health_block(
        db=db, service_type="bakery", service_exec="health_check", action="dispatch"
    )

    assert blocked == "service plugin bakery is initializing; dispatch blocked"
    assert allowed is None


class _InvalidResultAdapter(ExecutionAdapter):
    service_type = "bad"

    def validate(self, ctx: ExecutionContext) -> str | None:
        return None

    async def dispatch(self, ctx: ExecutionContext) -> ExecutionResult:
        return {"service_type": "bad", "status": "almost_done"}  # type: ignore[return-value]

    async def poll(self, ctx: ExecutionContext, service_exec_id: str) -> ExecutionResult:
        return {"service_type": "other", "status": "running"}  # type: ignore[return-value]

    def health_check(self) -> PluginHealthResult:
        return PluginHealthResult(service_type="bad", status="healthy")


class _ConfigurableAdapter(ExecutionAdapter):
    service_type = "configurable"

    def __init__(self, url: str = "http://default.test") -> None:
        self.url = url

    def validate(self, ctx: ExecutionContext) -> str | None:
        return None

    def default_operator_config(self) -> dict[str, object]:
        return {"url": self.url}

    def with_operator_config(self, config: dict[str, object] | None) -> "_ConfigurableAdapter":
        return _ConfigurableAdapter(url=str((config or {}).get("url") or self.url))

    async def dispatch(self, ctx: ExecutionContext) -> ExecutionResult:
        return ExecutionResult(
            service_type=self.service_type,
            status="succeeded",
            result={"url": self.url},
            raw={"url": self.url},
        )

    async def poll(self, ctx: ExecutionContext, service_exec_id: str) -> ExecutionResult:
        return ExecutionResult(
            service_type=self.service_type,
            status="succeeded",
            service_exec_id=service_exec_id,
            result={"url": self.url},
            raw={"url": self.url},
        )

    def health_check(self) -> PluginHealthResult:
        return PluginHealthResult(
            service_type=self.service_type,
            status="healthy",
            details={"url": self.url},
        )


def test_service_execution_plugins_may_require_external_helper_capabilities() -> None:
    plugin = ServicePluginManifest(
        service_type="consumer",
        adapter_factory=lambda: _ConfigurableAdapter(),
        scheduled_tasks=(
            {
                "task_key": "plugin-health-check:consumer",
                "task_type": "plugin_health_check",
                "service_type": "consumer",
                "service_exec": "health_check",
            },
        ),
        required_helper_capabilities={"github": ("repo.read", "repo.list")},
        allow_directory_mismatch=True,
    )

    validated = validate_service_plugin(plugin, directory_name="consumer")

    assert validated.required_helper_capabilities == {"github": ("repo.read", "repo.list")}
    assert validated.bootstrap_factory is None


def test_bootstrap_hooks_must_not_require_external_helper_capabilities() -> None:
    async def bootstrap(_db: object, _helpers: dict[str, object]) -> dict[str, object]:
        return {"processed": 1}

    plugin = ServicePluginManifest(
        service_type="consumer",
        adapter_factory=lambda: _ConfigurableAdapter(),
        scheduled_tasks=(
            {
                "task_key": "plugin-health-check:consumer",
                "task_type": "plugin_health_check",
                "service_type": "consumer",
                "service_exec": "health_check",
            },
        ),
        required_helper_capabilities={"github": ("repo.read",)},
        bootstrap_factory=bootstrap,
        allow_directory_mismatch=True,
    )

    with pytest.raises(ServicePluginManifestError, match="service_execution ingredients"):
        validate_service_plugin(plugin, directory_name="consumer")


def test_builtin_bootstrap_hooks_do_not_own_ingredient_registration() -> None:
    plugins = [
        _load_plugin(directory_name=name, module_name=module_name)
        for name, module_name in sorted(_discover_plugin_modules().items())
    ]

    for plugin in plugins:
        if plugin.bootstrap_factory is None:
            continue
        source = inspect.getsource(plugin.bootstrap_factory)
        assert "Ingredient" not in source
        assert "service-registry/ingredients" not in source
        assert "register_ingredient_templates" not in source


def test_startup_bootstrap_does_not_own_manifest_sync_writes() -> None:
    from api.services import plugin_bootstrap

    source = inspect.getsource(plugin_bootstrap.bootstrap_enabled_plugins)

    assert "_register_plugin_ingredients" not in source
    assert "_register_plugin_recipes" not in source
    assert "_register_scheduled_tasks" not in source
    assert "sync_global_policy_routes" not in source
    assert "register_ingredient_templates" not in source


@pytest.mark.parametrize("service_type", sorted(INTERNAL_SERVICE_TYPES))
def test_external_plugin_manifest_cannot_use_reserved_internal_service_type(
    service_type: str,
) -> None:
    plugin = ServicePluginManifest(
        service_type=service_type,
        adapter_factory=lambda: _ConfigurableAdapter(),
        allow_directory_mismatch=True,
    )

    with pytest.raises(ServicePluginManifestError, match="reserved for an internal"):
        validate_service_plugin(plugin, directory_name=service_type)


@pytest.mark.asyncio
async def test_orchestrator_validates_adapter_dispatch_result() -> None:
    registry = ExecutionAdapterRegistry()
    registry.register(_InvalidResultAdapter())
    result = await ExecutionOrchestrator(registry).dispatch(
        ExecutionContext(service_type="bad", service_exec="run", req_id="unit-test")
    )
    assert result.status == "errored"
    assert result.service_exec_error == "Adapter returned invalid ExecutionResult for dispatch"


@pytest.mark.asyncio
async def test_orchestrator_returns_errored_result_for_malformed_execution_context() -> None:
    result = await ExecutionOrchestrator(ExecutionAdapterRegistry()).dispatch(
        {
            "service_type": "bad",
            "service_exec": "run",
            "req_id": "unit-test",
            "service_payload": ["not", "an", "object"],
        }
    )

    assert result.service_type == "bad"
    assert result.status == "errored"
    assert result.retryable is False
    assert "Malformed ExecutionContext" in str(result.service_exec_error)


@pytest.mark.asyncio
async def test_orchestrator_rejects_mismatched_adapter_service_type() -> None:
    registry = ExecutionAdapterRegistry()
    registry.register(_InvalidResultAdapter())
    result = await ExecutionOrchestrator(registry).poll(
        ExecutionContext(service_type="bad", service_exec="poll", req_id="unit-test"),
        "bad:run:receipt",
    )
    assert result.status == "errored"
    assert "mismatched service_type" in str(result.service_exec_error)


@pytest.mark.asyncio
async def test_orchestrator_applies_operator_config_from_execution_context() -> None:
    registry = ExecutionAdapterRegistry()
    registry.register(_ConfigurableAdapter())

    result = await ExecutionOrchestrator(registry).dispatch(
        ExecutionContext(
            service_type="configurable",
            service_exec="run",
            req_id="unit-test",
            context={"operator_config": {"url": "http://configured.test"}},
        )
    )

    assert result.status == "succeeded"
    assert result.result == {"url": "http://configured.test"}


@pytest.mark.asyncio
async def test_build_enabled_plugin_registry_applies_saved_plugin_config(monkeypatch) -> None:
    monkeypatch.setattr(
        "api.plugins.catalog.get_enabled_plugins",
        lambda: [
            ServicePluginManifest(
                service_type="configurable",
                adapter_factory=lambda: _ConfigurableAdapter(),
            )
        ],
    )

    registry = build_enabled_plugin_registry(
        {"configurable": {"url": "http://registry-configured.test"}}
    )
    result = await ExecutionOrchestrator(registry).dispatch(
        ExecutionContext(service_type="configurable", service_exec="run", req_id="unit-test")
    )

    assert result.result == {"url": "http://registry-configured.test"}


@pytest.mark.parametrize(
    ("expected", "actual", "status", "matches"),
    [
        (True, {"success": True}, "succeeded", True),
        (False, {"success": False}, "failed", True),
        ("failed", {"status": "failed"}, None, True),
        ({"status": "succeeded"}, {"health": "ok"}, "succeeded", True),
        ({"result": {"code": 404}}, {"result": {"code": 404, "body": "missing"}}, None, True),
        ({"success": True}, {"success": False}, "failed", False),
        (None, {"success": False}, "failed", False),
        (None, {"success": True}, "succeeded", True),
    ],
)
def test_expected_outcome_matching(
    expected: object,
    actual: object,
    status: str | None,
    matches: bool,
) -> None:
    assert expected_outcome_matches(expected=expected, actual=actual, status=status) is matches
