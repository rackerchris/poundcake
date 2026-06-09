"""Unit tests for the StackStorm service plugin."""

from __future__ import annotations

import httpx
import pytest

from api.plugins.contract import validate_payload_schema, validate_service_payload_for_operation
from api.plugins.manifest import validate_service_plugin
from api.plugins.stackstorm import service as stackstorm_service
from api.plugins.stackstorm.adapter import (
    StackStormExecutionAdapter,
    _map_poundcake_terminal_status,
    _map_stackstorm_status,
)
from api.plugins.stackstorm.plugin import get_plugin
from api.plugins.stackstorm.service import StackStormClient, StackStormError
from api.plugins.stackstorm.content_sync import (
    load_stackstorm_action_definitions,
)
from api.plugins.stackstorm.templates import (
    STACKSTORM_INGREDIENT_TEMPLATES,
    STACKSTORM_RECIPE_TEMPLATES,
    STACKSTORM_SCHEDULED_TASKS,
)
from api.plugins.types import ExecutionContext


def test_stackstorm_raw_statuses_map_to_canonical_contract() -> None:
    assert _map_stackstorm_status("requested") == "dispatched"
    assert _map_stackstorm_status("scheduled") == "dispatched"
    assert _map_stackstorm_status("pending") == "dispatched"
    assert _map_stackstorm_status("pausing") == "dispatched"
    assert _map_stackstorm_status("resuming") == "dispatched"
    assert _map_stackstorm_status("running") == "running"
    assert _map_stackstorm_status("succeeded") == "succeeded"
    assert _map_stackstorm_status("failed") == "failed"
    assert _map_stackstorm_status("timeout") == "timeout"
    assert _map_stackstorm_status("canceled") == "canceled"
    assert _map_stackstorm_status("canceling") == "canceled"
    assert _map_stackstorm_status("abandoned") == "errored"
    assert _map_stackstorm_status("surprise") == "errored"


def test_poundcake_terminal_statuses_map_to_stackstorm_terminal_states() -> None:
    assert _map_poundcake_terminal_status("complete") == "succeeded"
    assert _map_poundcake_terminal_status("succeeded") == "succeeded"
    assert _map_poundcake_terminal_status("failed") == "failed"
    assert _map_poundcake_terminal_status("errored") == "failed"
    assert _map_poundcake_terminal_status("timeout") == "timeout"
    assert _map_poundcake_terminal_status("canceled") == "canceled"

    with pytest.raises(ValueError, match="Unsupported PoundCake terminal status"):
        _map_poundcake_terminal_status("running")


def _ctx(
    *,
    service_exec: str = "action_execution",
    payload: dict[str, object] | None = None,
    operation: str = "execute_action",
) -> ExecutionContext:
    return ExecutionContext(
        service_type="stackstorm",
        service_exec=service_exec,
        req_id="unit-test",
        service_payload=payload or {},
        service_exec_parameters={
            "operation": operation,
            "allowed_operations": ["execute_action"],
        },
    )


class _FakeStackStormClient:
    def __init__(self) -> None:
        self.cancel_status: str | None = None
        self.base_url = "http://stackstorm.test:9101"
        self.verify_ssl = True

    async def execute_action(
        self,
        *,
        req_id: str,
        action_ref: str,
        parameters: dict[str, object],
        timeout: int,
        action_is_workflow: bool = False,
    ) -> dict[str, object]:
        return {
            "id": "st2-exec-1",
            "status": "requested",
            "action": action_ref,
            "action_is_workflow": action_is_workflow,
            "parameters": parameters,
            "timeout": timeout,
            "req_id": req_id,
        }

    async def health_check(self, req_id: str | None = None) -> bool:
        return req_id in {"unit-test", "plugin-config-test"}

    async def get_execution(self, service_exec_id: str) -> dict[str, object]:
        return {
            "id": service_exec_id,
            "status": "succeeded",
            "result": {"success": True},
        }

    async def cancel_execution(self, service_exec_id: str, *, status: str) -> bool:
        self.cancel_status = status
        return service_exec_id == "st2-exec-1"


class _FakeStackStormManager:
    def __init__(self) -> None:
        self._client = _FakeStackStormClient()
        self.sync_called = False

    async def sync_action_definitions(
        self,
        actions: list[dict[str, object]],
    ) -> dict[str, object]:
        self.sync_called = True
        return {
            "created": len(actions),
            "updated": 0,
            "unchanged": 0,
            "processed": len(actions),
            "action_refs": [f"{action.get('pack')}.{action.get('name')}" for action in actions],
        }


