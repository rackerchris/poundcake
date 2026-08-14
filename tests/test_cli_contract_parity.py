from __future__ import annotations

import json

import httpx
import pytest
from click.testing import CliRunner

import cli.client as client_module
import cli.commands.suppressions as suppressions_module
from cli.main import cli


def _json_response(method: str, url: str, status_code: int, payload: object) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request(method, url),
    )


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _plugin_summary_payload() -> list[dict[str, object]]:
    return [
        {
            "service_type": "stackstorm",
            "plugin_short_id": "stackstorm",
            "plugin_type": "external_plugin",
            "plugin_tier": "supported",
            "plugin_log_key": "stackstorm",
            "enabled": True,
            "run_interval_seconds": None,
            "query_limit": None,
            "status_message": None,
            "config_editable": False,
            "ingredient_template_count": 3,
            "recipe_template_count": 2,
            "credential_status": "configured",
            "credential_error": None,
            "last_credential_bootstrap_at": None,
            "last_credential_rotation_at": None,
            "health_status": "healthy",
            "health_message": "ready",
            "health_error_code": None,
            "health_latency_ms": 11,
            "last_health_check_at": "2026-07-08T00:00:00+00:00",
            "next_health_check_at": "2026-07-08T00:05:00+00:00",
            "health_check_task_id": 7,
            "health_check_interval_seconds": 300,
            "health_check_enabled": True,
            "last_success_at": "2026-07-08T00:00:00+00:00",
            "consecutive_failures": 0,
            "health_check_state": "idle",
            "health_check_order_id": None,
            "health_check_started_at": None,
            "health_check_grace_until": None,
            "helper_available": True,
            "helper_capabilities": ["stackstorm.workflow"],
            "required_helper_capabilities": {"k8s": ["k8s.read"]},
            "missing_helper_capabilities": {},
        }
    ]


def _plugin_detail_payload() -> dict[str, object]:
    return {
        "id": 4,
        "service_type": "stackstorm",
        "plugin_short_id": "stackstorm",
        "plugin_type": "external_plugin",
        "plugin_tier": "supported",
        "plugin_log_key": "stackstorm",
        "enabled": True,
        "run_interval_seconds": None,
        "query_limit": None,
        "status_message": "Ready",
        "config_editable": False,
        "credential_status": "configured",
        "credential_error": None,
        "last_credential_bootstrap_at": None,
        "last_credential_rotation_at": None,
        "health_status": "healthy",
        "health_message": "ready",
        "health_error_code": None,
        "health_latency_ms": 9,
        "health_details": {"remote": "ok"},
        "capabilities_hash": "abc123",
        "registered_ingredient_count": 3,
        "registered_recipe_count": 2,
        "last_health_check_at": "2026-07-08T00:00:00+00:00",
        "next_health_check_at": "2026-07-08T00:05:00+00:00",
        "health_check_task_id": 7,
        "health_check_interval_seconds": 300,
        "health_check_enabled": True,
        "health_check_state": "idle",
        "health_check_order_id": None,
        "health_check_started_at": None,
        "health_check_grace_until": None,
        "helper_available": True,
        "helper_capabilities": ["stackstorm.workflow"],
        "required_helper_capabilities": {"k8s": ["k8s.read"]},
        "missing_helper_capabilities": {},
        "consecutive_failures": 0,
        "last_success_at": "2026-07-08T00:00:00+00:00",
        "created_at": "2026-07-08T00:00:00+00:00",
        "updated_at": "2026-07-08T00:00:00+00:00",
    }


def _plugin_configuration_payload() -> dict[str, object]:
    return {
        "service_type": "stackstorm",
        "config": {"base_url": "https://st2.example.test"},
        "config_schema": {"type": "object"},
        "credential_requirements": [{"credential_type": "stackstorm_api_key"}],
        "credential_type": "stackstorm_api_key",
        "credential_key_id": "default",
        "credential_configured": True,
        "updated_at": "2026-07-08T00:00:00+00:00",
    }


def _plugin_action_payload() -> dict[str, object]:
    return {
        "service_type": "stackstorm",
        "service_exec": "health_check",
        "status": "accepted",
        "message": "stackstorm connection check order accepted",
        "order_id": 88,
        "order_req_id": "cli-plugin-action",
        "submitted_at": "2026-07-08T00:00:00+00:00",
    }


def _prometheus_rules_payload() -> dict[str, object]:
    return {
        "service_type": "k8s",
        "namespace": "monitoring",
        "items": [
            {
                "name": "api-rules",
                "namespace": "monitoring",
                "labels": {},
                "annotations": {},
                "groups": [],
                "group_count": 2,
                "rule_count": 6,
                "alert_count": 4,
                "recording_count": 2,
                "raw": {},
            }
        ],
        "resource_count": 1,
        "group_count": 2,
        "rule_count": 6,
        "alert_count": 4,
        "recording_count": 2,
        "checked_at": "2026-07-08T00:00:00+00:00",
    }


def _prometheus_rule_detail_payload() -> dict[str, object]:
    payload = _prometheus_rules_payload()["items"][0]
    return {
        "service_type": "k8s",
        **payload,
        "checked_at": "2026-07-08T00:00:00+00:00",
    }


def _prometheus_rule_record_payload() -> dict[str, object]:
    return {
        "service_type": "k8s",
        "namespace": "monitoring",
        "crd_name": "api-rules",
        "group_name": "demo",
        "rule_name": "DemoAlert",
        "rule_kind": "alert",
        "source": {"file": "alerts/demo.yaml", "format": "spec.groups"},
        "rule_data": {"alert": "DemoAlert", "expr": "vector(1)"},
        "checked_at": "2026-07-08T00:00:00+00:00",
    }


