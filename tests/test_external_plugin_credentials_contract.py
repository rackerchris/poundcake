"""External plugin credential contract checks."""

from __future__ import annotations

from api.plugins.catalog import get_enabled_plugins
from api.services.credentials import CREDENTIAL_MANAGER_SERVICE_TYPE


def test_external_plugins_advertise_credentials_without_db_policy(monkeypatch) -> None:
    monkeypatch.setenv(
        "POUNDCAKE_ENABLED_PLUGINS",
        "alertmanager,bakery,dummy,genestack_monitoring,git,github,k8s,prometheus,stackstorm",
    )

    requirements_by_service = {
        plugin.service_type: plugin.adapter_factory().credential_requirements()
        for plugin in get_enabled_plugins()
    }

    assert requirements_by_service["alertmanager"] == [
        {
            "credential_type": "alertmanager_http_auth",
            "credential_key_id": "default",
            "required": False,
            "usage": "Optional Alertmanager API credentials for authenticated alert management.",
        }
    ]
    assert requirements_by_service["bakery"] == [
        {
            "credential_type": "bakery_monitor_hmac",
            "credential_key_id": "default",
            "required": True,
            "usage": (
                "Bakery monitor HMAC issued by remote registration. "
                "PoundCake registers with a bootstrap HMAC from "
                "POUNDCAKE_BAKERY_BOOTSTRAP_HMAC_KEY_ID/"
                "POUNDCAKE_BAKERY_BOOTSTRAP_HMAC_KEY and stores the "
                "returned bakery_monitor_hmac/default credential."
            ),
            "credential_schema": {
                "type": "object",
                "properties": {
                    "monitor_uuid": {"type": "string", "title": "Monitor UUID"},
                    "monitor_id": {"type": "string", "title": "Monitor ID"},
                    "hmac_key_id": {"type": "string", "title": "HMAC key ID"},
                    "hmac_secret": {"type": "string", "title": "HMAC secret"},
                },
                "required": ["monitor_uuid", "monitor_id", "hmac_key_id", "hmac_secret"],
                "additionalProperties": True,
            },
        },
    ]
    assert requirements_by_service["github"] == [
        {
            "credential_type": "github_token",
            "credential_key_id": "default",
            "required": False,
            "usage": (
                "GitHub API token. Required for write operations. For read "
                "operations, the credential manager controls whether public "
                "read endpoints (raw.githubusercontent.com) are permitted via "
                "the allow_public_read flag. Default is false — adapters must "
                "not bypass this flag."
            ),
        }
    ]
    assert requirements_by_service["git"] == [
        {
            "credential_type": "git_repository_auth",
            "credential_key_id": "default",
            "required": False,
            "usage": (
                "Git repository token or SSH key path. Required for write operations. "
                "For unauthenticated reads, the credential manager controls whether "
                "public read access is permitted via the allow_public_read flag."
            ),
        }
    ]
    assert requirements_by_service["prometheus"] == [
        {
            "credential_type": "prometheus_http_auth",
            "credential_key_id": "default",
            "required": False,
            "usage": "Optional Prometheus API credentials for authenticated monitoring endpoints.",
        }
    ]
    assert requirements_by_service["k8s"] == [
        {
            "credential_type": "kubernetes_kubeconfig",
            "credential_key_id": "default",
            "required": False,
            "usage": (
                "Optional kubeconfig for Kubernetes API access; falls back to "
                "in-cluster service account when absent."
            ),
        }
    ]
    assert requirements_by_service["stackstorm"] == [
        {
            "credential_type": "stackstorm_api_key",
            "credential_key_id": "default",
            "required": True,
            "usage": "StackStorm API key or auth token for action execution.",
        }
    ]
    assert requirements_by_service["genestack_monitoring"] == []
    assert {
        service_type
        for service_type, requirements in requirements_by_service.items()
        if requirements
    } == {"alertmanager", "bakery", "git", "github", "k8s", "prometheus", "stackstorm"}


def test_credential_manager_is_canonical_external_plugin_writer() -> None:
    assert CREDENTIAL_MANAGER_SERVICE_TYPE == "credential-manager"


def test_external_plugins_expose_operator_config_contract(monkeypatch) -> None:
    monkeypatch.setenv(
        "POUNDCAKE_ENABLED_PLUGINS",
        "alertmanager,bakery,dummy,genestack_monitoring,git,github,k8s,prometheus,stackstorm",
    )

    configurable = {
        plugin.service_type: plugin.adapter_factory().operator_config_schema()
        for plugin in get_enabled_plugins()
        if plugin.adapter_factory().operator_config_schema().get("properties")
    }

    assert set(configurable) == {
        "alertmanager",
        "bakery",
        "git",
        "github",
        "k8s",
        "prometheus",
        "stackstorm",
    }
    for service_type, schema in configurable.items():
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert isinstance(schema["properties"], dict), service_type
