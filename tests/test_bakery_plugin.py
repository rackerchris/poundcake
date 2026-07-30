"""Unit tests for the Bakery service plugin contract."""

from __future__ import annotations

import time

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
from api.plugins.contract import validate_payload_schema
from api.services.credentials import decrypt_payload, encrypt_payload
from api.plugins.manifest import validate_service_plugin
from api.plugins.types import ExecutionContext
from shared.hmac import build_hmac_signing_payload, hmac_sha256_hex


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


def test_bakery_capability_templates_match_communication_ingredient() -> None:
    capability = load_bakery_capability_templates()[0]

    assert capability["capability_id"] == "bakery.communication.open.default"
    assert capability["ingredient_ref"]["service_exec"] == "communication"
    assert capability["operation"] == "open"
    assert capability["mode"] == "communication"


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
    monkeypatch.delenv("POUNDCAKE_BAKERY_ALLOW_INSECURE_HTTP", raising=False)
    monkeypatch.setenv("POUNDCAKE_BAKERY_BASE_URL", "http://bakery.example.com")

    assert client.validate_transport_config() == (
        "POUNDCAKE_BAKERY_BASE_URL must use https outside test/dev"
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
            "allow_insecure_http": True,
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
        "allow_insecure_http": True,
        "plugin_id": "rackspace/kronos-poundcake",
        "tags": "dev,kind",
    }
    assert config["plugin_id"] == "poundcake/bakery-plugin"


def test_bakery_adapter_validates_monitor_hmac_payload() -> None:
    adapter = BakeryExecutionAdapter()

    assert (
        adapter.validate_credential_payload(
            "bakery_bootstrap_hmac",
            {
                "hmac_key_id": "bootstrap",
                "hmac_secret": "secret",
            },
        )
        is None
    )
    assert (
        adapter.validate_credential_payload(
            "bakery_bootstrap_hmac",
            {"hmac_key_id": "bootstrap"},
        )
        == "Bakery bootstrap credential requires hmac_key_id and hmac_secret"
    )
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


@pytest.mark.asyncio
async def test_bakery_bootstrap_uses_configured_bootstrap_hmac_credential(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    written: list[dict[str, object]] = []

    async def read_adapter_credential_payload(
        *,
        service_type: str,
        credential_type: str,
        credential_key_id: str = "default",
    ) -> dict[str, object] | None:
        if credential_type == "bakery_bootstrap_hmac":
            return {"hmac_key_id": "bootstrap-id", "hmac_secret": "bootstrap-secret"}
        return None

    class _Response:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {
                "monitor_uuid": "monitor-uuid",
                "monitor_id": "rackspace/kronos-poundcake",
                "hmac_key_id": "active-id",
                "hmac_secret": "active-secret",
            }

    async def request_with_retry(*args: object, **kwargs: object) -> _Response:
        calls.append({"args": args, "kwargs": kwargs})
        return _Response()

    async def write_adapter_credential(**kwargs: object) -> None:
        written.append(kwargs)

    token = client.set_bakery_client_config(
        BakeryClientConfig(
            base_url="https://bakery.example.com",
            plugin_id="rackspace/kronos-poundcake",
        )
    )
    monkeypatch.setattr(client, "read_adapter_credential_payload", read_adapter_credential_payload)
    monkeypatch.setattr(client, "request_with_retry", request_with_retry)
    monkeypatch.setattr(client, "write_adapter_credential", write_adapter_credential)
    try:
        credential = await client.bootstrap_monitor_credential(force=True)
    finally:
        client.reset_bakery_client_config(token)

    assert credential.monitor_uuid == "monitor-uuid"
    assert calls
    headers = calls[0]["kwargs"]["headers"]  # type: ignore[index]
    assert "bootstrap-id:" in headers["Authorization"]  # type: ignore[index]
    assert written[0]["credential_type"] == "bakery_monitor_hmac"


@pytest.mark.asyncio
async def test_bakery_health_execution_bootstraps_before_remote_health(monkeypatch) -> None:
    calls: list[tuple[str, str | None]] = []

    async def bootstrap(
        *,
        force: bool = False,
        db: object | None = None,
    ) -> BakeryMonitorCredential:
        calls.append(("bootstrap", "credential-manager"))
        return BakeryMonitorCredential(
            monitor_uuid="monitor-1",
            monitor_id="poundcake",
            hmac_key_id="key-1",
            hmac_secret="secret",
        )

    async def health() -> BakeryHealth:
        calls.append(("health", None))
        return BakeryHealth(status="healthy", version="unit")

    monkeypatch.setattr("api.plugins.bakery.adapter.bootstrap_monitor_credential", bootstrap)
    monkeypatch.setattr("api.plugins.bakery.adapter.get_health", health)

    result = await BakeryExecutionAdapter().dispatch(
        ExecutionContext(
            service_type="bakery",
            service_exec="health_check",
            req_id="unit-test",
        )
    )

    assert calls == [("bootstrap", "credential-manager"), ("health", None)]
    assert result.status == "succeeded"
    assert result.result
    assert result.result["status"] == "healthy"
    assert result.result["details"]["bootstrap_status"] == "ready"


@pytest.mark.asyncio
async def test_bakery_adapter_bootstrap_uses_credential_manager_boundary(monkeypatch) -> None:
    """Verify adapter goes through credential-manager boundary (writer_service_type removed)."""
    writers: list[bool] = []

    async def bootstrap(
        *,
        force: bool = False,
        db: object | None = None,
    ) -> BakeryMonitorCredential:
        writers.append(True)
        return BakeryMonitorCredential(
            monitor_uuid="monitor-1",
            monitor_id="poundcake",
            hmac_key_id="key-1",
            hmac_secret="secret",
        )

    monkeypatch.setattr("api.plugins.bakery.adapter.bootstrap_monitor_credential", bootstrap)

    await BakeryExecutionAdapter().bootstrap_credentials()

    assert writers == [True]


@pytest.mark.asyncio
async def test_bakery_bootstrap_failure_is_initializing_and_redacted(monkeypatch) -> None:
    async def bootstrap(
        *,
        force: bool = False,
        db: object | None = None,
    ) -> BakeryMonitorCredential:
        raise RuntimeError("Authorization HMAC signature hmac_secret encrypted_payload")

    monkeypatch.setattr("api.plugins.bakery.adapter.bootstrap_monitor_credential", bootstrap)

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
