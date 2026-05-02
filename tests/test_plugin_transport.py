"""Tests for the shared HTTP transport contract used by service plugins."""

from __future__ import annotations

from api.plugins.transport import PluginHttpTransportConfig, merge_plugin_request_kwargs


def test_plugin_transport_accepts_bearer_auth_over_https() -> None:
    transport = PluginHttpTransportConfig(
        service_label="Example",
        base_url="https://example.test",
        bearer_token="secret-token",
    )

    assert transport.auth_mode == "bearer"
    assert transport.secure_transport is True
    assert transport.validate_security() is None
    assert transport.safe_details() == {
        "url": "https://example.test",
        "verify_ssl": True,
        "auth_mode": "bearer",
        "secure_transport": True,
    }


def test_plugin_transport_allows_auth_to_in_cluster_service_urls() -> None:
    transport = PluginHttpTransportConfig(
        service_label="Example",
        base_url="http://example.monitoring.svc.cluster.local:9090",
        username="user",
        password="pass",
    )

    assert transport.auth_mode == "basic"
    assert transport.secure_transport is True
    assert transport.validate_security() is None


def test_plugin_transport_rejects_auth_over_insecure_remote_http() -> None:
    transport = PluginHttpTransportConfig(
        service_label="Example",
        base_url="http://example.test",
        bearer_token="secret-token",
    )

    assert (
        transport.validate_security()
        == "Example authentication requires HTTPS or an in-cluster service URL"
    )


def test_plugin_transport_merges_auth_headers_without_exposing_secret_metadata() -> None:
    transport = PluginHttpTransportConfig(
        service_label="Example",
        base_url="https://example.test",
        bearer_token="secret-token",
        verify_ssl=False,
    )

    kwargs = merge_plugin_request_kwargs(transport, {"headers": {"X-Request-ID": "unit"}})

    assert kwargs == {
        "verify": False,
        "headers": {
            "Authorization": "Bearer secret-token",
            "X-Request-ID": "unit",
        },
    }
    assert "secret-token" not in str(transport.safe_details())