def _scheduled_task_payload() -> dict[str, object]:
    return {
        "id": 12,
        "task_key": "stackstorm-health",
        "task_type": "service_execution",
        "service_type": "stackstorm",
        "service_exec": "health_check",
        "source": "plugin_manifest",
        "is_enabled": True,
        "run_interval_seconds": 300,
        "next_run_at": "2026-07-08T00:05:00+00:00",
        "priority": 25,
        "timeout_seconds": 120,
        "task_payload": {"workflow_ref": "packs.health"},
        "task_parameters": {
            "operation": "health_check",
            "operation_metadata": {
                "health_check": {
                    "label": "Health check",
                    "description": "Check plugin readiness.",
                }
            },
        },
        "expected_outcome": {"status": "succeeded"},
        "status": "idle",
        "last_status": "succeeded",
        "last_message": "Ready",
        "last_order_id": 88,
        "last_order_req_id": "req-88",
        "last_started_at": "2026-07-08T00:00:00+00:00",
        "last_completed_at": "2026-07-08T00:01:00+00:00",
        "consecutive_failures": 0,
        "created_at": "2026-07-08T00:00:00+00:00",
        "updated_at": "2026-07-08T00:00:00+00:00",
    }


def _scheduled_task_status_payload() -> dict[str, object]:
    return {
        "id": 12,
        "task_key": "stackstorm-health",
        "task_type": "service_execution",
        "service_type": "stackstorm",
        "service_exec": "health_check",
        "source": "plugin_manifest",
        "is_enabled": True,
        "run_interval_seconds": 300,
        "next_run_at": "2026-07-08T00:05:00+00:00",
        "priority": 25,
        "timeout_seconds": 120,
        "status": "queued",
        "last_status": "succeeded",
        "last_message": "Run requested by operator",
        "last_order_id": 88,
        "last_order_req_id": "req-88",
        "last_started_at": "2026-07-08T00:00:00+00:00",
        "last_completed_at": "2026-07-08T00:01:00+00:00",
        "consecutive_failures": 0,
        "run_now_label": "Health check",
        "run_now_description": "Request Dishwasher to run this plugin health check now.",
        "created_at": "2026-07-08T00:00:00+00:00",
        "updated_at": "2026-07-08T00:00:00+00:00",
    }


def _ingredient_payload() -> dict[str, object]:
    return {
        "id": 9,
        "service_type": "stackstorm",
        "service_exec": "workflow",
        "destination_target": "",
        "task_key_template": "stackstorm-health",
        "service_payload_template": {"workflow_ref": "packs.health"},
        "payload_schema": {"type": "object", "required": ["workflow_ref"]},
        "service_exec_parameters": {
            "operation": "health_check",
            "allowed_operations": ["health_check"],
            "operation_metadata": {
                "health_check": {
                    "label": "Health check",
                    "description": "Check plugin readiness.",
                }
            },
        },
        "default_expected_secs": 30,
        "default_timeout": 120,
        "service_exec_expected_outcome_default": {"status": "succeeded"},
        "ingredient_purpose": "utility",
        "is_active": True,
        "is_blocking": True,
        "retry_count": 0,
        "retry_delay": 5,
        "on_failure": "stop",
        "created_at": "2026-07-08T00:00:00+00:00",
        "updated_at": "2026-07-08T00:00:00+00:00",
        "deleted": False,
        "deleted_at": None,
    }


def _health_payload(status: str = "healthy") -> dict[str, object]:
    return {
        "status": status,
        "version": "2026.07.13",
        "instance_id": "poundcake-0",
        "timestamp": "2026-07-08T00:00:00+00:00",
        "components": {
            "database": {
                "status": status,
                "message": "Connected",
            }
        },
    }


def _settings_payload() -> dict[str, object]:
    return {
        "auth_enabled": True,
        "rbac_enabled": True,
        "auth_providers": [
            {
                "name": "local",
                "label": "Local",
                "login_mode": "password",
                "cli_login_mode": "password",
                "browser_login": False,
                "device_login": False,
                "password_login": True,
            }
        ],
        "prometheus_use_crds": True,
        "prometheus_crd_namespace": "monitoring",
        "prometheus_url": "http://prometheus.monitoring.svc",
        "git_provider": "github",
        "git_repo_url": "https://example.test/repo.git",
        "git_branch": "main",
        "git_rules_path": "alerts",
        "git_workflows_path": "recipes",
        "git_actions_path": "ingredients",
        "version": "2026.07.13",
        "global_communications_configured": True,
    }


def _recipe_status_payload() -> dict[str, object]:
    return {
        "id": 5,
        "name": "demo-recipe",
        "description": "Demo recipe",
        "enabled": True,
        "clear_timeout_sec": 300,
        "can_execute": True,
        "inactive_ingredient_count": 0,
        "step_count": 2,
        "communication_route_count": 1,
        "created_at": "2026-07-08T00:00:00+00:00",
        "updated_at": "2026-07-08T00:00:00+00:00",
    }


def _recipe_ingredient_status_payload() -> dict[str, object]:
    return {
        "id": 21,
        "recipe_id": 5,
        "ingredient_id": 9,
        "step_order": 1,
        "on_success": "continue",
        "parallel_group": 0,
        "depth": 0,
        "run_phase": "both",
        "run_condition": "always",
        "service_type": "stackstorm",
        "service_exec": "workflow",
        "task_key_template": "stackstorm-health",
        "ingredient_purpose": "utility",
        "ingredient_is_active": True,
        "ingredient_is_blocking": True,
        "expected_secs": 30,
        "timeout_secs": 120,
    }


