"""Unit tests for the Bakery service plugin contract."""

from __future__ import annotations

import json
import time
from copy import deepcopy

import pytest

from api.plugins.bakery.adapter import BakeryExecutionAdapter, _payload_with_dish_evidence
from api.plugins.bakery import client
from api.plugins.bakery.client import BakeryClientConfig, BakeryHealth, BakeryMonitorCredential
from api.plugins.bakery.contract import CommunicationOpenRequest
from api.plugins.bakery.templates import (
    BAKERY_SCHEDULED_TASKS,
    communication_routes,
    ingredient_templates,
    recipe_templates,
)
from api.plugins.bakery.capabilities import load_bakery_capability_templates
from api.plugins.catalog import get_enabled_plugins
from api.plugins.contract import (
    ServicePluginContractError,
    validate_payload_schema,
    validate_service_payload_for_operation,
)
from api.services.credentials import decrypt_payload, encrypt_payload
from api.plugins.manifest import validate_service_plugin
from api.plugins.types import ExecutionContext
from shared.hmac import build_hmac_signing_payload, hmac_sha256_hex


def _bakery_template(service_exec: str) -> dict[str, object]:
    return next(
        template for template in ingredient_templates() if template["service_exec"] == service_exec
    )


def test_bakery_manifest_validates(monkeypatch) -> None:
    monkeypatch.setenv("POUNDCAKE_ENABLED_PLUGINS", "bakery")
    plugins = get_enabled_plugins()
    bakery = next(plugin for plugin in plugins if plugin.service_type == "bakery")
    validated = validate_service_plugin(bakery, directory_name="bakery")

    assert validated.service_type == "bakery"
    assert validated.plugin_tier == "supported"
    assert validated.plugin_log_key == "bakery"
    assert validated.bootstrap_factory is None
    assert (
        len(validated.ingredient_templates) == 4
    )  # health_check, communication, incident_reconcile, collect
    assert len(validated.recipe_templates) == 1
    assert len(validated.communication_routes) == 1
    assert (
        len(validated.capability_templates) == 5
    )  # communication + incident_reconcile + 3 collectors


def test_bakery_plugin_excludes_dummy_when_both_are_configured(monkeypatch) -> None:
    monkeypatch.setenv("POUNDCAKE_ENABLED_PLUGINS", "dummy,bakery")

    plugins = get_enabled_plugins()

    assert [plugin.service_type for plugin in plugins] == ["bakery"]


def test_bakery_templates_advertise_default_global_comms_route(monkeypatch) -> None:
    monkeypatch.setenv("POUNDCAKE_BAKERY_ACTIVE_PROVIDER", "jira")

    routes = communication_routes()

    assert routes == (
        {
            "id": "bakery-global-comms",
            "label": "Bakery Jira",
            "service_type": "bakery",
            "destination_target": "jira",
            "provider_config": {},
            "enabled": True,
            "position": 1,
        },
    )


def test_bakery_templates_are_valid_service_plugin_templates() -> None:
    assert {template["service_exec"] for template in ingredient_templates()} == {
        "health_check",
        "communication",
        "incident_reconcile",
        "collect",
    }
    comms_template = next(
        template
        for template in ingredient_templates()
        if template["service_exec"] == "communication"
    )
    assert comms_template["service_exec_parameters"]["allowed_operations"] == [
        "open",
        "notify",
        "update",
        "close",
    ]
    assert {recipe["name"] for recipe in recipe_templates()} == {"plugin-health-check:bakery"}
    assert {task["task_key"] for task in BAKERY_SCHEDULED_TASKS} == {
        "plugin-health-check:bakery",
        "incident-reconcile:bakery",
    }
    for template in ingredient_templates():
        assert template["service_type"] == "bakery"
        validate_payload_schema(template["payload_schema"])


@pytest.mark.parametrize(
    "payload",
    [
        {"description": "Open a ticket", "source": "poundcake", "context": {}},
        {"title": "Open a ticket", "source": "poundcake", "context": {}},
        {"title": "Open a ticket", "description": "Open a ticket", "context": {}},
        {"title": "Open a ticket", "description": "Open a ticket", "source": "poundcake"},
    ],
)
def test_bakery_open_contract_rejects_missing_ticket_create_fields(
    payload: dict[str, object],
) -> None:
    template = _bakery_template("communication")
    parameters = deepcopy(template["service_exec_parameters"])
    parameters["operation"] = "open"

    with pytest.raises(ServicePluginContractError):
        validate_service_payload_for_operation(
            payload,
            template["payload_schema"],
            parameters,
        )


