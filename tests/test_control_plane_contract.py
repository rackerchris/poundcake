"""Guardrails for the service-plugin control-plane contract."""

from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path
from typing import Any, get_args, get_origin

from api.api.dishes import (
    _serialize_admin_dish_ingredient_history,
    _serialize_dish_ingredient_status,
)
from api.api.scheduled_tasks import OPERATOR_SCHEDULED_TASK_UPDATE_FIELDS
from api.main import app
from api.models.models import Dish, DishIngredient, Ingredient, Order, ServicePlugin
from api.schemas.schemas import (
    CommunicationActivityStatusRecord,
    DishIngredientResponse,
    DishIngredientStatusResponse,
    DishStatusResponse,
    HealthResponse,
    IngredientStatusResponse,
    IncidentTimelineResponse,
    ObservabilityActivityStatusRecord,
    ObservabilityOverviewResponse,
    OrderStatusResponse,
    RecipeIngredientStatusResponse,
    RecipeStatusResponse,
    ScheduledTaskStatusResponse,
    SuppressionStatsResponse,
    SuppressionStatusResponse,
)
from api.services.auth_service import _service_allowed_path, request_role_requirement
from api.services.route_surface_contract import (
    RouteSurface,
    is_guarded_route,
    route_surface_entries,
    route_surface_keys,
)

PUBLIC_ROUTES = {
    ("GET", "/"),
    ("GET", "/openapi.json"),
    ("GET", "/livez"),
    ("GET", "/readyz"),
    ("GET", "/api/v1/auth/providers"),
    ("POST", "/api/v1/auth/login"),
    ("GET", "/api/v1/auth/oidc/login"),
    ("GET", "/api/v1/auth/oidc/callback"),
    ("POST", "/api/v1/auth/device/start"),
    ("POST", "/api/v1/auth/device/poll"),
    ("POST", "/api/v1/webhook"),
}

STATUS_RESPONSE_SCHEMAS = (
    HealthResponse,
    OrderStatusResponse,
    IncidentTimelineResponse,
    RecipeStatusResponse,
    RecipeIngredientStatusResponse,
    DishStatusResponse,
    DishIngredientStatusResponse,
    IngredientStatusResponse,
    ScheduledTaskStatusResponse,
    SuppressionStatusResponse,
    SuppressionStatsResponse,
    ObservabilityOverviewResponse,
    ObservabilityActivityStatusRecord,
    CommunicationActivityStatusRecord,
)

SENSITIVE_STATUS_FIELDS = {
    "fingerprint",
    "fingerprint_when_active",
    "labels",
    "annotations",
    "raw_data",
    "service_payload",
    "service_exec_parameters",
    "service_exec_parameters_override",
    "service_exec_expected_outcome",
    "service_exec_actual_outcome",
    "service_exec_error",
    "service_exec_id",
    "service_exec_claimed_at",
    "service_exec_claimed_by",
    "task_payload",
    "task_parameters",
    "expected_outcome",
    "actual_outcome",
    "dish_actual_outcome",
    "claim_metadata",
    "metadata",
    "provider_reference_id",
    "ticket_id",
    "operation_id",
    "last_error",
    "error_message",
}

SENSITIVE_FIELD_NAME_FRAGMENTS = ("credential", "secret", "token", "password", "key")
NON_SECRET_STATUS_FIELD_ALLOWLIST = {"correlation_key", "task_key", "task_key_template"}

OPERATOR_PLUGIN_RUNTIME_FIELDS = {
    "enabled",
    "run_interval_seconds",
    "query_limit",
    "health_check_interval_seconds",
    "status_message",
}

