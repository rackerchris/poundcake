"""Scheduled task control-plane APIs for the Dishwasher intake worker."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import get_db
from api.core.time import utc_now_db
from api.api.auth import require_admin, require_operator, require_reader, require_service
from api.models.models import Ingredient, ScheduledTask
from api.plugins.contract import (
    ServicePluginContractError,
    validate_service_payload_for_operation,
)
from api.schemas.schemas import (
    ScheduledTaskCreate,
    ScheduledTaskResponse,
    ScheduledTaskStatusResponse,
    ScheduledTaskUpdate,
)

router = APIRouter()

OPERATOR_SCHEDULED_TASK_UPDATE_FIELDS = frozenset({"is_enabled", "run_interval_seconds"})


def _now() -> datetime:
    return utc_now_db()


def _scheduled_task_run_now_metadata(row: ScheduledTask) -> tuple[str, str]:
    if row.task_type == "plugin_health_check":
        return (
            "Run health check",
            "Request Dishwasher to run this plugin health check now.",
        )

    operation_label, operation_description = _operation_display_metadata(row)
    if operation_label:
        return operation_label, operation_description or _default_run_now_description(
            operation_label
        )

    service_exec = str(row.service_exec or "").strip().lower()
    task_key = str(row.task_key or "").strip().lower()
    if "sync" in service_exec or "sync" in task_key:
        label = _sync_label_from_identity(service_exec=service_exec, task_key=task_key)
        return label, _default_run_now_description(label)

    return "Run task", "Request Dishwasher to run this plugin scheduled task now."


def _operation_display_metadata(row: ScheduledTask) -> tuple[str, str]:
    task_parameters = row.task_parameters if isinstance(row.task_parameters, dict) else {}
    operation = str(task_parameters.get("operation") or "").strip()
    metadata_by_operation = task_parameters.get("operation_metadata")
    if not operation or not isinstance(metadata_by_operation, dict):
        return "", ""
    metadata = metadata_by_operation.get(operation)
    if not isinstance(metadata, dict):
        return "", ""
    label = str(metadata.get("label") or "").strip()
    description = str(metadata.get("description") or "").strip()
    return label, description


def _sync_label_from_identity(*, service_exec: str, task_key: str) -> str:
    raw = service_exec or task_key
    tokens = [token for token in raw.replace("-", "_").split("_") if token and token != "sync"]
    if not tokens:
        return "Sync"
    return f"Sync {' '.join(tokens)}"


def _default_run_now_description(label: str) -> str:
    normalized = label[:1].lower() + label[1:] if label else "run this task"
    return f"Request Dishwasher to {normalized} now."


def _serialize_scheduled_task_status(row: ScheduledTask) -> ScheduledTaskStatusResponse:
    run_now_label, run_now_description = _scheduled_task_run_now_metadata(row)
    return ScheduledTaskStatusResponse.model_validate(
        {
            "id": row.id,
            "task_key": row.task_key,
            "task_type": row.task_type,
            "service_type": row.service_type,
            "service_exec": row.service_exec,
            "source": row.source,
            "is_enabled": row.is_enabled,
            "run_interval_seconds": row.run_interval_seconds,
            "next_run_at": row.next_run_at,
            "priority": row.priority,
            "timeout_seconds": row.timeout_seconds,
            "status": row.status,
            "last_status": row.last_status,
            "last_message": row.last_message,
            "last_order_id": row.last_order_id,
            "last_order_req_id": row.last_order_req_id,
            "last_started_at": row.last_started_at,
            "last_completed_at": row.last_completed_at,
            "consecutive_failures": row.consecutive_failures,
            "run_now_label": run_now_label,
            "run_now_description": run_now_description,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )


async def _validate_scheduled_task_payload(
    db: AsyncSession,
    payload: ScheduledTaskCreate | ScheduledTaskUpdate,
    *,
    existing: ScheduledTask | None = None,
) -> None:
    task_type = getattr(payload, "task_type", None) or (existing.task_type if existing else None)
    if task_type != "service_execution":
        return

    service_type = (
        (
            getattr(payload, "service_type", None)
            or (existing.service_type if existing else None)
            or ""
        )
        .strip()
        .lower()
    )
    service_exec = (
        getattr(payload, "service_exec", None)
        or (existing.service_exec if existing else None)
        or ""
    ).strip()
    if not service_type or not service_exec:
        raise HTTPException(
            status_code=400,
            detail="service_execution tasks require service_type and service_exec",
        )

    task_payload = getattr(payload, "task_payload", None)
    if task_payload is None and existing is not None:
        task_payload = existing.task_payload
    if task_payload is None:
        task_payload = {}
    task_parameters = getattr(payload, "task_parameters", None)
    if task_parameters is None and existing is not None:
        task_parameters = existing.task_parameters

    result = await db.execute(
        select(Ingredient).where(
            Ingredient.service_type == service_type,
            Ingredient.service_exec == service_exec,
            Ingredient.is_active.is_(True),
            Ingredient.deleted.is_(False),
        )
    )
    ingredients = list(result.scalars().all())
    if not ingredients:
        raise HTTPException(
            status_code=400,
            detail=f"No active ingredient template found for {service_type}/{service_exec}",
        )

    errors: list[str] = []
    for ingredient in ingredients:
        service_exec_parameters = dict(ingredient.service_exec_parameters or {})
        if isinstance(task_parameters, dict):
            service_exec_parameters.update(task_parameters)
        try:
            validate_service_payload_for_operation(
                task_payload,
                ingredient.payload_schema,
                service_exec_parameters or None,
            )
            return
        except ServicePluginContractError as exc:
            errors.append(str(exc))
    raise HTTPException(
        status_code=400,
        detail=(
            f"task_payload does not match any active template for "
            f"{service_type}/{service_exec}: {'; '.join(errors)}"
        ),
    )


@router.post("/scheduled-tasks", response_model=ScheduledTaskResponse)
async def create_scheduled_task(
    payload: ScheduledTaskCreate,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_admin),
) -> ScheduledTaskResponse:
    """Create a recurring internal order injection definition."""
    await _validate_scheduled_task_payload(db, payload)
    now = _now()
    existing = await db.execute(
        select(ScheduledTask).where(ScheduledTask.task_key == payload.task_key)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="scheduled task already exists")
    row = ScheduledTask(
        task_key=payload.task_key,
        task_type=payload.task_type,
        service_type=(payload.service_type or "").strip().lower() or None,
        service_exec=payload.service_exec,
        source=payload.source,
        is_enabled=payload.is_enabled,
        run_interval_seconds=payload.run_interval_seconds,
        next_run_at=payload.next_run_at or now,
        priority=payload.priority,
        timeout_seconds=payload.timeout_seconds,
        task_payload=payload.task_payload,
        task_parameters=payload.task_parameters,
        expected_outcome=payload.expected_outcome,
        status="idle" if payload.is_enabled else "disabled",
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return ScheduledTaskResponse.model_validate(row)


@router.get("/scheduled-tasks", response_model=list[ScheduledTaskResponse])
async def list_scheduled_tasks(
    db: AsyncSession = Depends(get_db),
    task_type: str | None = None,
    service_type: str | None = None,
    _context: object = Depends(require_operator),
) -> list[ScheduledTaskResponse]:
    """List scheduled order injection definitions."""
    query = select(ScheduledTask).order_by(ScheduledTask.priority.asc(), ScheduledTask.id.asc())
    if task_type:
        query = query.where(ScheduledTask.task_type == task_type)
    if service_type:
        query = query.where(ScheduledTask.service_type == service_type.strip().lower())
    result = await db.execute(query)
    return [ScheduledTaskResponse.model_validate(row) for row in result.scalars().all()]


@router.get("/scheduled-tasks/status", response_model=list[ScheduledTaskStatusResponse])
async def list_scheduled_task_statuses(
    db: AsyncSession = Depends(get_db),
    task_type: str | None = None,
    service_type: str | None = None,
    _context: object = Depends(require_reader),
) -> list[ScheduledTaskStatusResponse]:
    """List redacted scheduled task status rows."""
    query = select(ScheduledTask).order_by(ScheduledTask.priority.asc(), ScheduledTask.id.asc())
    if task_type:
        query = query.where(ScheduledTask.task_type == task_type)
    if service_type:
        query = query.where(ScheduledTask.service_type == service_type.strip().lower())
    result = await db.execute(query)
    return [_serialize_scheduled_task_status(row) for row in result.scalars().all()]


@router.post("/scheduled-tasks/{task_id}/run-now", response_model=ScheduledTaskStatusResponse)
async def request_scheduled_task_run_now(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_operator),
) -> ScheduledTaskStatusResponse:
    """Request an immediate Dishwasher run for a registered plugin scheduled task."""
    row = await db.get(ScheduledTask, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="scheduled task not found")
    if row.source != "plugin_manifest":
        raise HTTPException(
            status_code=400,
            detail="Only plugin-manifest scheduled tasks can be requested to run now",
        )
    if row.task_type not in {"plugin_health_check", "service_execution"}:
        raise HTTPException(
            status_code=400,
            detail="Scheduled task type does not support operator run-now requests",
        )
    if not row.is_enabled:
        raise HTTPException(status_code=400, detail="Scheduled task is disabled")
    if not str(row.service_type or "").strip() or not str(row.service_exec or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Scheduled task is missing service execution identity",
        )
    now = _now()
    if row.status == "queued":
        row.last_message = "Run already queued by Dishwasher"
        row.updated_at = now
        await db.commit()
        await db.refresh(row)
        return _serialize_scheduled_task_status(row)
    if row.status != "idle":
        raise HTTPException(
            status_code=409,
            detail=f"Scheduled task is already {row.status}",
        )
    row.next_run_at = now
    row.last_message = "Run requested by operator"
    row.updated_at = now
    await db.commit()
    await db.refresh(row)
    return _serialize_scheduled_task_status(row)


@router.get("/scheduled-tasks/due", response_model=list[ScheduledTaskResponse])
async def claim_due_scheduled_tasks(
    limit: int = Query(default=25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_service),
) -> list[ScheduledTaskResponse]:
    """Atomically claim due scheduled tasks for Dishwasher order injection."""
    now = _now()
    result = await db.execute(
        select(ScheduledTask)
        .where(
            ScheduledTask.is_enabled.is_(True),
            ScheduledTask.status == "idle",
            ScheduledTask.next_run_at.is_not(None),
            ScheduledTask.next_run_at <= now,
        )
        .order_by(
            ScheduledTask.priority.asc(),
            ScheduledTask.next_run_at.asc(),
            ScheduledTask.id.asc(),
        )
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    rows = list(result.scalars().all())
    for task in rows:
        task.status = "queued"
        task.last_started_at = now
        task.updated_at = now
    await db.commit()
    return [ScheduledTaskResponse.model_validate(row) for row in rows]


@router.get("/scheduled-tasks/{task_id}", response_model=ScheduledTaskResponse)
async def get_scheduled_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_operator),
) -> ScheduledTaskResponse:
    row = await db.get(ScheduledTask, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="scheduled task not found")
    return ScheduledTaskResponse.model_validate(row)


@router.patch("/scheduled-tasks/{task_id}", response_model=ScheduledTaskResponse)
async def update_scheduled_task(
    task_id: int,
    payload: ScheduledTaskUpdate,
    db: AsyncSession = Depends(get_db),
    context: object = Depends(require_operator),
) -> ScheduledTaskResponse:
    row = await db.get(ScheduledTask, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="scheduled task not found")
    updates = payload.model_dump(exclude_unset=True)
    is_admin = (
        bool(getattr(context, "is_superuser", False)) or getattr(context, "role", None) == "admin"
    )
    if not is_admin:
        unsupported = set(updates).difference(OPERATOR_SCHEDULED_TASK_UPDATE_FIELDS)
        if unsupported:
            raise HTTPException(
                status_code=403,
                detail="Operators may only update scheduled task enabled state and run interval",
            )
    await _validate_scheduled_task_payload(db, payload, existing=row)
    for key, value in updates.items():
        setattr(row, key, value)
    if payload.is_enabled is not None:
        row.status = "idle" if payload.is_enabled else "disabled"
        if payload.is_enabled and row.next_run_at is None:
            row.next_run_at = _now() + timedelta(seconds=max(1, row.run_interval_seconds))
    row.updated_at = _now()
    await db.commit()
    await db.refresh(row)
    return ScheduledTaskResponse.model_validate(row)


@router.delete("/scheduled-tasks/{task_id}", response_model=ScheduledTaskResponse)
async def disable_scheduled_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_admin),
) -> ScheduledTaskResponse:
    """Disable a scheduled order injection definition."""
    row = await db.get(ScheduledTask, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="scheduled task not found")
    row.is_enabled = False
    row.status = "disabled"
    row.updated_at = _now()
    await db.commit()
    await db.refresh(row)
    return ScheduledTaskResponse.model_validate(row)
