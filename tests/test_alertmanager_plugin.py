"""Unit tests for the Alertmanager service plugin."""

from __future__ import annotations

import httpx
import pytest

from api.plugins.alertmanager.adapter import AlertmanagerExecutionAdapter
from api.plugins.alertmanager.plugin import get_plugin
from api.plugins.alertmanager.templates import (
    ALERTMANAGER_INGREDIENT_TEMPLATES,
    ALERTMANAGER_RECIPE_TEMPLATES,
    ALERTMANAGER_SCHEDULED_TASKS,
)
from api.plugins.contract import validate_payload_schema
from api.plugins.manifest import validate_service_plugin
from api.plugins.transport import PluginHttpTransportConfig
from api.plugins.types import ExecutionContext


def _ctx(
    service_exec: str,
    *,
    service_payload: dict | None = None,
    service_exec_parameters: dict | None = None,
) -> ExecutionContext:
    return ExecutionContext(
        service_type="alertmanager",
        service_exec=service_exec,
        req_id="unit-test",
        service_payload=service_payload or {},
        service_exec_parameters=service_exec_parameters,
    )


def _transport(**overrides: object) -> PluginHttpTransportConfig:
    values = {
        "service_label": "Alertmanager",
        "base_url": "https://alertmanager.example.test",
        "verify_ssl": True,
        "username": "",
        "password": "",
        "bearer_token": "",
        "timeout_seconds": 10.0,
    }
    values.update(overrides)
    return PluginHttpTransportConfig(**values)  # type: ignore[arg-type]


def test_alertmanager_manifest_validates() -> None:
    plugin = get_plugin()

    assert validate_service_plugin(plugin, directory_name="alertmanager") is plugin
    assert plugin.service_type == "alertmanager"
    assert plugin.plugin_tier == "community"
    assert plugin.plugin_log_key is None


def test_alertmanager_adapter_declares_optional_ecosystem_credentials() -> None:
    assert AlertmanagerExecutionAdapter(transport=_transport()).credential_requirements() == [
        {
            "credential_type": "alertmanager_http_auth",
            "credential_key_id": "default",
            "required": False,
            "usage": "Optional Alertmanager API credentials for authenticated alert management.",
        }
    ]


def test_alertmanager_templates_are_valid_service_plugin_templates() -> None:
    assert {template["service_exec"] for template in ALERTMANAGER_INGREDIENT_TEMPLATES} == {
        "health_check",
        "inspect",
        "suppression",
        "sync_silences",
    }
    assert {recipe["name"] for recipe in ALERTMANAGER_RECIPE_TEMPLATES} == {
        "plugin-health-check:alertmanager",
        "alertmanager-sync-silences",
    }
    assert {task["task_key"] for task in ALERTMANAGER_SCHEDULED_TASKS} == {
        "plugin-health-check:alertmanager",
        "alertmanager-sync-silences",
    }
    for template in ALERTMANAGER_INGREDIENT_TEMPLATES:
        assert template["service_type"] == "alertmanager"
        validate_payload_schema(template["payload_schema"])

    inspect_template = next(
        template
        for template in ALERTMANAGER_INGREDIENT_TEMPLATES
        if template["service_exec"] == "inspect"
    )
    assert inspect_template["task_key_template"] == "alertmanager-inspect"
    assert inspect_template["ingredient_purpose"] == "utility"
    assert inspect_template["is_blocking"] is False
    assert inspect_template["on_failure"] == "continue"
    assert inspect_template["service_exec_parameters"] == {
        "operation": "list_alerts",
        "allowed_operations": [
            "list_alerts",
            "list_groups",
            "find_inhibited_by_source",
            "verify_firing",
        ],
        "operation_metadata": {
            "list_alerts": {
                "label": "List alerts",
                "description": "Read active, silenced, and inhibited alerts from Alertmanager.",
            },
            "list_groups": {
                "label": "List alert groups",
                "description": "Read grouped alerts and route mute evidence from Alertmanager.",
            },
            "find_inhibited_by_source": {
                "label": "Find alerts inhibited by source",
                "description": "Find Alertmanager alerts inhibited by the current source alert fingerprint.",
            },
            "verify_firing": {
                "label": "Verify alert is firing",
                "description": "Read Alertmanager alerts and verify the source alert is still active.",
            },
        },
    }
    guard_template = next(
        template
        for template in ALERTMANAGER_INGREDIENT_TEMPLATES
        if template["task_key_template"] == "alertmanager-firing-guard"
    )
    assert guard_template["service_exec_expected_outcome_default"] == {"is_firing": True}
    assert guard_template["is_blocking"] is True
    assert guard_template["on_failure"] == "stop"


