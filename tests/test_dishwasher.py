"""Unit tests for Dishwasher scheduled order helpers."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from kitchen import dishwasher


class _FakeUuid:
    hex = "abcdef1234567890"


class _Response:
    def __init__(
        self,
        status_code: int,
        payload: object,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)
        self.headers = headers or {}

    def json(self) -> object:
        return self._payload


def _ingredient_row() -> dict[str, object]:
    return {
        "id": 101,
        "service_type": "dummy",
        "service_exec": "positive_result",
        "destination_target": "",
        "task_key_template": "dummy-positive-result",
    }


def _recipe_template(*, service_payload: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "name": "dummy-positive-result",
        "description": "Managed dummy recipe",
        "enabled": True,
        "clear_timeout_sec": None,
        "recipe_ingredients": [
            {
                "service_type": "dummy",
                "service_exec": "positive_result",
                "destination_target": "",
                "task_key_template": "dummy-positive-result",
                "step_order": 1,
                "on_success": "continue",
                "parallel_group": 0,
                "depth": 0,
                "service_payload": service_payload or {"result": "ok"},
                "service_exec_parameters_override": None,
                "service_exec_expected_secs": 10,
                "service_exec_timeout": 30,
                "service_exec_expected_outcome": {"status": "succeeded"},
                "run_phase": "firing",
                "run_condition": "always",
            }
        ],
    }


def _existing_recipe(*, service_payload: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "id": 7,
        "name": "dummy-positive-result",
        "description": "Managed dummy recipe",
        "enabled": True,
        "clear_timeout_sec": None,
        "recipe_ingredients": [
            {
                "id": 77,
                "recipe_id": 7,
                "ingredient_id": 101,
                "step_order": 1,
                "on_success": "continue",
                "parallel_group": 0,
                "depth": 0,
                "service_payload": service_payload or {"result": "ok"},
                "service_exec_parameters_override": None,
                "service_exec_expected_secs": 10,
                "service_exec_timeout": 30,
                "service_exec_expected_outcome": {"status": "succeeded"},
                "run_phase": "firing",
                "run_condition": "always",
                "ingredient": _ingredient_row(),
            }
        ],
    }


def test_recipe_contract_hash_matches_equivalent_existing_recipe() -> None:
    payload = dishwasher._recipe_payload(_recipe_template(), {_dishwasher_identity(): 101})

    assert dishwasher._recipe_contracts_match(payload, _existing_recipe()) is True


def test_recipe_payload_preserves_runtime_payload_marker_without_contract_drift() -> None:
    template = _recipe_template(service_payload={})
    template["recipe_ingredients"][0]["service_payload_from_order"] = True

    payload = dishwasher._recipe_payload(template, {_dishwasher_identity(): 101})

    assert payload["recipe_ingredients"][0]["service_payload_from_order"] is True
    assert dishwasher._recipe_contracts_match(payload, _existing_recipe(service_payload={}))


def test_sync_plugin_recipes_skips_unchanged_recipe(monkeypatch) -> None:
    calls: list[str] = []

    def request(method: str, _url: str, **_kwargs: object) -> _Response:
        calls.append(method)
        return _Response(200, _existing_recipe())

    monkeypatch.setattr(
        dishwasher, "get_enabled_plugin_recipe_templates", lambda: [_recipe_template()]
    )
    monkeypatch.setattr(dishwasher, "request_control_plane_sync", request)

    stats = dishwasher._sync_plugin_recipes(ingredient_rows=[_ingredient_row()], req_id="unit-test")

    assert stats == {"created": 0, "updated": 0, "skipped": 1, "errors": 0}
    assert calls == ["GET"]


def test_sync_plugin_recipes_updates_changed_recipe(monkeypatch) -> None:
    calls: list[str] = []

    def request(method: str, _url: str, **_kwargs: object) -> _Response:
        calls.append(method)
        if method == "GET":
            return _Response(200, _existing_recipe(service_payload={"result": "old"}))
        return _Response(200, {"id": 7})

    monkeypatch.setattr(
        dishwasher, "get_enabled_plugin_recipe_templates", lambda: [_recipe_template()]
    )
    monkeypatch.setattr(dishwasher, "request_control_plane_sync", request)

    stats = dishwasher._sync_plugin_recipes(ingredient_rows=[_ingredient_row()], req_id="unit-test")

    assert stats == {"created": 0, "updated": 1, "skipped": 0, "errors": 0}
    assert calls == ["GET", "PATCH"]


def test_sync_plugin_communication_routes_reports_no_change(monkeypatch, caplog) -> None:
    def request(_method: str, _url: str, **_kwargs: object) -> _Response:
        return _Response(200, {"configured": True}, headers={"X-PoundCake-Changed": "false"})

    monkeypatch.setattr(
        dishwasher, "get_enabled_plugin_communication_routes", lambda: [{"id": "dummy"}]
    )
    monkeypatch.setattr(dishwasher, "request_control_plane_sync", request)

    with caplog.at_level(logging.INFO, logger=dishwasher.logger.name):
        stats = dishwasher.sync_plugin_communication_routes(req_id="unit-test")

    assert stats == {"changed": False, "route_count": 1, "errors": 0}
    assert "Plugin communication policy sync complete" not in caplog.text


def test_sync_plugin_communication_routes_logs_change(monkeypatch, caplog) -> None:
    def request(_method: str, _url: str, **_kwargs: object) -> _Response:
        return _Response(200, {"configured": True}, headers={"X-PoundCake-Changed": "true"})

    monkeypatch.setattr(
        dishwasher, "get_enabled_plugin_communication_routes", lambda: [{"id": "dummy"}]
    )
    monkeypatch.setattr(dishwasher, "request_control_plane_sync", request)

    with caplog.at_level(logging.INFO, logger=dishwasher.logger.name):
        stats = dishwasher.sync_plugin_communication_routes(req_id="unit-test")

    assert stats == {"changed": True, "route_count": 1, "errors": 0}
    assert "Plugin communication policy sync complete" in caplog.text


def test_run_sync_noop_summary_logs_at_debug(monkeypatch, caplog) -> None:
    def request(method: str, url: str, **_kwargs: object) -> _Response:
        if method == "POST" and url.endswith("/internal/service-registry/ingredients/bulk"):
            return _Response(200, [_ingredient_row()])
        raise AssertionError(f"unexpected request: {method} {url}")

    monkeypatch.setattr(dishwasher, "get_enabled_plugin_ingredient_templates", lambda: [])
    monkeypatch.setattr(dishwasher, "get_enabled_plugins", lambda: [])
    monkeypatch.setattr(dishwasher, "request_control_plane_sync", request)
    monkeypatch.setattr(
        dishwasher,
        "sync_plugin_communication_routes",
        lambda **_kwargs: {"changed": False, "route_count": 0, "errors": 0},
    )
    monkeypatch.setattr(
        dishwasher,
        "_sync_plugin_recipes",
        lambda **_kwargs: {"created": 0, "updated": 0, "skipped": 1, "errors": 0},
    )
    monkeypatch.setattr(
        dishwasher,
        "sync_scheduled_tasks",
        lambda **_kwargs: {"created": 0, "updated": 0, "skipped": 1, "errors": 0},
    )

    with caplog.at_level(logging.INFO, logger=dishwasher.logger.name):
        assert dishwasher.run_sync() is True

    assert "Plugin manifest sync complete" not in caplog.text


def test_run_sync_changed_summary_logs_at_info(monkeypatch, caplog) -> None:
    def request(method: str, url: str, **_kwargs: object) -> _Response:
        if method == "POST" and url.endswith("/internal/service-registry/ingredients/bulk"):
            return _Response(200, [_ingredient_row()])
        raise AssertionError(f"unexpected request: {method} {url}")

    monkeypatch.setattr(dishwasher, "get_enabled_plugin_ingredient_templates", lambda: [])
    monkeypatch.setattr(dishwasher, "get_enabled_plugins", lambda: [])
    monkeypatch.setattr(dishwasher, "request_control_plane_sync", request)
    monkeypatch.setattr(
        dishwasher,
        "sync_plugin_communication_routes",
        lambda **_kwargs: {"changed": False, "route_count": 0, "errors": 0},
    )
    monkeypatch.setattr(
        dishwasher,
        "_sync_plugin_recipes",
        lambda **_kwargs: {"created": 0, "updated": 1, "skipped": 0, "errors": 0},
    )
    monkeypatch.setattr(
        dishwasher,
        "sync_scheduled_tasks",
        lambda **_kwargs: {"created": 0, "updated": 0, "skipped": 1, "errors": 0},
    )

    with caplog.at_level(logging.INFO, logger=dishwasher.logger.name):
        assert dishwasher.run_sync() is True

    assert "Plugin manifest sync complete" in caplog.text


def _dishwasher_identity() -> tuple[str, str, str, str]:
    return ("dummy", "positive_result", "", "dummy-positive-result")


def test_scheduled_task_req_id_prefers_supported_log_key(monkeypatch) -> None:
    monkeypatch.setattr(
        dishwasher,
        "_now",
        lambda: datetime(2026, 5, 1, 12, 30, 45, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(dishwasher, "uuid4", lambda: _FakeUuid())

    req_id = dishwasher._scheduled_task_req_id(
        {
            "id": 42,
            "service_type": "Bakery",
        },
        {"bakery": "bakery"},
    )

    assert req_id == "SYSTEM-SCHEDULED-bakery-42-20260501123045-abcdef"
    assert len(req_id) <= 100


def test_scheduled_task_req_id_falls_back_without_plugin_row(monkeypatch) -> None:
    monkeypatch.setattr(
        dishwasher,
        "_now",
        lambda: datetime(2026, 5, 1, 12, 30, 45, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(dishwasher, "uuid4", lambda: _FakeUuid())

    req_id = dishwasher._scheduled_task_req_id(
        {
            "id": 42,
            "service_type": "Rackspace/Core Plugin With A Very Long Name",
        },
        {},
    )

    assert req_id.startswith("SYSTEM-SCHEDULED-unknown-rackspace-core-plugin-wi-42-")
    assert len(req_id) <= 100
