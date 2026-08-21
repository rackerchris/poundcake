"""Unit tests for the Bakery service plugin contract."""

from __future__ import annotations

import json
import time
from copy import deepcopy

import httpx
import pytest

from api.plugins.bakery.adapter import BakeryExecutionAdapter, _payload_with_dish_evidence
from api.plugins.bakery import client
from api.plugins.bakery import heartbeat as bakery_heartbeat
from api.plugins.bakery.client import (
    BakeryClientConfig,
    BakeryHealth,
    BakeryMonitorCredential,
    BakeryTicketAccepted,
    BakeryTicketOperation,
)
from api.plugins.bakery.contract import CommunicationOpenRequest, MonitorHeartbeatResponse
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
    expected_outcome_matches,
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


def test_bakery_reopen_payload_uses_feedback_received_for_core() -> None:
    adapter = BakeryExecutionAdapter()

    assert adapter._reopen_payload("rackspace_core") == {
        "context": {"attributes": {"status": "Feedback Received"}}
    }
    assert adapter._reopen_payload("discord") == {"state": "open"}


def test_incident_reconciliation_reopen_payload_uses_feedback_received() -> None:
    from api.plugins.bakery import incident_reconciliation

    assert incident_reconciliation._reopen_payload("rackspace_core") == {
        "context": {"attributes": {"status": "Feedback Received"}}
    }
    assert incident_reconciliation._reopen_payload("discord") == {"state": "open"}


def test_incident_reconciliation_treats_confirmed_solved_as_reopenable() -> None:
    from api.plugins.bakery import incident_reconciliation

    assert "confirmed_solved" in incident_reconciliation.TICKET_REOPENABLE_STATES
    assert "confirmed_solved" not in incident_reconciliation.TICKET_TERMINAL_STATES


def _accepted(action: str, ticket_id: str = "TICKET-1") -> BakeryTicketAccepted:
    return BakeryTicketAccepted(
        ticket_id=ticket_id,
        operation_id=f"op-{action}",
        action=action,
        status="accepted",
        created_at="2026-08-21T00:00:00Z",
    )


@pytest.mark.asyncio
async def test_bakery_open_reuses_prior_ticket_without_creating_new(monkeypatch) -> None:
    monkeypatch.setattr("api.plugins.bakery.adapter.validate_transport_config", lambda: None)
    updates: list[dict] = []
    comments: list[dict] = []

    async def update_ticket_with_key(*, req_id, ticket_id, payload, idempotency_key):
        updates.append({"ticket_id": ticket_id, "payload": payload})
        return _accepted("update", ticket_id)

    async def add_ticket_comment_with_key(*, req_id, ticket_id, payload, idempotency_key):
        comments.append({"ticket_id": ticket_id, "payload": payload})
        return _accepted("comment", ticket_id)

    async def create_ticket_with_key(*, req_id, payload, idempotency_key):
        raise AssertionError("open with a prior ticket id must not create a new ticket")

    monkeypatch.setattr("api.plugins.bakery.adapter.update_ticket_with_key", update_ticket_with_key)
    monkeypatch.setattr(
        "api.plugins.bakery.adapter.add_ticket_comment_with_key", add_ticket_comment_with_key
    )
    monkeypatch.setattr("api.plugins.bakery.adapter.create_ticket_with_key", create_ticket_with_key)

    adapter = BakeryExecutionAdapter()
    ctx = ExecutionContext(
        service_type="bakery",
        service_exec="communication",
        req_id="unit-test",
        service_payload={
            "title": "Refired alert",
            "description": "Alert fired again.",
            "source": "poundcake",
            "context": {"order_id": 348},
        },
        service_exec_parameters={"operation": "open"},
        context={
            "destination_target": "rackspace_core",
            "ticket_id": "TICKET-1",
            "communication_reuse_mode": "reopen",
        },
    )

    result = await adapter.dispatch(ctx)

    assert result.status in {"dispatched", "succeeded"}
    assert len(updates) == 1
    assert updates[0]["payload"] == {"context": {"attributes": {"status": "Feedback Received"}}}
    assert len(comments) == 1
    assert comments[0]["ticket_id"] == "TICKET-1"
    assert result.context_updates.get("bakery_comms_id") == "TICKET-1"


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


