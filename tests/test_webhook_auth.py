from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.api import webhook
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
