"""Unit tests for dish ingredient execution segment selection."""

from __future__ import annotations

from kitchen.execution_segments import next_pending_execution_segment


def _row(
    row_id: int,
    *,
    status: str | None = "pending",
    service_type: str = "dummy",
    depth: int = 0,
    parallel_group: int = 0,
    on_failure: str = "stop",
) -> dict[str, object]:
    return {
        "id": row_id,
        "task_key": f"step_{row_id}",
        "service_type": service_type,
        "service_exec_status": status,
        "depth": depth,
        "parallel_group": parallel_group,
        "step_order": row_id,
        "on_failure": on_failure,
    }


def test_pending_rows_group_by_depth_and_parallel_group() -> None:
    segment = next_pending_execution_segment(
        {},
        [
            _row(1, depth=1, parallel_group=2),
            _row(2, depth=1, parallel_group=2, service_type="ansible"),
            _row(3, depth=2, parallel_group=1),
        ],
    )
    assert segment is not None
    assert segment.depth == 1
    assert segment.parallel_group == 2
    assert [row["id"] for row in segment.rows] == [1, 2]


def test_parallel_segment_returns_service_type_metadata_without_mixed_sentinel() -> None:
    segment = next_pending_execution_segment(
        {},
        [
            _row(1, service_type="dummy", depth=1, parallel_group=1),
            _row(2, service_type="k8s", depth=1, parallel_group=1),
        ],
    )
    assert segment is not None
    assert segment.service_types == ("dummy", "k8s")


def test_in_flight_row_blocks_later_segments() -> None:
    assert (
        next_pending_execution_segment(
            {},
            [
                _row(1, status="running", depth=1),
                _row(2, status="pending", depth=2),
            ],
        )
        is None
    )


def test_failed_row_blocks_unless_on_failure_continue() -> None:
    for status in ("failed", "errored"):
        assert (
            next_pending_execution_segment(
                {},
                [
                    _row(1, status=status, depth=1),
                    _row(2, status="pending", depth=2),
                ],
            )
            is None
        )
        segment = next_pending_execution_segment(
            {},
            [
                _row(1, status=status, depth=1, on_failure="continue"),
                _row(2, status="pending", depth=2),
            ],
        )
        assert segment is not None
        assert [row["id"] for row in segment.rows] == [2]