def test_alertmanager_adapter_requires_url() -> None:
    adapter = AlertmanagerExecutionAdapter(transport=_transport(base_url=""))

    assert (
        adapter.validate(_ctx("health_check"))
        == "POUNDCAKE_ALERTMANAGER_URL is required for alertmanager plugin"
    )


def test_alertmanager_adapter_validates_inspect_operations() -> None:
    adapter = AlertmanagerExecutionAdapter(transport=_transport())

    assert (
        adapter.validate(
            _ctx(
                "inspect",
                service_exec_parameters={"operation": "not-real"},
            )
        )
        == "alertmanager inspect operation must be one of: list_alerts, list_groups, find_inhibited_by_source, verify_firing"
    )
    assert (
        adapter.validate(
            _ctx(
                "inspect",
                service_exec_parameters={"operation": "find_inhibited_by_source"},
            )
        )
        == "alertmanager find_inhibited_by_source requires service_payload.fingerprint"
    )
    assert (
        adapter.validate(
            _ctx(
                "inspect",
                service_payload={"fingerprint": "root-fp"},
                service_exec_parameters={"operation": "find_inhibited_by_source"},
            )
        )
        is None
    )


def test_alertmanager_adapter_validates_suppression_operations() -> None:
    adapter = AlertmanagerExecutionAdapter(transport=_transport())

    assert (
        adapter.validate(
            _ctx(
                "suppression",
                service_exec_parameters={"operation": "not-real"},
            )
        )
        == "alertmanager suppression operation must be one of: create, update, expire, get"
    )
    assert (
        adapter.validate(
            _ctx(
                "suppression",
                service_exec_parameters={"operation": "create"},
                service_payload={"name": "maintenance"},
            )
        )
        == "alertmanager suppression create/update requires service_payload.matchers"
    )
    assert (
        adapter.validate(
            _ctx(
                "suppression",
                service_exec_parameters={"operation": "expire"},
                service_payload={},
            )
        )
        == "alertmanager suppression expire requires service_payload.source_ref"
    )
    assert (
        adapter.validate(
            _ctx(
                "suppression",
                service_exec_parameters={"operation": "create"},
                service_payload={
                    "name": "maintenance",
                    "starts_at": "2026-07-14T00:00:00+00:00",
                    "ends_at": "2026-07-14T01:00:00+00:00",
                    "matchers": [{"label_key": "alertname", "operator": "eq", "value": "Demo"}],
                },
            )
        )
        is None
    )


def test_alertmanager_adapter_rejects_auth_over_insecure_remote_transport() -> None:
    adapter = AlertmanagerExecutionAdapter(
        transport=_transport(
            base_url="http://alertmanager.example.test",
            bearer_token="secret-token",
        )
    )

    assert (
        adapter.validate(_ctx("health_check"))
        == "Alertmanager authentication requires HTTPS or an in-cluster service URL"
    )


def test_alertmanager_health_does_not_send_auth_over_insecure_transport(monkeypatch) -> None:
    def _fake_get(*args: object, **kwargs: object) -> httpx.Response:
        raise AssertionError("health_check should fail before sending insecure auth")

    monkeypatch.setattr(httpx, "get", _fake_get)
    adapter = AlertmanagerExecutionAdapter(
        transport=_transport(
            base_url="http://alertmanager.example.test",
            bearer_token="secret-token",
        )
    )

    health = adapter.health_check()

    assert health.status == "failed"
    assert health.error_code == "TransportSecurityError"
    assert (
        health.message == "Alertmanager authentication requires HTTPS or an in-cluster service URL"
    )
    assert health.details == {
        "url": "http://alertmanager.example.test",
        "verify_ssl": True,
        "auth_mode": "bearer",
        "secure_transport": False,
    }


def test_alertmanager_health_uses_standard_auth_and_safe_metadata(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_get(url: str, **kwargs: object) -> httpx.Response:
        captured["url"] = url
        captured["kwargs"] = kwargs
        return httpx.Response(200, json={"status": "success"})

    monkeypatch.setattr(httpx, "get", _fake_get)
    adapter = AlertmanagerExecutionAdapter(
        transport=_transport(
            verify_ssl=False,
            username="user",
            password="pass",
        )
    )

    health = adapter.health_check()

    assert health.status == "healthy"
    assert captured == {
        "url": "https://alertmanager.example.test/api/v2/status",
        "kwargs": {
            "timeout": 10.0,
            "verify": False,
            "auth": ("user", "pass"),
        },
    }
    assert health.details == {
        "url": "https://alertmanager.example.test",
        "verify_ssl": False,
        "auth_mode": "basic",
        "secure_transport": True,
        "status": {"status": "success"},
    }
    assert "pass" not in str(health.details)


def test_alertmanager_health_degrades_when_endpoint_is_unreachable(monkeypatch) -> None:
    def _fake_get(*_args: object, **_kwargs: object) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "get", _fake_get)
    adapter = AlertmanagerExecutionAdapter(transport=_transport())

    health = adapter.health_check()

    assert health.status == "degraded"
    assert health.error_code == "ConnectError"