@pytest.mark.parametrize("operation", ["notify", "update", "close"])
def test_bakery_ticket_mutation_contract_rejects_missing_ticket_id(operation: str) -> None:
    template = _bakery_template("communication")
    parameters = deepcopy(template["service_exec_parameters"])
    parameters["operation"] = operation

    with pytest.raises(ServicePluginContractError):
        validate_service_payload_for_operation(
            {"comment": "Ticket update"},
            template["payload_schema"],
            parameters,
        )


@pytest.mark.parametrize(
    "ticket_id_key",
    ["ticket_id", "bakery_ticket_id", "bakery_comms_id", "communication_id"],
)
def test_bakery_ticket_mutation_contract_accepts_supported_ticket_context_keys(
    ticket_id_key: str,
) -> None:
    template = _bakery_template("communication")
    parameters = deepcopy(template["service_exec_parameters"])
    parameters["operation"] = "notify"

    validate_service_payload_for_operation(
        {"context": {ticket_id_key: "TICKET-1"}},
        template["payload_schema"],
        parameters,
    )


def test_bakery_capability_templates_match_communication_ingredient() -> None:
    capability = load_bakery_capability_templates()[0]

    assert capability["capability_id"] == "bakery.communication.open.default"
    assert capability["ingredient_ref"]["service_exec"] == "communication"
    assert capability["operation"] == "open"
    assert capability["mode"] == "communication"
    assert capability["required_inputs"] == ["title", "description", "source", "context"]


def test_bakery_payload_aggregates_dish_evidence_context() -> None:
    ctx = ExecutionContext(
        service_type="bakery",
        service_exec="communication",
        req_id="unit-test",
        context={
            "dish": {
                "evidence": [
                    {
                        "task_key": "step_20_prometheus-inspect",
                        "service_type": "prometheus",
                        "managed_role": "gather_evidence",
                        "actual_outcome": {"success": True, "current": {"result": []}},
                    }
                ],
                "context_updates": {"bakery_ticket_id": "TICKET-1"},
            }
        },
    )

    payload = _payload_with_dish_evidence(
        {"source": "genestack_monitoring", "context": {"order_id": 42}},
        ctx,
    )

    assert payload["context"]["order_id"] == 42
    assert payload["context"]["evidence"][0]["service_type"] == "prometheus"
    assert payload["context"]["execution_context"] == {"bakery_ticket_id": "TICKET-1"}


def test_bakery_payload_includes_empty_evidence_list_when_dish_context_exists() -> None:
    ctx = ExecutionContext(
        service_type="bakery",
        service_exec="communication",
        req_id="unit-test",
        context={
            "dish": {
                "evidence": [],
                "context_updates": {"bakery_ticket_id": "TICKET-1"},
            }
        },
    )

    payload = _payload_with_dish_evidence(
        {"source": "genestack_monitoring", "context": {"order_id": 42}},
        ctx,
    )

    assert payload["context"]["order_id"] == 42
    assert payload["context"]["evidence"] == []
    assert payload["context"]["execution_context"] == {"bakery_ticket_id": "TICKET-1"}


def test_bakery_open_contract_accepts_standardized_state_field() -> None:
    request = CommunicationOpenRequest.model_validate(
        {
            "title": "Managed remediation opened",
            "description": "PoundCake opened a remediation communication.",
            "message": "Crash loop detected",
            "source": "genestack_monitoring",
            "state": "updated",
            "context": {"order_id": 347, "evidence": []},
        }
    )

    assert request.state == "updated"


def test_bakery_hmac_headers_match_shared_contract(monkeypatch) -> None:
    monkeypatch.setattr(time, "time", lambda: 1_700_000_000)

    headers = client._signed_headers(
        method="POST",
        path="/api/v1/communications",
        payload={"title": "hello"},
        key_id="active-id",
        secret="active-secret",
        monitor_uuid="monitor-1",
    )

    body = client._canonical_body({"title": "hello"}).encode("utf-8")
    signing_payload = build_hmac_signing_payload(
        "1700000000",
        "POST",
        "/api/v1/communications",
        body,
    )
    expected = hmac_sha256_hex("active-secret", signing_payload)
    assert headers["Authorization"] == f"HMAC active-id:{expected}"
    assert headers["X-Timestamp"] == "1700000000"
    assert headers["X-Bakery-Monitor-UUID"] == "monitor-1"


