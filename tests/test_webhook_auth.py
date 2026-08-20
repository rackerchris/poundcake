from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api.api import webhook
from api.main import app
from api.services.auth_service import AccessDeniedError, ensure_request_authorized


@pytest.mark.asyncio
async def test_webhook_bearer_sets_webhook_service_context(monkeypatch: pytest.MonkeyPatch) -> None:
    request = SimpleNamespace(state=SimpleNamespace())
    monkeypatch.setattr(
        webhook,
        "get_settings",
        lambda: SimpleNamespace(webhook_bearer_token="secret-token"),
    )

    context = await webhook.require_webhook_bearer(request, "Bearer secret-token")

    assert context.principal_type == "service"
    assert context.service_type == "webhook"
    assert context.username == "webhook:alertmanager"
    assert context.credential_scope == "alertmanager_webhook"
    assert request.state.auth_context is context


@pytest.mark.asyncio
async def test_webhook_service_context_cannot_call_control_plane_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = SimpleNamespace(state=SimpleNamespace())
    monkeypatch.setattr(
        webhook,
        "get_settings",
        lambda: SimpleNamespace(webhook_bearer_token="secret-token"),
    )

    context = await webhook.require_webhook_bearer(request, "Bearer secret-token")

    with pytest.raises(AccessDeniedError):
        ensure_request_authorized(context, "/api/v1/orders", "GET")

    with pytest.raises(AccessDeniedError):
        ensure_request_authorized(context, "/api/v1/plugins/stackstorm/credentials", "PUT")


@pytest.mark.asyncio
async def test_webhook_bearer_rejects_missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    request = SimpleNamespace(state=SimpleNamespace())
    monkeypatch.setattr(
        webhook,
        "get_settings",
        lambda: SimpleNamespace(webhook_bearer_token="secret-token"),
    )

    with pytest.raises(HTTPException) as exc:
        await webhook.require_webhook_bearer(request, None)

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_webhook_bearer_rejects_invalid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    request = SimpleNamespace(state=SimpleNamespace())
    monkeypatch.setattr(
        webhook,
        "get_settings",
        lambda: SimpleNamespace(webhook_bearer_token="secret-token"),
    )

    with pytest.raises(HTTPException) as exc:
        await webhook.require_webhook_bearer(request, "Bearer wrong")

    assert exc.value.status_code == 403


def test_webhook_validation_error_serializes_to_422(monkeypatch: pytest.MonkeyPatch) -> None:
    """A payload missing required label fields must return a JSON 422, not 500.

    Regression: the validation handler used ``exc.errors()`` directly, whose
    ``ctx`` holds a raw ``ValueError`` that is not JSON-serializable, so the
    response crashed with ``TypeError: Object of type ValueError is not JSON
    serializable`` and Alertmanager saw a 500 instead of a 422.
    """
    monkeypatch.setattr(
        webhook,
        "get_settings",
        lambda: SimpleNamespace(webhook_bearer_token="secret-token"),
    )

    payload = {
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": "Watchdog", "severity": "critical"},
                "annotations": {},
                "startsAt": "2026-08-20T00:00:00Z",
                "fingerprint": "test-fingerprint-1",
            }
        ],
    }

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/v1/webhook",
            json=payload,
            headers={"Authorization": "Bearer secret-token"},
        )

    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "validation_error"
    detail = body["detail"]
    assert any("group_name" in str(item.get("msg", "")) for item in detail)