def test_alertmanager_health_fails_on_auth_errors(monkeypatch) -> None:
    def _fake_get(*_args: object, **_kwargs: object) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    monkeypatch.setattr(httpx, "get", _fake_get)
    adapter = AlertmanagerExecutionAdapter(transport=_transport())

    health = adapter.health_check()

    assert health.status == "failed"
    assert health.error_code == "401"


@pytest.mark.asyncio
async def test_alertmanager_sync_silences_uses_bearer_auth() -> None:
    captured: dict[str, object] = {}

    class _FakeAsyncClient:
        def __init__(self, **kwargs: object) -> None:
            captured["client_kwargs"] = kwargs

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, url: str, **kwargs: object) -> httpx.Response:
            captured["url"] = url
            captured["kwargs"] = kwargs
            return httpx.Response(200, json=[])

    adapter = AlertmanagerExecutionAdapter(
        transport=_transport(
            bearer_token="secret-token",
            timeout_seconds=12.0,
        )
    )

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
        result = await adapter.dispatch(_ctx("sync_silences"))

    assert result.status == "succeeded"
    assert captured == {
        "client_kwargs": {"timeout": 12.0, "verify": True},
        "url": "https://alertmanager.example.test/api/v2/silences",
        "kwargs": {"headers": {"Authorization": "Bearer secret-token"}},
    }


@pytest.mark.asyncio
async def test_alertmanager_create_suppression_posts_bounded_payload() -> None:
    captured: dict[str, object] = {}

    class _FakeAsyncClient:
        def __init__(self, **kwargs: object) -> None:
            captured.setdefault("client_kwargs", kwargs)

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, **kwargs: object) -> httpx.Response:
            captured["post_url"] = url
            captured["post_kwargs"] = kwargs
            return httpx.Response(200, json={"silenceID": "sil-123"})

        async def get(self, url: str, **kwargs: object) -> httpx.Response:
            captured["get_url"] = url
            captured["get_kwargs"] = kwargs
            return httpx.Response(
                200,
                json={
                    "id": "sil-123",
                    "startsAt": "2026-07-14T00:00:00+00:00",
                    "endsAt": "2026-07-14T01:00:00+00:00",
                    "createdBy": "alice",
                    "comment": "PoundCake suppression: Database maintenance\n---\nKernel patching",
                    "matchers": [{"name": "alertname", "value": "NodeDown", "isRegex": False, "isEqual": True}],
                    "status": {"state": "active"},
                },
            )

    adapter = AlertmanagerExecutionAdapter(transport=_transport())

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
        result = await adapter.dispatch(
            _ctx(
                "suppression",
                service_payload={
                    "name": "Database maintenance",
                    "reason": "Kernel patching",
                    "starts_at": "2026-07-14T00:00:00+00:00",
                    "ends_at": "2026-07-14T01:00:00+00:00",
                    "created_by": "alice",
                    "summary_ticket_enabled": True,
                    "matchers": [{"label_key": "alertname", "operator": "eq", "value": "NodeDown"}],
                },
                service_exec_parameters={"operation": "create"},
            )
        )

    assert result.status == "succeeded"
    assert result.result is not None
    assert result.result["operation"] == "create"
    assert result.result["suppression"]["source_ref"] == "sil-123"
    assert result.result["suppression"]["name"] == "Database maintenance"
    assert result.result["suppression"]["reason"] == "Kernel patching"
    assert captured["post_url"] == "https://alertmanager.example.test/api/v2/silences"
    assert captured["post_kwargs"] == {
        "json": {
            "matchers": [{"name": "alertname", "value": "NodeDown", "isRegex": False, "isEqual": True}],
            "startsAt": "2026-07-14T00:00:00+00:00",
            "endsAt": "2026-07-14T01:00:00+00:00",
            "createdBy": "alice",
            "comment": "PoundCake suppression: Database maintenance\n---\nKernel patching",
        }
    }
    assert captured["get_url"] == "https://alertmanager.example.test/api/v2/silence/sil-123"


