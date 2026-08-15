"""Unit tests for Timer reconciliation contract behavior."""

from __future__ import annotations

import ast
from pathlib import Path

import kitchen.timer as timer
from api.plugins.state import verdict_status


def test_timer_does_not_import_provider_adapters_or_clients() -> None:
    source = Path(timer.__file__).read_text(encoding="utf-8")
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


def test_timer_uses_expediter_only_for_readonly_status_and_explicit_cancel() -> None:
    source = Path(timer.__file__).read_text(encoding="utf-8")

    assert "/expediter/status/" in source
    assert "/expediter/cancel/" in source
    assert "/expediter/dispatch" not in source
    assert "expediter_dispatch_from_cook" not in source
    assert "adapter.dispatch" not in source
    assert "adapter.poll" not in source


def test_verdict_status_treats_expected_negative_outcome_as_success() -> None:
    assert (
        verdict_status(
            requested_status="failed",
            expected_outcome={"success": False},
            actual_outcome={"success": False},
        )
        == "succeeded"
    )


def test_terminal_expected_outcomes_are_rewritten_to_success() -> None:
    assert (
        verdict_status(
            requested_status="errored",
            expected_outcome={"status": "errored"},
            actual_outcome={"status": "errored"},
        )
        == "succeeded"
    )
    assert (
        verdict_status(
            requested_status="timeout",
            expected_outcome={"timeout": True},
            actual_outcome={"timeout": True},
        )
        == "succeeded"
    )


def test_canceled_verdict_preserves_canceled_lifecycle_status() -> None:
    assert (
        verdict_status(
            requested_status="canceled",
            expected_outcome={"status": "canceled"},
            actual_outcome={"status": "canceled"},
        )
        == "canceled"
    )
    assert (
        verdict_status(
            requested_status="canceled",
            expected_outcome={"success": True},
            actual_outcome={"status": "canceled"},
        )
        == "canceled"
    )


def test_timer_poll_transport_failure_releases_claimed_row(
    monkeypatch,
) -> None:
    released: list[int] = []
    row = {
        "id": 99,
        "req_id": "unit-test",
        "service_type": "dummy",
        "service_exec_id": "dummy:positive_result:receipt",
        "service_exec_timeout": 30,
        "service_exec_start_time": "2026-05-01T00:00:00+00:00",
    }

    monkeypatch.setattr(timer, "_claim_row", lambda _row, _req_id: dict(row))
    monkeypatch.setattr(timer, "_runtime_exceeded_timeout", lambda _row: False)
    monkeypatch.setattr(
        timer,
        "request_control_plane_sync",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("dns unavailable")),
    )
    monkeypatch.setattr(
        timer, "_release_row", lambda claimed, _req_id: released.append(claimed["id"])
    )

    timer._poll_row(row, "unit-test")

    assert released == [99]


def test_timer_poll_transport_failure_times_out_after_runtime_timeout(monkeypatch) -> None:
    reconciled: list[dict] = []
    canceled: list[int] = []
    released: list[int] = []
    row = {
        "id": 100,
        "dish_id": 201,
        "req_id": "unit-test",
        "service_type": "dummy",
        "service_exec_id": "dummy:slow_result:receipt",
        "service_exec_timeout": 30,
        "service_exec_start_time": "2026-05-01T00:00:00+00:00",
    }

    monkeypatch.setattr(timer, "_claim_row", lambda _row, _req_id: dict(row))
    monkeypatch.setattr(timer, "_runtime_exceeded_timeout", lambda _row: True)
    monkeypatch.setattr(
        timer,
        "request_control_plane_sync",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("timed out")),
    )
    monkeypatch.setattr(
        timer, "_cancel_execution", lambda claimed, _req_id: canceled.append(claimed["id"])
    )
    monkeypatch.setattr(
        timer,
        "_post_reconcile",
        lambda _row, payload, _req_id: reconciled.append(payload)
        or {"service_exec_status": payload["service_exec_status"]},
    )
    monkeypatch.setattr(
        timer, "_release_row", lambda claimed, _req_id: released.append(claimed["id"])
    )

    result = timer._poll_row(row, "unit-test")

    assert canceled == [100]
    assert released == []
    assert reconciled[0]["service_exec_status"] == "timeout"
    assert (
        reconciled[0]["service_exec_actual_outcome"]["reason"]
        == "expediter_status_poll_transport_failure"
    )
    assert result["terminal"] is True
    assert result["blocking_failure"] is True


