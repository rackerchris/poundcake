"""Unit tests for the Prometheus service plugin."""

from __future__ import annotations

import pytest

from api.plugins.contract import validate_payload_schema
from api.plugins.prometheus.adapter import PrometheusExecutionAdapter
from api.plugins.prometheus.helper import PrometheusAlertRuleHelper
from api.plugins.prometheus.plugin import get_plugin
from api.plugins.prometheus.templates import (
    PROMETHEUS_INGREDIENT_TEMPLATES,
    PROMETHEUS_RECIPE_TEMPLATES,
    PROMETHEUS_SCHEDULED_TASKS,
)
from api.plugins.manifest import validate_service_plugin
from api.plugins.types import ExecutionContext
from api.services import prometheus_service
from api.services.prometheus_service import PrometheusClient


class _FakePrometheusClient:
    base_url = "https://prometheus.example.test"
    verify_ssl = False
    auth_mode = "basic"
    secure_transport = True

    def validate_transport_security(self) -> str | None:
        return None

    async def health_check(self) -> dict[str, object]:
        return {"status": "healthy", "url": self.base_url, "latency_ms": 1}

    async def get_rules(self) -> list[dict[str, object]]:
        return [{"name": "DemoAlert"}]

    async def get_rule_groups(self) -> list[dict[str, object]]:
        return [{"name": "demo", "rules": []}]

    async def get_metric_names(self) -> list[str]:
        return ["up"]

    async def get_label_names(self, metric: str | None = None) -> list[str]:
        return ["job", metric or "instance"]

    async def get_label_values(self, label_name: str, metric: str | None = None) -> list[str]:
        return [f"{label_name}:{metric or 'all'}"]

    async def query(self, query: str, *, time_value: str | None = None) -> dict[str, object]:
        return {
            "success": True,
            "status": "succeeded",
            "data": {"resultType": "vector", "result": [{"query": query, "time": time_value}]},
        }

    async def range_query(
        self,
        query: str,
        *,
        start: str | None = None,
        end: str | None = None,
        step: str | int | None = None,
    ) -> dict[str, object]:
        return {
            "success": True,
            "status": "succeeded",
            "data": {
                "resultType": "matrix",
                "result": [{"query": query, "start": start, "end": end, "step": step}],
            },
        }

    async def alert_evidence(
        self,
        *,
        alert_name: str,
        query: str,
        labels: dict[str, object] | None = None,
        lookback_seconds: int = 3600,
        step_seconds: int = 60,
    ) -> dict[str, object]:
        return {
            "alert_name": alert_name,
            "query": query,
            "labels": labels or {},
            "current": await self.query(query),
            "trend": await self.range_query(query),
            "lookback_seconds": lookback_seconds,
            "step_seconds": step_seconds,
        }

    async def reload_config(self) -> dict[str, object]:
        return {"status": "success", "message": "Prometheus configuration reloaded"}


def _ctx(service_exec: str, payload: dict[str, object] | None = None) -> ExecutionContext:
    parameters = None
    if service_exec == "inspect":
        parameters = {
            "operation": "list_rules",
            "allowed_operations": [
                "alert_evidence",
                "list_rules",
                "list_rule_groups",
                "list_metrics",
                "list_labels",
                "list_label_values",
                "query",
                "range_query",
            ],
        }
    return ExecutionContext(
        service_type="prometheus",
        service_exec=service_exec,
        req_id="unit-test",
        service_payload=payload or {},
        service_exec_parameters=parameters,
    )


def test_prometheus_manifest_validates() -> None:
    plugin = get_plugin()

    assert validate_service_plugin(plugin, directory_name="prometheus") is plugin
    assert plugin.service_type == "prometheus"
    assert plugin.plugin_tier == "community"
    assert plugin.plugin_log_key is None
    assert plugin.helper_factory is not None
    assert plugin.helper_capabilities == (
        "alert_rules.parse",
        "alert_rules.index",
        "alert_rules.render",
    )


def test_prometheus_adapter_declares_optional_ecosystem_credentials() -> None:
    assert PrometheusExecutionAdapter(client=_FakePrometheusClient()).credential_requirements() == [  # type: ignore[arg-type]
        {
            "credential_type": "prometheus_http_auth",
            "credential_key_id": "default",
            "required": False,
            "usage": "Optional Prometheus API credentials for authenticated monitoring endpoints.",
        }
    ]


def test_prometheus_templates_are_valid_service_plugin_templates() -> None:
    assert {template["service_exec"] for template in PROMETHEUS_INGREDIENT_TEMPLATES} == {
        "health_check",
        "inspect",
        "reload_config",
        "watchdog",
    }
    inspect_template = next(
        template
        for template in PROMETHEUS_INGREDIENT_TEMPLATES
        if template["service_exec"] == "inspect"
    )
    assert inspect_template["service_exec_parameters"]["allowed_operations"] == [
        "alert_evidence",
        "list_rules",
        "list_rule_groups",
        "list_metrics",
        "list_labels",
        "list_label_values",
        "query",
        "range_query",
    ]
    assert {recipe["name"] for recipe in PROMETHEUS_RECIPE_TEMPLATES} == {
        "plugin-health-check:prometheus"
    }
    assert {task["task_key"] for task in PROMETHEUS_SCHEDULED_TASKS} == {
        "plugin-health-check:prometheus",
        "watchdog-heartbeat-check:prometheus",
    }
    for template in PROMETHEUS_INGREDIENT_TEMPLATES:
        assert template["service_type"] == "prometheus"
        validate_payload_schema(template["payload_schema"])