def _recipe_detail_minimal_payload() -> dict[str, object]:
    return {
        "id": 5,
        "name": "demo-recipe",
        "description": "Demo recipe",
        "enabled": True,
        "clear_timeout_sec": 300,
        "created_at": "2026-07-08T00:00:00+00:00",
        "updated_at": "2026-07-08T00:00:00+00:00",
        "deleted": False,
        "deleted_at": None,
        "recipe_ingredients": [],
        "communications": {
            "mode": "inherit",
            "effective_source": "global_policy",
            "routes": [],
        },
        "can_execute": True,
        "inactive_ingredient_ids": [],
    }


def _ingredient_status_payload() -> dict[str, object]:
    return {
        "id": 9,
        "service_type": "stackstorm",
        "service_exec": "workflow",
        "destination_target": "",
        "task_key_template": "stackstorm-health",
        "ingredient_purpose": "utility",
        "is_active": True,
        "is_blocking": True,
        "default_expected_secs": 30,
        "default_timeout": 120,
        "retry_count": 0,
        "retry_delay": 5,
        "on_failure": "stop",
        "created_at": "2026-07-08T00:00:00+00:00",
        "updated_at": "2026-07-08T00:00:00+00:00",
    }


def _suppression_status_payload() -> dict[str, object]:
    return {
        "id": 42,
        "name": "demo-suppression",
        "reason": "maintenance",
        "scope": "matchers",
        "status": "active",
        "enabled": True,
        "starts_at": "2026-07-08T00:00:00+00:00",
        "ends_at": "2026-07-08T01:00:00+00:00",
        "canceled_at": None,
        "source": "plugin",
        "source_service_type": "alertmanager",
        "source_ref": "sil-42",
        "last_synced_at": "2026-07-08T00:00:00+00:00",
        "created_at": "2026-07-08T00:00:00+00:00",
        "updated_at": "2026-07-08T00:00:00+00:00",
    }


def _suppression_stats_payload() -> dict[str, object]:
    return {
        "suppression_id": 42,
        "total_suppressed": 3,
        "by_alertname": {"DemoAlert": 3},
        "by_severity": {"warning": 3},
        "first_seen_at": "2026-07-08T00:00:00+00:00",
        "last_seen_at": "2026-07-08T00:30:00+00:00",
    }


def _suppression_detail_payload() -> dict[str, object]:
    return {
        "id": 42,
        "name": "demo-suppression",
        "reason": "maintenance",
        "scope": "matchers",
        "status": "active",
        "enabled": True,
        "starts_at": "2026-07-08T00:00:00+00:00",
        "ends_at": "2026-07-08T01:00:00+00:00",
        "canceled_at": None,
        "created_by": "alice",
        "summary_ticket_enabled": True,
        "source": "plugin",
        "source_service_type": "alertmanager",
        "source_ref": "sil-42",
        "source_payload": {"id": "sil-42"},
        "last_synced_at": "2026-07-08T00:00:00+00:00",
        "created_at": "2026-07-08T00:00:00+00:00",
        "updated_at": "2026-07-08T00:00:00+00:00",
        "matchers": [
            {
                "label_key": "alertname",
                "operator": "eq",
                "value": "DemoAlert",
            }
        ],
        "summary": None,
        "counters": _suppression_stats_payload(),
    }


def _suppression_response_payload() -> dict[str, object]:
    payload = _suppression_detail_payload().copy()
    payload.pop("summary", None)
    payload.pop("counters", None)
    return payload


def _order_timeline_payload() -> dict[str, object]:
    return {
        "order": {
            "id": 88,
            "req_id": "req-88",
            "order_type": "webhook_alert",
            "alert_status": "firing",
            "alert_group_name": "NodeDown",
            "processing_status": "processing",
            "is_active": True,
            "remediation_outcome": "pending",
            "clear_timeout_sec": 300,
            "clear_deadline_at": "2026-07-08T00:05:00+00:00",
            "clear_timed_out_at": None,
            "auto_close_eligible": False,
            "severity": "critical",
            "instance": "compute-1",
            "correlation_key": "node:compute-1",
            "counter": 1,
            "starts_at": "2026-07-08T00:00:00+00:00",
            "ends_at": None,
            "order_lifetime_secs": None,
            "communication_route_count": 1,
            "created_at": "2026-07-08T00:00:00+00:00",
            "updated_at": "2026-07-08T00:00:00+00:00",
            "labels": {
                "instance": "compute-1",
                "cluster": "region-a",
            },
        },
        "events": [],
    }


def _observability_activity_payload() -> dict[str, object]:
    return {
        "type": "order",
        "status": "processing",
        "title": "DemoAlert",
        "summary": "firing | warning",
        "timestamp": "2026-07-08T00:00:00+00:00",
        "target_kind": "order",
        "target_id": "88",
        "link_hint": "/orders/88",
        "metadata": {"severity": "warning"},
    }


def _communication_activity_payload() -> dict[str, object]:
    return {
        "communication_id": "comm-1",
        "reference_type": "order",
        "reference_id": "88",
        "reference_name": "DemoAlert",
        "channel": "slack",
        "destination": "#ops",
        "ticket_id": "INC-1",
        "provider_reference_id": "provider-1",
        "operation_id": "op-1",
        "lifecycle_state": "sent",
        "remote_state": "delivered",
        "last_error": None,
        "writable": True,
        "reopenable": False,
        "updated_at": "2026-07-08T00:00:00+00:00",
    }