def test_bakery_transport_requires_https_outside_dev(monkeypatch) -> None:
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setenv("POUNDCAKE_BAKERY_BASE_URL", "http://bakery.example.com")

    assert client.validate_transport_config() == (
        "POUNDCAKE_BAKERY_BASE_URL must use https, loopback HTTP, " "or in-cluster service DNS"
    )


def test_bakery_adapter_exposes_operator_connection_config(monkeypatch) -> None:
    monkeypatch.setenv("POUNDCAKE_BAKERY_BASE_URL", "https://bakery.example.com")
    monkeypatch.setenv("HOSTNAME", "pod-ephemeral-name")
    adapter = BakeryExecutionAdapter()

    schema = adapter.operator_config_schema()
    config = adapter.default_operator_config()
    configured = adapter.with_operator_config(
        {
            **config,
            "url": "https://remote-bakery.example.com/",
            "verify_ssl": False,
            "timeout_seconds": "20",
            "max_retries": "3",
            "poll_interval_seconds": "1.5",
            "poll_timeout_seconds": "90",
            "plugin_id": "rackspace/kronos-poundcake",
            "tags": "dev,kind",
        }
    )

    assert schema["properties"]["url"]["title"] == "Bakery URL"
    assert configured.default_operator_config() == {
        **config,
        "url": "https://remote-bakery.example.com",
        "verify_ssl": False,
        "timeout_seconds": 20,
        "max_retries": 3,
        "poll_interval_seconds": 1.5,
        "poll_timeout_seconds": 90,
        "plugin_id": "rackspace/kronos-poundcake",
        "tags": "dev,kind",
    }
    assert config["plugin_id"] == "poundcake/bakery-plugin"


def test_bakery_adapter_validates_monitor_hmac_payload() -> None:
    adapter = BakeryExecutionAdapter()

    assert (
        adapter.validate_credential_payload(
            "bakery_monitor_hmac",
            {
                "monitor_uuid": "monitor-1",
                "monitor_id": "rackspace/kronos-poundcake",
                "hmac_key_id": "key-1",
                "hmac_secret": "secret",
            },
        )
        is None
    )
    assert (
        adapter.validate_credential_payload(
            "bakery_monitor_hmac",
            {"monitor_uuid": "monitor-1", "hmac_key_id": "key-1"},
        )
        == "Bakery credential requires monitor_uuid, monitor_id, hmac_key_id, and hmac_secret"
    )


def test_bakery_adapter_rejects_non_object_service_payload() -> None:
    adapter = BakeryExecutionAdapter()
    ctx = ExecutionContext.model_construct(
        service_type="bakery",
        service_exec="communication",
        req_id="unit-test",
        service_payload=["not", "an", "object"],
        service_exec_parameters={"operation": "open"},
        context={},
    )

    assert adapter.validate(ctx) == "service_payload must be an object when provided"


def test_bakery_adapter_requires_ticket_create_payload_contract(monkeypatch) -> None:
    monkeypatch.setattr("api.plugins.bakery.adapter.validate_transport_config", lambda: None)
    adapter = BakeryExecutionAdapter()
    ctx = ExecutionContext(
        service_type="bakery",
        service_exec="communication",
        req_id="unit-test",
        service_payload={
            "title": "Open ticket",
            "description": "Open ticket for failed remediation.",
            "source": "poundcake",
        },
        service_exec_parameters={"operation": "open"},
        context={"destination_target": "rackspace_core"},
    )

    assert adapter.validate(ctx) == "Bakery create requires payload.context"


def test_bakery_adapter_resolves_ticket_id_from_supported_context_keys(monkeypatch) -> None:
    monkeypatch.setattr("api.plugins.bakery.adapter.validate_transport_config", lambda: None)
    adapter = BakeryExecutionAdapter()
    ctx = ExecutionContext(
        service_type="bakery",
        service_exec="communication",
        req_id="unit-test",
        service_payload={"comment": "Ticket update"},
        service_exec_parameters={"operation": "notify"},
        context={
            "destination_target": "rackspace_core",
            "dish": {"context_updates": {"bakery_ticket_id": "TICKET-1"}},
        },
    )

    assert adapter.validate(ctx) is None


def test_plugin_credentials_encrypt_without_plaintext(monkeypatch) -> None:
    monkeypatch.setenv("POUNDCAKE_PLUGIN_CREDENTIAL_ENCRYPTION_KEY", "unit-test-key")
    payload = {
        "monitor_uuid": "monitor-1",
        "hmac_key_id": "key-1",
        "hmac_secret": "super-secret",
    }

    encrypted = encrypt_payload(payload)

    assert "super-secret" not in encrypted
    assert decrypt_payload(encrypted) == payload


