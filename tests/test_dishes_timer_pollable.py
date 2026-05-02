"""Regression tests for Timer in-flight row selection."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from api.api.dishes import (
    _advance_ready_representative,
    _collapse_dish_ingredient_records,
    _dispatch_identity_timed_out,
    _timer_pollable,
)
from api.api.expediter import expediter_runner_claimable


def _row(
    *,
    service_exec_start_time: datetime | None,
    service_exec_timeout: int | None = 30,
    service_exec_id: str | None = None,
    service_exec: str = "run",
) -> SimpleNamespace:
    return SimpleNamespace(
        service_exec=service_exec,
        service_exec_id=service_exec_id,
        service_exec_start_time=service_exec_start_time,
        service_exec_timeout=service_exec_timeout,
    )


def _runtime_row(
    row_id: int,
    *,
    status: str,
    depth: int,
    on_failure: str = "stop",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=row_id,
        service_exec_status=status,
        depth=depth,
        step_order=depth,
        parallel_group=0,
        on_failure=on_failure,
    )


def _plugin(*, enabled: bool, health_status: str) -> SimpleNamespace:
    return SimpleNamespace(enabled=enabled, health_status=health_status)


def test_dispatch_identity_timeout_aligns_naive_start_with_aware_now() -> None:
    row = _row(service_exec_start_time=datetime(2026, 5, 3, 6, 0, 0))
    now = datetime(2026, 5, 3, 6, 0, 31, tzinfo=timezone.utc)

    assert _dispatch_identity_timed_out(row, now) is True


def test_dispatch_identity_timeout_aligns_aware_start_with_naive_now() -> None:
    row = _row(service_exec_start_time=datetime(2026, 5, 3, 6, 0, 0, tzinfo=timezone.utc))
    now = datetime(2026, 5, 3, 6, 0, 10)

    assert _dispatch_identity_timed_out(row, now) is False


def test_timer_pollable_handles_missing_execution_identity_before_timeout() -> None:
    row = _row(service_exec_start_time=datetime(2026, 5, 3, 6, 0, 0))
    now = datetime(2026, 5, 3, 6, 0, 10, tzinfo=timezone.utc)

    assert _timer_pollable(row, now) is False


def test_timer_pollable_keeps_execution_identity_pollable_without_timeout() -> None:
    row = _row(
        service_exec_id="dummy:positive_result:receipt",
        service_exec_start_time=None,
        service_exec_timeout=None,
    )
    now = datetime(2026, 5, 3, 6, 0, 10, tzinfo=timezone.utc)

    assert _timer_pollable(row, now) is True


def test_timer_does_not_poll_runner_receipts_before_timeout() -> None:
    row = _row(
        service_exec_id="expediter-runner:42",
        service_exec_start_time=datetime(2026, 5, 3, 6, 0, 0),
        service_exec_timeout=30,
    )
    now = datetime(2026, 5, 3, 6, 0, 10, tzinfo=timezone.utc)

    assert _timer_pollable(row, now) is False


def test_timer_can_timeout_runner_receipts() -> None:
    row = _row(
        service_exec_id="expediter-runner:42",
        service_exec_start_time=datetime(2026, 5, 3, 6, 0, 0),
        service_exec_timeout=30,
    )
    now = datetime(2026, 5, 3, 6, 0, 31, tzinfo=timezone.utc)

    assert _timer_pollable(row, now) is True


def test_runner_claimable_blocks_disabled_adapter_before_timeout() -> None:
    row = _row(
        service_exec_start_time=datetime(2026, 5, 3, 6, 0, 0),
        service_exec_timeout=30,
        service_exec_id="expediter-runner:42",
    )
    now = datetime(2026, 5, 3, 6, 0, 10, tzinfo=timezone.utc)

    assert (
        expediter_runner_claimable(row, _plugin(enabled=False, health_status="disabled"), now)
        is False
    )


def test_runner_claimable_allows_disabled_adapter_after_timeout() -> None:
    row = _row(
        service_exec_start_time=datetime(2026, 5, 3, 6, 0, 0),
        service_exec_timeout=30,
        service_exec_id="expediter-runner:42",
    )
    now = datetime(2026, 5, 3, 6, 0, 31, tzinfo=timezone.utc)

    assert (
        expediter_runner_claimable(row, _plugin(enabled=False, health_status="disabled"), now)
        is True
    )


def test_runner_claimable_allows_callable_adapter_before_timeout() -> None:
    row = _row(
        service_exec_start_time=datetime(2026, 5, 3, 6, 0, 0),
        service_exec_timeout=30,
        service_exec_id="expediter-runner:42",
    )
    now = datetime(2026, 5, 3, 6, 0, 10, tzinfo=timezone.utc)

    assert (
        expediter_runner_claimable(row, _plugin(enabled=True, health_status="healthy"), now) is True
    )


def test_runner_claimable_allows_health_check_for_failed_adapter_before_timeout() -> None:
    row = _row(
        service_exec="health_check",
        service_exec_start_time=datetime(2026, 5, 3, 6, 0, 0),
        service_exec_timeout=30,
        service_exec_id="expediter-runner:42",
    )
    now = datetime(2026, 5, 3, 6, 0, 10, tzinfo=timezone.utc)

    assert (
        expediter_runner_claimable(row, _plugin(enabled=True, health_status="failed"), now) is True
    )


def test_runner_claimable_blocks_health_check_for_disabled_adapter_before_timeout() -> None:
    row = _row(
        service_exec="health_check",
        service_exec_start_time=datetime(2026, 5, 3, 6, 0, 0),
        service_exec_timeout=30,
        service_exec_id="expediter-runner:42",
    )
    now = datetime(2026, 5, 3, 6, 0, 10, tzinfo=timezone.utc)

    assert (
        expediter_runner_claimable(row, _plugin(enabled=False, health_status="failed"), now)
        is False
    )


def test_collapse_dish_ingredient_records_handles_naive_runtime_timestamps() -> None:
    record = SimpleNamespace(
        id=1,
        recipe_ingredient=None,
        task_key="dummy.positive_result",
        service_payload=None,
        service_exec_parameters=None,
        service_exec_actual_outcome=None,
        service_exec_start_time=datetime(2026, 5, 3, 6, 0, 0),
        service_exec_completed_time=None,
        created_at=datetime(2026, 5, 3, 6, 0, 0),
        updated_at=datetime(2026, 5, 3, 6, 0, 1),
    )

    assert _collapse_dish_ingredient_records([record]) == [(record, None)]


def test_advance_ready_representative_returns_blocking_terminal_before_future_pending() -> None:
    rows = [
        _runtime_row(1, status="failed", depth=10),
        _runtime_row(2, status="pending", depth=20),
        _runtime_row(3, status="pending", depth=30),
    ]

    assert _advance_ready_representative(rows).id == 1


def test_advance_ready_representative_returns_sync_success_for_timer_driven_next_step() -> None:
    rows = [
        _runtime_row(1, status="succeeded", depth=10),
        _runtime_row(2, status="pending", depth=20),
    ]

    assert _advance_ready_representative(rows).id == 1


def test_advance_ready_representative_skips_dishes_with_in_flight_rows() -> None:
    rows = [
        _runtime_row(1, status="succeeded", depth=10),
        _runtime_row(2, status="running", depth=20),
    ]

    assert _advance_ready_representative(rows) is None