def test_timer_status_poll_timeout_uses_service_execution_timeout(monkeypatch) -> None:
    calls: list[dict] = []
    row = {
        "id": 106,
        "dish_id": 207,
        "req_id": "unit-test",
        "service_type": "dummy",
        "service_exec_id": "dummy:slow_result:receipt",
        "service_exec_timeout": 45,
        "service_exec_start_time": "2026-05-01T00:00:00+00:00",
    }

    class Response:
        status_code = 200

        def json(self) -> dict[str, str]:
            return {"status": "running"}

    def request(*_args, **kwargs) -> Response:
        calls.append(kwargs)
        return Response()

    monkeypatch.setattr(timer, "_claim_row", lambda _row, _req_id: dict(row))
    monkeypatch.setattr(timer, "_runtime_exceeded_timeout", lambda _row: False)
    monkeypatch.setattr(timer, "request_control_plane_sync", request)
    monkeypatch.setattr(timer, "_release_row", lambda _row, _req_id: None)

    timer._poll_row(row, "unit-test")

    assert calls[0]["timeout"] == 45


def test_timer_status_http_error_marks_runtime_errored(monkeypatch) -> None:
    reconciled: list[dict] = []
    row = {
        "id": 101,
        "dish_id": 202,
        "req_id": "unit-test",
        "service_type": "dummy",
        "service_exec_id": "dummy:positive_result:receipt",
        "service_exec_timeout": 30,
        "service_exec_start_time": "2026-05-01T00:00:00+00:00",
    }

    class Response:
        status_code = 503
        text = "plugin failed"

        def json(self) -> dict[str, str]:
            return {"detail": "service plugin dummy is failed"}

    monkeypatch.setattr(timer, "_claim_row", lambda _row, _req_id: dict(row))
    monkeypatch.setattr(timer, "_runtime_exceeded_timeout", lambda _row: False)
    monkeypatch.setattr(timer, "request_control_plane_sync", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(
        timer,
        "_post_reconcile",
        lambda _row, payload, _req_id: reconciled.append(payload)
        or {"service_exec_status": payload["service_exec_status"]},
    )

    result = timer._poll_row(row, "unit-test")

    assert reconciled[0]["service_exec_status"] == "errored"
    assert reconciled[0]["service_exec_actual_outcome"]["reason"] == "expediter_status_poll_failed"
    assert result["terminal"] is True
    assert result["blocking_failure"] is True


def test_timer_missing_execution_identity_releases_before_timeout(monkeypatch) -> None:
    released: list[int] = []
    reconciled: list[dict] = []
    row = {
        "id": 104,
        "dish_id": 205,
        "req_id": "unit-test",
        "service_type": "dummy",
        "service_exec_id": None,
        "service_exec_timeout": 30,
        "service_exec_start_time": "2026-05-01T00:00:00+00:00",
    }

    monkeypatch.setattr(timer, "_claim_row", lambda _row, _req_id: dict(row))
    monkeypatch.setattr(timer, "_runtime_exceeded_timeout", lambda _row: False)
    monkeypatch.setattr(
        timer, "_release_row", lambda claimed, _req_id: released.append(claimed["id"])
    )
    monkeypatch.setattr(
        timer,
        "_post_reconcile",
        lambda _row, payload, _req_id: reconciled.append(payload) or True,
    )

    timer._poll_row(row, "unit-test")

    assert released == [104]
    assert reconciled == []


def test_timer_missing_execution_identity_times_out_after_timeout(monkeypatch) -> None:
    reconciled: list[dict] = []
    row = {
        "id": 105,
        "dish_id": 206,
        "req_id": "unit-test",
        "service_type": "dummy",
        "service_exec_id": None,
        "service_exec_timeout": 30,
        "service_exec_start_time": "2026-05-01T00:00:00+00:00",
    }

    monkeypatch.setattr(timer, "_claim_row", lambda _row, _req_id: dict(row))
    monkeypatch.setattr(timer, "_runtime_exceeded_timeout", lambda _row: True)
    monkeypatch.setattr(
        timer,
        "_post_reconcile",
        lambda _row, payload, _req_id: reconciled.append(payload)
        or {"service_exec_status": payload["service_exec_status"]},
    )
    monkeypatch.setattr(timer, "_cancel_blocked_future_rows", lambda *_args, **_kwargs: None)

    result = timer._poll_row(row, "unit-test")

    assert reconciled[0]["service_exec_status"] == "timeout"
    assert (
        reconciled[0]["service_exec_actual_outcome"]["reason"]
        == "missing_service_execution_identity"
    )
    assert result["terminal"] is True
    assert result["blocking_failure"] is True


def test_timer_terminal_status_wins_over_local_timeout(monkeypatch) -> None:
    reconciled: list[dict] = []
    canceled: list[int] = []
    calls: list[tuple[str, str]] = []
    row = {
        "id": 102,
        "dish_id": 203,
        "req_id": "unit-test",
        "service_type": "dummy",
        "service_exec": "sleep_10",
        "service_exec_id": "dummy:sleep_10:receipt",
        "service_exec_expected_secs": 30,
        "service_exec_timeout": 30,
        "service_exec_start_time": "2026-05-01T00:00:00+00:00",
    }

    class Response:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {
                "status": "succeeded",
                "service_exec_actual_outcome": {"success": True},
            }

    def request(method: str, url: str, **_kwargs) -> Response:
        calls.append((method, url))
        return Response()

    monkeypatch.setattr(timer, "_claim_row", lambda _row, _req_id: dict(row))
    monkeypatch.setattr(timer, "_runtime_exceeded_timeout", lambda _row: True)
    monkeypatch.setattr(timer, "request_control_plane_sync", request)
    monkeypatch.setattr(
        timer,
        "_post_reconcile",
        lambda _row, payload, _req_id: reconciled.append(payload)
        or {"service_exec_status": payload["service_exec_status"]},
    )
    monkeypatch.setattr(
        timer, "_cancel_execution", lambda claimed, _req_id: canceled.append(claimed["id"])
    )

    result = timer._poll_row(row, "unit-test")

    assert calls == [
        (
            "GET",
            f"{timer.API_BASE_URL}/expediter/status/dummy/dummy:sleep_10:receipt",
        )
    ]
    assert canceled == []
    assert reconciled[0]["service_exec_status"] == "succeeded"
    assert reconciled[0]["service_exec_actual_outcome"] == {"success": True}
    assert result["terminal"] is True
    assert result["blocking_failure"] is False


def test_timer_nonterminal_status_times_out_after_local_timeout(monkeypatch) -> None:
    reconciled: list[dict] = []
    canceled: list[int] = []
    row = {
        "id": 103,
        "dish_id": 204,
        "req_id": "unit-test",
        "service_type": "dummy",
        "service_exec": "slow_result",
        "service_exec_id": "dummy:slow_result:receipt",
        "service_exec_timeout": 30,
        "service_exec_start_time": "2026-05-01T00:00:00+00:00",
    }

    class Response:
        status_code = 200

        def json(self) -> dict[str, str]:
            return {"status": "running"}

    monkeypatch.setattr(timer, "_claim_row", lambda _row, _req_id: dict(row))
    monkeypatch.setattr(timer, "_runtime_exceeded_timeout", lambda _row: True)
    monkeypatch.setattr(timer, "request_control_plane_sync", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(
        timer,
        "_post_reconcile",
        lambda _row, payload, _req_id: reconciled.append(payload)
        or {"service_exec_status": payload["service_exec_status"]},
    )
    monkeypatch.setattr(
        timer, "_cancel_execution", lambda claimed, _req_id: canceled.append(claimed["id"])
    )

    result = timer._poll_row(row, "unit-test")

    assert canceled == [103]
    assert reconciled[0]["service_exec_status"] == "timeout"
    assert result["terminal"] is True
    assert result["blocking_failure"] is True


def test_timer_cancel_requests_flow_through_expediter(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    row = {
        "id": 107,
        "dish_id": 208,
        "service_type": "dummy",
        "service_exec_id": "dummy:slow_result:receipt",
    }

    class Response:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {
                "status": "canceled",
                "service_exec_actual_outcome": {"status": "canceled"},
            }

    def request(method: str, url: str, **_kwargs) -> Response:
        calls.append((method, url))
        return Response()

    monkeypatch.setattr(timer, "request_control_plane_sync", request)

    body = timer._cancel_execution(row, "unit-test")

    assert calls == [
        (
            "POST",
            f"{timer.API_BASE_URL}/expediter/cancel/dummy/dummy:slow_result:receipt",
        )
    ]
    assert body == {
        "status": "canceled",
        "service_exec_actual_outcome": {"status": "canceled"},
    }


def test_expected_timeout_verdict_does_not_count_as_blocking_failure() -> None:
    row = {
        "on_failure": "stop",
    }

    assert not timer._is_blocking_failure(row, {"service_exec_status": "succeeded"})


def test_unexpected_timeout_counts_as_blocking_failure() -> None:
    row = {
        "on_failure": "stop",
        "service_exec_expected_outcome": {"success": True},
    }

    assert timer._is_blocking_failure(row, {"service_exec_status": "timeout"})


def test_monitor_in_flight_advances_once_after_group_reconciles(monkeypatch) -> None:
    rows = [
        {"id": 1, "dish_id": 10, "req_id": "req-1", "depth": 1, "parallel_group": 1},
        {"id": 2, "dish_id": 10, "req_id": "req-1", "depth": 1, "parallel_group": 1},
    ]
    advanced: list[tuple[int, str]] = []

    class Response:
        status_code = 200

        def json(self) -> list[dict]:
            return rows

    def fake_poll(row: dict, req_id: str) -> dict:
        return {
            "dish_id": row["dish_id"],
            "req_id": req_id,
            "row_id": row["id"],
            "row": row,
            "reconciled": {"service_exec_status": "succeeded"},
            "terminal": True,
            "blocking_failure": False,
            "bucket": (1, 1),
        }

    monkeypatch.setattr(timer, "request_control_plane_sync", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(timer, "_poll_row", fake_poll)
    monkeypatch.setattr(
        timer,
        "_fetch_dish_ingredients",
        lambda _dish_id, _req_id: [
            {**rows[0], "service_exec_status": "succeeded"},
            {**rows[1], "service_exec_status": "succeeded"},
        ],
    )
    monkeypatch.setattr(
        timer,
        "_advance_dish",
        lambda row, req_id: advanced.append((row["dish_id"], req_id)),
    )

    timer.monitor_in_flight()

    assert advanced == [(10, "req-1")]


def test_monitor_in_flight_defers_when_group_sibling_still_running(monkeypatch) -> None:
    rows = [
        {"id": 1, "dish_id": 10, "req_id": "req-1", "depth": 1, "parallel_group": 1},
        {"id": 2, "dish_id": 10, "req_id": "req-1", "depth": 1, "parallel_group": 1},
    ]
    advanced: list[int] = []
    cascaded: list[int] = []

    class Response:
        status_code = 200

        def json(self) -> list[dict]:
            return rows

    def fake_poll(row: dict, req_id: str) -> dict:
        return {
            "dish_id": row["dish_id"],
            "req_id": req_id,
            "row_id": row["id"],
            "row": row,
            "reconciled": {"service_exec_status": "succeeded"} if row["id"] == 1 else None,
            "terminal": row["id"] == 1,
            "blocking_failure": False,
            "bucket": (1, 1),
        }

    monkeypatch.setattr(timer, "request_control_plane_sync", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(timer, "_poll_row", fake_poll)
    monkeypatch.setattr(
        timer,
        "_fetch_dish_ingredients",
        lambda _dish_id, _req_id: [
            {**rows[0], "service_exec_status": "succeeded"},
            {**rows[1], "service_exec_status": "running"},
        ],
    )
    monkeypatch.setattr(
        timer, "_advance_dish", lambda row, _req_id: advanced.append(row["dish_id"])
    )
    monkeypatch.setattr(
        timer,
        "_cancel_blocked_future_rows",
        lambda row, _req_id, *, reason: cascaded.append(row["dish_id"]),
    )

    timer.monitor_in_flight()

    assert advanced == []
    assert cascaded == []


def test_monitor_in_flight_cascades_once_after_group_failure(monkeypatch) -> None:
    rows = [
        {"id": 1, "dish_id": 10, "req_id": "req-1", "depth": 1, "parallel_group": 1},
        {"id": 2, "dish_id": 10, "req_id": "req-1", "depth": 1, "parallel_group": 1},
    ]
    advanced: list[int] = []
    cascaded: list[tuple[int, str]] = []

    class Response:
        status_code = 200

        def json(self) -> list[dict]:
            return rows

    def fake_poll(row: dict, req_id: str) -> dict:
        failed = row["id"] == 1
        return {
            "dish_id": row["dish_id"],
            "req_id": req_id,
            "row_id": row["id"],
            "row": row,
            "reconciled": {"service_exec_status": "timeout" if failed else "succeeded"},
            "terminal": True,
            "blocking_failure": failed,
            "reason": (
                "Service execution timed out before downstream groups ran" if failed else None
            ),
            "bucket": (1, 1),
        }

    monkeypatch.setattr(timer, "request_control_plane_sync", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(timer, "_poll_row", fake_poll)
    monkeypatch.setattr(
        timer,
        "_fetch_dish_ingredients",
        lambda _dish_id, _req_id: [
            {**rows[0], "service_exec_status": "timeout"},
            {**rows[1], "service_exec_status": "succeeded"},
        ],
    )
    monkeypatch.setattr(
        timer, "_advance_dish", lambda row, _req_id: advanced.append(row["dish_id"])
    )
    monkeypatch.setattr(
        timer,
        "_cancel_blocked_future_rows",
        lambda row, _req_id, *, reason: cascaded.append((row["id"], reason)),
    )

    timer.monitor_in_flight()

    assert cascaded == [(1, "Service execution timed out before downstream groups ran")]
    assert advanced == [10]


def test_monitor_advance_ready_advances_synchronous_success(monkeypatch) -> None:
    row = {
        "id": 1,
        "dish_id": 10,
        "req_id": "req-1",
        "depth": 1,
        "parallel_group": 1,
        "service_exec_status": "succeeded",
        "on_failure": "stop",
    }
    advanced: list[tuple[int, str]] = []
    cascaded: list[int] = []

    class Response:
        status_code = 200

        def json(self) -> list[dict]:
            return [row]

    monkeypatch.setattr(timer, "request_control_plane_sync", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(
        timer,
        "_cancel_blocked_future_rows",
        lambda row, _req_id, *, reason: cascaded.append(row["id"]),
    )
    monkeypatch.setattr(
        timer,
        "_advance_dish",
        lambda row, req_id: advanced.append((row["dish_id"], req_id)),
    )

    timer.monitor_advance_ready()

    assert cascaded == []
    assert advanced == [(10, "req-1")]


def test_monitor_advance_ready_cascades_synchronous_failure_then_advances_once(
    monkeypatch,
) -> None:
    row = {
        "id": 1,
        "dish_id": 10,
        "req_id": "req-1",
        "depth": 1,
        "parallel_group": 1,
        "service_exec_status": "failed",
        "service_exec_error": "first step failed",
        "on_failure": "stop",
    }
    advanced: list[tuple[int, str]] = []
    cascaded: list[tuple[int, str]] = []

    class Response:
        status_code = 200

        def json(self) -> list[dict]:
            return [row]

    monkeypatch.setattr(timer, "request_control_plane_sync", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(
        timer,
        "_cancel_blocked_future_rows",
        lambda row, _req_id, *, reason: cascaded.append((row["id"], reason)),
    )
    monkeypatch.setattr(
        timer,
        "_advance_dish",
        lambda row, req_id: advanced.append((row["dish_id"], req_id)),
    )

    timer.monitor_advance_ready()

    assert cascaded == [(1, "first step failed")]
    assert advanced == [(10, "req-1")]


def test_cancel_blocked_future_rows_cancels_all_future_pending_rows_once(monkeypatch) -> None:
    blocking = {"id": 1, "dish_id": 10, "req_id": "req-1", "depth": 1, "parallel_group": 1}
    rows = [
        {**blocking, "service_exec_status": "failed"},
        {
            "id": 2,
            "dish_id": 10,
            "req_id": "req-1",
            "depth": 2,
            "parallel_group": 1,
            "service_exec_status": "pending",
        },
        {
            "id": 3,
            "dish_id": 10,
            "req_id": "req-1",
            "depth": 3,
            "parallel_group": 1,
            "service_exec_status": "pending",
        },
        {
            "id": 4,
            "dish_id": 10,
            "req_id": "req-1",
            "depth": 4,
            "parallel_group": 1,
            "service_exec_status": "pending",
        },
    ]
    reconciled: list[tuple[int, str]] = []

    class Response:
        status_code = 200

        def json(self) -> list[dict]:
            return rows

    monkeypatch.setattr(timer, "request_control_plane_sync", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(
        timer,
        "_post_reconcile",
        lambda row, payload, _req_id: reconciled.append((row["id"], payload["service_exec_status"]))
        or {"service_exec_status": payload["service_exec_status"]},
    )

    timer._cancel_blocked_future_rows(blocking, "req-1", reason="first step failed")

    assert reconciled == [(2, "canceled"), (3, "canceled"), (4, "canceled")]


def test_timer_suppression_lifecycle_throttles_within_interval(monkeypatch) -> None:
    monkeypatch.setattr(timer, "LAST_SUPPRESSION_LIFECYCLE_RUN", 1000.0)
    monkeypatch.setattr(timer, "SUPPRESSION_LIFECYCLE_INTERVAL", 30)

    class _Time:
        @staticmethod
        def time() -> float:
            return 1010.0

    monkeypatch.setattr(timer, "time", _Time)

    calls: list[dict] = []

    def request(*_args, **kwargs) -> object:
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(timer, "request_control_plane_sync", request)

    timer.run_suppression_lifecycle()

    assert calls == []


def test_timer_suppression_lifecycle_posts_when_due(monkeypatch) -> None:
    monkeypatch.setattr(timer, "LAST_SUPPRESSION_LIFECYCLE_RUN", 0.0)
    monkeypatch.setattr(timer, "SUPPRESSION_LIFECYCLE_INTERVAL", 30)

    class _Time:
        @staticmethod
        def time() -> float:
            return 1000.0

    monkeypatch.setattr(timer, "time", _Time)

    calls: list[tuple[str, str]] = []

    class Response:
        status_code = 200

        def json(self) -> dict[str, int]:
            return {"status": "ok", "finalized": 2}

    def request(method: str, url: str, **_kwargs) -> Response:
        calls.append((method, url))
        return Response()

    monkeypatch.setattr(timer, "request_control_plane_sync", request)

    timer.run_suppression_lifecycle()

    assert calls == [("POST", f"{timer.API_BASE_URL}/suppressions/run-lifecycle")]
    assert timer.LAST_SUPPRESSION_LIFECYCLE_RUN == 1000.0
