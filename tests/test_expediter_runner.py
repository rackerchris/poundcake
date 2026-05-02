"""Unit tests for the internal expediter runner."""

from __future__ import annotations

import ast
from pathlib import Path

import kitchen.expediter_runner as runner


def test_expediter_runner_does_not_import_provider_adapters_or_clients() -> None:
    source = Path(runner.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    provider_imports: list[str] = []
    allowed_api_plugin_modules = {"api.plugins.state"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if (
                    alias.name.startswith("api.plugins.")
                    and alias.name not in allowed_api_plugin_modules
                ):
                    provider_imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith("api.plugins.") and module not in allowed_api_plugin_modules:
                provider_imports.append(module)

    assert provider_imports == []


def test_expediter_runner_uses_expediter_execute_but_not_status_or_cancel() -> None:
    source = Path(runner.__file__).read_text(encoding="utf-8")

    assert "/expediter/execute/" in source
    assert "/expediter/status/" not in source
    assert "/expediter/cancel/" not in source
    assert "adapter.dispatch" not in source
    assert "adapter.poll" not in source
    assert "adapter.cancel" not in source


def test_expediter_runner_reconciles_terminal_result_and_advances(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    reconciled: list[dict] = []
    advanced: list[int] = []
    row = {
        "id": 7,
        "dish_id": 11,
        "req_id": "unit-test",
        "service_exec_id": "expediter-runner:7",
        "service_exec_start_time": "2026-05-05T00:00:00+00:00",
    }

    class Response:
        status_code = 200
        text = ""

        def json(self) -> dict:
            return {
                "status": "succeeded",
                "service_exec_id": "expediter-runner:7",
                "service_exec_actual_outcome": {"success": True},
            }

    monkeypatch.setattr(runner, "_claim_row", lambda _row, _req_id: dict(row))
    monkeypatch.setattr(
        runner,
        "_post_reconcile",
        lambda _row, payload, _req_id: reconciled.append(payload) or payload,
    )
    monkeypatch.setattr(
        runner, "_advance_dish", lambda claimed, _req_id: advanced.append(claimed["dish_id"])
    )

    def request(method: str, url: str, **_kwargs):
        calls.append((method, url))
        return Response()

    monkeypatch.setattr(runner, "request_control_plane_sync", request)

    runner._execute_row(row, "unit-test")

    assert calls == [("POST", f"{runner.API_BASE_URL}/expediter/execute/7")]
    assert reconciled[0]["service_exec_status"] == "succeeded"
    assert reconciled[0]["service_exec_actual_outcome"] == {"success": True}
    assert advanced == [11]


def test_expediter_runner_releases_nonterminal_provider_receipt(monkeypatch) -> None:
    reconciled: list[dict] = []
    released: list[int] = []
    advanced: list[int] = []
    row = {
        "id": 8,
        "dish_id": 12,
        "req_id": "unit-test",
        "service_exec_id": "expediter-runner:8",
        "service_exec_start_time": "2026-05-05T00:00:00+00:00",
    }

    class Response:
        status_code = 200
        text = ""

        def json(self) -> dict:
            return {
                "status": "running",
                "service_exec_id": "provider:work:abc",
            }

    monkeypatch.setattr(runner, "_claim_row", lambda _row, _req_id: dict(row))
    monkeypatch.setattr(
        runner,
        "_post_reconcile",
        lambda _row, payload, _req_id: reconciled.append(payload) or payload,
    )
    monkeypatch.setattr(
        runner, "_release_row", lambda claimed, _req_id: released.append(claimed["id"])
    )
    monkeypatch.setattr(
        runner, "_advance_dish", lambda claimed, _req_id: advanced.append(claimed["dish_id"])
    )
    monkeypatch.setattr(runner, "request_control_plane_sync", lambda *_args, **_kwargs: Response())

    runner._execute_row(row, "unit-test")

    assert reconciled[0] == {
        "service_exec_id": "provider:work:abc",
        "service_exec_status": "running",
        "service_exec_error": None,
    }
    assert released == [8]
    assert advanced == []


def test_expediter_runner_preserves_context_updates_for_later_steps() -> None:
    row = {
        "id": 9,
        "service_exec_id": "expediter-runner:9",
        "service_exec_actual_outcome": {"_context_updates": {"bakery_ticket_id": "TICKET-1"}},
        "service_exec_start_time": "2026-05-05T00:00:00+00:00",
    }
    body = {
        "status": "succeeded",
        "service_exec_id": "provider:work:done",
        "service_exec_actual_outcome": {"success": True},
        "context_updates": {"runbook_id": "RB-1"},
    }

    payload = runner._terminal_payload(row, body, "succeeded")

    assert payload["service_exec_actual_outcome"] == {
        "success": True,
        "_context_updates": {
            "bakery_ticket_id": "TICKET-1",
            "runbook_id": "RB-1",
        },
    }