def test_stackstorm_manifest_validates() -> None:
    plugin = get_plugin()

    validated = validate_service_plugin(plugin, directory_name="stackstorm")

    assert validated.service_type == "stackstorm"
    assert validated.plugin_tier == "community"
    assert validated.helper_factory is None
    assert validated.helper_capabilities == ()
    assert validated.required_helper_capabilities is None
    assert validated.bootstrap_factory is None


def test_stackstorm_adapter_declares_required_api_key_credential() -> None:
    assert StackStormExecutionAdapter(manager=_FakeStackStormManager()).credential_requirements() == [  # type: ignore[arg-type]
        {
            "credential_type": "stackstorm_api_key",
            "credential_key_id": "default",
            "required": True,
            "usage": "StackStorm API key or auth token for action execution.",
        }
    ]


@pytest.mark.asyncio
async def test_stackstorm_adapter_does_not_import_api_key_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("POUNDCAKE_STACKSTORM_API_KEY", raising=False)

    await StackStormExecutionAdapter(manager=_FakeStackStormManager()).bootstrap_credentials()  # type: ignore[arg-type]


def test_stackstorm_templates_are_valid_service_plugin_templates() -> None:
    assert {template["service_exec"] for template in STACKSTORM_INGREDIENT_TEMPLATES} == {
        "action_execution",
        "content_sync",
        "health_check",
        "workflow_execution",
    }
    action_template = next(
        template
        for template in STACKSTORM_INGREDIENT_TEMPLATES
        if template["service_exec"] == "action_execution"
    )
    workflow_template = next(
        template
        for template in STACKSTORM_INGREDIENT_TEMPLATES
        if template["service_exec"] == "workflow_execution"
    )
    assert action_template["service_exec_parameters"]["allowed_operations"] == ["execute_action"]
    assert workflow_template["service_exec_parameters"]["allowed_operations"] == [
        "execute_workflow"
    ]
    validate_service_payload_for_operation(
        {"action_ref": "core.local", "parameters": {"cmd": "date"}},
        action_template["payload_schema"],
        action_template["service_exec_parameters"],
    )
    validate_service_payload_for_operation(
        {"workflow_ref": "poundcake.host_down_remediation", "inputs": {"host": "compute-1"}},
        workflow_template["payload_schema"],
        workflow_template["service_exec_parameters"],
    )
    assert {recipe["name"] for recipe in STACKSTORM_RECIPE_TEMPLATES} == {
        "plugin-content-sync:stackstorm",
        "plugin-health-check:stackstorm",
    }
    assert {task["task_key"] for task in STACKSTORM_SCHEDULED_TASKS} == {
        "plugin-content-sync:stackstorm",
        "plugin-health-check:stackstorm",
    }
    for template in STACKSTORM_INGREDIENT_TEMPLATES:
        assert template["service_type"] == "stackstorm"
        validate_payload_schema(template["payload_schema"])


def test_stackstorm_adapter_validates_explicit_action_operation() -> None:
    adapter = StackStormExecutionAdapter(manager=_FakeStackStormManager())  # type: ignore[arg-type]

    assert (
        adapter.validate(_ctx(payload={"action_ref": "core.local", "parameters": {"cmd": "date"}}))
        is None
    )
    assert (
        adapter.validate(_ctx(payload={"parameters": {}}))
        == "stackstorm execute_action requires service_payload.action_ref"
    )
    assert (
        adapter.validate(_ctx(payload={"action_ref": "core.local"}, operation="delete_action"))
        == "stackstorm action_execution operation must be: execute_action"
    )
    assert (
        adapter.validate(
            _ctx(
                service_exec="workflow_execution",
                payload={"workflow_ref": "poundcake.host_down_remediation", "inputs": {}},
                operation="execute_workflow",
            )
        )
        is None
    )
    assert (
        adapter.validate(
            _ctx(service_exec="workflow_execution", payload={}, operation="execute_workflow")
        )
        == "stackstorm execute_workflow requires service_payload.workflow_ref"
    )
    assert adapter.validate(_ctx(service_exec="content_sync", operation="sync_content")) is None
    assert (
        adapter.validate(_ctx(service_exec="content_sync", operation="delete_content"))
        == "stackstorm content_sync operation must be: sync_content"
    )