@pytest.mark.asyncio
async def test_alertmanager_expire_suppression_reposts_silence_with_now_end() -> None:
    captured: dict[str, object] = {}
    get_calls = 0

    class _FakeAsyncClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, **kwargs: object) -> httpx.Response:
            captured["post_url"] = url
            captured["post_kwargs"] = kwargs
            return httpx.Response(200, json={"silenceID": "sil-123"})

        async def get(self, url: str, **kwargs: object) -> httpx.Response:
            nonlocal get_calls
            get_calls += 1
            captured[f"get_url_{get_calls}"] = url
            captured[f"get_kwargs_{get_calls}"] = kwargs
            state = "active" if get_calls == 1 else "expired"
            return httpx.Response(
                200,
                json={
                    "id": "sil-123",
                    "startsAt": "2026-07-14T00:00:00+00:00",
                    "endsAt": "2026-07-14T01:00:00+00:00",
                    "createdBy": "alice",
                    "comment": "PoundCake suppression: Database maintenance\n---\nKernel patching",
                    "matchers": [{"name": "alertname", "value": "NodeDown", "isRegex": False, "isEqual": True}],
                    "status": {"state": state},
                },
            )

    adapter = AlertmanagerExecutionAdapter(transport=_transport())

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
        result = await adapter.dispatch(
            _ctx(
                "suppression",
                service_payload={"source_ref": "sil-123"},
                service_exec_parameters={"operation": "expire"},
            )
        )

    assert result.status == "succeeded"
    assert result.result is not None
    assert result.result["operation"] == "expire"
    assert result.result["suppression"]["status"] == "expired"
    assert captured["post_url"] == "https://alertmanager.example.test/api/v2/silences"
    assert captured["post_kwargs"]["json"]["id"] == "sil-123"
    assert captured["get_url_1"] == "https://alertmanager.example.test/api/v2/silence/sil-123"
    assert captured["get_url_2"] == "https://alertmanager.example.test/api/v2/silence/sil-123"


@pytest.mark.asyncio
async def test_alertmanager_list_alerts_calls_api_with_query_params() -> None:
    captured: dict[str, object] = {}

    class _FakeAsyncClient:
        def __init__(self, **kwargs: object) -> None:
            captured["client_kwargs"] = kwargs

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, url: str, **kwargs: object) -> httpx.Response:
            captured["url"] = url
            captured["kwargs"] = kwargs
            return httpx.Response(
                200,
                json=[
                    {
                        "fingerprint": "fp-1",
                        "labels": {"alertname": "RootAlert"},
                        "annotations": {"summary": "root"},
                        "receivers": [{"name": "default"}],
                        "status": {
                            "state": "active",
                            "silencedBy": [],
                            "inhibitedBy": [],
                        },
                    }
                ],
            )

    adapter = AlertmanagerExecutionAdapter(transport=_transport())

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
        result = await adapter.dispatch(
            _ctx(
                "inspect",
                service_payload={
                    "labels": {"alertname": "RootAlert"},
                    "active": True,
                    "silenced": True,
                    "inhibited": True,
                    "receiver": "default",
                },
                service_exec_parameters={"operation": "list_alerts"},
            )
        )

    assert result.status == "succeeded"
    assert result.result is not None
    assert result.result["operation"] == "list_alerts"
    assert result.result["alert_count"] == 1
    assert result.result["alerts"][0]["fingerprint"] == "fp-1"
    assert captured == {
        "client_kwargs": {"timeout": 10.0, "verify": True},
        "url": "https://alertmanager.example.test/api/v2/alerts",
        "kwargs": {
            "params": [
                ("receiver", "default"),
                ("active", "true"),
                ("silenced", "true"),
                ("inhibited", "true"),
                ("filter", 'alertname="RootAlert"'),
            ]
        },
    }


@pytest.mark.asyncio
async def test_alertmanager_verify_firing_filters_by_fingerprint() -> None:
    class _FakeAsyncClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, *_args: object, **_kwargs: object) -> httpx.Response:
            return httpx.Response(
                200,
                json=[
                    {
                        "fingerprint": "fp-other",
                        "labels": {"alertname": "RootAlert"},
                        "status": {"state": "active"},
                    },
                    {
                        "fingerprint": "fp-source",
                        "labels": {"alertname": "RootAlert"},
                        "status": {"state": "active"},
                    },
                ],
            )

    adapter = AlertmanagerExecutionAdapter(transport=_transport())

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
        result = await adapter.dispatch(
            _ctx(
                "inspect",
                service_payload={
                    "fingerprint": "fp-source",
                    "labels": {"alertname": "RootAlert"},
                    "active": True,
                },
                service_exec_parameters={"operation": "verify_firing"},
            )
        )

    assert result.status == "succeeded"
    assert result.result is not None
    assert result.result["success"] is True
    assert result.result["status"] == "firing"
    assert result.result["is_firing"] is True
    assert result.result["alert_count"] == 1
    assert result.result["alerts"][0]["fingerprint"] == "fp-source"


