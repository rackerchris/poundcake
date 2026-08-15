"""Expediter router: the sole outbound service-plugin gateway."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.api.auth import require_service
from api.core.database import get_db
from api.core.logging import get_logger
from api.core.time import align_datetime_pair
from api.models.models import Dish, DishIngredient, ServicePlugin
from api.schemas.schemas import (
    ExecutionEnvelopeResponse,
)
from api.services.plugin_orchestrator import ExecutionOrchestrator, get_execution_orchestrator
from api.plugins.state import (
    EXPEDITER_RUNNER_RECEIPT_PREFIX,
    PLUGIN_RUN_STATE_UNKNOWN,
    TERMINAL_EXECUTION_STATUSES,
    normalize_plugin_run_state,
    plugin_run_state_blocks_dispatch,
)
from api.plugins.types import ExecutionContext, ExecutionResult

router = APIRouter()
logger = get_logger(__name__)


@router.post(
    "/expediter/execute/{dish_ingredient_id}",
    response_model=ExecutionEnvelopeResponse,
)
async def execute_service_execution(
    dish_ingredient_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    orchestrator: ExecutionOrchestrator = Depends(get_execution_orchestrator),
    _context: object = Depends(require_service),
) -> ExecutionEnvelopeResponse:
    """Execute a previously dispatched runtime row through the adapter boundary."""
    row = await db.get(DishIngredient, dish_ingredient_id)
    if row is None or row.deleted:
        raise HTTPException(status_code=404, detail="Dish ingredient not found")
    if not _is_expediter_runner_receipt(row.service_exec_id):
        raise HTTPException(status_code=409, detail="Dish ingredient is not runner-dispatched")
    if row.service_exec_status not in {"dispatched", "running"}:
        raise HTTPException(status_code=409, detail="Dish ingredient is not executable")

    if _runner_row_timed_out(row, datetime.now(timezone.utc)):
        return ExecutionEnvelopeResponse.model_validate(
            {
                "service_exec_id": row.service_exec_id,
                "service_type": row.service_type,
                "status": "timeout",
                "service_exec_error": "Expediter runner execution exceeded service timeout",
                "service_exec_actual_outcome": {
                    "success": False,
                    "status": "timeout",
                    "reason": "service_exec_timeout",
                    "dispatch_attempted": False,
                },
                "raw": {
                    "success": False,
                    "status": "timeout",
                    "reason": "service_exec_timeout",
                    "dispatch_attempted": False,
                },
            }
        )

    operator_config = await _plugin_operator_config(db=db, service_type=row.service_type)
    health_block = await _plugin_health_block(
        db=db,
        service_type=row.service_type,
        service_exec=row.service_exec,
        action="execute",
    )
    if health_block is not None:
        return ExecutionEnvelopeResponse.model_validate(
            {
                "service_exec_id": row.service_exec_id,
                "service_type": row.service_type,
                "status": "running",
                "service_exec_error": health_block,
                "service_exec_actual_outcome": {
                    "success": True,
                    "status": "running",
                    "reason": "service_plugin_unhealthy",
                    "dispatch_attempted": False,
                },
                "raw": None,
            }
        )

    dish_context = await _dish_execution_context(db=db, row=row)
    context = {
        "req_id": row.req_id,
        "dish_id": row.dish_id,
        "order_id": dish_context.get("order_id"),
        "recipe_ingredient_id": row.recipe_ingredient_id,
        "destination_target": row.destination_target or "",
        "dish": dish_context,
    }
    context.update(dish_context.get("context_updates") or {})
    if operator_config:
        context["operator_config"] = operator_config
    try:
        ctx = ExecutionContext.model_validate(
            {
                "service_type": row.service_type,
                "service_exec": row.service_exec,
                "service_payload": ({} if row.service_payload is None else row.service_payload),
                "service_exec_parameters": (
                    {} if row.service_exec_parameters is None else row.service_exec_parameters
                ),
                "retry_count": row.retry_count or 0,
                "retry_delay": row.retry_delay or 0,
                "service_exec_timeout": row.service_exec_timeout or 300,
                "context": context,
                "req_id": request.state.req_id,
            }
        )
    except ValidationError as exc:
        message = str(exc)
        logger.warning(
            "Expediter execution context validation failed",
            extra={
                "req_id": request.state.req_id,
                "dish_ingredient_id": row.id,
                "service_type": row.service_type,
                "service_exec": row.service_exec,
            },
        )
        return ExecutionEnvelopeResponse.model_validate(
            {
                "service_exec_id": row.service_exec_id,
                "service_type": row.service_type,
                "status": "errored",
                "service_exec_error": message,
                "service_exec_actual_outcome": {
                    "success": False,
                    "status": "errored",
                    "reason": "execution_contract_error",
                    "message": message,
                    "dispatch_attempted": False,
                },
                "raw": {
                    "success": False,
                    "status": "errored",
                    "reason": "execution_contract_error",
                    "message": message,
                    "dispatch_attempted": False,
                },
            }
        )
    result = await orchestrator.dispatch(ctx)
    if not result.service_exec_id:
        result.service_exec_id = row.service_exec_id
    logger.info(
        "Expediter executed service workload",
        extra={
            "req_id": request.state.req_id,
            "dish_ingredient_id": row.id,
            "dish_id": row.dish_id,
            "service_type": row.service_type,
            "service_exec": row.service_exec,
            "service_exec_id": result.service_exec_id,
            "service_exec_status": result.status,
        },
    )
    return _envelope_from_result(result)


@router.get(
    "/expediter/status/{service_type}/{service_exec_id}",
    response_model=ExecutionEnvelopeResponse,
)
async def get_service_execution_status(
    service_type: str,
    service_exec_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    orchestrator: ExecutionOrchestrator = Depends(get_execution_orchestrator),
    _context: object = Depends(require_service),
) -> ExecutionEnvelopeResponse:
    """Poll a service plugin execution by receipt id."""
    if _is_expediter_runner_receipt(service_exec_id):
        row_id = _dish_ingredient_id_from_runner_receipt(service_exec_id)
        if row_id is None:
            raise HTTPException(status_code=404, detail="Execution receipt not found")
        row = await db.get(DishIngredient, row_id)
        if row is None or row.deleted:
            raise HTTPException(status_code=404, detail="Execution receipt not found")
        if row.service_type.strip().lower() != service_type.strip().lower():
            raise HTTPException(status_code=404, detail="Execution receipt not found")
        return ExecutionEnvelopeResponse.model_validate(
            {
                "service_exec_id": row.service_exec_id,
                "service_type": row.service_type,
                "status": row.service_exec_status,
                "service_exec_error": row.service_exec_error,
                "service_exec_actual_outcome": row.service_exec_actual_outcome,
                "raw": row.service_exec_actual_outcome,
                "context_updates": _stored_context_updates(row.service_exec_actual_outcome),
            }
        )
    health_block = await _plugin_health_block(
        db=db,
        service_type=service_type,
        service_exec=_service_exec_from_receipt(service_type, service_exec_id),
        action="poll",
    )
    if health_block is not None:
        normalized = service_type.strip().lower()
        return ExecutionEnvelopeResponse.model_validate(
            {
                "service_exec_id": service_exec_id,
                "service_type": normalized,
                "status": "errored",
                "service_exec_error": health_block,
                "service_exec_actual_outcome": {
                    "success": False,
                    "status": "errored",
                    "reason": "service_plugin_unhealthy",
                    "service_type": normalized,
                    "service_exec_id": service_exec_id,
                    "dispatch_attempted": False,
                },
                "raw": {
                    "success": False,
                    "status": "errored",
                    "reason": "service_plugin_unhealthy",
                    "service_type": normalized,
                    "service_exec_id": service_exec_id,
                    "dispatch_attempted": False,
                },
            }
        )
    ctx = ExecutionContext.model_validate(
        {
            "service_type": service_type,
            "service_exec": "poll",
            "context": await _execution_context_for_plugin(db=db, service_type=service_type),
            "req_id": request.state.req_id,
        }
    )
    result = await orchestrator.poll(ctx, service_exec_id)
    return _envelope_from_result(result)


@router.post(
    "/expediter/cancel/{service_type}/{service_exec_id}",
    response_model=ExecutionEnvelopeResponse,
)
async def cancel_service_execution(
    service_type: str,
    service_exec_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    orchestrator: ExecutionOrchestrator = Depends(get_execution_orchestrator),
    _context: object = Depends(require_service),
) -> ExecutionEnvelopeResponse:
    """Cancel a service plugin execution when supported."""
    ctx = ExecutionContext.model_validate(
        {
            "service_type": service_type,
            "service_exec": "cancel",
            "context": await _execution_context_for_plugin(db=db, service_type=service_type),
            "req_id": request.state.req_id,
        }
    )
    result = await orchestrator.cancel(ctx, service_exec_id)
    return _envelope_from_result(result)


def _envelope_from_result(result: ExecutionResult) -> ExecutionEnvelopeResponse:
    result = ExecutionResult.model_validate(result)
    return ExecutionEnvelopeResponse.model_validate(
        {
            "service_exec_id": result.service_exec_id,
            "service_type": result.service_type,
            "status": result.status,
            "service_exec_error": result.service_exec_error,
            "service_exec_actual_outcome": result.result,
            "raw": result.raw,
            "context_updates": result.context_updates,
            "attempts": result.attempts,
        }
    )


def _is_expediter_runner_receipt(service_exec_id: str | None) -> bool:
    return str(service_exec_id or "").strip().startswith(EXPEDITER_RUNNER_RECEIPT_PREFIX)


def _dish_ingredient_id_from_runner_receipt(service_exec_id: str) -> int | None:
    value = str(service_exec_id or "").strip()
    if not value.startswith(EXPEDITER_RUNNER_RECEIPT_PREFIX):
        return None
    raw_id = value.removeprefix(EXPEDITER_RUNNER_RECEIPT_PREFIX).split(":", 1)[0]
    if not raw_id.isdigit():
        return None
    return int(raw_id)


def _stored_context_updates(outcome: object) -> dict:
    if not isinstance(outcome, dict):
        return {}
    updates = outcome.get("_context_updates")
    if not isinstance(updates, dict):
        updates = outcome.get("context_updates")
    return dict(updates) if isinstance(updates, dict) else {}


def _runtime_context_entry(row: DishIngredient) -> dict:
    params = row.service_exec_parameters if isinstance(row.service_exec_parameters, dict) else {}
    return {
        "id": row.id,
        "recipe_ingredient_id": row.recipe_ingredient_id,
        "task_key": row.task_key,
        "step_order": row.step_order,
        "parallel_group": row.parallel_group,
        "depth": row.depth,
        "service_type": row.service_type,
        "service_exec": row.service_exec,
        "destination_target": row.destination_target or "",
        "managed_role": params.get("managed_role"),
        "evidence_family": params.get("evidence_family"),
        "status": row.service_exec_status,
        "service_exec_id": row.service_exec_id,
        "actual_outcome": row.service_exec_actual_outcome,
        "error": row.service_exec_error,
    }


def _is_evidence_runtime_row(row: DishIngredient) -> bool:
    params = row.service_exec_parameters if isinstance(row.service_exec_parameters, dict) else {}
    role = str(params.get("managed_role") or "").strip().lower()
    if role.startswith("gather_"):
        return True
    return bool(str(params.get("evidence_family") or "").strip())


async def _dish_execution_context(
    *,
    db: AsyncSession,
    row: DishIngredient,
) -> dict:
    result = await db.execute(select(Dish).where(Dish.id == row.dish_id))
    dish = result.scalars().first()
    order_id = dish.order_id if dish is not None else None
    result = await db.execute(
        select(DishIngredient)
        .where(
            DishIngredient.dish_id == row.dish_id,
            DishIngredient.deleted.is_(False),
            DishIngredient.id != row.id,
            DishIngredient.service_exec_status.in_(TERMINAL_EXECUTION_STATUSES),
        )
        .order_by(
            DishIngredient.depth.asc(),
            DishIngredient.parallel_group.asc(),
            DishIngredient.step_order.asc(),
            DishIngredient.id.asc(),
        )
    )
    completed_rows = list(result.scalars().all())
    entries = [_runtime_context_entry(item) for item in completed_rows]
    context_updates: dict = {}
    if order_id is not None:
        prior_result = await db.execute(
            select(DishIngredient)
            .join(Dish, Dish.id == DishIngredient.dish_id)
            .where(
                Dish.order_id == order_id,
                Dish.id != row.dish_id,
                DishIngredient.deleted.is_(False),
                DishIngredient.id != row.id,
                DishIngredient.service_exec_status.in_(TERMINAL_EXECUTION_STATUSES),
            )
            .order_by(DishIngredient.id.asc())
        )
        for item in prior_result.scalars().all():
            context_updates.update(_stored_context_updates(item.service_exec_actual_outcome))
    for item in completed_rows:
        context_updates.update(_stored_context_updates(item.service_exec_actual_outcome))
    return {
        "id": row.dish_id,
        "order_id": order_id,
        "run_phase": dish.run_phase if dish is not None else None,
        "ingredients": entries,
        "evidence": [
            entry for item, entry in zip(completed_rows, entries) if _is_evidence_runtime_row(item)
        ],
        "context_updates": context_updates,
    }


def _runner_row_timed_out(row: DishIngredient, now: datetime) -> bool:
    timeout = getattr(row, "service_exec_timeout", None)
    if timeout is None or int(timeout) <= 0:
        return False
    started_at = getattr(row, "service_exec_start_time", None)
    if started_at is None:
        return False
    if started_at.tzinfo is None and now.tzinfo is not None:
        started_at = started_at.replace(tzinfo=now.tzinfo)
    if started_at.tzinfo is not None and now.tzinfo is None:
        started_at = started_at.replace(tzinfo=None)
    started_at, comparable_now = align_datetime_pair(started_at, now)
    return started_at + timedelta(seconds=int(timeout)) < comparable_now


def expediter_runner_adapter_callable(plugin: ServicePlugin | None) -> bool:
    if plugin is None or not plugin.enabled:
        return False
    run_state = normalize_plugin_run_state(plugin.health_status or PLUGIN_RUN_STATE_UNKNOWN)
    return not plugin_run_state_blocks_dispatch(run_state)


def expediter_runner_claimable(
    row: DishIngredient,
    plugin: ServicePlugin | None,
    now: datetime,
) -> bool:
    if (
        plugin is not None
        and plugin.enabled
        and str(row.service_exec or "").strip() == "health_check"
    ):
        return True
    return _runner_row_timed_out(row, now) or expediter_runner_adapter_callable(plugin)


async def _plugin_health_block(
    *,
    db: AsyncSession | None,
    service_type: str,
    service_exec: str | None = None,
    action: str,
) -> str | None:
    if db is None:
        return None
    normalized = service_type.strip().lower()
    result = await db.execute(
        select(ServicePlugin).where(
            ServicePlugin.service_type == normalized,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    if not row.enabled:
        return f"service plugin {normalized} is disabled; {action} blocked"
    normalized_exec = (service_exec or "").strip().lower()
    run_state = normalize_plugin_run_state(row.health_status or PLUGIN_RUN_STATE_UNKNOWN)
    if normalized_exec == "health_check":
        return None
    if plugin_run_state_blocks_dispatch(run_state):
        detail = f"service plugin {normalized} is {run_state}; {action} blocked"
        if row.health_message:
            detail = f"{detail}: {row.health_message}"
        return detail
    return None


async def _plugin_operator_config(
    *,
    db: AsyncSession | None,
    service_type: str,
) -> dict[str, object] | None:
    if db is None:
        return None
    normalized = service_type.strip().lower()
    result = await db.execute(
        select(ServicePlugin.plugin_config).where(ServicePlugin.service_type == normalized)
    )
    config = result.scalar_one_or_none()
    return dict(config) if isinstance(config, dict) else None


async def _execution_context_for_plugin(
    *,
    db: AsyncSession | None,
    service_type: str,
) -> dict[str, object]:
    config = await _plugin_operator_config(db=db, service_type=service_type)
    return {"operator_config": config} if config else {}


def _service_exec_from_receipt(service_type: str, service_exec_id: str) -> str | None:
    prefix = service_type.strip().lower()
    parts = service_exec_id.split(":", 2)
    if len(parts) == 3 and parts[0].strip().lower() == prefix:
        return parts[1].strip().lower()
    return None