RBAC_ROLE_RESPONSIBILITY_CONTRACT = {
    "reader": {
        ("GET", "/api/v1/health/status"),
        ("GET", "/api/v1/orders/status"),
        ("GET", "/api/v1/dishes/status"),
        ("GET", "/api/v1/recipes/status"),
        ("GET", "/api/v1/service-registry/ingredients/status"),
        ("GET", "/api/v1/scheduled-tasks/status"),
        ("GET", "/api/v1/suppressions/status"),
        ("GET", "/api/v1/observability/activity/status"),
        ("GET", "/api/v1/communications/activity/status"),
    },
    "operator": {
        ("GET", "/api/v1/recipes/"),
        ("POST", "/api/v1/recipes/"),
        ("PUT", "/api/v1/recipes/1"),
        ("PATCH", "/api/v1/recipes/1"),
        ("DELETE", "/api/v1/recipes/1"),
        ("GET", "/api/v1/service-registry/ingredients"),
        ("PATCH", "/api/v1/plugins/prep-chef"),
        ("GET", "/api/v1/plugins/stackstorm/configuration"),
        ("PUT", "/api/v1/plugins/stackstorm/configuration"),
        ("POST", "/api/v1/plugins/stackstorm/test-connection"),
        ("POST", "/api/v1/plugins/prometheus/reload"),
        ("PATCH", "/api/v1/scheduled-tasks/1"),
        ("POST", "/api/v1/scheduled-tasks/1/run-now"),
        ("POST", "/api/v1/suppressions"),
    },
    "admin": {
        ("PUT", "/api/v1/plugins/stackstorm/credentials"),
        ("GET", "/api/v1/orders/1/execution-history"),
        ("GET", "/api/v1/dishes/1/ingredients"),
        ("GET", "/api/v1/dishes/1/ingredient-history"),
        ("GET", "/api/v1/scheduled-tasks"),
        ("POST", "/api/v1/scheduled-tasks"),
        ("GET", "/api/v1/auth/bindings"),
    },
    "service": {
        ("GET", "/api/v1/orders"),
        ("GET", "/api/v1/orders/1"),
        ("GET", "/api/v1/dishes"),
        ("GET", "/api/v1/dish-ingredients/in-flight"),
        ("POST", "/api/v1/internal/service-registry/ingredients/bulk"),
        ("POST", "/api/v1/cook/orders/1"),
        ("POST", "/api/v1/cook/dishes/1/advance"),
        ("POST", "/api/v1/expediter/cancel/stackstorm/abc123"),
    },
}


def _route_keys() -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        for method in methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            keys.add((method, path))
    return keys


def _route_response_model_by_key() -> dict[tuple[str, str], Any]:
    models: dict[tuple[str, str], Any] = {}
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        response_model = getattr(route, "response_model", None)
        for method in methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            models[(method, path)] = response_model
    return models


def _model_contains_allowed_status_schema(model: Any) -> bool:
    allowed = set(STATUS_RESPONSE_SCHEMAS)
    if model in allowed:
        return True
    origin = get_origin(model)
    if origin in {list, tuple, set}:
        return any(arg in allowed for arg in get_args(model))
    return False


def test_guarded_routes_are_explicitly_classified() -> None:
    routes = _route_keys()
    guarded_routes = {route for route in routes if is_guarded_route(route)}
    assert guarded_routes <= route_surface_keys()


def test_provider_execution_dispatch_stays_inside_expediter_boundary() -> None:
    allowed_dispatch_files = {
        Path("api/api/expediter.py"),
        Path("api/services/plugin_orchestrator.py"),
    }
    allowed_orchestrator_instantiation_files = {
        Path("api/services/plugin_orchestrator.py"),
    }
    violations: list[str] = []
    for path in sorted(Path("api").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "dispatch"
                and path not in allowed_dispatch_files
            ):
                violations.append(f"{path}:{node.lineno} calls dispatch()")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "ExecutionOrchestrator"
                and path not in allowed_orchestrator_instantiation_files
            ):
                violations.append(f"{path}:{node.lineno} instantiates ExecutionOrchestrator")

    assert violations == []


def test_route_surface_inventory_references_registered_routes() -> None:
    assert route_surface_keys() <= _route_keys()


def test_route_surface_inventory_has_no_duplicate_route_keys() -> None:
    entries = route_surface_entries()
    assert len(entries) == len({entry.key for entry in entries})


def test_route_surface_inventory_matches_role_resolver() -> None:
    for entry in route_surface_entries():
        assert request_role_requirement(entry.path, entry.method) == entry.expected_role