def test_prometheus_helper_extracts_and_indexes_alert_rules() -> None:
    helper = PrometheusAlertRuleHelper()
    content = """
groups:
  - name: demo
    rules:
      - alert: DemoAlert
        expr: vector(1)
"""

    assert helper.alert_names_from_content(content, path="alerts/demo.yaml") == {"DemoAlert"}
    index = helper.index_files({"alerts/demo.yaml": content})
    assert index["files_scanned"] == 1
    assert sorted(index["alerts"]) == ["DemoAlert"]


def test_prometheus_adapter_health_reports_configured_remote_url() -> None:
    health = PrometheusExecutionAdapter(client=_FakePrometheusClient()).health_check()  # type: ignore[arg-type]

    assert health.status == "healthy"
    assert health.details == {
        "mode": "prometheus-api",
        "url": "https://prometheus.example.test",
        "verify_ssl": False,
        "auth_mode": "basic",
        "secure_transport": True,
    }


def test_prometheus_adapter_validates_label_value_payload() -> None:
    error = PrometheusExecutionAdapter(client=_FakePrometheusClient()).validate(  # type: ignore[arg-type]
        ExecutionContext(
            service_type="prometheus",
            service_exec="inspect",
            req_id="unit-test",
            service_payload={},
            service_exec_parameters={
                "operation": "list_label_values",
                "allowed_operations": [
                    "list_rules",
                    "list_rule_groups",
                    "list_metrics",
                    "list_labels",
                    "list_label_values",
                    "query",
                    "range_query",
                    "alert_evidence",
                ],
            },
        )
    )

    assert error == "prometheus list_label_values requires service_payload.label_name"


def test_prometheus_adapter_validates_query_payloads() -> None:
    adapter = PrometheusExecutionAdapter(client=_FakePrometheusClient())  # type: ignore[arg-type]

    query_error = adapter.validate(
        ExecutionContext(
            service_type="prometheus",
            service_exec="inspect",
            req_id="unit-test",
            service_payload={},
            service_exec_parameters={"operation": "query"},
        )
    )
    evidence_error = adapter.validate(
        ExecutionContext(
            service_type="prometheus",
            service_exec="inspect",
            req_id="unit-test",
            service_payload={"query": "up"},
            service_exec_parameters={"operation": "alert_evidence"},
        )
    )

    assert query_error == "prometheus query requires service_payload.query"
    assert evidence_error == "prometheus alert_evidence requires service_payload.alert_name"


def test_prometheus_adapter_rejects_auth_over_insecure_remote_transport() -> None:
    client = _FakePrometheusClient()
    client.base_url = "http://prometheus.example.test"
    client.secure_transport = False

    def _security_error() -> str:
        return "Prometheus authentication requires HTTPS or an in-cluster service URL"

    client.validate_transport_security = _security_error  # type: ignore[method-assign]

    error = PrometheusExecutionAdapter(client=client).validate(_ctx("health_check"))  # type: ignore[arg-type]

    assert error == "Prometheus authentication requires HTTPS or an in-cluster service URL"


@pytest.mark.asyncio
async def test_prometheus_adapter_maps_client_result_to_execution_result() -> None:
    adapter = PrometheusExecutionAdapter(client=_FakePrometheusClient())  # type: ignore[arg-type]
    result = await adapter.dispatch(_ctx("inspect"))

    assert result.status == "succeeded"
    assert result.service_exec_id is not None
    assert result.result == {
        "success": True,
        "status": "succeeded",
        "rules": [{"name": "DemoAlert"}],
    }


@pytest.mark.asyncio
async def test_prometheus_adapter_collects_alert_evidence() -> None:
    adapter = PrometheusExecutionAdapter(client=_FakePrometheusClient())  # type: ignore[arg-type]

    result = await adapter.dispatch(
        ExecutionContext(
            service_type="prometheus",
            service_exec="inspect",
            req_id="unit-test",
            service_payload={
                "alert_name": "DemoAlert",
                "query": "up == 0",
                "labels": {"job": "demo"},
                "lookback_seconds": 600,
                "step_seconds": 30,
            },
            service_exec_parameters={"operation": "alert_evidence"},
        )
    )

    assert result.status == "succeeded"
    assert result.result == {
        "success": True,
        "status": "succeeded",
        "evidence": {
            "alert_name": "DemoAlert",
            "query": "up == 0",
            "labels": {"job": "demo"},
            "current": {
                "success": True,
                "status": "succeeded",
                "data": {
                    "resultType": "vector",
                    "result": [{"query": "up == 0", "time": None}],
                },
            },
            "trend": {
                "success": True,
                "status": "succeeded",
                "data": {
                    "resultType": "matrix",
                    "result": [{"query": "up == 0", "start": None, "end": None, "step": None}],
                },
            },
            "lookback_seconds": 600,
            "step_seconds": 30,
        },
    }


@pytest.mark.asyncio
async def test_prometheus_health_uses_api_health_endpoint_without_retries(monkeypatch) -> None:
    client = PrometheusClient()
    observed: dict[str, object] = {}

    async def _request_with_retry(method: str, url: str, **kwargs: object) -> object:
        observed.update({"method": method, "url": url, **kwargs})

        class _Response:
            status_code = 200

        return _Response()

    monkeypatch.setattr(prometheus_service, "request_with_retry", _request_with_retry)

    result = await client.health_check()

    assert result["status"] == "healthy"
    assert observed["method"] == "GET"
    assert observed["url"] == f"{client.base_url}/-/healthy"
    assert observed["retries"] == 0
    assert observed["timeout"] == 10


@pytest.mark.asyncio
async def test_prometheus_health_degrades_when_endpoint_is_unreachable(monkeypatch) -> None:
    client = PrometheusClient()

    async def _raise_connection_error(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(client, "_request", _raise_connection_error)

    result = await client.health_check()

    assert result["status"] == "degraded"
    assert result["error"] == "connection refused"