def _dish_ingredient_payload() -> dict[str, object]:
    return {
        "id": 301,
        "req_id": "req-88",
        "dish_id": 7,
        "recipe_ingredient_id": 21,
        "service_exec_id": "exec-301",
        "task_key": "stackstorm-health",
        "step_order": 1,
        "parallel_group": 0,
        "depth": 0,
        "service_type": "stackstorm",
        "service_exec": "workflow",
        "destination_target": "",
        "service_payload": {"workflow_ref": "packs.health"},
        "service_exec_parameters": {"operation": "health_check"},
        "service_exec_expected_secs": 30,
        "service_exec_timeout": 120,
        "service_exec_expected_outcome": {"status": "succeeded"},
        "retry_count": 0,
        "retry_delay": 5,
        "on_failure": "stop",
        "service_exec_status": "succeeded",
        "attempt": 1,
        "service_exec_start_time": "2026-07-08T00:00:00+00:00",
        "service_exec_completed_time": "2026-07-08T00:00:10+00:00",
        "service_exec_canceled_time": None,
        "service_exec_run_time": 10,
        "service_exec_sla_exceeded": False,
        "service_exec_claimed_at": None,
        "service_exec_claimed_by": None,
        "service_exec_actual_outcome": {"status": "succeeded"},
        "service_exec_error": None,
        "deleted": False,
        "deleted_at": None,
        "created_at": "2026-07-08T00:00:00+00:00",
        "updated_at": "2026-07-08T00:00:10+00:00",
    }