@pytest.mark.asyncio
async def test_stackstorm_adapter_dispatches_and_polls_action_execution() -> None:
    adapter = StackStormExecutionAdapter(manager=_FakeStackStormManager())  # type: ignore[arg-type]

    dispatch = await adapter.dispatch(
        _ctx(payload={"action_ref": "core.local", "parameters": {"cmd": "date"}})
    )
    result = await adapter.poll(_ctx(payload={"action_ref": "core.local"}), "st2-exec-1")

    assert dispatch.status == "dispatched"
    assert dispatch.service_exec_id == "st2-exec-1"
    assert result.status == "succeeded"
    assert result.result == {"success": True}


@pytest.mark.asyncio
async def test_stackstorm_adapter_dispatches_workflow_execution_as_stackstorm_action() -> None:
    adapter = StackStormExecutionAdapter(manager=_FakeStackStormManager())  # type: ignore[arg-type]

    dispatch = await adapter.dispatch(
        _ctx(
            service_exec="workflow_execution",
            payload={
                "workflow_ref": "poundcake.host_down_remediation",
                "inputs": {"host": "compute-1"},
            },
            operation="execute_workflow",
        )
    )

    assert dispatch.status == "dispatched"
    assert dispatch.raw["action"] == "poundcake.host_down_remediation"
    assert dispatch.raw["action_is_workflow"] is True
    assert dispatch.raw["parameters"] == {"host": "compute-1"}


@pytest.mark.asyncio
async def test_stackstorm_adapter_cancel_uses_stackstorm_canceled_terminal_state() -> None:
    manager = _FakeStackStormManager()
    adapter = StackStormExecutionAdapter(manager=manager)  # type: ignore[arg-type]

    result = await adapter.cancel(_ctx(), "st2-exec-1")

    assert result.status == "canceled"
    assert result.raw == {"status": "canceled", "cancel_requested": True}
    assert manager._client.cancel_status == "canceled"


@pytest.mark.asyncio
async def test_stackstorm_adapter_polls_health_check_through_client() -> None:
    adapter = StackStormExecutionAdapter(manager=_FakeStackStormManager())  # type: ignore[arg-type]
    result = await adapter.dispatch(_ctx(service_exec="health_check"))

    assert result.status == "succeeded"
    assert result.result == {"success": True, "status": "healthy"}


@pytest.mark.asyncio
async def test_stackstorm_health_check_uses_operator_config_from_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _ConfiguredClient:
        def __init__(
            self,
            *,
            base_url: str | None = None,
            verify_ssl: bool | None = None,
            credential_payload: dict[str, object] | None = None,
            credential_key_id: str = "default",
        ) -> None:
            captured["base_url"] = base_url
            captured["verify_ssl"] = verify_ssl
            captured["credential_payload"] = credential_payload
            captured["credential_key_id"] = credential_key_id

        async def health_check(self, req_id: str | None = None) -> bool:
            captured["req_id"] = req_id
            return True

    class _ConfiguredManager:
        def __init__(self, client: _ConfiguredClient) -> None:
            self._client = client

    monkeypatch.setattr("api.plugins.stackstorm.adapter.StackStormClient", _ConfiguredClient)
    monkeypatch.setattr(
        "api.plugins.stackstorm.adapter.StackStormActionManager", _ConfiguredManager
    )

    adapter = StackStormExecutionAdapter(manager=_FakeStackStormManager())  # type: ignore[arg-type]
    ctx = _ctx(service_exec="health_check")
    ctx.context["operator_config"] = {
        "url": "http://stackstorm-api.stackstorm.svc.cluster.local:9101",
        "verify_ssl": False,
    }

    result = await adapter.dispatch(ctx)

    assert result.status == "succeeded"
    assert captured == {
        "base_url": "http://stackstorm-api.stackstorm.svc.cluster.local:9101",
        "verify_ssl": False,
        "credential_payload": None,
        "credential_key_id": "default",
        "req_id": "unit-test",
    }


@pytest.mark.asyncio
async def test_stackstorm_adapter_test_connection_returns_health_details() -> None:
    adapter = StackStormExecutionAdapter(manager=_FakeStackStormManager())  # type: ignore[arg-type]

    result = await adapter.test_connection(credential_key_id="default")

    assert result.status == "healthy"
    assert result.details == {
        "mode": "stackstorm-api",
        "credential_type": "stackstorm_api_key",
        "url": "http://stackstorm.test:9101",
    }