class _FakeBakeryResponse:
    def __init__(self, payload: dict[str, object], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self) -> dict[str, object]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http_{self.status_code}")


@pytest.mark.asyncio
async def test_bakery_credential_check_reuses_configured_monitor_hmac(
    monkeypatch,
) -> None:
    async def read_adapter_credential_payload(
        *,
        service_type: str,
        credential_type: str,
        credential_key_id: str = "default",
    ) -> dict[str, object] | None:
        if credential_type == "bakery_monitor_hmac" and credential_key_id == "default":
            return {
                "monitor_uuid": "monitor-uuid",
                "monitor_id": "rackspace/kronos-poundcake",
                "hmac_key_id": "active-id",
                "hmac_secret": "active-secret",
            }
        return None

    async def unexpected_remote_call(*args: object, **kwargs: object) -> object:
        raise AssertionError("Existing monitor HMAC must not trigger remote registration")

    token = client.set_bakery_client_config(
        BakeryClientConfig(
            base_url="https://bakery.example.com",
            plugin_id="rackspace/kronos-poundcake",
        )
    )
    monkeypatch.setattr(client, "read_adapter_credential_payload", read_adapter_credential_payload)
    monkeypatch.setattr(client, "request_with_retry", unexpected_remote_call)
    try:
        credential = await client.bootstrap_monitor_credential(force=True)
    finally:
        client.reset_bakery_client_config(token)

    assert credential.monitor_uuid == "monitor-uuid"
    assert credential.monitor_id == "rackspace/kronos-poundcake"
    assert credential.hmac_key_id == "active-id"


@pytest.mark.asyncio
async def test_bakery_bootstrap_registers_with_bootstrap_hmac(monkeypatch) -> None:
    written: list[dict[str, object]] = []
    captured: dict[str, object] = {}

    async def read_adapter_credential_payload(
        *,
        service_type: str,
        credential_type: str,
        credential_key_id: str = "default",
    ) -> dict[str, object] | None:
        return None

    async def write_adapter_credential(**kwargs: object) -> None:
        written.append(dict(kwargs))

    async def request_with_retry(method: str, url: str, **kwargs: object) -> _FakeBakeryResponse:
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = kwargs["headers"]
        captured["content"] = kwargs["content"]
        return _FakeBakeryResponse(
            {
                "monitor_uuid": "issued-uuid",
                "monitor_id": "rackspace/poundcake",
                "hmac_key_id": "active",
                "hmac_secret": "issued-secret",
                "heartbeat_interval_sec": 30,
                "miss_threshold": 5,
                "route_sync_required": True,
            }
        )

    monkeypatch.setenv("POUNDCAKE_BAKERY_MONITOR_ID", "rackspace/poundcake")
    monkeypatch.setenv("POUNDCAKE_BAKERY_BOOTSTRAP_HMAC_KEY_ID", "bootstrap")
    monkeypatch.setenv("POUNDCAKE_BAKERY_BOOTSTRAP_HMAC_KEY", "bootstrap-secret")
    monkeypatch.setattr(client, "read_adapter_credential_payload", read_adapter_credential_payload)
    monkeypatch.setattr(client, "write_adapter_credential", write_adapter_credential)
    monkeypatch.setattr(client, "request_with_retry", request_with_retry)
    monkeypatch.setattr(client, "mark_adapter_credential_error", lambda **kwargs: None)

    token = client.set_bakery_client_config(
        BakeryClientConfig(
            base_url="https://bakery.example.com",
            namespace="rackspace",
            release_name="poundcake",
        )
    )
    try:
        credential = await client.bootstrap_monitor_credential()
    finally:
        client.reset_bakery_client_config(token)

    assert credential.monitor_uuid == "issued-uuid"
    assert credential.hmac_secret == "issued-secret"
    assert captured["method"] == "POST"
    assert captured["url"] == "https://bakery.example.com/api/v1/monitors/register"
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert str(headers["Authorization"]).startswith("HMAC bootstrap:")
    assert "X-Bakery-Monitor-UUID" not in headers
    body = json.loads(captured["content"])
    assert body["monitor_id"] == "rackspace/poundcake"
    assert written[0]["payload"] == {
        "monitor_id": "rackspace/poundcake",
        "monitor_uuid": "issued-uuid",
        "hmac_key_id": "active",
        "hmac_secret": "issued-secret",
    }