def test_plugins_list_uses_canonical_command(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_request_with_retry_sync(**kwargs: object) -> httpx.Response:
        assert kwargs["cookies"] == {"session_token": "session-123"}
        return _json_response(
            "GET",
            "http://example.test/api/v1/plugins",
            200,
            _plugin_summary_payload(),
        )

    monkeypatch.setattr(client_module, "request_with_retry_sync", fake_request_with_retry_sync)
    result = runner.invoke(
        cli,
        ["--url", "http://example.test", "--token", "session-123", "--format", "json", "plugins", "list"],
    )

    assert result.exit_code == 0
    output = json.loads(result.output)
    assert output[0]["service_type"] == "stackstorm"
    assert output[0]["helper_capabilities"] == ["stackstorm.workflow"]


def test_plugins_show_table_surfaces_helper_and_health_check_metadata(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_request_with_retry_sync(**kwargs: object) -> httpx.Response:
        return _json_response(
            "GET",
            "http://example.test/api/v1/plugins/stackstorm",
            200,
            _plugin_detail_payload(),
        )

    monkeypatch.setattr(client_module, "request_with_retry_sync", fake_request_with_retry_sync)
    result = runner.invoke(
        cli,
        ["--url", "http://example.test", "--token", "session-123", "plugins", "show", "stackstorm"],
    )

    assert result.exit_code == 0
    assert "Helper Capabilities" in result.output
    assert "stackstorm.workflow" in result.output
    assert "health_check_interval_seconds" in result.output


def test_plugins_update_and_config_set_send_typed_payloads(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, object | None]] = []

    def fake_request_with_retry_sync(**kwargs: object) -> httpx.Response:
        method = str(kwargs["method"])
        url = str(kwargs["url"])
        calls.append((method, url, kwargs.get("json")))
        if url.endswith("/api/v1/plugins/stackstorm") and method == "PATCH":
            assert kwargs["json"] == {"enabled": False, "status_message": "paused"}
            return _json_response(method, url, 200, _plugin_detail_payload() | {"enabled": False})
        if url.endswith("/api/v1/plugins/stackstorm/configuration") and method == "PUT":
            assert kwargs["json"] == {"config": {"base_url": "https://st2.example.test"}}
            return _json_response(method, url, 200, _plugin_configuration_payload())
        raise AssertionError(f"Unexpected request {method} {url}")

    monkeypatch.setattr(client_module, "request_with_retry_sync", fake_request_with_retry_sync)

    result_update = runner.invoke(
        cli,
        [
            "--url",
            "http://example.test",
            "--token",
            "session-123",
            "--format",
            "json",
            "plugins",
            "update",
            "stackstorm",
            "--disabled",
            "--status-message",
            "paused",
        ],
    )
    result_config = runner.invoke(
        cli,
        [
            "--url",
            "http://example.test",
            "--token",
            "session-123",
            "--format",
            "json",
            "plugins",
            "config",
            "set",
            "stackstorm",
            "--config-json",
            '{"base_url":"https://st2.example.test"}',
        ],
    )

    assert result_update.exit_code == 0
    assert result_config.exit_code == 0
    assert len(calls) == 2


def test_plugins_credentials_test_connection_and_prometheus_rules(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_request_with_retry_sync(**kwargs: object) -> httpx.Response:
        method = str(kwargs["method"])
        url = str(kwargs["url"])
        if url.endswith("/api/v1/plugins/stackstorm/credentials"):
            assert kwargs["json"] == {
                "credential_type": "stackstorm_api_key",
                "credential_key_id": "default",
                "credential_payload": {"api_key": "secret"},
                "rotate_credential": True,
            }
            return _json_response(method, url, 200, _plugin_configuration_payload())
        if url.endswith("/api/v1/plugins/stackstorm/test-connection"):
            assert kwargs["json"] == {"credential_key_id": "default"}
            return _json_response(method, url, 202, _plugin_action_payload())
        if url.endswith("/api/v1/plugins/prometheus/reload"):
            return _json_response(
                method,
                url,
                202,
                {
                    **_plugin_action_payload(),
                    "service_type": "prometheus",
                    "service_exec": "reload_config",
                    "message": "prometheus reload order accepted",
                },
            )
        if url.endswith("/api/v1/plugins/k8s/prometheus-rules"):
            assert kwargs["params"] == {"namespace": "monitoring"}
            return _json_response(method, url, 200, _prometheus_rules_payload())
        if url.endswith("/api/v1/plugins/k8s/prometheus-rules/api-rules"):
            assert kwargs["params"] == {"namespace": "monitoring"}
            return _json_response(method, url, 200, _prometheus_rule_detail_payload())
        if url.endswith("/api/v1/plugins/k8s/prometheus-rules/api-rules/rules/DemoAlert"):
            if method == "GET":
                params = kwargs.get("params") or {}
                assert params["group_name"] == "demo"
                return _json_response(method, url, 200, _prometheus_rule_record_payload())
            assert kwargs["json"] == {
                "group_name": "demo",
                "rule_data": {"alert": "DemoAlert", "expr": "vector(2)"},
            }
            return _json_response(
                method,
                url,
                200,
                {
                    **_prometheus_rule_record_payload(),
                    "rule_data": {"alert": "DemoAlert", "expr": "vector(2)"},
                },
            )
        if url.endswith("/api/v1/plugins/k8s/prometheus-rules/api-rules/rules"):
            assert kwargs["json"] == {
                "group_name": "demo",
                "rule_name": "NewAlert",
                "rule_data": {"alert": "NewAlert", "expr": "vector(1)"},
            }
            return _json_response(
                method,
                url,
                200,
                {
                    **_prometheus_rule_record_payload(),
                    "rule_name": "NewAlert",
                    "rule_data": {"alert": "NewAlert", "expr": "vector(1)"},
                },
            )
        if url.endswith("/api/v1/plugins/genestack_monitoring/export-alert-updates"):
            assert kwargs["json"] == {
                "namespace": "monitoring",
                "crd_name": "api-rules",
                "group_name": "demo",
                "rule_name": "DemoAlert",
            }
            return _json_response(
                method,
                url,
                202,
                {
                    **_plugin_action_payload(),
                    "service_type": "genestack_monitoring",
                    "service_exec": "repo_sync",
                    "message": "Genestack alert export order accepted",
                },
            )
        raise AssertionError(f"Unexpected request {method} {url}")

    monkeypatch.setattr(client_module, "request_with_retry_sync", fake_request_with_retry_sync)

    result_credentials = runner.invoke(
        cli,
        [
            "--url",
            "http://example.test",
            "--token",
            "session-123",
            "--format",
            "json",
            "plugins",
            "credentials",
            "set",
            "stackstorm",
            "--credential-type",
            "stackstorm_api_key",
            "--payload-json",
            '{"api_key":"secret"}',
            "--rotate-credential",
        ],
    )
    result_connection = runner.invoke(
        cli,
        [
            "--url",
            "http://example.test",
            "--token",
            "session-123",
            "--format",
            "json",
            "plugins",
            "test-connection",
            "stackstorm",
        ],
    )
    result_rules = runner.invoke(
        cli,
        [
            "--url",
            "http://example.test",
            "--token",
            "session-123",
            "--format",
            "json",
            "plugins",
            "k8s",
            "prometheus-rules",
            "--namespace",
            "monitoring",
        ],
    )
    result_prometheus_reload = runner.invoke(
        cli,
        [
            "--url",
            "http://example.test",
            "--token",
            "session-123",
            "--format",
            "json",
            "plugins",
            "prometheus",
            "reload",
        ],
    )
    result_rule = runner.invoke(
        cli,
        [
            "--url",
            "http://example.test",
            "--token",
            "session-123",
            "--format",
            "json",
            "plugins",
            "k8s",
            "prometheus-rule",
            "--crd-name",
            "api-rules",
            "--namespace",
            "monitoring",
        ],
    )
    result_rule_show = runner.invoke(
        cli,
        [
            "--url",
            "http://example.test",
            "--token",
            "session-123",
            "--format",
            "json",
            "plugins",
            "k8s",
            "rule",
            "show",
            "--crd-name",
            "api-rules",
            "--group-name",
            "demo",
            "--rule-name",
            "DemoAlert",
            "--namespace",
            "monitoring",
        ],
    )
    result_rule_set = runner.invoke(
        cli,
        [
            "--url",
            "http://example.test",
            "--token",
            "session-123",
            "--format",
            "json",
            "plugins",
            "k8s",
            "rule",
            "set",
            "--crd-name",
            "api-rules",
            "--group-name",
            "demo",
            "--rule-name",
            "DemoAlert",
            "--rule-json",
            '{"alert":"DemoAlert","expr":"vector(2)"}',
            "--namespace",
            "monitoring",
        ],
    )
    result_rule_add = runner.invoke(
        cli,
        [
            "--url",
            "http://example.test",
            "--token",
            "session-123",
            "--format",
            "json",
            "plugins",
            "k8s",
            "rule",
            "add",
            "--crd-name",
            "api-rules",
            "--group-name",
            "demo",
            "--rule-name",
            "NewAlert",
            "--rule-json",
            '{"alert":"NewAlert","expr":"vector(1)"}',
            "--namespace",
            "monitoring",
        ],
    )
    result_export = runner.invoke(
        cli,
        [
            "--url",
            "http://example.test",
            "--token",
            "session-123",
            "--format",
            "json",
            "plugins",
            "genestack-monitoring",
            "export-alert-updates",
            "--crd-name",
            "api-rules",
            "--group-name",
            "demo",
            "--rule-name",
            "DemoAlert",
            "--namespace",
            "monitoring",
        ],
    )

    assert result_credentials.exit_code == 0
    assert result_connection.exit_code == 0
    assert result_prometheus_reload.exit_code == 0
    assert result_rules.exit_code == 0
    assert result_rule.exit_code == 0
    assert result_rule_show.exit_code == 0
    assert result_rule_set.exit_code == 0
    assert result_rule_add.exit_code == 0
    assert result_export.exit_code == 0


def test_cli_dry_run_mutation_commands_do_not_send_requests(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_request(**kwargs: object) -> httpx.Response:
        method = str(kwargs["method"])
        url = str(kwargs["url"])
        if url.endswith("/api/v1/service-registry/ingredients/1") and method == "GET":
            return _json_response(method, url, 200, _ingredient_payload() | {"id": 1})
        if url.endswith("/api/v1/communications/policy") and method == "GET":
            return _json_response(
                method,
                url,
                200,
                {
                    "configured": True,
                    "routes": [],
                    "available_routes": [],
                    "lifecycle_summary": {},
                },
            )
        if url.endswith("/api/v1/plugins/stackstorm/configuration") and method == "GET":
            return _json_response(method, url, 200, _plugin_configuration_payload())
        raise AssertionError(f"Unexpected network call during dry-run: {kwargs}")

    monkeypatch.setattr(client_module, "request_with_retry_sync", fail_request)

    result_recipe = runner.invoke(
        cli,
        [
            "--url",
            "http://example.test",
            "--token",
            "session-123",
            "recipes",
            "create",
            "--name",
            "demo-recipe",
            "--step-json",
            '{"ingredient_id":1}',
            "--dry-run",
        ],
    )
    result_policy = runner.invoke(
        cli,
        [
            "--url",
            "http://example.test",
            "--token",
            "session-123",
            "comm-policy",
            "set",
            "--route-json",
            '{"label":"Primary","service_type":"github","enabled":true}',
            "--dry-run",
        ],
    )
    result_plugin = runner.invoke(
        cli,
        [
            "--url",
            "http://example.test",
            "--token",
            "session-123",
            "plugins",
            "credentials",
            "set",
            "stackstorm",
            "--credential-type",
            "stackstorm_api_key",
            "--payload-json",
            '{"api_key":"secret"}',
            "--dry-run",
        ],
    )

    assert result_recipe.exit_code == 0
    assert "Dry Run" in result_recipe.output
    assert "Changes" in result_recipe.output
    assert "demo-recipe" in result_recipe.output
    assert result_policy.exit_code == 0
    assert "global communication policy" in result_policy.output
    assert "Before" not in result_policy.output
    assert "Routes" in result_policy.output
    assert result_plugin.exit_code == 0
    assert "[redacted]" in result_plugin.output
    assert "Credential fields" in result_plugin.output


def test_scheduled_tasks_create_and_update_send_typed_payloads(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, object | None]] = []

    def fake_request_with_retry_sync(**kwargs: object) -> httpx.Response:
        method = str(kwargs["method"])
        url = str(kwargs["url"])
        calls.append((method, url, kwargs.get("json")))
        if url.endswith("/api/v1/scheduled-tasks") and method == "POST":
            assert kwargs["json"] == {
                "task_key": "stackstorm-health",
                "task_type": "service_execution",
                "service_type": "stackstorm",
                "service_exec": "health_check",
                "run_interval_seconds": 300,
            }
            return _json_response(method, url, 200, _scheduled_task_payload())
        if url.endswith("/api/v1/scheduled-tasks/12") and method == "PATCH":
            assert kwargs["json"] == {"run_interval_seconds": 600}
            return _json_response(
                method,
                url,
                200,
                _scheduled_task_payload() | {"run_interval_seconds": 600},
            )
        raise AssertionError(f"Unexpected request {method} {url}")

    monkeypatch.setattr(client_module, "request_with_retry_sync", fake_request_with_retry_sync)

    result_create = runner.invoke(
        cli,
        [
            "--url",
            "http://example.test",
            "--token",
            "session-123",
            "--format",
            "json",
            "scheduled-tasks",
            "create",
            "--task-key",
            "stackstorm-health",
            "--task-type",
            "service_execution",
            "--service-type",
            "stackstorm",
            "--service-exec",
            "health_check",
            "--run-interval-seconds",
            "300",
        ],
    )
    result_update = runner.invoke(
        cli,
        [
            "--url",
            "http://example.test",
            "--token",
            "session-123",
            "--format",
            "json",
            "scheduled-tasks",
            "update",
            "12",
            "--run-interval-seconds",
            "600",
        ],
    )

    assert result_create.exit_code == 0
    assert result_update.exit_code == 0
    assert len(calls) == 2


def test_scheduled_tasks_and_auth_binding_dry_run(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_request(**kwargs: object) -> httpx.Response:
        method = str(kwargs["method"])
        url = str(kwargs["url"])
        if url.endswith("/api/v1/scheduled-tasks/12") and method == "GET":
            return _json_response(method, url, 200, _scheduled_task_payload())
        raise AssertionError(f"Unexpected network call during dry-run: {kwargs}")

    monkeypatch.setattr(client_module, "request_with_retry_sync", fail_request)

    result_task = runner.invoke(
        cli,
        [
            "--url",
            "http://example.test",
            "--token",
            "session-123",
            "scheduled-tasks",
            "update",
            "12",
            "--run-interval-seconds",
            "600",
            "--dry-run",
        ],
    )
    result_binding = runner.invoke(
        cli,
        [
            "--url",
            "http://example.test",
            "--token",
            "session-123",
            "auth",
            "bindings",
            "create",
            "--provider",
            "auth0",
            "--type",
            "group",
            "--role",
            "operator",
            "--group",
            "monitoring-operators",
            "--dry-run",
        ],
    )

    assert result_task.exit_code == 0
    assert "task 12" in result_task.output
    assert "Run interval (sec)" in result_task.output
    assert result_binding.exit_code == 0
    assert "monitoring-operators" in result_binding.output


def test_scheduled_tasks_status_show_run_now_and_delete(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_request_with_retry_sync(**kwargs: object) -> httpx.Response:
        method = str(kwargs["method"])
        url = str(kwargs["url"])
        if url.endswith("/api/v1/scheduled-tasks/status"):
            return _json_response(method, url, 200, [_scheduled_task_status_payload()])
        if url.endswith("/api/v1/scheduled-tasks/12") and method == "GET":
            return _json_response(method, url, 200, _scheduled_task_payload())
        if url.endswith("/api/v1/scheduled-tasks/12/run-now"):
            return _json_response(method, url, 200, _scheduled_task_status_payload())
        if url.endswith("/api/v1/scheduled-tasks/12") and method == "DELETE":
            return _json_response(method, url, 200, _scheduled_task_payload() | {"is_enabled": False})
        raise AssertionError(f"Unexpected request {method} {url}")

    monkeypatch.setattr(client_module, "request_with_retry_sync", fake_request_with_retry_sync)

    result_status = runner.invoke(
        cli,
        [
            "--url",
            "http://example.test",
            "--token",
            "session-123",
            "scheduled-tasks",
            "status",
        ],
    )
    result_show = runner.invoke(
        cli,
        ["--url", "http://example.test", "--token", "session-123", "scheduled-tasks", "show", "12"],
    )
    result_run_now = runner.invoke(
        cli,
        ["--url", "http://example.test", "--token", "session-123", "scheduled-tasks", "run-now", "12"],
    )
    result_delete = runner.invoke(
        cli,
        ["--url", "http://example.test", "--token", "session-123", "scheduled-tasks", "delete", "12"],
    )

    assert result_status.exit_code == 0
    assert "stackstorm/health_check" in result_status.output
    assert result_show.exit_code == 0
    assert "Task Parameters" in result_show.output
    assert result_run_now.exit_code == 0
    assert "Run requested by operator" in result_run_now.output
    assert result_delete.exit_code == 0


def test_ingredients_show_table_surfaces_contract_fields(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_request_with_retry_sync(**kwargs: object) -> httpx.Response:
        return _json_response(
            "GET",
            "http://example.test/api/v1/service-registry/ingredients/9",
            200,
            _ingredient_payload(),
        )

    monkeypatch.setattr(client_module, "request_with_retry_sync", fake_request_with_retry_sync)
    result = runner.invoke(
        cli,
        ["--url", "http://example.test", "--token", "session-123", "ingredients", "show", "9"],
    )

    assert result.exit_code == 0
    assert "Execution Contract" in result.output
    assert "Payload Schema" in result.output
    assert "service_exec_expected_outcome_default" in result.output


def test_health_and_settings_commands_use_distinct_routes(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_request_with_retry_sync(**kwargs: object) -> httpx.Response:
        method = str(kwargs["method"])
        url = str(kwargs["url"])
        if url.endswith("/api/v1/health"):
            return _json_response(method, url, 200, _health_payload("healthy"))
        if url.endswith("/api/v1/health/status"):
            return _json_response(method, url, 200, _health_payload("degraded"))
        if url.endswith("/api/v1/settings"):
            return _json_response(method, url, 200, _settings_payload())
        raise AssertionError(f"Unexpected request {method} {url}")

    def fake_httpx_get(url: str, timeout: float) -> httpx.Response:
        assert url == "http://example.test/api/v1/ready"
        assert timeout == 10.0
        return _json_response("GET", url, 200, _health_payload("healthy"))

    monkeypatch.setattr(client_module, "request_with_retry_sync", fake_request_with_retry_sync)
    monkeypatch.setattr(client_module.httpx, "get", fake_httpx_get)

    result_ready = runner.invoke(
        cli,
        ["--url", "http://example.test", "--format", "json", "ready"],
    )
    result_health = runner.invoke(
        cli,
        ["--url", "http://example.test", "--token", "session-123", "--format", "json", "health"],
    )
    result_health_status = runner.invoke(
        cli,
        [
            "--url",
            "http://example.test",
            "--token",
            "session-123",
            "--format",
            "json",
            "health-status",
        ],
    )
    result_settings = runner.invoke(
        cli,
        [
            "--url",
            "http://example.test",
            "--token",
            "session-123",
            "settings",
            "show",
        ],
    )

    assert result_ready.exit_code == 0
    assert json.loads(result_ready.output)["status"] == "healthy"
    assert result_health.exit_code == 0
    assert json.loads(result_health.output)["status"] == "healthy"
    assert result_health_status.exit_code == 0
    assert json.loads(result_health_status.output)["status"] == "degraded"
    assert result_settings.exit_code == 0
    assert "Auth Providers" in result_settings.output
    assert "global_communications_configured" in result_settings.output


def test_extended_operator_routes_have_typed_cli_commands(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_request_with_retry_sync(**kwargs: object) -> httpx.Response:
        method = str(kwargs["method"])
        url = str(kwargs["url"])
        if url.endswith("/api/v1/recipes/status"):
            return _json_response(method, url, 200, [_recipe_status_payload()])
        if url.endswith("/api/v1/recipes/5/status"):
            return _json_response(method, url, 200, _recipe_status_payload())
        if url.endswith("/api/v1/recipes/5/ingredient-status"):
            return _json_response(method, url, 200, [_recipe_ingredient_status_payload()])
        if url.endswith("/api/v1/recipes/by-name/demo-recipe"):
            return _json_response(method, url, 200, _recipe_detail_minimal_payload())
        if url.endswith("/api/v1/service-registry/ingredients/status"):
            return _json_response(method, url, 200, [_ingredient_status_payload()])
        if url.endswith("/api/v1/suppressions/status"):
            return _json_response(method, url, 200, [_suppression_status_payload()])
        if url.endswith("/api/v1/suppressions/42/stats"):
            return _json_response(method, url, 200, _suppression_stats_payload())
        if url.endswith("/api/v1/suppressions/42") and method == "PATCH":
            assert kwargs["json"] == {"reason": "updated"}
            return _json_response(
                method,
                url,
                200,
                _suppression_response_payload() | {"reason": "updated"},
            )
        if url.endswith("/api/v1/observability/activity"):
            return _json_response(method, url, 200, [_observability_activity_payload()])
        if url.endswith("/api/v1/communications/activity"):
            return _json_response(method, url, 200, [_communication_activity_payload()])
        if url.endswith("/api/v1/orders/88/execution-history"):
            return _json_response(method, url, 200, [_dish_ingredient_payload()])
        if url.endswith("/api/v1/dishes/7/ingredients"):
            return _json_response(method, url, 200, [_dish_ingredient_payload()])
        if url.endswith("/api/v1/dishes/7/ingredient-history"):
            return _json_response(method, url, 200, [_dish_ingredient_payload()])
        raise AssertionError(f"Unexpected request {method} {url}")

    monkeypatch.setattr(client_module, "request_with_retry_sync", fake_request_with_retry_sync)

    result_recipe_status = runner.invoke(
        cli,
        ["--url", "http://example.test", "--token", "session-123", "recipes", "status"],
    )
    result_recipe_status_show = runner.invoke(
        cli,
        ["--url", "http://example.test", "--token", "session-123", "recipes", "status-show", "5"],
    )
    result_recipe_ingredient_status = runner.invoke(
        cli,
        ["--url", "http://example.test", "--token", "session-123", "recipes", "ingredient-status", "5"],
    )
    result_recipe_by_name = runner.invoke(
        cli,
        ["--url", "http://example.test", "--token", "session-123", "recipes", "show-by-name", "demo-recipe"],
    )
    result_ingredient_status = runner.invoke(
        cli,
        ["--url", "http://example.test", "--token", "session-123", "ingredients", "status"],
    )
    result_suppression_status = runner.invoke(
        cli,
        ["--url", "http://example.test", "--token", "session-123", "suppressions", "status"],
    )
    result_suppression_stats = runner.invoke(
        cli,
        ["--url", "http://example.test", "--token", "session-123", "suppressions", "stats", "42"],
    )
    result_suppression_update = runner.invoke(
        cli,
        [
            "--url",
            "http://example.test",
            "--token",
            "session-123",
            "suppressions",
            "update",
            "42",
            "--reason",
            "updated",
        ],
    )
    result_observability = runner.invoke(
        cli,
        ["--url", "http://example.test", "--token", "session-123", "observability", "activity"],
    )
    result_communications = runner.invoke(
        cli,
        ["--url", "http://example.test", "--token", "session-123", "communications", "activity"],
    )
    result_order_history = runner.invoke(
        cli,
        ["--url", "http://example.test", "--token", "session-123", "orders", "execution-history", "88"],
    )
    result_dish_ingredients = runner.invoke(
        cli,
        ["--url", "http://example.test", "--token", "session-123", "dishes", "ingredients", "7"],
    )
    result_dish_history = runner.invoke(
        cli,
        ["--url", "http://example.test", "--token", "session-123", "dishes", "ingredient-history", "7"],
    )

    assert result_recipe_status.exit_code == 0
    assert "inactive_ingredient_count" in result_recipe_status.output
    assert result_recipe_status_show.exit_code == 0
    assert "demo-recipe" in result_recipe_status_show.output
    assert result_recipe_ingredient_status.exit_code == 0
    assert "Recipe Step Status" in result_recipe_ingredient_status.output
    assert result_recipe_by_name.exit_code == 0
    assert "Communication Routes" in result_recipe_by_name.output
    assert result_ingredient_status.exit_code == 0
    assert "task_key_template" in result_ingredient_status.output
    assert result_suppression_status.exit_code == 0
    assert "demo-suppression" in result_suppression_status.output
    assert result_suppression_stats.exit_code == 0
    assert "total_suppressed" in result_suppression_stats.output
    assert result_suppression_update.exit_code == 0
    assert "updated" in result_suppression_update.output
    assert result_observability.exit_code == 0
    assert "target_kind" in result_observability.output
    assert result_communications.exit_code == 0
    assert "provider_reference_id" in result_communications.output
    assert result_order_history.exit_code == 0
    assert "Order Execution History" in result_order_history.output
    assert result_dish_ingredients.exit_code == 0
    assert "Dish Ingredients" in result_dish_ingredients.output
    assert result_dish_history.exit_code == 0


def test_suppressions_from_order_builds_matchers_from_timeline_labels(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, object | None]] = []

    def fake_request_with_retry_sync(**kwargs: object) -> httpx.Response:
        method = str(kwargs["method"])
        url = str(kwargs["url"])
        calls.append((method, url, kwargs.get("json")))
        if url.endswith("/api/v1/orders/88/timeline") and method == "GET":
            return _json_response(method, url, 200, _order_timeline_payload())
        if url.endswith("/api/v1/suppressions") and method == "POST":
            assert kwargs["json"] == {
                "name": "planned-maintenance",
                "starts_at": "2026-07-08T00:00:00Z",
                "ends_at": "2026-07-08T02:00:00Z",
                "matchers": [
                    {"label_key": "instance", "operator": "eq", "value": "compute-1"},
                    {"label_key": "cluster", "operator": "eq", "value": "region-a"},
                ],
                "reason": "node work",
                "created_by": "codex",
                "summary_ticket_enabled": True,
            }
            return _json_response(method, url, 200, _suppression_response_payload())
        raise AssertionError(f"Unexpected request {method} {url}")

    monkeypatch.setattr(client_module, "request_with_retry_sync", fake_request_with_retry_sync)
    monkeypatch.setattr(suppressions_module.getpass, "getuser", lambda: "codex")

    result = runner.invoke(
        cli,
        [
            "--url",
            "http://example.test",
            "--token",
            "session-123",
            "suppressions",
            "from-order",
            "88",
            "--name",
            "planned-maintenance",
            "--starts-at",
            "2026-07-08T00:00:00+00:00",
            "--ends-at",
            "2026-07-08T02:00:00+00:00",
            "--label-key",
            "instance",
            "--label-key",
            "cluster",
            "--reason",
            "node work",
        ],
    )

    assert result.exit_code == 0
    assert len(calls) == 2