def test_reporting_status_routes_use_reporting_response_models() -> None:
    response_models = _route_response_model_by_key()
    for route in route_surface_keys(RouteSurface.REPORTING_STATUS):
        assert _model_contains_allowed_status_schema(response_models[route])


def test_rbac_role_responsibility_contract_is_enforced() -> None:
    for expected_role, routes in RBAC_ROLE_RESPONSIBILITY_CONTRACT.items():
        for method, path in routes:
            assert request_role_requirement(path, method) == expected_role


def test_reporting_status_routes_are_reader_owned() -> None:
    concrete_paths = {
        "/api/v1/health/status",
        "/api/v1/orders/status",
        "/api/v1/orders/1/status",
        "/api/v1/orders/1/timeline",
        "/api/v1/recipes/status",
        "/api/v1/recipes/1/status",
        "/api/v1/recipes/1/ingredient-status",
        "/api/v1/dishes/status",
        "/api/v1/dishes/1/ingredient-status",
        "/api/v1/service-registry/ingredients/status",
        "/api/v1/scheduled-tasks/status",
        "/api/v1/observability/overview",
        "/api/v1/observability/activity/status",
        "/api/v1/communications/activity/status",
    }
    for path in concrete_paths:
        assert request_role_requirement(path, "GET") == "reader"


def test_rich_runtime_detail_reads_are_service_only() -> None:
    service_paths = {
        "/api/v1/orders",
        "/api/v1/orders/1",
        "/api/v1/dishes",
    }
    for path in service_paths:
        assert request_role_requirement(path, "GET") == "service"


def test_admin_can_read_execution_history_without_mutation_authority() -> None:
    admin_paths = {
        "/api/v1/orders/1/execution-history",
        "/api/v1/dishes/1/ingredients",
        "/api/v1/dishes/1/ingredient-history",
    }
    for path in admin_paths:
        assert request_role_requirement(path, "GET") == "admin"
    assert _service_allowed_path("timer", "/api/v1/dishes/1/ingredient-status", "GET")
    assert request_role_requirement("/api/v1/dish-ingredients/1/reconcile", "POST") == "service"


def test_recipe_and_ingredient_definition_reads_are_operator_owned() -> None:
    operator_paths = {
        "/api/v1/recipes/",
        "/api/v1/recipes/1",
        "/api/v1/recipes/by-name/host-down-events",
        "/api/v1/service-registry/ingredients",
        "/api/v1/service-registry/ingredients/1",
    }
    for path in operator_paths:
        assert request_role_requirement(path, "GET") == "operator"


def test_plugin_registry_reads_are_reader_owned_but_live_actions_are_privileged() -> None:
    reader_registry_paths = {
        "/api/v1/plugins",
        "/api/v1/plugins/stackstorm",
        "/api/v1/plugins/stackstorm/health",
    }
    for path in reader_registry_paths:
        assert request_role_requirement(path, "GET") == "reader"

    assert request_role_requirement("/api/v1/plugins/stackstorm/configuration", "GET") == (
        "operator"
    )
    assert request_role_requirement("/api/v1/plugins/stackstorm/test-connection", "POST") == (
        "operator"
    )
    assert request_role_requirement("/api/v1/plugins/prometheus/reload", "POST") == "operator"


