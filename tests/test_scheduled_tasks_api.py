"""Unit tests for scheduled task operator runtime actions."""

from __future__ import annotations

from fastapi import HTTPException
import pytest

from api.api.scheduled_tasks import (
    _serialize_scheduled_task_status,
    request_scheduled_task_run_now,
)
from api.core.time import utc_now_db
from api.models.models import ScheduledTask
from api.plugins.genestack_monitoring.templates import GENESTACK_MONITORING_CONTENT_SYNC_PARAMETERS
from api.plugins.stackstorm.templates import (
    STACKSTORM_CONTENT_OPERATION_METADATA,
    STACKSTORM_CONTENT_OPERATIONS,
)


class _Db:
    def __init__(self, row: ScheduledTask | None) -> None:
        self.row = row
        self.committed = False
        self.refreshed = False

    async def get(self, _model: object, _id: int) -> ScheduledTask | None:
        return self.row

    async def commit(self) -> None:
        self.committed = True

    async def refresh(self, _row: ScheduledTask) -> None:
        self.refreshed = True


def _task(**overrides: object) -> ScheduledTask:
    values: dict[str, object] = {
        "id": 1,
        "task_key": "plugin-content-sync:dummy",
        "task_type": "service_execution",
        "service_type": "dummy",
        "service_exec": "positive_result",
        "source": "plugin_manifest",
        "is_enabled": True,
        "run_interval_seconds": 300,
        "priority": 50,
        "timeout_seconds": 30,
        "status": "idle",
        "consecutive_failures": 0,
        "created_at": utc_now_db(),
        "updated_at": utc_now_db(),
    }
    values.update(overrides)
    return ScheduledTask(**values)


@pytest.mark.asyncio
@pytest.mark.parametrize("task_type", ["plugin_health_check", "service_execution"])
async def test_operator_can_request_plugin_scheduled_task_run_now(task_type: str) -> None:
    row = _task(
        task_key=f"plugin-{task_type}:dummy",
        task_type=task_type,
        service_exec="health_check" if task_type == "plugin_health_check" else "positive_result",
    )
    db = _Db(row)

    response = await request_scheduled_task_run_now(
        row.id,
        db=db,  # type: ignore[arg-type]
        _context=object(),
    )

    assert db.committed is True
    assert db.refreshed is True
    assert row.next_run_at is not None
    assert row.last_message == "Run requested by operator"
    assert row.status == "idle"
    assert response.id == row.id
    assert response.last_message == "Run requested by operator"


@pytest.mark.asyncio
async def test_operator_run_now_is_idempotent_for_queued_plugin_scheduled_task() -> None:
    row = _task(status="queued", next_run_at=utc_now_db())
    original_next_run_at = row.next_run_at
    db = _Db(row)

    response = await request_scheduled_task_run_now(
        row.id,
        db=db,  # type: ignore[arg-type]
        _context=object(),
    )

    assert db.committed is True
    assert db.refreshed is True
    assert row.status == "queued"
    assert row.next_run_at == original_next_run_at
    assert row.last_message == "Run already queued by Dishwasher"
    assert response.last_message == "Run already queued by Dishwasher"


def test_scheduled_task_status_labels_genestack_content_sync() -> None:
    response = _serialize_scheduled_task_status(
        _task(
            task_key="plugin-content-sync:genestack_monitoring",
            service_type="genestack_monitoring",
            service_exec="content_sync",
            task_parameters=GENESTACK_MONITORING_CONTENT_SYNC_PARAMETERS,
        )
    )

    assert response.run_now_label == "Sync content"
    assert response.run_now_description == (
        "Refresh PoundCake recipes from the Genestack Monitoring alert catalog."
    )


def test_scheduled_task_status_labels_stackstorm_content_sync() -> None:
    response = _serialize_scheduled_task_status(
        _task(
            task_key="stackstorm-content-sync",
            service_type="stackstorm",
            service_exec="content_sync",
            task_parameters={
                "operation": STACKSTORM_CONTENT_OPERATIONS[0],
                "allowed_operations": STACKSTORM_CONTENT_OPERATIONS,
                "operation_metadata": STACKSTORM_CONTENT_OPERATION_METADATA,
            },
        )
    )

    assert response.run_now_label == "Sync content"
    assert response.run_now_description == "Sync PoundCake-owned StackStorm action metadata."


def test_scheduled_task_status_labels_alertmanager_silence_sync() -> None:
    response = _serialize_scheduled_task_status(
        _task(
            task_key="alertmanager-sync-silences",
            service_type="alertmanager",
            service_exec="sync_silences",
            task_parameters={},
        )
    )

    assert response.run_now_label == "Sync silences"
    assert response.run_now_description == "Request Dishwasher to sync silences now."


def test_scheduled_task_status_labels_health_check() -> None:
    response = _serialize_scheduled_task_status(
        _task(
            task_key="plugin-health-check:dummy",
            task_type="plugin_health_check",
            service_exec="health_check",
        )
    )

    assert response.run_now_label == "Run health check"
    assert response.run_now_description == (
        "Request Dishwasher to run this plugin health check now."
    )


def test_scheduled_task_status_stays_redacted() -> None:
    response = _serialize_scheduled_task_status(
        _task(
            task_payload={"token": "secret"},
            task_parameters={"operation": "sync_secret"},
            expected_outcome={"success": True},
        )
    )

    dumped = response.model_dump()
    assert "task_payload" not in dumped
    assert "task_parameters" not in dumped
    assert "expected_outcome" not in dumped


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "status_code"),
    [
        (_task(source="registered"), 400),
        (_task(is_enabled=False), 400),
        (_task(status="running"), 409),
        (_task(task_type="core_cleanup"), 400),
        (_task(service_type=None), 400),
        (_task(service_exec=None), 400),
    ],
)
async def test_operator_run_now_rejects_non_runnable_tasks(
    row: ScheduledTask,
    status_code: int,
) -> None:
    db = _Db(row)

    with pytest.raises(HTTPException) as exc:
        await request_scheduled_task_run_now(
            row.id,
            db=db,  # type: ignore[arg-type]
            _context=object(),
        )

    assert exc.value.status_code == status_code
    assert db.committed is False


@pytest.mark.asyncio
async def test_operator_run_now_rejects_missing_task() -> None:
    with pytest.raises(HTTPException) as exc:
        await request_scheduled_task_run_now(
            404,
            db=_Db(None),  # type: ignore[arg-type]
            _context=object(),
        )

    assert exc.value.status_code == 404