@pytest.mark.asyncio
async def test_alertmanager_verify_firing_returns_resolved_when_not_found() -> None:
    class _FakeAsyncClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, *_args: object, **_kwargs: object) -> httpx.Response:
            return httpx.Response(200, json=[])

    adapter = AlertmanagerExecutionAdapter(transport=_transport())

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
        result = await adapter.dispatch(
            _ctx(
                "inspect",
                service_payload={
                    "fingerprint": "{{ order.raw_data.fingerprint }}",
                    "labels": {"alertname": "RootAlert"},
                    "active": True,
                },
                service_exec_parameters={"operation": "verify_firing"},
            )
        )

    assert result.status == "succeeded"
    assert result.result is not None
    assert result.result["success"] is False
    assert result.result["status"] == "resolved"
    assert result.result["is_firing"] is False
    assert result.result["alert_count"] == 0


@pytest.mark.asyncio
async def test_alertmanager_list_groups_calls_groups_endpoint() -> None:
    captured: dict[str, object] = {}

    class _FakeAsyncClient:
        def __init__(self, **kwargs: object) -> None:
            captured["client_kwargs"] = kwargs

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, url: str, **kwargs: object) -> httpx.Response:
            captured["url"] = url
            captured["kwargs"] = kwargs
            return httpx.Response(
                200,
                json=[
                    {
                        "receiver": {"name": "ops"},
                        "labels": {"alertname": "RootAlert"},
                        "alerts": [
                            {
                                "fingerprint": "fp-muted",
                                "labels": {"alertname": "MutedAlert"},
                                "status": {
                                    "state": "suppressed",
                                    "mutedBy": ["maintenance-hours"],
                                },
                            }
                        ],
                    }
                ],
            )

    adapter = AlertmanagerExecutionAdapter(transport=_transport())

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
        result = await adapter.dispatch(
            _ctx(
                "inspect",
                service_exec_parameters={"operation": "list_groups"},
            )
        )

    assert result.status == "succeeded"
    assert result.result is not None
    assert result.result["operation"] == "list_groups"
    assert result.result["group_count"] == 1
    assert result.result["suppression"]["muted_by"] == ["maintenance-hours"]
    assert captured["url"] == "https://alertmanager.example.test/api/v2/alerts/groups"
    assert captured["kwargs"] == {"params": [("muted", "true")]}


@pytest.mark.asyncio
async def test_alertmanager_find_inhibited_by_source_filters_by_fingerprint() -> None:
    class _FakeAsyncClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, *_args: object, **_kwargs: object) -> httpx.Response:
            return httpx.Response(
                200,
                json=[
                    {
                        "fingerprint": "child-1",
                        "labels": {"alertname": "ChildOne"},
                        "status": {
                            "state": "suppressed",
                            "inhibitedBy": ["root-fp"],
                            "silencedBy": [],
                        },
                    },
                    {
                        "fingerprint": "child-2",
                        "labels": {"alertname": "ChildTwo"},
                        "status": {
                            "state": "suppressed",
                            "inhibitedBy": ["other-root"],
                            "silencedBy": [],
                        },
                    },
                ],
            )

    adapter = AlertmanagerExecutionAdapter(transport=_transport())

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
        result = await adapter.dispatch(
            _ctx(
                "inspect",
                service_payload={"fingerprint": "root-fp"},
                service_exec_parameters={"operation": "find_inhibited_by_source"},
            )
        )

    assert result.status == "succeeded"
    assert result.result is not None
    assert result.result["source_fingerprint"] == "root-fp"
    assert result.result["alert_count"] == 1
    assert result.result["alerts"][0]["fingerprint"] == "child-1"
    assert result.result["suppression"]["inhibited_by"] == ["root-fp"]


@pytest.mark.asyncio
async def test_alertmanager_inspect_http_failure_is_safe() -> None:
    class _FakeAsyncClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, *_args: object, **_kwargs: object) -> httpx.Response:
            return httpx.Response(503, text="backend unavailable")

    adapter = AlertmanagerExecutionAdapter(transport=_transport(bearer_token="secret-token"))

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
        result = await adapter.dispatch(
            _ctx("inspect", service_exec_parameters={"operation": "list_alerts"})
        )

    assert result.status == "failed"
    assert result.result is not None
    assert result.result["message"] == "Alertmanager alerts returned HTTP 503"
    assert "secret-token" not in str(result.result)
