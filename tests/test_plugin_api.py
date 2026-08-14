"""Unit tests for service plugin API serialization helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from starlette.requests import Request

from api.api.plugins import _empty_helper_metadata, _summary_from_row
from api.api.plugins import _credential_configured
from api.api.plugins import _helper_metadata
from api.api.plugins import (
    create_kubernetes_prometheus_rule_rule,
    _prometheus_rule_resource_from_crd,
    export_genestack_alert_updates,
    get_kubernetes_prometheus_rule,
    get_kubernetes_prometheus_rule_rule,
    list_kubernetes_prometheus_rules,
    reload_prometheus_plugin_rule_state,
    update_kubernetes_prometheus_rule_rule,
)
from api.api.plugins import get_plugin_configuration, get_plugin_health, update_plugin_configuration
from api.api.plugins import test_plugin_connection as run_plugin_connection_test
from api.api.plugins import update_plugin_credential
from api.api.plugins import update_service_plugin
from api.models.models import ScheduledTask, ServicePlugin
from api.schemas.schemas import (
    GenestackAlertExportRequest,
    PrometheusRuleRuleCreateRequest,
    PrometheusRuleRuleUpdateRequest,
    ServicePluginConnectionTestRequest,
    ServicePluginConfigurationUpdate,
    ServicePluginUpdate,
)
from api.plugins.dummy.plugin import get_plugin as get_dummy_plugin
from api.plugins.genestack_monitoring.plugin import get_plugin as get_genestack_plugin
from api.plugins.alertmanager.plugin import get_plugin as get_alertmanager_plugin
from api.plugins.k8s.plugin import get_plugin as get_k8s_plugin
from api.plugins.prometheus.plugin import get_plugin as get_prometheus_plugin
from api.plugins.stackstorm.plugin import get_plugin as get_stackstorm_plugin
from api.plugins.base import ExecutionAdapter
from api.plugins.manifest import ServicePlugin as ServicePluginManifest
from api.plugins.types import (
    ExecutionContext,
    ExecutionResult,
    PluginHealthResult,
)
from api.services.credential_manager import AdapterCredentialResult, ServicePluginCredentialError
from fastapi import HTTPException
import pytest
from api.core.time import utc_now_db


def test_plugin_api_helper_metadata_does_not_expose_helper_object() -> None:
    metadata = _helper_metadata(get_dummy_plugin())

    assert metadata == {
        "helper_available": True,
        "helper_capabilities": ["dummy.echo"],
        "required_helper_capabilities": {"dummy": ["dummy.echo"]},
        "missing_helper_capabilities": {},
    }


def test_plugin_api_reports_missing_helper_capabilities() -> None:
    metadata = _helper_metadata(get_genestack_plugin())

    assert metadata["helper_available"] is False
    assert metadata["helper_capabilities"] == []
    assert metadata["required_helper_capabilities"] == {
        "github": ["pull_request.create", "repo.list", "repo.read", "repo.write"],
        "k8s": ["k8s.prometheusrules.manage"],
        "prometheus": ["alert_rules.parse", "alert_rules.render"],
    }
    assert metadata["missing_helper_capabilities"] == {
        "github": ["pull_request.create", "repo.list", "repo.read", "repo.write"],
        "k8s": ["k8s.prometheusrules.manage"],
        "prometheus": ["alert_rules.parse", "alert_rules.render"],
    }


def test_internal_plugin_summary_is_editable_without_helpers() -> None:
    row = ServicePlugin(
        id=1,
        service_type="timer",
        plugin_short_id="timer001",
        plugin_type="internal_plugin",
        plugin_tier="supported",
        plugin_log_key="timer",
        enabled=False,
        run_interval_seconds=17,
        query_limit=50,
        status_message="Paused by operator",
        health_status="disabled",
        credential_status="not_required",
        registered_ingredient_count=0,
        registered_recipe_count=0,
        consecutive_failures=0,
        created_at=utc_now_db(),
        updated_at=utc_now_db(),
    )

    summary = _summary_from_row(row)

    assert summary.plugin_type == "internal_plugin"
    assert summary.plugin_tier == "supported"
    assert summary.config_editable is True
    assert summary.run_interval_seconds == 17
    assert summary.query_limit == 50
    assert summary.status_message == "Paused by operator"
    assert summary.helper_available is False


def test_empty_helper_metadata_for_internal_plugins() -> None:
    assert _empty_helper_metadata() == {
        "helper_available": False,
        "helper_capabilities": [],
        "required_helper_capabilities": {},
        "missing_helper_capabilities": {},
    }


class _Scalars:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows

    def all(self) -> list[object]:
        return self.rows

    def first(self) -> object | None:
        return self.rows[0] if self.rows else None


class _Result:
    def __init__(self, row: object | list[object] | None) -> None:
        self.row = row

    def scalar_one_or_none(self) -> object | None:
        if isinstance(self.row, list):
            return self.row[0] if self.row else None
        return self.row

    def scalars(self) -> _Scalars:
        if isinstance(self.row, list):
            return _Scalars(self.row)
        return _Scalars([] if self.row is None else [self.row])


class _Db:
    def __init__(
        self,
        row: ServicePlugin | None,
        task: ScheduledTask | list[ScheduledTask] | None = None,
    ) -> None:
        self.row = row
        self.tasks = task if isinstance(task, list) else ([] if task is None else [task])
        self.execute_count = 0
        self.committed = False

    async def execute(self, _statement: object) -> _Result:
        self.execute_count += 1
        if self.execute_count == 1:
            return _Result(self.row)
        return _Result(self.tasks)

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.execute_count = 0

    async def refresh(self, _row: ServicePlugin) -> None:
        return None


class _StackStormConfigDb:
    def __init__(self, row: ServicePlugin, credential: object | None = None) -> None:
        self.row = row
        self.credential = credential
        self.execute_count = 0
        self.committed = False
        self.rolled_back = False

    async def execute(self, _statement: object) -> _Result:
        self.execute_count += 1
        if self.execute_count == 1:
            return _Result(self.row)
        return _Result(self.credential)

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True
        self.execute_count = 0

    async def refresh(self, _row: ServicePlugin) -> None:
        return None


class _SecretRequest:
    def __init__(self, body: dict[str, object]) -> None:
        self.body = body

    async def json(self) -> dict[str, object]:
        return self.body


def _credential_presence_result(configured: bool) -> AdapterCredentialResult | None:
    if not configured:
        return None
    return AdapterCredentialResult(payload={"token": "configured"}, allow_public_read=False)


def _stackstorm_plugin_row() -> ServicePlugin:
    return ServicePlugin(
        id=10,
        service_type="stackstorm",
        plugin_short_id="st2",
        plugin_type="external_plugin",
        plugin_tier="supported",
        plugin_log_key="stackstorm",
        enabled=True,
        health_status="unknown",
        credential_status="unknown",
        registered_ingredient_count=0,
        registered_recipe_count=0,
        consecutive_failures=0,
        created_at=utc_now_db(),
        updated_at=utc_now_db(),
    )


def _external_plugin_row(service_type: str) -> ServicePlugin:
    return ServicePlugin(
        id=10,
        service_type=service_type,
        plugin_short_id=service_type[:8],
        plugin_type="external_plugin",
        plugin_tier="community",
        plugin_log_key=None,
        enabled=True,
        health_status="unknown",
        credential_status="unknown",
        registered_ingredient_count=0,
        registered_recipe_count=0,
        consecutive_failures=0,
        created_at=utc_now_db(),
        updated_at=utc_now_db(),
    )


class _GenericCredentialAdapter(ExecutionAdapter):
    service_type = "custom_plugin"

    def validate(self, ctx: ExecutionContext) -> str | None:
        return None

    async def dispatch(self, ctx: ExecutionContext) -> ExecutionResult:
        return ExecutionResult(status="succeeded")

    async def poll(self, ctx: ExecutionContext, service_exec_id: str) -> ExecutionResult:
        return ExecutionResult(status="succeeded")

    def health_check(self) -> PluginHealthResult:
        return PluginHealthResult(status="healthy")

    def credential_requirements(self) -> list[dict[str, object]]:
        return [{"credential_type": "custom_api_key"}]

    def validate_credential_payload(
        self,
        credential_type: str,
        payload: dict[str, object],
    ) -> str | None:
        if credential_type != "custom_api_key":
            return "unsupported credential type"
        if not isinstance(payload.get("api_key"), str) or not payload["api_key"]:
            return "api_key is required"
        return None


@pytest.mark.asyncio
async def test_plugin_api_returns_stackstorm_operator_configuration(monkeypatch) -> None:
    row = _stackstorm_plugin_row()
    row.plugin_config = {
        "url": "http://stackstorm-api.poundcake.svc.cluster.local:9101",
        "verify_ssl": True,
    }

    async def read_credential_with_policy(**_kwargs: object) -> AdapterCredentialResult | None:
        return _credential_presence_result(True)

    monkeypatch.setattr(
        "api.api.plugins._plugin_by_service_type", lambda _service_type: get_stackstorm_plugin()
    )
    monkeypatch.setattr(
        "api.api.plugins.read_adapter_credential_with_policy", read_credential_with_policy
    )

    response = await get_plugin_configuration(
        "stackstorm", db=_StackStormConfigDb(row)  # type: ignore[arg-type]
    )

    assert response.service_type == "stackstorm"
    assert response.config == {
        "url": "http://stackstorm-api.poundcake.svc.cluster.local:9101",
        "verify_ssl": True,
        "capabilities_enabled": {},
        "capability_overrides": {},
    }
    assert response.credential_key_id == "default"
    assert response.credential_configured is True


@pytest.mark.asyncio
async def test_plugin_api_updates_stackstorm_config_without_credentials(monkeypatch) -> None:
    row = _stackstorm_plugin_row()

    async def read_credential_with_policy(**_kwargs: object) -> AdapterCredentialResult | None:
        return _credential_presence_result(False)

    monkeypatch.setattr(
        "api.api.plugins._plugin_by_service_type", lambda _service_type: get_stackstorm_plugin()
    )
    monkeypatch.setattr(
        "api.api.plugins.read_adapter_credential_with_policy", read_credential_with_policy
    )
    db = _StackStormConfigDb(row)

    response = await update_plugin_configuration(
        "stackstorm",
        ServicePluginConfigurationUpdate(
            config={"url": "http://stackstorm-api:9101/", "verify_ssl": False},
        ),
        db=db,  # type: ignore[arg-type]
        _context=object(),
    )

    assert db.committed is True
    assert row.plugin_config == {
        "url": "http://stackstorm-api:9101",
        "verify_ssl": False,
        "capabilities_enabled": {},
        "capability_overrides": {},
    }
    assert response.config == row.plugin_config


@pytest.mark.asyncio
async def test_plugin_api_returns_k8s_operator_configuration(monkeypatch) -> None:
    row = _external_plugin_row("k8s")
    row.plugin_config = {
        "namespace": "monitoring",
    }

    async def read_credential_with_policy(**_kwargs: object) -> AdapterCredentialResult | None:
        return _credential_presence_result(False)

    monkeypatch.setattr(
        "api.api.plugins._plugin_by_service_type", lambda _service_type: get_k8s_plugin()
    )
    monkeypatch.setattr(
        "api.api.plugins.read_adapter_credential_with_policy", read_credential_with_policy
    )

    response = await get_plugin_configuration(
        "k8s", db=_StackStormConfigDb(row)  # type: ignore[arg-type]
    )

    assert response.service_type == "k8s"
    assert response.config == {
        "namespace": "monitoring",
        "capabilities_enabled": {},
        "capability_overrides": {},
    }
    assert response.credential_key_id == "default"
    assert response.credential_configured is False


@pytest.mark.asyncio
async def test_plugin_api_updates_k8s_config_with_capability_overrides(monkeypatch) -> None:
    row = _external_plugin_row("k8s")

    async def read_credential_with_policy(**_kwargs: object) -> AdapterCredentialResult | None:
        return _credential_presence_result(False)

    monkeypatch.setattr(
        "api.api.plugins._plugin_by_service_type", lambda _service_type: get_k8s_plugin()
    )
    monkeypatch.setattr(
        "api.api.plugins.read_adapter_credential_with_policy", read_credential_with_policy
    )
    db = _StackStormConfigDb(row)

    response = await update_plugin_configuration(
        "k8s",
        ServicePluginConfigurationUpdate(
            config={
                "namespace": "monitoring",
                "capabilities_enabled": {
                    "k8s.remediation.kubernetes.kube-pod-crash-looping": True,
                },
                "capability_overrides": {
                    "k8s.remediation.kubernetes.kube-pod-crash-looping": {
                        "defaults": {
                            "service_payload": {
                                "namespace": "override-namespace",
                            }
                        }
                    }
                },
            },
        ),
        db=db,  # type: ignore[arg-type]
        _context=object(),
    )

    assert db.committed is True
    assert row.plugin_config == {
        "namespace": "monitoring",
        "capabilities_enabled": {
            "k8s.remediation.kubernetes.kube-pod-crash-looping": True,
        },
        "capability_overrides": {
            "k8s.remediation.kubernetes.kube-pod-crash-looping": {
                "defaults": {
                    "service_payload": {
                        "namespace": "override-namespace",
                    }
                }
            }
        },
    }
    assert response.config == row.plugin_config


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service_type", "plugin_factory", "url"),
    [
        ("prometheus", get_prometheus_plugin, "http://poundcake-prometheus:9090/"),
        ("alertmanager", get_alertmanager_plugin, "http://poundcake-alertmanager:9093/"),
    ],
)
async def test_plugin_api_updates_monitoring_operator_config(
    monkeypatch,
    service_type: str,
    plugin_factory: object,
    url: str,
) -> None:
    row = _external_plugin_row(service_type)

    async def read_credential_with_policy(**_kwargs: object) -> AdapterCredentialResult | None:
        return _credential_presence_result(False)

    monkeypatch.setattr(
        "api.api.plugins._plugin_by_service_type", lambda _service_type: plugin_factory()
    )
    monkeypatch.setattr(
        "api.api.plugins.read_adapter_credential_with_policy", read_credential_with_policy
    )
    db = _StackStormConfigDb(row)

    response = await update_plugin_configuration(
        service_type,
        ServicePluginConfigurationUpdate(
            config={"url": url, "verify_ssl": False, "timeout_seconds": 5},
        ),
        db=db,  # type: ignore[arg-type]
        _context=object(),
    )

    assert db.committed is True
    assert row.plugin_config == {
        "url": url.rstrip("/"),
        "verify_ssl": False,
        "timeout_seconds": 5.0,
    }
    assert response.config == row.plugin_config
    assert response.credential_type in {"prometheus_http_auth", "alertmanager_http_auth"}


@pytest.mark.asyncio
async def test_credential_configured_uses_credential_manager_policy_reader(monkeypatch) -> None:
    row = _stackstorm_plugin_row()
    captured: dict[str, object] = {}

    async def read_credential_with_policy(**kwargs: object) -> AdapterCredentialResult | None:
        captured.update(kwargs)
        return _credential_presence_result(True)

    monkeypatch.setattr(
        "api.api.plugins.read_adapter_credential_with_policy", read_credential_with_policy
    )

    configured = await _credential_configured(
        row=row,
        credential_type="stackstorm_api_key",
        credential_key_id="default",
    )

    assert configured is True
    assert captured == {
        "service_type": "stackstorm",
        "credential_type": "stackstorm_api_key",
        "credential_key_id": "default",
    }


@pytest.mark.asyncio
async def test_credential_configured_returns_false_when_policy_reader_rejects(monkeypatch) -> None:
    row = _stackstorm_plugin_row()

    async def read_credential_with_policy(**_kwargs: object) -> AdapterCredentialResult | None:
        raise ServicePluginCredentialError("credential reader denied")

    monkeypatch.setattr(
        "api.api.plugins.read_adapter_credential_with_policy", read_credential_with_policy
    )

    configured = await _credential_configured(
        row=row,
        credential_type="stackstorm_api_key",
        credential_key_id="default",
    )

    assert configured is False


class _ConnectionTestAdapter(ExecutionAdapter):
    service_type = "connection"

    def __init__(self, url: str = "http://default.test") -> None:
        self.url = url

    def validate(self, ctx: ExecutionContext) -> str | None:
        return None

    def operator_config_schema(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {"url": {"type": "string", "title": "URL"}},
            "additionalProperties": False,
        }

    def default_operator_config(self) -> dict[str, object]:
        return {"url": self.url}

    def normalize_operator_config(self, config: dict[str, object] | None) -> dict[str, object]:
        return {"url": str((config or {}).get("url") or self.url).rstrip("/")}

    def with_operator_config(self, config: dict[str, object] | None) -> "_ConnectionTestAdapter":
        return _ConnectionTestAdapter(url=str((config or {}).get("url") or self.url))

    async def dispatch(self, ctx: ExecutionContext) -> ExecutionResult:
        raise AssertionError("test-connection must not dispatch workflow work")

    async def poll(self, ctx: ExecutionContext, service_exec_id: str) -> ExecutionResult:
        raise AssertionError("test-connection must not poll workflow work")

    def health_check(self) -> PluginHealthResult:
        return PluginHealthResult(
            service_type=self.service_type,
            status="healthy",
            message="adapter checked",
            details={"url": self.url},
        )


class _PrometheusRuleHelper:
    def __init__(self) -> None:
        self.namespace = "monitoring"
        self.crd = {
            "metadata": {
                "name": "demo-rules",
                "namespace": "monitoring",
                "labels": {"release": "poundcake-prometheus"},
                "annotations": {
                    "poundcake.io/alert-rule-sources": (
                        '{"DemoAlert": {"file": "alerts/demo.yaml", "format": "spec.groups"}}'
                    )
                },
            },
            "spec": {
                "groups": [
                    {
                        "name": "demo",
                        "rules": [
                            {"alert": "DemoAlert", "expr": "vector(1)"},
                            {"record": "demo:up:sum", "expr": "sum(up)"},
                        ],
                    }
                ]
            },
        }

    async def list_prometheus_rules(self) -> list[dict[str, object]]:
        return [self.crd]

    async def get_prometheus_rule(self, crd_name: str) -> dict[str, object] | None:
        if crd_name == "demo-rules":
            return self.crd
        return None

    async def update_rule_in_named_crd(
        self,
        *,
        crd_name: str,
        group_name: str,
        rule_name: str,
        rule_data: dict[str, object],
        source_metadata: object | None = None,
    ) -> dict[str, object]:
        _ = source_metadata
        if crd_name != "demo-rules":
            return {"status": "error", "message": "not found"}
        rules = self.crd["spec"]["groups"][0]["rules"]
        for idx, rule in enumerate(rules):
            if rule.get("alert") == rule_name or rule.get("record") == rule_name:
                rules[idx] = rule_data
                return {"status": "success", "message": "updated"}
        return {"status": "error", "message": "not found"}

    async def add_rule_to_named_crd(
        self,
        *,
        crd_name: str,
        group_name: str,
        rule_name: str,
        rule_data: dict[str, object],
        source_metadata: object | None = None,
    ) -> dict[str, object]:
        _ = source_metadata
        if crd_name != "demo-rules":
            return {"status": "error", "message": "not found"}
        groups = self.crd["spec"]["groups"]
        for group in groups:
            if group.get("name") == group_name:
                group.setdefault("rules", []).append(rule_data)
                return {"status": "success", "message": "created"}
        groups.append({"name": group_name, "rules": [rule_data]})
        return {"status": "success", "message": "created"}


class _PrometheusRuleAdapter(ExecutionAdapter):
    service_type = "k8s"

    def __init__(
        self,
        namespace: str = "default",
        helper: _PrometheusRuleHelper | None = None,
    ) -> None:
        self.namespace = namespace
        self.helper = helper or _PrometheusRuleHelper()
        self.helper.namespace = namespace

    def default_operator_config(self) -> dict[str, object]:
        return {"namespace": self.namespace}

    def normalize_operator_config(self, config: dict[str, object] | None) -> dict[str, object]:
        return {"namespace": str((config or {}).get("namespace") or self.namespace)}

    def with_operator_config(self, config: dict[str, object] | None) -> "_PrometheusRuleAdapter":
        return _PrometheusRuleAdapter(
            namespace=str((config or {}).get("namespace") or self.namespace),
            helper=self.helper,
        )

    def validate(self, ctx: ExecutionContext) -> str | None:
        return None

    async def dispatch(self, ctx: ExecutionContext) -> ExecutionResult:
        raise AssertionError("not used")

    async def poll(self, ctx: ExecutionContext, service_exec_id: str) -> ExecutionResult:
        raise AssertionError("not used")

    def health_check(self) -> PluginHealthResult:
        return PluginHealthResult(service_type="k8s", status="healthy")


@pytest.mark.asyncio
async def test_plugin_api_test_connection_submits_health_check_order(monkeypatch) -> None:
    row = _external_plugin_row("connection")
    monkeypatch.setattr(
        "api.api.plugins._plugin_by_service_type",
        lambda _service_type: ServicePluginManifest(
            service_type="connection",
            adapter_factory=lambda: _ConnectionTestAdapter(),
        ),
    )
    action_orders: list[dict[str, object]] = []

    async def fake_submit_operator_action_order(**kwargs: object) -> SimpleNamespace:
        action_orders.append(dict(kwargs))
        return SimpleNamespace(
            order_id=105,
            order_req_id=str(kwargs["req_id"]),
            service_type=str(kwargs["service_type"]),
            service_exec=str(kwargs["service_exec"]),
            submitted_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
        )

    monkeypatch.setattr(
        "api.api.plugins.submit_operator_action_order",
        fake_submit_operator_action_order,
    )

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/plugins/connection/test-connection",
            "headers": [],
        }
    )
    request.state.req_id = "TEST-PLUGIN-CONNECTION"

    response = await run_plugin_connection_test(
        "connection",
        request,
        payload=ServicePluginConnectionTestRequest(),
        db=_StackStormConfigDb(row),  # type: ignore[arg-type]
        _context=object(),
    )

    assert response.status == "accepted"
    assert response.message == "connection connection check order accepted"
    assert response.order_id == 105
    assert len(action_orders) == 1
    assert action_orders[0]["req_id"] == "TEST-PLUGIN-CONNECTION"
    assert action_orders[0]["recipe_name"] == "plugin-health-check:connection"
    assert action_orders[0]["service_type"] == "connection"
    assert action_orders[0]["service_exec"] == "health_check"
    assert action_orders[0]["task_key_template"] == "connection-health-check"
    assert action_orders[0]["service_payload"] == {}


@pytest.mark.asyncio
async def test_plugin_api_test_connection_rejects_transient_config(monkeypatch) -> None:
    row = _external_plugin_row("connection")
    monkeypatch.setattr(
        "api.api.plugins._plugin_by_service_type",
        lambda _service_type: ServicePluginManifest(
            service_type="connection",
            adapter_factory=lambda: _ConnectionTestAdapter(),
        ),
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/plugins/connection/test-connection",
            "headers": [],
        }
    )
    request.state.req_id = "TEST-PLUGIN-CONNECTION"

    with pytest.raises(HTTPException) as exc:
        await run_plugin_connection_test(
            "connection",
            request,
            payload=ServicePluginConnectionTestRequest(config={"url": "http://configured.test"}),
            db=_StackStormConfigDb(row),  # type: ignore[arg-type]
            _context=object(),
        )

    assert exc.value.status_code == 400
    assert "stored plugin health-check recipe" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_plugin_health_route_reads_stored_registry_state_without_live_probe(
    monkeypatch,
) -> None:
    class _ExplodingAdapter(_ConnectionTestAdapter):
        def health_check(self) -> PluginHealthResult:
            raise AssertionError("GET plugin health must not perform live adapter probes")

        async def test_connection(
            self,
            *,
            credential_key_id: str = "default",
        ) -> PluginHealthResult:
            raise AssertionError("GET plugin health must not perform connection tests")

    row = _external_plugin_row("connection")
    row.plugin_short_id = "conn001"
    row.plugin_tier = "supported"
    row.plugin_log_key = "connection"
    row.health_status = "healthy"
    row.health_message = "stored health"
    row.registered_ingredient_count = 2
    row.registered_recipe_count = 1
    row.consecutive_failures = 0
    row.health_check_state = "idle"
    row.updated_at = utc_now_db()
    task = ScheduledTask(
        id=5,
        task_key="plugin-health-check:connection",
        task_type="plugin_health_check",
        service_type="connection",
        service_exec="health_check",
        is_enabled=True,
        run_interval_seconds=30,
        status="idle",
        created_at=utc_now_db(),
        updated_at=utc_now_db(),
    )
    manifest = ServicePluginManifest(
        service_type="connection",
        adapter_factory=lambda: _ExplodingAdapter(),
    )
    monkeypatch.setattr(
        "api.api.plugins._plugin_by_service_type",
        lambda _service_type: manifest,
    )

    response = await get_plugin_health(
        "connection",
        db=_Db(row, task),  # type: ignore[arg-type]
    )

    assert response.service_type == "connection"
    assert response.health_status == "healthy"
    assert response.health_message == "stored health"
    assert response.health_check_task_id == 5
    assert response.health_check_interval_seconds == 30


def test_prometheus_rule_resource_summary_counts_alert_and_recording_rules() -> None:
    response = _prometheus_rule_resource_from_crd(
        {
            "metadata": {
                "name": "demo-rules",
                "namespace": "monitoring",
                "annotations": {"source": "unit"},
            },
            "spec": {
                "groups": [
                    {
                        "name": "demo",
                        "rules": [
                            {"alert": "DemoAlert", "expr": "vector(1)"},
                            {"record": "demo:up:sum", "expr": "sum(up)"},
                        ],
                    }
                ]
            },
        }
    )

    assert response.name == "demo-rules"
    assert response.namespace == "monitoring"
    assert response.group_count == 1
    assert response.rule_count == 2
    assert response.alert_count == 1
    assert response.recording_count == 1
    assert response.groups[0].alert_names == ["DemoAlert"]


@pytest.mark.asyncio
async def test_plugin_api_lists_prometheus_rules_through_k8s_adapter(monkeypatch) -> None:
    row = _external_plugin_row("k8s")
    adapter = _PrometheusRuleAdapter(namespace="default")

    async def fake_external_plugin_row_or_404(
        _db: object, _service_type: str
    ) -> tuple[object, object, object]:
        return row, object(), adapter

    monkeypatch.setattr(
        "api.api.plugins._external_plugin_row_or_404",
        fake_external_plugin_row_or_404,
    )

    response = await list_kubernetes_prometheus_rules(
        namespace="monitoring",
        db=object(),  # type: ignore[arg-type]
    )

    assert response.namespace == "monitoring"
    assert response.resource_count == 1
    assert response.items[0].name == "demo-rules"
    assert response.alert_count == 1
    assert response.recording_count == 1


@pytest.mark.asyncio
async def test_plugin_api_reads_one_prometheus_rule_crd(monkeypatch) -> None:
    row = _external_plugin_row("k8s")
    adapter = _PrometheusRuleAdapter(namespace="default")

    async def fake_external_plugin_row_or_404(
        _db: object, _service_type: str
    ) -> tuple[object, object, object]:
        return row, object(), adapter

    monkeypatch.setattr(
        "api.api.plugins._external_plugin_row_or_404",
        fake_external_plugin_row_or_404,
    )

    response = await get_kubernetes_prometheus_rule(
        "demo-rules", namespace="monitoring", db=object()
    )

    assert response.name == "demo-rules"
    assert response.rule_count == 2


@pytest.mark.asyncio
async def test_plugin_api_reads_one_prometheus_rule_entry(monkeypatch) -> None:
    row = _external_plugin_row("k8s")
    adapter = _PrometheusRuleAdapter(namespace="default")

    async def fake_external_plugin_row_or_404(
        _db: object, _service_type: str
    ) -> tuple[object, object, object]:
        return row, object(), adapter

    monkeypatch.setattr(
        "api.api.plugins._external_plugin_row_or_404",
        fake_external_plugin_row_or_404,
    )

    response = await get_kubernetes_prometheus_rule_rule(
        "demo-rules",
        "DemoAlert",
        group_name="demo",
        namespace="monitoring",
        db=object(),
    )

    assert response.rule_name == "DemoAlert"
    assert response.source == {"file": "alerts/demo.yaml", "format": "spec.groups"}


@pytest.mark.asyncio
async def test_plugin_api_updates_one_prometheus_rule_entry(monkeypatch) -> None:
    row = _external_plugin_row("k8s")
    adapter = _PrometheusRuleAdapter(namespace="default")

    async def fake_external_plugin_row_or_404(
        _db: object, _service_type: str
    ) -> tuple[object, object, object]:
        return row, object(), adapter

    monkeypatch.setattr(
        "api.api.plugins._external_plugin_row_or_404",
        fake_external_plugin_row_or_404,
    )
    action_orders: list[dict[str, object]] = []

    async def fake_submit_operator_action_order(**kwargs: object) -> SimpleNamespace:
        action_orders.append(dict(kwargs))
        return SimpleNamespace(
            order_id=101,
            order_req_id=str(kwargs["req_id"]),
            service_type=str(kwargs["service_type"]),
            service_exec=str(kwargs["service_exec"]),
            submitted_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
        )

    monkeypatch.setattr(
        "api.api.plugins.submit_operator_action_order",
        fake_submit_operator_action_order,
    )
    request = Request(
        {
            "type": "http",
            "method": "PUT",
            "path": "/api/v1/plugins/k8s/prometheus-rules/demo-rules/rules/DemoAlert",
            "headers": [],
        }
    )
    request.state.req_id = "TEST-PROM-RELOAD"

    response = await update_kubernetes_prometheus_rule_rule(
        "demo-rules",
        "DemoAlert",
        payload=PrometheusRuleRuleUpdateRequest(
            group_name="demo",
            rule_data={"alert": "DemoAlert", "expr": "vector(2)"},
        ),
        request=request,
        namespace="monitoring",
        db=object(),
        _context=object(),
    )

    assert response.status == "accepted"
    assert response.message == "PrometheusRule update order accepted"
    assert response.order_id == 101
    assert response.order_req_id == "TEST-PROM-RELOAD"
    assert len(action_orders) == 1
    assert action_orders[0]["recipe_name"] == "operator-action:k8s:prometheus-rule-apply"
    assert action_orders[0]["service_type"] == "k8s"
    assert action_orders[0]["service_exec"] == "prometheus_rule"
    assert action_orders[0]["service_payload"] == {
        "crd_name": "demo-rules",
        "group_name": "demo",
        "rule_name": "DemoAlert",
        "rule_data": {"alert": "DemoAlert", "expr": "vector(2)"},
        "namespace": "monitoring",
    }


@pytest.mark.asyncio
async def test_plugin_api_creates_one_prometheus_rule_entry_and_reloads(monkeypatch) -> None:
    row = _external_plugin_row("k8s")
    adapter = _PrometheusRuleAdapter(namespace="default")

    async def fake_external_plugin_row_or_404(
        _db: object, _service_type: str
    ) -> tuple[object, object, object]:
        return row, object(), adapter

    monkeypatch.setattr(
        "api.api.plugins._external_plugin_row_or_404",
        fake_external_plugin_row_or_404,
    )
    action_orders: list[dict[str, object]] = []

    async def fake_submit_operator_action_order(**kwargs: object) -> SimpleNamespace:
        action_orders.append(dict(kwargs))
        return SimpleNamespace(
            order_id=102,
            order_req_id=str(kwargs["req_id"]),
            service_type=str(kwargs["service_type"]),
            service_exec=str(kwargs["service_exec"]),
            submitted_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
        )

    monkeypatch.setattr(
        "api.api.plugins.submit_operator_action_order",
        fake_submit_operator_action_order,
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/plugins/k8s/prometheus-rules/demo-rules/rules",
            "headers": [],
        }
    )
    request.state.req_id = "TEST-PROM-CREATE"

    response = await create_kubernetes_prometheus_rule_rule(
        "demo-rules",
        payload=PrometheusRuleRuleCreateRequest(
            group_name="demo",
            rule_name="NewAlert",
            rule_data={"alert": "NewAlert", "expr": "vector(3)"},
        ),
        request=request,
        namespace="monitoring",
        db=object(),
        _context=object(),
    )

    assert response.status == "accepted"
    assert response.message == "PrometheusRule create order accepted"
    assert response.order_id == 102
    assert response.order_req_id == "TEST-PROM-CREATE"
    assert len(action_orders) == 1
    assert action_orders[0]["recipe_name"] == "operator-action:k8s:prometheus-rule-apply"
    assert action_orders[0]["service_type"] == "k8s"
    assert action_orders[0]["service_exec"] == "prometheus_rule"
    assert action_orders[0]["service_payload"] == {
        "crd_name": "demo-rules",
        "group_name": "demo",
        "rule_name": "NewAlert",
        "rule_data": {"alert": "NewAlert", "expr": "vector(3)"},
        "namespace": "monitoring",
    }


@pytest.mark.asyncio
async def test_plugin_api_reloads_prometheus_rule_state(monkeypatch) -> None:
    row = _external_plugin_row("prometheus")

    async def fake_external_plugin_row_or_404(
        _db: object, _service_type: str
    ) -> tuple[object, object, object]:
        return row, object(), _ConnectionTestAdapter(url="https://prom.example.test")

    async def fake_reload_prometheus_rules(**kwargs: object) -> SimpleNamespace:
        assert kwargs["req_id"] == "TEST-PROM-MANUAL-RELOAD"
        assert kwargs["operator_config"] == {"url": "https://prom.example.test"}
        return SimpleNamespace(
            order_id=103,
            order_req_id=str(kwargs["req_id"]),
            service_type="prometheus",
            service_exec="reload_config",
            submitted_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
        )

    monkeypatch.setattr(
        "api.api.plugins._external_plugin_row_or_404",
        fake_external_plugin_row_or_404,
    )
    monkeypatch.setattr(
        "api.api.plugins.reload_prometheus_rules",
        fake_reload_prometheus_rules,
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/plugins/prometheus/reload",
            "headers": [],
        }
    )
    request.state.req_id = "TEST-PROM-MANUAL-RELOAD"

    response = await reload_prometheus_plugin_rule_state(
        request=request,
        db=object(),
        _context=object(),
    )

    assert response.service_type == "prometheus"
    assert response.service_exec == "reload_config"
    assert response.status == "accepted"
    assert response.message == "prometheus reload order accepted"
    assert response.order_id == 103


class _ExportAdapter(_PrometheusRuleAdapter):
    service_type = "genestack_monitoring"

    def validate(self, ctx: ExecutionContext) -> str | None:
        return None

    async def dispatch(self, ctx: ExecutionContext) -> ExecutionResult:
        return ExecutionResult(
            service_type="genestack_monitoring",
            status="succeeded",
            result={
                "status": "succeeded",
                "message": "Prepared Genestack alert update.",
                "branch": "poundcake/demo",
                "pull_request": {"number": 12, "url": "https://example.test/pr/12"},
                "exported": {"files": 1, "rule_name": "DemoAlert"},
                "skipped": {"missing_source_metadata": 0},
                "warnings": [],
            },
            raw={"success": True},
        )

    async def poll(self, ctx: ExecutionContext, service_exec_id: str) -> ExecutionResult:
        raise AssertionError("not used")

    def health_check(self) -> PluginHealthResult:
        return PluginHealthResult(service_type="genestack_monitoring", status="healthy")


@pytest.mark.asyncio
async def test_plugin_api_exports_genestack_alert_updates_through_order_workflow(
    monkeypatch,
) -> None:
    row = _external_plugin_row("genestack_monitoring")
    adapter = _ExportAdapter()

    async def fake_external_plugin_row_or_404(
        _db: object, _service_type: str
    ) -> tuple[object, object, object]:
        return row, object(), adapter

    monkeypatch.setattr(
        "api.api.plugins._external_plugin_row_or_404",
        fake_external_plugin_row_or_404,
    )
    action_orders: list[dict[str, object]] = []

    async def fake_submit_operator_action_order(**kwargs: object) -> SimpleNamespace:
        action_orders.append(dict(kwargs))
        return SimpleNamespace(
            order_id=104,
            order_req_id=str(kwargs["req_id"]),
            service_type=str(kwargs["service_type"]),
            service_exec=str(kwargs["service_exec"]),
            submitted_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
        )

    monkeypatch.setattr(
        "api.api.plugins.submit_operator_action_order",
        fake_submit_operator_action_order,
    )

    response = await export_genestack_alert_updates(
        payload=GenestackAlertExportRequest(
            namespace="monitoring",
            crd_name="demo-rules",
            group_name="demo",
            rule_name="DemoAlert",
        ),
        request=type("Request", (), {"state": type("State", (), {"req_id": "req-1"})()})(),
        db=object(),
        _context=object(),
    )

    assert response.status == "accepted"
    assert response.message == "Genestack alert export order accepted"
    assert response.order_id == 104
    assert response.service_type == "genestack_monitoring"
    assert response.service_exec == "repo_sync"
    assert len(action_orders) == 1
    assert action_orders[0]["recipe_name"] == (
        "operator-action:genestack-monitoring:export-alert-updates"
    )
    assert action_orders[0]["service_type"] == "genestack_monitoring"
    assert action_orders[0]["service_exec"] == "repo_sync"


@pytest.mark.asyncio
async def test_plugin_api_updates_stackstorm_credential_through_credential_manager(
    monkeypatch,
) -> None:
    row = _stackstorm_plugin_row()
    saved: dict[str, object] = {}

    async def save_credential(**kwargs: object) -> None:
        saved.update(kwargs)

    monkeypatch.setattr(
        "api.api.plugins._plugin_by_service_type", lambda _service_type: get_stackstorm_plugin()
    )
    monkeypatch.setattr("api.api.plugins.write_adapter_credential", save_credential)

    async def read_credential_with_policy(**_kwargs: object) -> AdapterCredentialResult | None:
        return _credential_presence_result(False)

    monkeypatch.setattr(
        "api.api.plugins.read_adapter_credential_with_policy", read_credential_with_policy
    )
    db = _StackStormConfigDb(row)

    response = await update_plugin_credential(
        "stackstorm",
        _SecretRequest(
            {
                "credential_payload": {"api_key": "secret"},
                "credential_key_id": "default",
                "rotate_credential": True,
            }
        ),
        db=db,  # type: ignore[arg-type]
        _context=object(),
    )

    assert db.committed is False
    assert db.rolled_back is True
    assert saved["service_type"] == "stackstorm"
    assert saved["credential_type"] == "stackstorm_api_key"
    assert saved["payload"] == {"api_key": "secret"}
    assert "db" not in saved
    assert response.credential_configured is False


@pytest.mark.asyncio
async def test_plugin_api_updates_any_adapter_credential_through_credential_manager(
    monkeypatch,
) -> None:
    row = _external_plugin_row("custom_plugin")
    plugin = ServicePluginManifest(
        service_type="custom_plugin",
        adapter_factory=_GenericCredentialAdapter,
    )
    saved: dict[str, object] = {}

    async def save_credential(**kwargs: object) -> None:
        saved.update(kwargs)

    monkeypatch.setattr("api.api.plugins._plugin_by_service_type", lambda _service_type: plugin)
    monkeypatch.setattr("api.api.plugins.write_adapter_credential", save_credential)

    async def read_credential_with_policy(**_kwargs: object) -> AdapterCredentialResult | None:
        return _credential_presence_result(True)

    monkeypatch.setattr(
        "api.api.plugins.read_adapter_credential_with_policy", read_credential_with_policy
    )
    db = _StackStormConfigDb(row)

    response = await update_plugin_credential(
        "custom_plugin",
        _SecretRequest(
            {
                "credential_type": "custom_api_key",
                "credential_payload": {"api_key": "secret"},
                "credential_key_id": "primary",
                "rotate_credential": True,
            }
        ),
        db=db,  # type: ignore[arg-type]
        _context=object(),
    )

    assert db.committed is False
    assert db.rolled_back is True
    assert saved == {
        "service_type": "custom_plugin",
        "credential_type": "custom_api_key",
        "credential_key_id": "primary",
        "payload": {"api_key": "secret"},
        "rotated": True,
    }
    assert response.credential_key_id == "primary"
    assert response.credential_configured is True


@pytest.mark.asyncio
async def test_plugin_api_rejects_stackstorm_credential_without_token(monkeypatch) -> None:
    row = _stackstorm_plugin_row()
    monkeypatch.setattr(
        "api.api.plugins._plugin_by_service_type", lambda _service_type: get_stackstorm_plugin()
    )

    with pytest.raises(HTTPException) as exc:
        await update_plugin_credential(
            "stackstorm",
            _SecretRequest({"credential_payload": {"username": "stanley"}}),
            db=_StackStormConfigDb(row),  # type: ignore[arg-type]
            _context=object(),
        )

    assert exc.value.status_code == 400
    assert "api_key" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_plugin_api_redacts_malformed_credential_payload(monkeypatch) -> None:
    row = _stackstorm_plugin_row()
    monkeypatch.setattr(
        "api.api.plugins._plugin_by_service_type", lambda _service_type: get_stackstorm_plugin()
    )

    with pytest.raises(HTTPException) as exc:
        await update_plugin_credential(
            "stackstorm",
            _SecretRequest({"credential_payload": "secret-that-must-not-echo"}),
            db=_StackStormConfigDb(row),  # type: ignore[arg-type]
            _context=object(),
        )

    assert exc.value.status_code == 422
    assert exc.value.detail == "Invalid credential request body"
    assert "secret-that-must-not-echo" not in str(exc.value.detail)


@pytest.mark.asyncio
async def test_plugin_api_updates_internal_runtime_config() -> None:
    row = ServicePlugin(
        id=1,
        service_type="timer",
        plugin_short_id="timer001",
        plugin_type="internal_plugin",
        plugin_tier="supported",
        plugin_log_key="timer",
        enabled=True,
        run_interval_seconds=10,
        query_limit=25,
        health_status="healthy",
        credential_status="not_required",
        registered_ingredient_count=0,
        registered_recipe_count=0,
        consecutive_failures=0,
        created_at=utc_now_db(),
        updated_at=utc_now_db(),
    )
    db = _Db(row)

    response = await update_service_plugin(
        "timer",
        ServicePluginUpdate(enabled=False, run_interval_seconds=22, query_limit=77),
        db=db,  # type: ignore[arg-type]
        _context=object(),
    )

    assert db.committed is True
    assert row.enabled is False
    assert row.health_status == "disabled"
    assert row.run_interval_seconds == 22
    assert row.query_limit == 77
    assert response.query_limit == 77
    assert response.config_editable is True


@pytest.mark.asyncio
async def test_plugin_api_updates_external_health_check_interval() -> None:
    row = ServicePlugin(
        id=1,
        service_type="dummy",
        plugin_short_id="dummy001",
        plugin_type="external_plugin",
        plugin_tier="supported",
        plugin_log_key="dummy",
        enabled=True,
        health_status="healthy",
        credential_status="ready",
        registered_ingredient_count=7,
        registered_recipe_count=10,
        consecutive_failures=0,
        created_at=utc_now_db(),
        updated_at=utc_now_db(),
    )
    task = ScheduledTask(
        id=5,
        task_key="plugin-health-check:dummy",
        task_type="plugin_health_check",
        service_type="dummy",
        service_exec="health_check",
        is_enabled=True,
        run_interval_seconds=30,
        next_run_at=utc_now_db(),
        status="idle",
        created_at=utc_now_db(),
        updated_at=utc_now_db(),
    )

    response = await update_service_plugin(
        "dummy",
        ServicePluginUpdate(health_check_interval_seconds=90),
        db=_Db(row, task),  # type: ignore[arg-type]
        _context=object(),
    )

    assert task.run_interval_seconds == 90
    assert row.next_health_check_at == task.next_run_at
    assert response.health_check_task_id == 5
    assert response.health_check_interval_seconds == 90
    assert response.config_editable is False


@pytest.mark.asyncio
async def test_plugin_api_disables_external_plugin_and_manifest_tasks() -> None:
    row = ServicePlugin(
        id=1,
        service_type="dummy",
        plugin_short_id="dummy001",
        plugin_type="external_plugin",
        plugin_tier="supported",
        plugin_log_key="dummy",
        enabled=True,
        health_status="healthy",
        credential_status="ready",
        registered_ingredient_count=7,
        registered_recipe_count=10,
        consecutive_failures=0,
        created_at=utc_now_db(),
        updated_at=utc_now_db(),
    )
    task = ScheduledTask(
        id=5,
        task_key="plugin-health-check:dummy",
        task_type="plugin_health_check",
        service_type="dummy",
        service_exec="health_check",
        source="plugin_manifest",
        is_enabled=True,
        run_interval_seconds=30,
        next_run_at=utc_now_db(),
        status="idle",
        created_at=utc_now_db(),
        updated_at=utc_now_db(),
    )

    response = await update_service_plugin(
        "dummy",
        ServicePluginUpdate(enabled=False),
        db=_Db(row, task),  # type: ignore[arg-type]
        _context=object(),
    )

    assert row.enabled is False
    assert row.health_status == "disabled"
    assert row.status_message == "Disabled by operator"
    assert task.is_enabled is False
    assert task.status == "disabled"
    assert task.next_run_at is None
    assert response.enabled is False
    assert response.health_check_enabled is False


@pytest.mark.asyncio
async def test_plugin_api_enables_external_plugin_and_manifest_tasks() -> None:
    row = ServicePlugin(
        id=1,
        service_type="dummy",
        plugin_short_id="dummy001",
        plugin_type="external_plugin",
        plugin_tier="supported",
        plugin_log_key="dummy",
        enabled=False,
        health_status="disabled",
        status_message="Disabled by operator",
        credential_status="ready",
        registered_ingredient_count=7,
        registered_recipe_count=10,
        consecutive_failures=0,
        created_at=utc_now_db(),
        updated_at=utc_now_db(),
    )
    task = ScheduledTask(
        id=5,
        task_key="plugin-health-check:dummy",
        task_type="plugin_health_check",
        service_type="dummy",
        service_exec="health_check",
        source="plugin_manifest",
        is_enabled=False,
        run_interval_seconds=30,
        next_run_at=None,
        status="disabled",
        created_at=utc_now_db(),
        updated_at=utc_now_db(),
    )

    response = await update_service_plugin(
        "dummy",
        ServicePluginUpdate(enabled=True),
        db=_Db(row, task),  # type: ignore[arg-type]
        _context=object(),
    )

    assert row.enabled is True
    assert row.health_status == "unknown"
    assert row.status_message is None
    assert task.is_enabled is True
    assert task.status == "idle"
    assert task.next_run_at is not None
    assert row.next_health_check_at == task.next_run_at
    assert response.enabled is True
    assert response.health_check_enabled is True


@pytest.mark.asyncio
async def test_plugin_api_rejects_external_runtime_config_update() -> None:
    row = ServicePlugin(
        service_type="dummy",
        plugin_short_id="dummy001",
        plugin_type="external_plugin",
        plugin_tier="supported",
        plugin_log_key="dummy",
        enabled=True,
        health_status="healthy",
    )

    with pytest.raises(HTTPException) as exc:
        await update_service_plugin(
            "dummy",
            ServicePluginUpdate(run_interval_seconds=22),
            db=_Db(row),  # type: ignore[arg-type]
            _context=object(),
        )

    assert exc.value.status_code == 400
