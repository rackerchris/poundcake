"""Unit tests for operator suppression lifecycle routes."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from api.api.suppressions import cancel_suppression, create_suppression, patch_suppression
from api.models.models import AlertSuppression, AlertSuppressionMatcher
from api.schemas.schemas import SuppressionCreate, SuppressionUpdate


def _request(path: str) -> Request:
    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "query_string": b"",
            "headers": [(b"host", b"testserver")],
            "scheme": "http",
            "server": ("testserver", 80),
        },
        receive,
    )
    request.state.req_id = "test-suppression-route"
    return request


def _suppression_row() -> AlertSuppression:
    row = AlertSuppression(
        id=42,
        name="Database maintenance",
        reason="Kernel patching",
        scope="matchers",
        enabled=True,
        starts_at=datetime(2026, 7, 14, 0, 0, tzinfo=timezone.utc),
        ends_at=datetime(2026, 7, 14, 1, 0, tzinfo=timezone.utc),
        canceled_at=None,
        created_by="alice",
        summary_ticket_enabled=True,
        source="plugin",
        source_service_type="alertmanager",
        source_ref="sil-42",
        source_payload={"id": "sil-42"},
        last_synced_at=datetime(2026, 7, 14, 0, 0, tzinfo=timezone.utc),
        created_at=datetime(2026, 7, 14, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 14, 0, 0, tzinfo=timezone.utc),
    )
    row.matchers = [
        AlertSuppressionMatcher(
            suppression_id=42,
            label_key="alertname",
            operator="eq",
            value="NodeDown",
        )
    ]
    row.summary = None
    return row


class _Db:
    async def execute(self, _statement: object) -> object:
        return SimpleNamespace(scalar_one_or_none=lambda: None)


@pytest.mark.asyncio
async def test_create_suppression_route_uses_alertmanager_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def _fake_create_alertmanager_suppression(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            order_id=201,
            order_req_id=str(kwargs["req_id"]),
            service_type="alertmanager",
            service_exec="suppression",
            submitted_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
        )

    monkeypatch.setattr(
        "api.api.suppressions.create_alertmanager_suppression",
        _fake_create_alertmanager_suppression,
    )

    payload = SuppressionCreate(
        name="Database maintenance",
        starts_at=datetime(2026, 7, 14, 0, 0, tzinfo=timezone.utc),
        ends_at=datetime(2026, 7, 14, 1, 0, tzinfo=timezone.utc),
        matchers=[{"label_key": "alertname", "operator": "eq", "value": "NodeDown"}],
        reason="Kernel patching",
        created_by="alice",
        summary_ticket_enabled=True,
    )

    response = await create_suppression(
        request=_request("/api/v1/suppressions"),
        payload=payload,
        db=_Db(),  # type: ignore[arg-type]
        _context=object(),
    )

    assert captured["req_id"] == "test-suppression-route"
    assert captured["payload"].name == "Database maintenance"
    assert response.status == "accepted"
    assert response.message == "Suppression create order accepted"
    assert response.order_id == 201
    assert response.order_req_id == "test-suppression-route"
    assert response.service_type == "alertmanager"
    assert response.service_exec == "suppression"


@pytest.mark.asyncio
async def test_cancel_suppression_route_expires_alertmanager_silence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suppression = _suppression_row()
    captured: dict[str, object] = {}

    async def _fake_get_suppression(_db: object, suppression_id: int) -> AlertSuppression | None:
        assert suppression_id == 42
        return suppression

    async def _fake_expire_alertmanager_suppression(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            order_id=202,
            order_req_id=str(kwargs["req_id"]),
            service_type="alertmanager",
            service_exec="suppression",
            submitted_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
        )

    monkeypatch.setattr("api.api.suppressions.get_suppression", _fake_get_suppression)
    monkeypatch.setattr(
        "api.api.suppressions.expire_alertmanager_suppression",
        _fake_expire_alertmanager_suppression,
    )

    response = await cancel_suppression(
        request=_request("/api/v1/suppressions/42/cancel"),
        suppression_id=42,
        db=_Db(),  # type: ignore[arg-type]
        _context=object(),
    )

    assert captured["req_id"] == "test-suppression-route"
    assert captured["suppression"].source_ref == "sil-42"
    assert response.status == "accepted"
    assert response.message == "Suppression cancel order accepted"
    assert response.order_id == 202
    assert response.service_type == "alertmanager"
    assert response.service_exec == "suppression"


@pytest.mark.asyncio
async def test_update_suppression_route_updates_alertmanager_backed_suppression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suppression = _suppression_row()
    captured: dict[str, object] = {}

    async def _fake_get_suppression(_db: object, suppression_id: int) -> AlertSuppression | None:
        assert suppression_id == 42
        return suppression

    async def _fake_update_alertmanager_suppression(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            order_id=203,
            order_req_id=str(kwargs["req_id"]),
            service_type="alertmanager",
            service_exec="suppression",
            submitted_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
        )

    monkeypatch.setattr("api.api.suppressions.get_suppression", _fake_get_suppression)
    monkeypatch.setattr(
        "api.api.suppressions.update_alertmanager_suppression",
        _fake_update_alertmanager_suppression,
    )

    payload = SuppressionUpdate(reason="Extended maintenance")
    response = await patch_suppression(
        request=_request("/api/v1/suppressions/42"),
        suppression_id=42,
        payload=payload,
        db=_Db(),  # type: ignore[arg-type]
        _context=object(),
    )

    assert captured["req_id"] == "test-suppression-route"
    assert captured["suppression"].source_ref == "sil-42"
    assert captured["payload"].reason == "Extended maintenance"
    assert response.status == "accepted"
    assert response.message == "Suppression update order accepted"
    assert response.order_id == 203