def test_kubernetes_python_client_imports_stay_inside_k8s_plugin() -> None:
    violations: list[str] = []
    for path in sorted(Path("api").rglob("*.py")):
        if "__pycache__" in path.parts or path.is_relative_to(Path("api/plugins/k8s")):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "kubernetes" or alias.name.startswith("kubernetes."):
                        violations.append(f"{path}:{node.lineno} imports {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "kubernetes" or module.startswith("kubernetes."):
                    violations.append(f"{path}:{node.lineno} imports from {module}")

    assert violations == []


def test_admin_detail_reads_are_not_operator_reporting_surfaces() -> None:
    admin_paths = {
        "/api/v1/scheduled-tasks",
        "/api/v1/scheduled-tasks/1",
    }
    for path in admin_paths:
        assert request_role_requirement(path, "GET") == "admin"
    assert request_role_requirement("/api/v1/scheduled-tasks/due", "GET") == "service"


def test_reporting_status_schemas_omit_sensitive_control_plane_fields() -> None:
    for schema in STATUS_RESPONSE_SCHEMAS:
        field_names = set(schema.model_fields)
        assert field_names.isdisjoint(SENSITIVE_STATUS_FIELDS)
        leaked_secret_names = {
            field
            for field in field_names
            if field not in NON_SECRET_STATUS_FIELD_ALLOWLIST
            and any(fragment in field for fragment in SENSITIVE_FIELD_NAME_FRAGMENTS)
        }
        assert leaked_secret_names == set()


def test_ui_reporting_surfaces_use_status_routes() -> None:
    app_source = Path("ui/src/App.tsx").read_text()
    forbidden_fragments = {
        '"/api/v1/health"',
        '"/api/v1/dishes?limit=100"',
        '"/api/v1/stats"',
        '"/api/v1/observability/activity?limit=10',
        '"/api/v1/communications/activity?limit=8"',
        '"/api/v1/suppressions?limit=8"',
        "/api/v1/scheduled-tasks?service_type=",
    }
    for fragment in forbidden_fragments:
        assert fragment not in app_source
    required_fragments = {
        '"/api/v1/health/status"',
        '"/api/v1/dishes/status?limit=100&order_scope=operator"',
        '"/api/v1/observability/activity/status?limit=10',
        '"/api/v1/communications/activity/status?limit=8"',
        '"/api/v1/suppressions/status?limit=8"',
        "/api/v1/scheduled-tasks/status?service_type=",
    }
    for fragment in required_fragments:
        assert fragment in app_source


def test_ui_plugin_connection_test_uses_only_saved_adapter_state() -> None:
    app_source = Path("ui/src/App.tsx").read_text()
    run_now_mutation = app_source.split("const runScheduledTaskNowMutation = useMutation({", 1)[1]
    run_now_mutation = run_now_mutation.split("const internalPlugins = servicePlugins", 1)[0]

    assert "/api/v1/scheduled-tasks/${task.id}/run-now" in run_now_mutation
    assert "scheduledTaskStatusRecordSchema" in run_now_mutation
    assert "/test-connection" not in app_source
    assert "/sync-content" not in app_source
    assert "serializeUiConfig(operatorConfigInput)" not in run_now_mutation
    assert "operatorCredentialInput" not in run_now_mutation
    assert "operatorCredentialKeyIdInput" not in run_now_mutation
    assert "/credentials" not in run_now_mutation

    assert "const canSaveOperatorPluginConfig = Boolean(" in app_source
    assert "const operatorCredentialRequired = hasRequiredCredentialRequirement(" in app_source
    assert "const canUseSavedAdapterState = Boolean(" in app_source
    assert "function hasRequiredCredentialRequirement(" in app_source
    assert (
        "(!operatorCredentialRequired || operatorConfigQuery.data?.credential_configured)"
        in app_source
    )
    assert "operatorConfigQuery.data?.credential_configured" in app_source
    assert "!operatorConfigDirty" in app_source
    assert "!saveOperatorPluginConfigMutation.isPending" in app_source
    assert "isOperatorRunnableScheduledTask(task)" in app_source
    assert "scheduledTaskRunActionLabel(task)" in app_source
    assert "scheduledTaskRunBlockedMessage({" in app_source
    assert 'notify("error", blockedRunMessage' in app_source
    assert "task.run_now_label" in app_source
    assert "task.run_now_description" in app_source
    assert 'identity.includes("sync")' not in app_source


def test_ui_form_numeric_values_match_api_contracts() -> None:
    app_source = Path("ui/src/App.tsx").read_text()

    assert (
        "serializeUiConfig(operatorConfigInput, operatorConfigQuery.data?.config_schema)"
        in app_source
    )
    assert (
        "comparableOperatorConfig(operatorConfig, operatorConfigQuery.data?.config_schema)"
        in app_source
    )
    assert "function isNumericOperatorConfigField(" in app_source
    assert 'field.type === "number" || field.type === "integer"' in app_source

    # Ingredient template mutation form was removed; UI is now read-only.
    # These numeric field registrations were part of that removed form.

    for field_name in (
        "ingredient_id",
        "parallel_group",
        "depth",
    ):
        field_registration = app_source.split(
            f"form.register(`recipe_ingredients.${{index}}.{field_name}` as const",
            1,
        )[1].split(")", 1)[0]
        assert "valueAsNumber: true" in field_registration


def test_removed_execution_routes_stay_removed() -> None:
    routes = _route_keys()
    forbidden = {
        ("POST", "/api/v1/expediter/dispatch"),
        ("POST", "/api/v1/orders/{order_id}/dispatch"),
        ("POST", "/api/v1/orders/{order_id}/reconcile"),
        ("POST", "/api/v1/dishes/{dish_id}/claim"),
        ("POST", "/api/v1/dishes/{dish_id}/finalize-claim"),
        ("POST", "/api/v1/dish-ingredients/bulk"),
        ("POST", "/api/v1/cook/execute"),
        ("GET", "/api/v1/cook/executions/{execution_id}"),
        ("GET", "/api/v1/prometheus/rules"),
        ("GET", "/api/v1/prometheus/rule-groups"),
        ("GET", "/api/v1/prometheus/labels"),
        ("GET", "/api/v1/prometheus/label-values/{label_name}"),
        ("GET", "/api/v1/prometheus/health"),
        ("GET", "/api/v1/stats"),
        ("POST", "/api/v1/prometheus/rules"),
        ("PUT", "/api/v1/prometheus/rules/{rule_name}"),
        ("DELETE", "/api/v1/prometheus/rules/{rule_name}"),
        ("POST", "/api/v1/plugins/{service_type}/sync-content"),
    }
    assert routes.isdisjoint(forbidden)


def test_orders_only_execution_routes_are_registered() -> None:
    routes = _route_keys()
    required = {
        ("POST", "/api/v1/cook/orders/{order_id}"),
        ("POST", "/api/v1/cook/dishes/{dish_id}/advance"),
        ("GET", "/api/v1/expediter/status/{service_type}/{service_exec_id}"),
        ("POST", "/api/v1/expediter/cancel/{service_type}/{service_exec_id}"),
        ("GET", "/api/v1/dish-ingredients/in-flight"),
        ("GET", "/api/v1/dish-ingredients/advance-ready"),
        ("GET", "/api/v1/dish-ingredients/execution-pending"),
        ("POST", "/api/v1/expediter/execute/{dish_ingredient_id}"),
        ("POST", "/api/v1/dish-ingredients/{dish_ingredient_id}/reconcile"),
    }
    assert required.issubset(routes)


def test_internal_workflow_routes_have_service_role_requirements() -> None:
    service_routes = {
        ("POST", "/api/v1/cook/orders/1"),
        ("POST", "/api/v1/cook/dishes/1/advance"),
        ("GET", "/api/v1/dish-ingredients/in-flight"),
        ("GET", "/api/v1/dish-ingredients/cancel-requested"),
        ("GET", "/api/v1/dish-ingredients/advance-ready"),
        ("GET", "/api/v1/dish-ingredients/execution-pending"),
        ("POST", "/api/v1/dish-ingredients/1/execution-claim"),
        ("POST", "/api/v1/dish-ingredients/1/execution-release"),
        ("POST", "/api/v1/dish-ingredients/1/poll-claim"),
        ("POST", "/api/v1/dish-ingredients/1/poll-release"),
        ("POST", "/api/v1/dish-ingredients/1/reconcile"),
        ("POST", "/api/v1/expediter/cancel/dummy/abc123"),
        ("POST", "/api/v1/expediter/execute/1"),
        ("POST", "/api/v1/orders"),
        ("PUT", "/api/v1/orders/1"),
    }
    for method, path in service_routes:
        assert request_role_requirement(path, method) == "service"


def test_webhook_ingress_uses_route_level_webhook_auth() -> None:
    assert request_role_requirement("/api/v1/webhook", "POST") is None


def test_dish_ingredient_raw_detail_is_admin_readable_while_status_is_human_readable() -> None:
    assert request_role_requirement("/api/v1/dishes/1/ingredients", "GET") == "admin"
    assert not _service_allowed_path("timer", "/api/v1/dishes/1/ingredients", "GET")
    assert _service_allowed_path("timer", "/api/v1/dishes/1/ingredient-status", "GET")
    assert request_role_requirement("/api/v1/dishes/1/ingredient-status", "GET") == "reader"


def test_admin_dish_ingredient_history_response_redacts_secret_like_nested_fields() -> None:
    row = DishIngredient(
        id=1,
        req_id="unit-test",
        dish_id=2,
        service_type="k8s",
        service_exec="node_triage",
        service_exec_status="succeeded",
        attempt=0,
        step_order=1,
        parallel_group=0,
        depth=1,
        service_exec_sla_exceeded=False,
        service_payload={"node": "worker-1", "token": "secret-token"},
        service_exec_parameters={"operation": "node_pressure", "password": "secret-password"},
        service_exec_expected_outcome={"success": True},
        service_exec_actual_outcome={
            "success": True,
            "nested": {"authorization": "Bearer secret", "node": "worker-1"},
        },
        deleted=False,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )

    response = _serialize_admin_dish_ingredient_history(row)

    assert isinstance(response, DishIngredientResponse)
    assert response.service_payload == {"node": "worker-1", "token": "[redacted]"}
    assert response.service_exec_parameters == {
        "operation": "node_pressure",
        "password": "[redacted]",
    }
    assert response.service_exec_actual_outcome == {
        "success": True,
        "nested": {"authorization": "[redacted]", "node": "worker-1"},
    }


def test_dish_ingredient_status_response_projects_sanitized_runtime_summary() -> None:
    row = DishIngredient(
        id=1,
        req_id="unit-test",
        dish_id=2,
        service_type="k8s",
        service_exec="pvc_diagnostics",
        service_exec_status="succeeded",
        attempt=0,
        step_order=1,
        parallel_group=0,
        depth=1,
        service_exec_sla_exceeded=False,
        service_exec_parameters={
            "operation": "pvc_diagnostics",
            "role": "gather_evidence",
            "password": "secret-password",
        },
        service_exec_actual_outcome={
            "status": "succeeded",
            "message": "PVC diagnostics collected token=secret-token",
            "summary": {
                "namespace": "openstack",
                "pvc_phase": "Bound",
                "token": "secret-token",
                "nested": {"authorization": "Bearer secret", "safe": "yes"},
            },
            "events": [{"message": "raw event payload is not part of the status summary"}],
        },
        deleted=False,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )

    response = _serialize_dish_ingredient_status(row)

    assert isinstance(response, DishIngredientStatusResponse)
    assert response.operation == "pvc_diagnostics"
    assert response.execution_role == "gather_evidence"
    assert response.result_status == "succeeded"
    assert response.result_message == "PVC diagnostics collected token=[redacted]"
    assert response.result_summary == {
        "summary": {
            "namespace": "openstack",
            "pvc_phase": "Bound",
            "token": "[redacted]",
            "nested": {"authorization": "[redacted]", "safe": "yes"},
        }
    }
    dumped = response.model_dump(mode="json")
    assert "service_exec_actual_outcome" not in dumped
    assert "secret-token" not in str(dumped)
    assert "secret-password" not in str(dumped)


def test_expediter_runner_service_scope_is_narrow() -> None:
    assert _service_allowed_path(
        "expediter-runner", "/api/v1/dish-ingredients/execution-pending", "GET"
    )
    assert _service_allowed_path(
        "expediter-runner", "/api/v1/dish-ingredients/1/execution-claim", "POST"
    )
    assert _service_allowed_path(
        "expediter-runner", "/api/v1/dish-ingredients/1/execution-release", "POST"
    )
    assert _service_allowed_path(
        "expediter-runner", "/api/v1/dish-ingredients/1/execution-reconcile", "POST"
    )
    assert _service_allowed_path("expediter-runner", "/api/v1/expediter/execute/1", "POST")
    assert _service_allowed_path("expediter-runner", "/api/v1/cook/dishes/1/advance", "POST")
    assert not _service_allowed_path(
        "expediter-runner", "/api/v1/expediter/status/dummy/abc123", "GET"
    )
    assert not _service_allowed_path(
        "expediter-runner", "/api/v1/expediter/cancel/dummy/abc123", "POST"
    )
    assert not _service_allowed_path(
        "expediter-runner", "/api/v1/dish-ingredients/1/poll-claim", "POST"
    )
    assert not _service_allowed_path(
        "expediter-runner", "/api/v1/dish-ingredients/1/poll-release", "POST"
    )
    assert not _service_allowed_path("timer", "/api/v1/dishes/1/ingredients", "GET")
    assert not _service_allowed_path("expediter-runner", "/api/v1/scheduled-tasks/due", "GET")
    assert not _service_allowed_path("timer", "/api/v1/dish-ingredients/execution-pending", "GET")
    assert not _service_allowed_path("timer", "/api/v1/expediter/execute/1", "POST")
    assert not _service_allowed_path(
        "dishwasher", "/api/v1/dish-ingredients/execution-pending", "GET"
    )


def test_dish_ingredient_status_response_omits_sensitive_runtime_fields() -> None:
    sensitive_fields = {
        "req_id",
        "service_exec_id",
        "destination_target",
        "service_payload",
        "service_exec_parameters",
        "service_exec_expected_outcome",
        "service_exec_claimed_at",
        "service_exec_claimed_by",
        "service_exec_actual_outcome",
        "service_exec_error",
        "deleted",
        "deleted_at",
    }
    assert sensitive_fields.isdisjoint(DishIngredientStatusResponse.model_fields)


def test_order_status_response_omits_control_plane_payload_fields() -> None:
    sensitive_fields = {
        "fingerprint",
        "fingerprint_when_active",
        "labels",
        "annotations",
        "raw_data",
    }
    assert sensitive_fields.isdisjoint(OrderStatusResponse.model_fields)
    assert request_role_requirement("/api/v1/orders/status", "GET") == "reader"
    assert request_role_requirement("/api/v1/orders/1/status", "GET") == "reader"
    assert "correlation_key" in Order.__table__.columns
    assert "correlation_key" in OrderStatusResponse.model_fields


def test_recipe_status_response_omits_workflow_control_plane_fields() -> None:
    sensitive_fields = {
        "recipe_ingredients",
        "communications",
        "inactive_ingredient_ids",
        "deleted",
        "deleted_at",
    }
    assert sensitive_fields.isdisjoint(RecipeStatusResponse.model_fields)
    assert request_role_requirement("/api/v1/recipes/status", "GET") == "reader"
    assert request_role_requirement("/api/v1/recipes/1/status", "GET") == "reader"


def test_recipe_ingredient_status_response_omits_payload_override_fields() -> None:
    sensitive_fields = {
        "service_payload",
        "service_exec_parameters_override",
        "service_exec_expected_outcome",
        "ingredient",
        "deleted",
        "deleted_at",
    }
    assert sensitive_fields.isdisjoint(RecipeIngredientStatusResponse.model_fields)
    assert request_role_requirement("/api/v1/recipes/1/ingredient-status", "GET") == "reader"


def test_human_control_plane_routes_keep_configuration_writes_admin_only() -> None:
    assert request_role_requirement("/api/v1/plugins/dummy", "PATCH") == "operator"
    assert request_role_requirement("/api/v1/recipes/", "POST") == "operator"
    assert (
        request_role_requirement("/api/v1/internal/service-registry/ingredients/bulk", "POST")
        == "service"
    )
    assert request_role_requirement("/api/v1/scheduled-tasks", "POST") == "admin"
    assert request_role_requirement("/api/v1/scheduled-tasks/1", "PATCH") == "operator"
    assert request_role_requirement("/api/v1/scheduled-tasks/1/run-now", "POST") == "operator"
    assert request_role_requirement("/api/v1/scheduled-tasks/1/run-now", "GET") == "admin"
    assert request_role_requirement("/api/v1/scheduled-tasks/due", "GET") == "service"
    assert not _service_allowed_path(
        "dishwasher", "/api/v1/service-registry/ingredients/bulk", "POST"
    )
    assert _service_allowed_path(
        "dishwasher", "/api/v1/internal/service-registry/ingredients/bulk", "POST"
    )
    assert not _service_allowed_path(
        "prep-chef", "/api/v1/internal/service-registry/ingredients/bulk", "POST"
    )
    assert not _service_allowed_path(
        "timer", "/api/v1/internal/service-registry/ingredients/bulk", "POST"
    )
    assert not _service_allowed_path("dishwasher", "/api/v1/scheduled-tasks/1/run-now", "POST")
    assert request_role_requirement("/api/v1/auth/bindings", "GET") == "admin"


def test_operator_mutation_allowlists_exclude_payload_and_secret_fields() -> None:
    sensitive_fragments = (
        "credential",
        "secret",
        "token",
        "password",
        "payload",
        "parameters",
        "outcome",
    )
    for field_name in OPERATOR_SCHEDULED_TASK_UPDATE_FIELDS | OPERATOR_PLUGIN_RUNTIME_FIELDS:
        assert not any(fragment in field_name for fragment in sensitive_fragments)
    assert OPERATOR_SCHEDULED_TASK_UPDATE_FIELDS == {"is_enabled", "run_interval_seconds"}


def test_adapter_configuration_routes_enforce_operator_config_admin_secrets() -> None:
    assert request_role_requirement("/api/v1/plugins/stackstorm/configuration", "GET") == (
        "operator"
    )
    assert request_role_requirement("/api/v1/plugins/stackstorm/configuration", "PUT") == (
        "operator"
    )
    assert request_role_requirement("/api/v1/plugins/stackstorm/credentials", "PUT") == "admin"
    assert request_role_requirement("/api/v1/plugins/stackstorm/test-connection", "POST") == (
        "operator"
    )
    assert request_role_requirement("/api/v1/plugins/prometheus/reload", "POST") == "operator"


def test_poundcake_api_health_routes_require_reader_auth() -> None:
    assert request_role_requirement("/api/v1/live", "GET") == "reader"
    assert request_role_requirement("/api/v1/ready", "GET") == "reader"
    assert request_role_requirement("/api/v1/health", "GET") == "reader"
    assert request_role_requirement("/api/v1/health/status", "GET") == "reader"
    assert request_role_requirement("/livez", "GET") is None
    assert request_role_requirement("/readyz", "GET") is None


def test_dish_table_has_no_legacy_execution_columns() -> None:
    dish_columns = set(Dish.__table__.columns.keys())
    assert "execution_ref" not in dish_columns
    assert "execution_status" not in dish_columns
    assert "expected_duration_sec" not in dish_columns
    assert "actual_duration_sec" not in dish_columns
    assert "retry_attempt" not in dish_columns
    assert {"dish_exec_status", "expected_run_secs", "run_time_secs"}.issubset(dish_columns)


def test_service_plugin_contract_columns_are_db_enforced() -> None:
    ingredient_constraints = {constraint.name for constraint in Ingredient.__table__.constraints}
    runtime_constraints = {constraint.name for constraint in DishIngredient.__table__.constraints}
    order_constraints = {constraint.name for constraint in Order.__table__.constraints}
    plugin_constraints = {constraint.name for constraint in ServicePlugin.__table__.constraints}

    assert "ck_ingredients_default_expected_secs_positive" in ingredient_constraints
    assert "ck_ingredients_default_timeout_positive" in ingredient_constraints
    assert "ck_ingredients_ingredient_purpose" in ingredient_constraints
    assert "ck_dish_ingredients_service_exec_status" in runtime_constraints
    assert "ck_dish_ingredients_timeout_positive" in runtime_constraints
    assert "ck_orders_processing_status" in order_constraints
    assert "ck_service_plugins_health_status" in plugin_constraints
    assert "ck_service_plugins_health_check_state" in plugin_constraints
    assert "ck_service_plugins_plugin_type" in plugin_constraints
    assert "ck_service_plugins_query_limit_positive" in plugin_constraints


def test_all_declared_ingredient_purposes_are_schema_supported() -> None:
    from typing import get_args

    from api.plugins.catalog import get_enabled_plugins
    from api.types import ExecutionPurpose

    supported = set(get_args(ExecutionPurpose))
    declared = {
        str(template["ingredient_purpose"])
        for plugin in get_enabled_plugins()
        for template in plugin.ingredient_templates
    }

    assert declared.issubset(supported)