@pytest.mark.asyncio
async def test_bakery_bootstrap_force_reregisters_when_bootstrap_hmac_present(
    monkeypatch,
) -> None:
    async def read_adapter_credential_payload(
        *,
        service_type: str,
        credential_type: str,
        credential_key_id: str = "default",
    ) -> dict[str, object] | None:
        return {
            "monitor_uuid": "old-uuid",
            "monitor_id": "rackspace/poundcake",
            "hmac_key_id": "active",
            "hmac_secret": "old-secret",
        }

    async def write_adapter_credential(**kwargs: object) -> None:
        return None

    async def request_with_retry(*args: object, **kwargs: object) -> _FakeBakeryResponse:
        return _FakeBakeryResponse(
            {
                "monitor_uuid": "new-uuid",
                "monitor_id": "rackspace/poundcake",
                "hmac_key_id": "active",
                "hmac_secret": "new-secret",
            }
        )

    monkeypatch.setenv("POUNDCAKE_BAKERY_MONITOR_ID", "rackspace/poundcake")
    monkeypatch.setenv("POUNDCAKE_BAKERY_BOOTSTRAP_HMAC_KEY_ID", "bootstrap")
    monkeypatch.setenv("POUNDCAKE_BAKERY_BOOTSTRAP_HMAC_KEY", "bootstrap-secret")
    monkeypatch.setattr(client, "read_adapter_credential_payload", read_adapter_credential_payload)
    monkeypatch.setattr(client, "write_adapter_credential", write_adapter_credential)
    monkeypatch.setattr(client, "request_with_retry", request_with_retry)

    token = client.set_bakery_client_config(
        BakeryClientConfig(base_url="https://bakery.example.com")
    )
    try:
        credential = await client.bootstrap_monitor_credential(force=True)
    finally:
        client.reset_bakery_client_config(token)

    assert credential.monitor_uuid == "new-uuid"
    assert credential.hmac_secret == "new-secret"


@pytest.mark.asyncio
async def test_bakery_health_execution_bootstraps_before_remote_health(monkeypatch) -> None:
    calls: list[tuple[str, str | None]] = []

    async def ensure(*, force: bool = False) -> BakeryMonitorCredential:
        del force
        calls.append(("credential_check", "credential-manager"))
        return BakeryMonitorCredential(
            monitor_uuid="monitor-1",
            monitor_id="poundcake",
            hmac_key_id="key-1",
            hmac_secret="secret",
        )

    async def health() -> BakeryHealth:
        calls.append(("health", None))
        return BakeryHealth(status="healthy", version="unit")

    monkeypatch.setattr("api.plugins.bakery.adapter.bootstrap_monitor_credential", ensure)
    monkeypatch.setattr("api.plugins.bakery.adapter.get_health", health)

    result = await BakeryExecutionAdapter().dispatch(
        ExecutionContext(
            service_type="bakery",
            service_exec="health_check",
            req_id="unit-test",
        )
    )

    assert calls == [("credential_check", "credential-manager"), ("health", None)]
    assert result.status == "succeeded"
    assert result.result
    assert result.result["status"] == "healthy"
    assert result.result["details"]["credential_check_status"] == "ready"


@pytest.mark.asyncio
async def test_bakery_adapter_bootstrap_uses_credential_manager_boundary(monkeypatch) -> None:
    """Verify adapter goes through credential-manager boundary (writer_service_type removed)."""
    writers: list[bool] = []

    async def ensure(*, force: bool = False) -> BakeryMonitorCredential:
        del force
        writers.append(True)
        return BakeryMonitorCredential(
            monitor_uuid="monitor-1",
            monitor_id="poundcake",
            hmac_key_id="key-1",
            hmac_secret="secret",
        )

    monkeypatch.setattr("api.plugins.bakery.adapter.bootstrap_monitor_credential", ensure)

    await BakeryExecutionAdapter().bootstrap_credentials()

    assert writers == [True]


@pytest.mark.asyncio
async def test_bakery_credential_failure_is_initializing_and_redacted(monkeypatch) -> None:
    async def ensure(*, force: bool = False) -> BakeryMonitorCredential:
        del force
        raise RuntimeError("Authorization HMAC signature hmac_secret encrypted_payload")

    monkeypatch.setattr("api.plugins.bakery.adapter.bootstrap_monitor_credential", ensure)

    result = await BakeryExecutionAdapter().dispatch(
        ExecutionContext(service_type="bakery", service_exec="health_check", req_id="unit-test")
    )

    assert result.status == "succeeded"
    assert result.result
    assert result.result["status"] == "initializing"
    details = str(result.result["details"])
    assert "Authorization" not in details
    assert "HMAC" not in details
    assert "hmac_secret" not in details
    assert "encrypted_payload" not in details
