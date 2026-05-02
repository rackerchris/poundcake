"""Unit tests for service plugin API serialization helpers."""

from __future__ import annotations

from contextlib import asynccontextmanager

from api.api.plugins import _empty_helper_metadata, _summary_from_row
from api.api.plugins import _credential_configured
from api.api.plugins import _helper_metadata
from api.api.plugins import _prometheus_rule_resource_from_crd, list_kubernetes_prometheus_rules
from api.api.plugins import get_plugin_configuration, get_plugin_health, update_plugin_configuration
from api.api.plugins import test_plugin_connection as run_plugin_connection_test
from api.api.plugins import update_plugin_credential
from api.api.plugins import update_service_plugin
from api.models.models import ScheduledTask, ServicePlugin
from api.schemas.schemas import (
    ServicePluginConnectionTestRequest,
    ServicePluginConfigurationUpdate,
    ServicePluginUpdate,
)
from api.plugins.dummy.plugin import get_plugin as get_dummy_plugin
from api.plugins.genestack_monitoring.plugin import get_plugin as get_genestack_plugin
from api.plugins.alertmanager.plugin import get_plugin as get_alertmanager_plugin
from api.plugins.prometheus.plugin import get_plugin as get_prometheus_plugin
from api.plugins.stackstorm.plugin import get_plugin as get_stackstorm_plugin
from api.plugins.base import ExecutionAdapter
from api.plugins.manifest import ServicePlugin as ServicePluginManifest
from api.plugins.types import (
    ExecutionContext,
    ExecutionResult,
    PluginHealthResult,
)
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
        "github": ["repo.list", "repo.read"],
        "k8s": ["k8s.prometheusrules.manage"],
        "prometheus": ["alert_rules.parse"],
    }
    assert metadata["missing_helper_capabilities"] == {
        "github": ["repo.list", "repo.read"],
        "k8s": ["k8s.prometheusrules.manage"],
        "prometheus": ["alert_rules.parse"],
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


class _CredentialPresenceDb:
    def __init__(self, credential: object | None) -> None:
        self.credential = credential

    async def execute(self, _statement: object) -> _Result:
        return _Result(self.credential)


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

    @asynccontextmanager
    async def credential_session():
        yield _CredentialPresenceDb(object())

    monkeypatch.setattr(
        "api.api.plugins._plugin_by_service_type", lambda _service_type: get_stackstorm_plugin()
    )
    monkeypatch.setattr("api.api.plugins.credential_manager_db_session", credential_session)

    response = await get_plugin_configuration(
        "stackstorm", db=_StackStormConfigDb(row)  # type: ignore[arg-type]
    )

    assert response.service_type == "stackstorm"
    assert response.config == row.plugin_config
    assert response.credential_key_id == "default"
    assert response.credential_configured is True


@pytest.mark.asyncio
async def test_plugin_api_updates_stackstorm_config_without_credentials(monkeypatch) -> None:
    row = _stackstorm_plugin_row()

    @asynccontextmanager
    async def credential_session():
        yield _CredentialPresenceDb(None)

    monkeypatch.setattr(
        "api.api.plugins._plugin_by_service_type", lambda _service_type: get_stackstorm_plugin()
    )
    monkeypatch.setattr("api.api.plugins.credential_manager_db_session", credential_session)
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
    assert row.plugin_config == {"url": "http://stackstorm-api:9101", "verify_ssl": False}
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

    @asynccontextmanager
    async def credential_session():
        yield _CredentialPresenceDb(None)

    monkeypatch.setattr(
        "api.api.plugins._plugin_by_service_type", lambda _service_type: plugin_factory()
    )
    monkeypatch.setattr("api.api.plugins.credential_manager_db_session", credential_session)
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
async def test_credential_configured_uses_credential_manager_session(monkeypatch) -> None:
    row = _stackstorm_plugin_row()
    captured: dict[str, object] = {}

    @asynccontextmanager
    async def credential_session():
        captured["opened"] = True
        yield _CredentialPresenceDb(object())

    monkeypatch.setattr("api.api.plugins.credential_manager_db_session", credential_session)

    configured = await _credential_configured(
        row=row,
        credential_type="stackstorm_api_key",
        credential_key_id="default",
    )

    assert configured is True
    assert captured == {"opened": True}


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

    async def list_prometheus_rules(self) -> list[dict[str, object]]:
        return [
            {
                "metadata": {
                    "name": "demo-rules",
                    "namespace": self.namespace,
                    "labels": {"release": "poundcake-prometheus"},
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
        ]


class _PrometheusRuleAdapter(ExecutionAdapter):
    service_type = "k8s"

    def __init__(self, namespace: str = "default") -> None:
        self.namespace = namespace
        self.helper = _PrometheusRuleHelper()
        self.helper.namespace = namespace

    def default_operator_config(self) -> dict[str, object]:
        return {"namespace": self.namespace}

    def normalize_operator_config(self, config: dict[str, object] | None) -> dict[str, object]:
        return {"namespace": str((config or {}).get("namespace") or self.namespace)}

    def with_operator_config(self, config: dict[str, object] | None) -> "_PrometheusRuleAdapter":
        return _PrometheusRuleAdapter(
            namespace=str((config or {}).get("namespace") or self.namespace)
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
async def test_plugin_api_test_connection_is_adapter_only(monkeypatch) -> None:
    row = _external_plugin_row("connection")
    monkeypatch.setattr(
        "api.api.plugins._plugin_by_service_type",
        lambda _service_type: ServicePluginManifest(
            service_type="connection",
            adapter_factory=lambda: _ConnectionTestAdapter(),
        ),
    )

    response = await run_plugin_connection_test(
        "connection",
        payload=ServicePluginConnectionTestRequest(config={"url": "http://configured.test"}),
        db=_StackStormConfigDb(row),  # type: ignore[arg-type]
        _context=object(),
    )

    assert response.status == "healthy"
    assert response.details["url"] == "http://configured.test"


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

    @asynccontextmanager
    async def credential_session():
        yield _CredentialPresenceDb(None)

    monkeypatch.setattr("api.api.plugins.credential_manager_db_session", credential_session)
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

    @asynccontextmanager
    async def credential_session():
        yield _CredentialPresenceDb(object())

    monkeypatch.setattr("api.api.plugins.credential_manager_db_session", credential_session)
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