@pytest.mark.asyncio
async def test_stackstorm_adapter_dispatches_content_sync_through_order_contract() -> None:
    adapter = StackStormExecutionAdapter(manager=_FakeStackStormManager())  # type: ignore[arg-type]
    dispatch = await adapter.dispatch(_ctx(service_exec="content_sync", operation="sync_content"))

    assert dispatch.status == "succeeded"
    assert dispatch.service_exec_id is not None
    assert dispatch.service_exec_id.startswith("stackstorm:content_sync:")
    assert dispatch.result["success"] is True
    assert dispatch.result["actions"]["processed"] >= 1


@pytest.mark.asyncio
async def test_stackstorm_adapter_bootstrap_does_not_sync_provider_content() -> None:
    manager = _FakeStackStormManager()
    adapter = StackStormExecutionAdapter(manager=manager)  # type: ignore[arg-type]

    result = await adapter.bootstrap_plugin(_ctx(service_exec="health_check"))

    assert result.status == "ready"
    assert result.details["bootstrap_status"] == "ready"
    assert manager.sync_called is False


def test_stackstorm_content_metadata_includes_poundcake_workflow_action() -> None:
    actions = load_stackstorm_action_definitions()

    assert any(
        action["name"] == "host_down_remediation"
        and action["pack"] == "poundcake"
        and action["runner_type"] == "orquesta"
        and action["entry_point"] == "workflows/host_down_remediation.yaml"
        for action in actions
    )


@pytest.mark.asyncio
async def test_stackstorm_client_loads_headers_from_adapter_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def load_credential(**_kwargs: object) -> dict[str, object]:
        return {"api_key": "secret-key"}

    monkeypatch.setattr(stackstorm_service, "read_adapter_credential_payload", load_credential)

    assert await StackStormClient()._get_headers() == {
        "Content-Type": "application/json",
        "St2-Api-Key": "secret-key",
    }


@pytest.mark.asyncio
async def test_stackstorm_client_rejects_missing_adapter_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def load_credential(**_kwargs: object) -> None:
        return None

    monkeypatch.setattr(stackstorm_service, "read_adapter_credential_payload", load_credential)

    with pytest.raises(StackStormError, match="credential is not available"):
        await StackStormClient()._get_headers()


@pytest.mark.asyncio
async def test_stackstorm_client_rejects_malformed_adapter_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def load_credential(**_kwargs: object) -> dict[str, object]:
        return {}

    monkeypatch.setattr(stackstorm_service, "read_adapter_credential_payload", load_credential)

    with pytest.raises(StackStormError, match="must include api_key or auth_token"):
        await StackStormClient()._get_headers()


@pytest.mark.asyncio
async def test_stackstorm_client_marks_workflow_execution_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def request_with_retry(method: str, url: str, **kwargs: object) -> httpx.Response:
        captured.update({"method": method, "url": url, **kwargs})
        return httpx.Response(
            201,
            json={
                "id": "st2-exec-workflow",
                "status": "requested",
                "action": "poundcake.host_down_remediation",
            },
        )

    monkeypatch.setattr(stackstorm_service, "request_with_retry", request_with_retry)
    client = StackStormClient(
        base_url="http://stackstorm.test:9101",
        credential_payload={"api_key": "secret-key"},
    )

    result = await client.execute_action(
        req_id="unit-test",
        action_ref="poundcake.host_down_remediation",
        parameters={"host": "compute-1"},
        action_is_workflow=True,
    )

    assert result["id"] == "st2-exec-workflow"
    assert captured["method"] == "POST"
    assert captured["url"] == "http://stackstorm.test:9101/v1/executions"
    assert captured["json"] == {
        "action": "poundcake.host_down_remediation",
        "parameters": {"host": "compute-1"},
        "action_is_workflow": True,
    }


@pytest.mark.asyncio
async def test_stackstorm_client_cancels_execution_with_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def request_with_retry(method: str, url: str, **kwargs: object) -> httpx.Response:
        captured.update({"method": method, "url": url, **kwargs})
        return httpx.Response(200, json={})

    monkeypatch.setattr(stackstorm_service, "request_with_retry", request_with_retry)
    client = StackStormClient(
        base_url="http://stackstorm.test:9101",
        credential_payload={"api_key": "secret-key"},
    )

    assert await client.cancel_execution("st2-exec-1") is True
    assert captured["method"] == "DELETE"
    assert captured["url"] == "http://stackstorm.test:9101/v1/executions/st2-exec-1"
    assert "json" not in captured