def _heartbeat_response_payload() -> dict[str, object]:
    return {
        "monitor_uuid": "monitor-uuid",
        "monitor_id": "rackspace/poundcake",
        "status": "healthy",
        "route_sync_required": False,
        "heartbeat_interval_sec": 30,
        "miss_threshold": 5,
        "recorded_at": "2026-08-14T12:00:00Z",
    }


def _heartbeat_credential() -> BakeryMonitorCredential:
    return BakeryMonitorCredential(
        monitor_uuid="monitor-uuid",
        monitor_id="rackspace/poundcake",
        hmac_key_id="active-id",
        hmac_secret="active-secret",
    )


async def _heartbeat_credential_provider() -> BakeryMonitorCredential:
    return _heartbeat_credential()


@pytest.mark.asyncio
async def test_bakery_send_heartbeat_posts_signed_payload(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def request_with_retry(method: str, url: str, **kwargs: object) -> _FakeBakeryResponse:
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = kwargs["headers"]
        captured["content"] = kwargs["content"]
        return _FakeBakeryResponse(_heartbeat_response_payload())

    monkeypatch.setattr(client, "request_with_retry", request_with_retry)
    monkeypatch.setattr(
        client, "ensure_monitor_credential_configured", _heartbeat_credential_provider
    )
    token = client.set_bakery_client_config(
        BakeryClientConfig(base_url="https://bakery.example.com", plugin_id="rackspace/poundcake")
    )
    try:
        response = await client.send_heartbeat({"installation_id": "instance-1"})
    finally:
        client.reset_bakery_client_config(token)

    assert captured["method"] == "POST"
    assert captured["url"] == "https://bakery.example.com/api/v1/monitors/heartbeat"
    headers = captured["headers"]
    assert str(headers["Authorization"]).startswith("HMAC active-id:")
    assert headers["X-Timestamp"]
    assert headers["X-Bakery-Monitor-UUID"] == "monitor-uuid"
    assert json.loads(captured["content"].decode("utf-8")) == {"installation_id": "instance-1"}
    assert isinstance(response, MonitorHeartbeatResponse)
    assert response.heartbeat_interval_sec == 30


@pytest.mark.asyncio
async def test_bakery_heartbeat_once_re_registers_on_401(monkeypatch) -> None:
    calls = {"request": 0, "bootstrap": 0}
    bootstrap_force: list[bool] = []

    async def request_with_retry(method: str, url: str, **kwargs: object) -> _FakeBakeryResponse:
        calls["request"] += 1
        if calls["request"] == 1:
            raise httpx.HTTPStatusError(
                "unauthorized", request=httpx.Request("POST", url), response=httpx.Response(401)
            )
        return _FakeBakeryResponse(_heartbeat_response_payload())

    async def bootstrap_monitor_credential(*, force: bool = False) -> BakeryMonitorCredential:
        calls["bootstrap"] += 1
        bootstrap_force.append(force)
        return _heartbeat_credential()

    monkeypatch.setattr(client, "request_with_retry", request_with_retry)
    monkeypatch.setattr(
        client, "ensure_monitor_credential_configured", _heartbeat_credential_provider
    )
    monkeypatch.setattr(
        bakery_heartbeat, "bootstrap_monitor_credential", bootstrap_monitor_credential
    )
    token = client.set_bakery_client_config(
        BakeryClientConfig(base_url="https://bakery.example.com", plugin_id="rackspace/poundcake")
    )
    try:
        response = await bakery_heartbeat.heartbeat_once()
    finally:
        client.reset_bakery_client_config(token)

    assert calls["request"] == 2
    assert calls["bootstrap"] == 1
    assert bootstrap_force == [True]
    assert response.monitor_id == "rackspace/poundcake"


@pytest.mark.asyncio
async def test_bakery_heartbeat_once_propagates_non_401(monkeypatch) -> None:
    async def request_with_retry(method: str, url: str, **kwargs: object) -> _FakeBakeryResponse:
        raise httpx.HTTPStatusError(
            "boom", request=httpx.Request("POST", url), response=httpx.Response(500)
        )

    async def unexpected_bootstrap(*, force: bool = False) -> object:
        raise AssertionError("non-401 heartbeat failure must not re-register")

    monkeypatch.setattr(client, "request_with_retry", request_with_retry)
    monkeypatch.setattr(
        client, "ensure_monitor_credential_configured", _heartbeat_credential_provider
    )
    monkeypatch.setattr(bakery_heartbeat, "bootstrap_monitor_credential", unexpected_bootstrap)
    token = client.set_bakery_client_config(
        BakeryClientConfig(base_url="https://bakery.example.com", plugin_id="rackspace/poundcake")
    )
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await bakery_heartbeat.heartbeat_once()
    finally:
        client.reset_bakery_client_config(token)


def test_bakery_heartbeat_enabled_requires_plugin_and_transport(monkeypatch) -> None:
    monkeypatch.setenv("POUNDCAKE_ENABLED_PLUGINS", "bakery")
    monkeypatch.setenv("POUNDCAKE_BAKERY_BASE_URL", "https://bakery.example.com")
    assert bakery_heartbeat.heartbeat_enabled() is True

    monkeypatch.delenv("POUNDCAKE_BAKERY_BASE_URL", raising=False)
    assert bakery_heartbeat.heartbeat_enabled() is False

    monkeypatch.setenv("POUNDCAKE_BAKERY_BASE_URL", "https://bakery.example.com")
    monkeypatch.setenv("POUNDCAKE_ENABLED_PLUGINS", "dummy")
    assert bakery_heartbeat.heartbeat_enabled() is False


def _succeeded_operation() -> BakeryTicketOperation:
    return BakeryTicketOperation(
        operation_id="op-1",
        ticket_id="260814-03355",
        action="create",
        status="succeeded",
        attempt_count=1,
        max_attempts=3,
        created_at="2026-08-14T12:00:00Z",
        updated_at="2026-08-14T12:00:05Z",
    )


@pytest.mark.asyncio
async def test_bakery_poll_outcome_carries_success_flag(monkeypatch) -> None:
    async def poll_operation(operation_id: str) -> BakeryTicketOperation:
        assert operation_id == "bakery:communication:op-1"
        return _succeeded_operation()

    monkeypatch.setattr("api.plugins.bakery.adapter.poll_operation", poll_operation)
    adapter = BakeryExecutionAdapter(
        BakeryClientConfig(base_url="https://bakery.example.com", plugin_id="rackspace/poundcake")
    )
    ctx = ExecutionContext(service_type="bakery", service_exec="communication", req_id="unit-test")

    result = await adapter.poll(ctx, "bakery:communication:op-1")

    assert result.status == "succeeded"
    assert result.result
    assert result.result["success"] is True
    # ticket_id must stay top-level for observability consumers
    assert result.result["ticket_id"] == "260814-03355"
    assert (
        expected_outcome_matches(
            expected={"success": True}, actual=result.result, status=result.status
        )
        is True
    )


@pytest.mark.asyncio
async def test_bakery_poll_outcome_marks_failure(monkeypatch) -> None:
    def _failed_operation() -> BakeryTicketOperation:
        return BakeryTicketOperation(
            operation_id="op-1",
            ticket_id="260814-03355",
            action="create",
            status="failed",
            attempt_count=3,
            max_attempts=3,
            last_error="provider rejected",
            created_at="2026-08-14T12:00:00Z",
            updated_at="2026-08-14T12:00:05Z",
        )

    async def poll_operation(operation_id: str) -> BakeryTicketOperation:
        return _failed_operation()

    monkeypatch.setattr("api.plugins.bakery.adapter.poll_operation", poll_operation)
    adapter = BakeryExecutionAdapter(
        BakeryClientConfig(base_url="https://bakery.example.com", plugin_id="rackspace/poundcake")
    )
    ctx = ExecutionContext(service_type="bakery", service_exec="communication", req_id="unit-test")

    result = await adapter.poll(ctx, "bakery:communication:op-1")

    assert result.status == "failed"
    assert result.result
    assert result.result["success"] is False
    assert (
        expected_outcome_matches(
            expected={"success": True}, actual=result.result, status=result.status
        )
        is False
    )
