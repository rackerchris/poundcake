#  ___                        _  ____      _
# |  _ \ ___  _   _ _ __   __| |/ ___|__ _| | _____
# | |_) / _ \| | | | '_ \ / _` | |   / _` | |/ / _ \
# |  __/ (_) | |_| | | | | (_| | |__| (_| |   <  __/
# |_|   \___/ \__,_|_| |_|\__,_|\____\__,_|_|\_\___|
#
"""API routes for Dish (execution) management."""

import re
from contextlib import asynccontextmanager
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, update, asc, desc, or_, case
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any, List, cast
from datetime import datetime, timedelta, timezone

from api.core.config import get_settings
from api.api.auth import require_admin, require_reader, require_service
from api.core.database import get_db
from api.core.time import align_datetime_pair, utc_now_db
from api.core.logging import get_logger
from api.core.statuses import EXECUTION_TERMINAL_STATUSES
from api.models.models import Order, Dish, Recipe, RecipeIngredient, DishIngredient, ServicePlugin
from api.schemas.schemas import (
    DishDetailResponse,
    DishIngredientResponse,
    DishIngredientStatusResponse,
    DishIngredientUpsert,
    DishStatusResponse,
)
from api.schemas.query_params import DishQueryParams, validate_query_params
from api.services.dish_planner import (
    build_step_parameters,
    build_step_payload,
    build_step_task_key,
)
from api.services.order_types import (
    require_order_type,
    order_matches_filters,
)
from api.types import OrderScope, OrderType
from api.plugins.state import (
    EXPEDITER_RUNNER_RECEIPT_PREFIX,
    ServiceExecutionStateError,
    runtime_seconds,
    sla_exceeded,
    validate_execution_transition,
    verdict_status,
)
from api.api.expediter import expediter_runner_claimable

router = APIRouter()
logger = get_logger(__name__)
SENSITIVE_RUNTIME_KEY_FRAGMENTS = (
    "authorization",
    "credential",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "token",
)
STATUS_RESULT_MAX_DEPTH = 6
STATUS_RESULT_MAX_LIST_ITEMS = 20
STATUS_RESULT_MAX_STRING_LENGTH = 1_000
STATUS_RESULT_SUMMARY_KEYS = ("summary", "event_summary")
SENSITIVE_STATUS_TEXT_PATTERNS = tuple(
    re.compile(rf"(?i)\b({re.escape(fragment)})\b\s*[:=]\s*([^\s,;]+)")
    for fragment in SENSITIVE_RUNTIME_KEY_FRAGMENTS
)


def _aware_datetime(value: datetime | None, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    if value.tzinfo is None and fallback.tzinfo is not None:
        return value.replace(tzinfo=fallback.tzinfo)
    if value.tzinfo is not None and fallback.tzinfo is None:
        return value.replace(tzinfo=None)
    return value


def _rowcount(result: object) -> int:
    """Get affected row count from SQLAlchemy DML result."""
    return int(getattr(cast(Any, result), "rowcount", 0) or 0)


def _service_type_from_request(request: Request) -> str:
    context = getattr(request.state, "auth_context", None)
    return str(getattr(context, "service_type", "") or "").strip().lower()


def _require_reconcile_owner(
    request: Request,
    row: DishIngredient,
    *,
    expected_service_type: str,
    require_runner_receipt: bool,
) -> None:
    caller = _service_type_from_request(request)
    if caller != expected_service_type:
        raise HTTPException(status_code=403, detail="Reconcile operation not permitted")
    has_runner_receipt = _has_expediter_runner_receipt(row)
    if require_runner_receipt and not has_runner_receipt:
        raise HTTPException(
            status_code=409,
            detail="Execution reconcile is only valid for expediter-runner dispatched rows",
        )
    if not require_runner_receipt and has_runner_receipt:
        raise HTTPException(
            status_code=409,
            detail="Timer reconcile cannot mutate expediter-runner dispatched rows",
        )


@asynccontextmanager
async def _write_transaction(db: AsyncSession):
    if not db.in_transaction():
        async with db.begin():
            yield
        return
    try:
        yield
    except Exception:
        await db.rollback()
        raise
    else:
        await db.commit()


def _has_service_execution_identity(record: DishIngredient) -> bool:
    return bool(str(getattr(record, "service_exec_id", "") or "").strip())


def _has_expediter_runner_receipt(record: DishIngredient) -> bool:
    return (
        str(getattr(record, "service_exec_id", "") or "")
        .strip()
        .startswith(EXPEDITER_RUNNER_RECEIPT_PREFIX)
    )


def _dispatch_identity_timed_out(record: DishIngredient, now: datetime) -> bool:
    timeout = getattr(record, "service_exec_timeout", None)
    if timeout is None or int(timeout) <= 0:
        return False
    started_at = _aware_datetime(getattr(record, "service_exec_start_time", None), now)
    started_at, comparable_now = align_datetime_pair(started_at, now)
    return started_at + timedelta(seconds=int(timeout)) < comparable_now


def _timer_pollable(record: DishIngredient, now: datetime) -> bool:
    if _has_expediter_runner_receipt(record):
        return _dispatch_identity_timed_out(record, now)
    return _has_service_execution_identity(record) or _dispatch_identity_timed_out(record, now)


def _runtime_bucket(record: DishIngredient) -> tuple[int, int]:
    depth = record.depth if isinstance(record.depth, int) else record.step_order
    if not isinstance(depth, int):
        depth = 1_000_000
    parallel_group = record.parallel_group if isinstance(record.parallel_group, int) else 0
    return depth, parallel_group


def _is_blocking_failure(record: DishIngredient) -> bool:
    status = str(record.service_exec_status or "").strip().lower()
    on_failure = str(record.on_failure or "stop").strip().lower()
    return status in {"failed", "errored", "timeout", "canceled"} and on_failure != "continue"


def _advance_ready_representative(rows: list[DishIngredient]) -> DishIngredient | None:
    if any(
        str(row.service_exec_status or "").strip().lower() in {"dispatched", "running"}
        for row in rows
    ):
        return None
    ordered = sorted(rows, key=lambda row: (_runtime_bucket(row), row.step_order, row.id))
    blocking_rows = [
        row
        for row in ordered
        if row.service_exec_status in EXECUTION_TERMINAL_STATUSES and _is_blocking_failure(row)
    ]
    if blocking_rows:
        return blocking_rows[0]
    terminal_rows = [
        row for row in ordered if row.service_exec_status in EXECUTION_TERMINAL_STATUSES
    ]
    if not terminal_rows:
        pending_rows = [
            row
            for row in ordered
            if str(row.service_exec_status or "").strip().lower() == "pending"
        ]
        return pending_rows[0] if pending_rows else None
    if any(str(row.service_exec_status or "").strip().lower() == "pending" for row in ordered):
        return terminal_rows[-1]
    return terminal_rows[-1]


def _build_recipe_step_lookup(recipe: Recipe | None) -> dict[str, RecipeIngredient]:
    """Resolve current recipe steps by both raw and workflow task keys."""
    lookup: dict[str, RecipeIngredient] = {}
    if recipe is None:
        return lookup
    for recipe_ingredient in list(getattr(recipe, "recipe_ingredients", []) or []):
        ingredient = getattr(recipe_ingredient, "ingredient", None)
        if ingredient is None:
            continue
        raw_task_key = str(getattr(ingredient, "task_key_template", "") or "").strip()
        if raw_task_key:
            lookup.setdefault(raw_task_key, recipe_ingredient)
            lookup.setdefault(raw_task_key.replace(".", "_"), recipe_ingredient)
        lookup.setdefault(build_step_task_key(recipe_ingredient), recipe_ingredient)
    return lookup


def _resolve_recipe_ingredient(
    record: DishIngredient,
    recipe_step_lookup: dict[str, RecipeIngredient] | None = None,
) -> RecipeIngredient | None:
    recipe_ingredient = getattr(record, "recipe_ingredient", None)
    if recipe_ingredient is not None:
        return recipe_ingredient
    if not recipe_step_lookup:
        return None
    task_key = str(getattr(record, "task_key", "") or "").strip()
    if not task_key:
        return None
    return recipe_step_lookup.get(task_key)


def _dish_ingredient_identity(
    record: DishIngredient,
    recipe_ingredient: RecipeIngredient | None,
) -> tuple[str, str]:
    resolved_recipe_ingredient_id = getattr(recipe_ingredient, "id", None)
    if resolved_recipe_ingredient_id is not None:
        return ("recipe", str(resolved_recipe_ingredient_id))
    task_key = str(getattr(record, "task_key", "") or "").strip()
    if task_key:
        return ("task", task_key)
    return ("row", str(getattr(record, "id", 0) or 0))


def _dish_ingredient_rank(
    record: DishIngredient,
    recipe_ingredient: RecipeIngredient | None,
) -> tuple[int, int, int, int, datetime, datetime, int]:
    payload = getattr(record, "service_payload", None)
    parameters = getattr(record, "service_exec_parameters", None)
    result_payload = getattr(record, "service_exec_actual_outcome", None)
    min_aware = datetime.min.replace(tzinfo=timezone.utc)
    updated_at = _aware_datetime(getattr(record, "updated_at", None), min_aware)
    created_at = _aware_datetime(getattr(record, "created_at", None), min_aware)
    return (
        1 if recipe_ingredient is not None else 0,
        1 if isinstance(payload, dict) and payload else 0,
        1 if isinstance(parameters, dict) and parameters else 0,
        1 if isinstance(result_payload, dict) and result_payload else 0,
        updated_at,
        created_at,
        int(getattr(record, "id", 0) or 0),
    )


def _collapse_dish_ingredient_records(
    records: list[DishIngredient],
    *,
    recipe_step_lookup: dict[str, RecipeIngredient] | None = None,
) -> list[tuple[DishIngredient, RecipeIngredient | None]]:
    """Collapse duplicate logical steps and keep the richest/latest runtime row."""
    selected: dict[tuple[str, str], tuple[DishIngredient, RecipeIngredient | None]] = {}
    for record in records:
        recipe_ingredient = _resolve_recipe_ingredient(record, recipe_step_lookup)
        identity = _dish_ingredient_identity(record, recipe_ingredient)
        current = selected.get(identity)
        if current is None or _dish_ingredient_rank(
            record, recipe_ingredient
        ) > _dish_ingredient_rank(current[0], current[1]):
            selected[identity] = (record, recipe_ingredient)
    collapsed = list(selected.values())
    max_aware = datetime.max.replace(tzinfo=timezone.utc)
    collapsed.sort(
        key=lambda item: (
            item[0].service_exec_start_time is None,
            _aware_datetime(item[0].service_exec_start_time, max_aware),
            _aware_datetime(item[0].service_exec_completed_time, max_aware),
            _aware_datetime(item[0].created_at, max_aware),
            item[0].id or 0,
        )
    )
    return collapsed


def _serialize_dish_ingredient(
    record: DishIngredient,
    *,
    recipe_step_lookup: dict[str, RecipeIngredient] | None = None,
) -> DishIngredientResponse:
    """Backfill runtime service payloads from the current recipe step when the row stores JSON null."""
    payload = DishIngredientResponse.model_validate(record).model_dump(mode="python")
    recipe_ingredient = _resolve_recipe_ingredient(record, recipe_step_lookup)
    ingredient = getattr(recipe_ingredient, "ingredient", None)

    if recipe_ingredient is not None:
        if payload.get("recipe_ingredient_id") is None:
            payload["recipe_ingredient_id"] = getattr(recipe_ingredient, "id", None)
        if payload.get("service_payload") is None:
            resolved_payload = build_step_payload(recipe_ingredient)
            if resolved_payload is not None:
                payload["service_payload"] = resolved_payload
        if payload.get("service_exec_parameters") is None:
            resolved_parameters = build_step_parameters(recipe_ingredient)
            if resolved_parameters is not None:
                payload["service_exec_parameters"] = resolved_parameters

    if ingredient is not None:
        if not payload.get("service_type"):
            payload["service_type"] = getattr(ingredient, "service_type", None)
        if not payload.get("service_exec"):
            payload["service_exec"] = getattr(ingredient, "service_exec", None)
        if payload.get("destination_target") in (None, ""):
            payload["destination_target"] = getattr(ingredient, "destination_target", "") or ""

    return DishIngredientResponse.model_validate(payload)


def _redact_runtime_value(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(fragment in key_text for fragment in SENSITIVE_RUNTIME_KEY_FRAGMENTS):
                redacted[key] = "[redacted]"
            else:
                redacted[key] = _redact_runtime_value(item)
        return redacted
    if isinstance(value, list):
        return [_redact_runtime_value(item) for item in value]
    return value


def _truncate_status_string(value: str) -> str:
    if len(value) <= STATUS_RESULT_MAX_STRING_LENGTH:
        return value
    return f"{value[:STATUS_RESULT_MAX_STRING_LENGTH]}...[truncated]"


def _sanitize_status_string(value: str) -> str:
    sanitized = _truncate_status_string(value)
    for pattern in SENSITIVE_STATUS_TEXT_PATTERNS:
        sanitized = pattern.sub(lambda match: f"{match.group(1)}=[redacted]", sanitized)
    return sanitized


def _sanitize_status_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= STATUS_RESULT_MAX_DEPTH:
        return "[truncated]"
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(fragment in key_text for fragment in SENSITIVE_RUNTIME_KEY_FRAGMENTS):
                sanitized[str(key)] = "[redacted]"
            else:
                sanitized[str(key)] = _sanitize_status_value(item, depth=depth + 1)
        return sanitized
    if isinstance(value, list):
        return [
            _sanitize_status_value(item, depth=depth + 1)
            for item in value[:STATUS_RESULT_MAX_LIST_ITEMS]
        ]
    if isinstance(value, str):
        return _sanitize_status_string(value)
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return _sanitize_status_string(str(value))


def _status_string(value: Any) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return None


def _status_parameters(record: DishIngredient) -> dict[str, Any]:
    parameters = getattr(record, "service_exec_parameters", None)
    return parameters if isinstance(parameters, dict) else {}


def _status_outcome(record: DishIngredient) -> dict[str, Any]:
    outcome = getattr(record, "service_exec_actual_outcome", None)
    return outcome if isinstance(outcome, dict) else {}


def _status_execution_role(
    record: DishIngredient,
    parameters: dict[str, Any],
) -> str | None:
    explicit_role = _status_string(parameters.get("role"))
    if explicit_role:
        return explicit_role
    operation = _status_string(parameters.get("operation"))
    if operation == "verify_firing":
        return "validate_alert"
    if operation and (operation.endswith("_diagnostics") or operation.endswith("_triage")):
        return "gather_evidence"
    if _status_string(parameters.get("evidence_family")):
        return "gather_evidence"
    if _status_string(parameters.get("mutation_family")):
        return "action_alert"
    task_key = str(getattr(record, "task_key", "") or "").lower()
    if "evidence" in task_key or "diagnostic" in task_key:
        return "gather_evidence"
    if "guard" in task_key or "validate" in task_key:
        return "validate_alert"
    if "action" in task_key or "remediation" in task_key:
        return "action_alert"
    return None


def _status_result_status(
    record: DishIngredient,
    outcome: dict[str, Any],
) -> str | None:
    for key in ("status", "result", "state"):
        value = _status_string(outcome.get(key))
        if value:
            return value
    if isinstance(outcome.get("success"), bool):
        return "succeeded" if outcome["success"] else "failed"
    return _status_string(getattr(record, "service_exec_status", None))


def _status_result_message(outcome: dict[str, Any]) -> str | None:
    for key in ("message", "reason", "detail"):
        value = _status_string(outcome.get(key))
        if value:
            return _sanitize_status_string(value)
    return None


def _status_result_summary(outcome: dict[str, Any]) -> dict[str, Any] | None:
    summary: dict[str, Any] = {}
    for key in STATUS_RESULT_SUMMARY_KEYS:
        value = outcome.get(key)
        if isinstance(value, dict):
            summary[key] = _sanitize_status_value(value)
    return summary or None


def _serialize_admin_dish_ingredient_history(record: DishIngredient) -> DishIngredientResponse:
    """Return full runtime evidence for admins with secret-like nested keys redacted."""
    payload = DishIngredientResponse.model_validate(record).model_dump(mode="python")
    payload["service_exec_error"] = _sanitize_status_string(
        str(payload.get("service_exec_error") or "")
    )
    if not payload["service_exec_error"]:
        payload["service_exec_error"] = None
    for key in (
        "service_payload",
        "service_exec_parameters",
        "service_exec_expected_outcome",
        "service_exec_actual_outcome",
    ):
        payload[key] = _redact_runtime_value(payload.get(key))
    return DishIngredientResponse.model_validate(payload)


def _serialize_dish_ingredient_status(
    record: DishIngredient,
    *,
    recipe_step_lookup: dict[str, RecipeIngredient] | None = None,
) -> DishIngredientStatusResponse:
    """Return the operator-safe runtime status without payloads or plugin-private fields."""
    recipe_ingredient = _resolve_recipe_ingredient(record, recipe_step_lookup)
    ingredient = getattr(recipe_ingredient, "ingredient", None)
    parameters = _status_parameters(record)
    outcome = _status_outcome(record)
    payload = {
        "id": record.id,
        "dish_id": record.dish_id,
        "recipe_ingredient_id": record.recipe_ingredient_id
        or getattr(recipe_ingredient, "id", None),
        "task_key": record.task_key,
        "step_order": record.step_order,
        "parallel_group": record.parallel_group,
        "depth": record.depth,
        "service_type": record.service_type or getattr(ingredient, "service_type", None),
        "service_exec": record.service_exec or getattr(ingredient, "service_exec", None),
        "retry_count": record.retry_count,
        "retry_delay": record.retry_delay,
        "on_failure": record.on_failure,
        "service_exec_status": record.service_exec_status,
        "attempt": record.attempt,
        "execution_role": _status_execution_role(record, parameters),
        "operation": _status_string(parameters.get("operation")),
        "result_status": _status_result_status(record, outcome),
        "result_message": _status_result_message(outcome),
        "result_summary": _status_result_summary(outcome),
        "service_exec_start_time": record.service_exec_start_time,
        "service_exec_completed_time": record.service_exec_completed_time,
        "service_exec_canceled_time": record.service_exec_canceled_time,
        "service_exec_run_time": record.service_exec_run_time,
        "service_exec_sla_exceeded": record.service_exec_sla_exceeded,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }
    return DishIngredientStatusResponse.model_validate(payload)


def _serialize_dish_status(dish: Dish) -> DishStatusResponse:
    recipe = getattr(dish, "recipe", None)
    order = getattr(dish, "order", None)
    return DishStatusResponse.model_validate(
        {
            "id": dish.id,
            "order_id": dish.order_id,
            "order_type": require_order_type(getattr(order, "raw_data", None)),
            "recipe_id": dish.recipe_id,
            "recipe_name": getattr(recipe, "name", None),
            "processing_status": dish.processing_status,
            "run_phase": dish.run_phase,
            "dish_exec_status": dish.dish_exec_status,
            "started_at": dish.started_at,
            "completed_at": dish.completed_at,
            "expected_run_secs": dish.expected_run_secs,
            "run_time_secs": dish.run_time_secs,
            "work_execution_time_secs": dish.work_execution_time_secs,
            "work_execution_groups": dish.work_execution_groups,
            "created_at": dish.created_at,
            "updated_at": dish.updated_at,
        }
    )


def _filter_dishes(
    dishes: list[Dish],
    *,
    order_scope: OrderScope,
    order_type: OrderType | None,
) -> list[Dish]:
    return [
        dish
        for dish in dishes
        if order_matches_filters(
            raw_data=getattr(getattr(dish, "order", None), "raw_data", None),
            order_scope=order_scope,
            order_type=order_type,
        )
    ]


async def _dish_ingredient_history_for_dish(
    db: AsyncSession,
    *,
    dish_id: int,
) -> list[DishIngredientResponse]:
    result = await db.execute(select(Dish).where(Dish.id == dish_id))
    dish = result.scalars().first()
    if not dish:
        raise HTTPException(status_code=404, detail="Dish not found")
    rows_result = await db.execute(
        select(DishIngredient)
        .where(DishIngredient.dish_id == dish_id)
        .order_by(
            DishIngredient.depth.asc(),
            DishIngredient.parallel_group.asc(),
            DishIngredient.step_order.asc(),
            DishIngredient.attempt.asc(),
            DishIngredient.created_at.asc(),
            DishIngredient.id.asc(),
        )
    )
    return [
        _serialize_admin_dish_ingredient_history(record) for record in rows_result.scalars().all()
    ]


async def _dish_ingredient_history_for_order(
    db: AsyncSession,
    *,
    order_id: int,
) -> list[DishIngredientResponse]:
    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalars().first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    rows_result = await db.execute(
        select(DishIngredient)
        .join(Dish, Dish.id == DishIngredient.dish_id)
        .where(Dish.order_id == order_id)
        .order_by(
            Dish.created_at.asc(),
            Dish.id.asc(),
            DishIngredient.depth.asc(),
            DishIngredient.parallel_group.asc(),
            DishIngredient.step_order.asc(),
            DishIngredient.attempt.asc(),
            DishIngredient.created_at.asc(),
            DishIngredient.id.asc(),
        )
    )
    return [
        _serialize_admin_dish_ingredient_history(record) for record in rows_result.scalars().all()
    ]


@router.get("/dishes", response_model=List[DishDetailResponse])
async def fetch_dishes(
    request: Request,
    params: DishQueryParams = Depends(validate_query_params(DishQueryParams)),
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_service),
):
    """
    Query Parameters:
    - processing_status: Filter by processing status (new/pending/processing/complete/failed)
    - req_id: Filter by request ID
    - order_id: Filter by order ID (integer)
    - limit: Maximum number of results (default: 100, max: 1000)
    - offset: Number of results to skip (default: 0)

    Returns 422 Unprocessable Entity if unknown or invalid query parameters are provided.
    """
    request_id = request.state.req_id

    logger.debug(
        "Fetching dishes",
        extra={
            "req_id": request_id,
            "processing_status": params.processing_status,
            "filter_req_id": params.req_id,
            "order_id": params.order_id,
            "limit": params.limit,
            "offset": params.offset,
        },
    )

    query = select(Dish).options(
        joinedload(Dish.recipe)
        .joinedload(Recipe.recipe_ingredients)
        .joinedload(RecipeIngredient.ingredient),
        joinedload(Dish.order),
        selectinload(Dish.dish_ingredients),
    )

    if params.processing_status:
        query = query.where(Dish.processing_status == params.processing_status)
    if params.req_id:
        query = query.where(Dish.req_id == params.req_id)
    if params.order_id:
        query = query.where(Dish.order_id == params.order_id)

    if params.processing_status and params.processing_status == "new":
        query = query.order_by(asc(Dish.created_at))
    elif params.processing_status and params.processing_status == "processing":
        # MariaDB doesn't support NULLS FIRST, use CASE to sort NULL values first
        query = query.order_by(
            case((Dish.started_at.is_(None), 0), else_=1),
            asc(Dish.started_at),
            asc(Dish.created_at),
        )
    else:
        query = query.order_by(desc(Dish.created_at))

    query = query.limit(1000)
    result = await db.execute(query)
    dishes = _filter_dishes(
        list(result.unique().scalars().all()),
        order_scope=params.order_scope,
        order_type=params.order_type,
    )[params.offset : params.offset + params.limit]

    logger.debug("Dishes fetched", extra={"req_id": request_id, "count": len(dishes)})

    return dishes


@router.get("/dishes/status", response_model=List[DishStatusResponse])
async def fetch_dish_statuses(
    request: Request,
    params: DishQueryParams = Depends(validate_query_params(DishQueryParams)),
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_reader),
) -> list[DishStatusResponse]:
    """List redacted dish execution status rows."""
    request_id = request.state.req_id
    query = select(Dish).options(
        joinedload(Dish.recipe),
        joinedload(Dish.order),
        selectinload(Dish.dish_ingredients),
    )

    if params.order_id is not None:
        query = query.where(Dish.order_id == params.order_id)
    if params.processing_status:
        query = query.where(Dish.processing_status == params.processing_status)
    if params.processing_status and params.processing_status == "new":
        query = query.order_by(asc(Dish.created_at))
    elif params.processing_status and params.processing_status == "processing":
        query = query.order_by(
            case((Dish.started_at.is_(None), 0), else_=1),
            asc(Dish.started_at),
            asc(Dish.created_at),
        )
    else:
        query = query.order_by(desc(Dish.created_at))

    query = query.limit(1000)
    result = await db.execute(query)
    dishes = _filter_dishes(
        list(result.unique().scalars().all()),
        order_scope=params.order_scope,
        order_type=params.order_type,
    )[params.offset : params.offset + params.limit]
    logger.debug("Dish statuses fetched", extra={"req_id": request_id, "count": len(dishes)})
    return [_serialize_dish_status(dish) for dish in dishes]


@router.get(
    "/dishes/{dish_id}/ingredient-status",
    response_model=List[DishIngredientStatusResponse],
)
async def list_dish_ingredient_status(
    request: Request,
    dish_id: int,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_reader),
):
    """List redacted dish ingredient execution status for a dish."""
    req_id = request.state.req_id

    result = await db.execute(
        select(Dish)
        .options(
            joinedload(Dish.recipe)
            .joinedload(Recipe.recipe_ingredients)
            .joinedload(RecipeIngredient.ingredient)
        )
        .where(Dish.id == dish_id)
    )
    dish = result.scalars().first()
    if not dish:
        raise HTTPException(status_code=404, detail="Dish not found")
    recipe_step_lookup = _build_recipe_step_lookup(getattr(dish, "recipe", None))

    result = await db.execute(
        select(DishIngredient)
        .options(
            selectinload(DishIngredient.recipe_ingredient).selectinload(RecipeIngredient.ingredient)
        )
        .where(DishIngredient.dish_id == dish_id, DishIngredient.deleted.is_(False))
        .order_by(
            DishIngredient.service_exec_start_time.is_(None),
            DishIngredient.service_exec_start_time.asc(),
            DishIngredient.service_exec_completed_time.asc(),
            DishIngredient.created_at.asc(),
            DishIngredient.id.asc(),
        )
    )
    records = result.scalars().all()
    logger.debug(
        "Dish ingredient status fetched",
        extra={"req_id": req_id, "dish_id": dish_id, "count": len(records)},
    )
    collapsed = _collapse_dish_ingredient_records(
        list(records),
        recipe_step_lookup=recipe_step_lookup,
    )
    return [
        _serialize_dish_ingredient_status(record, recipe_step_lookup=recipe_step_lookup)
        for record, _recipe_ingredient in collapsed
    ]


@router.get("/dishes/{dish_id}/ingredients", response_model=List[DishIngredientResponse])
async def list_dish_ingredients(
    request: Request,
    dish_id: int,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_admin),
):
    """List dish ingredient executions for a dish."""
    req_id = request.state.req_id

    result = await db.execute(
        select(Dish)
        .options(
            joinedload(Dish.recipe)
            .joinedload(Recipe.recipe_ingredients)
            .joinedload(RecipeIngredient.ingredient)
        )
        .where(Dish.id == dish_id)
    )
    dish = result.scalars().first()
    if not dish:
        raise HTTPException(status_code=404, detail="Dish not found")
    recipe_step_lookup = _build_recipe_step_lookup(getattr(dish, "recipe", None))

    result = await db.execute(
        select(DishIngredient)
        .options(
            selectinload(DishIngredient.recipe_ingredient).selectinload(RecipeIngredient.ingredient)
        )
        .where(DishIngredient.dish_id == dish_id, DishIngredient.deleted.is_(False))
        .order_by(
            DishIngredient.service_exec_start_time.is_(None),
            DishIngredient.service_exec_start_time.asc(),
            DishIngredient.service_exec_completed_time.asc(),
            DishIngredient.created_at.asc(),
            DishIngredient.id.asc(),
        )
    )
    records = result.scalars().all()
    logger.debug(
        "Dish ingredients fetched",
        extra={"req_id": req_id, "dish_id": dish_id, "count": len(records)},
    )
    collapsed = _collapse_dish_ingredient_records(
        list(records),
        recipe_step_lookup=recipe_step_lookup,
    )
    auth_context = getattr(request.state, "auth_context", None)
    if getattr(auth_context, "role", None) != "service":
        return [
            _serialize_admin_dish_ingredient_history(record)
            for record, _recipe_ingredient in collapsed
        ]
    return [
        _serialize_dish_ingredient(record, recipe_step_lookup=recipe_step_lookup)
        for record, _recipe_ingredient in collapsed
    ]


@router.get("/dishes/{dish_id}/ingredient-history", response_model=List[DishIngredientResponse])
async def list_dish_ingredient_history(
    request: Request,
    dish_id: int,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_admin),
) -> list[DishIngredientResponse]:
    """List every dish ingredient runtime row for admin execution-history review."""
    req_id = request.state.req_id
    rows = await _dish_ingredient_history_for_dish(db, dish_id=dish_id)
    logger.debug(
        "Dish ingredient execution history fetched",
        extra={"req_id": req_id, "dish_id": dish_id, "count": len(rows)},
    )
    return rows


@router.get("/orders/{order_id}/execution-history", response_model=List[DishIngredientResponse])
async def list_order_execution_history(
    request: Request,
    order_id: int,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_admin),
) -> list[DishIngredientResponse]:
    """List every dish ingredient runtime row for all dishes in an order."""
    req_id = request.state.req_id
    rows = await _dish_ingredient_history_for_order(db, order_id=order_id)
    logger.debug(
        "Order execution history fetched",
        extra={"req_id": req_id, "order_id": order_id, "count": len(rows)},
    )
    return rows


@router.get("/dish-ingredients/in-flight", response_model=List[DishIngredientResponse])
async def list_in_flight_dish_ingredients(
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_service),
) -> list[DishIngredientResponse]:
    """List service execution rows that Timer should reconcile."""
    now = utc_now_db()
    requested_limit = max(1, min(limit, 1000))
    result = await db.execute(
        select(DishIngredient)
        .where(
            DishIngredient.deleted.is_(False),
            DishIngredient.service_exec_status.in_(("dispatched", "running")),
        )
        .order_by(DishIngredient.service_exec_start_time.asc(), DishIngredient.id.asc())
        .limit(1000)
    )
    return [
        DishIngredientResponse.model_validate(record)
        for record in result.scalars().all()
        if _timer_pollable(record, now)
    ][:requested_limit]


@router.get("/dish-ingredients/execution-pending", response_model=List[DishIngredientResponse])
async def list_execution_pending_dish_ingredients(
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_service),
) -> list[DishIngredientResponse]:
    """List runner-owned service execution rows that still need workload execution."""
    requested_limit = max(1, min(limit, 1000))
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(DishIngredient)
        .where(
            DishIngredient.deleted.is_(False),
            DishIngredient.service_exec_status.in_(("dispatched", "running")),
            DishIngredient.service_exec_id.like(f"{EXPEDITER_RUNNER_RECEIPT_PREFIX}%"),
        )
        .order_by(DishIngredient.service_exec_start_time.asc(), DishIngredient.id.asc())
        .limit(1000)
    )
    records = list(result.scalars().all())
    service_types = {
        str(record.service_type or "").strip().lower()
        for record in records
        if str(record.service_type or "").strip()
    }
    plugins_by_service_type: dict[str, ServicePlugin] = {}
    if service_types:
        plugin_result = await db.execute(
            select(ServicePlugin).where(ServicePlugin.service_type.in_(service_types))
        )
        plugins_by_service_type = {
            plugin.service_type.strip().lower(): plugin for plugin in plugin_result.scalars().all()
        }
    claimable = [
        record
        for record in records
        if expediter_runner_claimable(
            record,
            plugins_by_service_type.get(str(record.service_type or "").strip().lower()),
            now,
        )
    ][:requested_limit]
    return [DishIngredientResponse.model_validate(record) for record in claimable]


@router.get("/dish-ingredients/cancel-requested", response_model=List[DishIngredientResponse])
async def list_cancel_requested_dish_ingredients(
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_service),
) -> list[DishIngredientResponse]:
    """List one active firing-phase runtime segment Timer should cancel after alert resolve."""
    now = utc_now_db()
    criteria = (
        DishIngredient.deleted.is_(False),
        DishIngredient.service_exec_status.in_(("dispatched", "running")),
        Dish.run_phase == "firing",
        Dish.processing_status.in_(("new", "processing", "finalizing")),
        Order.alert_status == "resolved",
        Order.processing_status == "resolving",
    )
    segment_result = await db.execute(
        select(DishIngredient)
        .join(Dish, Dish.id == DishIngredient.dish_id)
        .join(Order, Order.id == Dish.order_id)
        .where(*criteria)
        .order_by(
            DishIngredient.service_exec_start_time.asc(),
            DishIngredient.dish_id.asc(),
            DishIngredient.depth.asc(),
            DishIngredient.parallel_group.asc(),
            DishIngredient.id.asc(),
        )
        .limit(1000)
    )
    first = next(
        (record for record in segment_result.scalars().all() if _timer_pollable(record, now)),
        None,
    )
    if first is None:
        return []

    result = await db.execute(
        select(DishIngredient)
        .join(Dish, Dish.id == DishIngredient.dish_id)
        .join(Order, Order.id == Dish.order_id)
        .where(
            *criteria,
            DishIngredient.dish_id == first.dish_id,
            DishIngredient.depth == first.depth,
            DishIngredient.parallel_group == first.parallel_group,
        )
        .order_by(DishIngredient.step_order.asc(), DishIngredient.id.asc())
    )
    return [
        DishIngredientResponse.model_validate(record)
        for record in result.scalars().all()
        if _timer_pollable(record, now)
    ]


@router.get("/dish-ingredients/advance-ready", response_model=List[DishIngredientResponse])
async def list_advance_ready_dish_ingredients(
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_service),
) -> list[DishIngredientResponse]:
    """List active dishes Timer should cascade or advance after synchronous terminal work."""
    requested_limit = max(1, min(limit, 1000))
    candidate_result = await db.execute(
        select(Dish.id)
        .where(
            Dish.processing_status.in_(("new", "processing", "finalizing")),
        )
        .order_by(Dish.created_at.asc(), Dish.id.asc())
        .limit(1000)
    )
    dish_ids: list[int] = []
    seen: set[int] = set()
    for dish_id in candidate_result.scalars().all():
        if dish_id in seen:
            continue
        seen.add(dish_id)
        dish_ids.append(dish_id)
    if not dish_ids:
        return []

    ready: list[DishIngredient] = []
    for dish_id in dish_ids:
        rows_result = await db.execute(
            select(DishIngredient)
            .where(DishIngredient.dish_id == dish_id, DishIngredient.deleted.is_(False))
            .order_by(
                DishIngredient.depth.asc(),
                DishIngredient.parallel_group.asc(),
                DishIngredient.step_order.asc(),
                DishIngredient.id.asc(),
            )
        )
        rows = list(rows_result.scalars().all())
        representative = _advance_ready_representative(rows)
        if representative is not None:
            ready.append(representative)
        if len(ready) >= requested_limit:
            break
    return [DishIngredientResponse.model_validate(record) for record in ready]


@router.post(
    "/dish-ingredients/{dish_ingredient_id}/poll-claim",
    response_model=DishIngredientResponse,
)
async def claim_dish_ingredient_for_poll(
    request: Request,
    dish_ingredient_id: int,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_service),
) -> DishIngredientResponse:
    """Atomically claim an in-flight runtime row for Timer polling."""
    req_id = request.state.req_id
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(seconds=get_settings().lock_timeout_seconds)
    async with _write_transaction(db):
        update_result = await db.execute(
            update(DishIngredient)
            .where(
                DishIngredient.id == dish_ingredient_id,
                DishIngredient.deleted.is_(False),
                DishIngredient.service_exec_status.in_(("dispatched", "running")),
                or_(
                    DishIngredient.service_exec_claimed_at.is_(None),
                    DishIngredient.service_exec_claimed_at < stale_cutoff,
                ),
            )
            .values(
                service_exec_status="running",
                service_exec_claimed_at=now,
                service_exec_claimed_by=req_id,
                updated_at=now,
            )
        )
        if _rowcount(update_result) == 0:
            raise HTTPException(status_code=409, detail="Dish ingredient already claimed")
        result = await db.execute(
            select(DishIngredient).where(DishIngredient.id == dish_ingredient_id)
        )
        row = result.scalars().first()
        if row is None:
            raise HTTPException(status_code=404, detail="Dish ingredient not found")
    return DishIngredientResponse.model_validate(row)


@router.post(
    "/dish-ingredients/{dish_ingredient_id}/execution-claim",
    response_model=DishIngredientResponse,
)
async def claim_dish_ingredient_for_execution(
    request: Request,
    dish_ingredient_id: int,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_service),
) -> DishIngredientResponse:
    """Atomically claim a runner-owned runtime row for workload execution."""
    req_id = request.state.req_id
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(seconds=get_settings().lock_timeout_seconds)
    async with _write_transaction(db):
        result = await db.execute(
            select(DishIngredient)
            .where(
                DishIngredient.id == dish_ingredient_id,
                DishIngredient.deleted.is_(False),
            )
            .with_for_update()
        )
        row = result.scalars().first()
        if row is None:
            raise HTTPException(status_code=404, detail="Dish ingredient not found")
        if row.service_exec_status not in {
            "dispatched",
            "running",
        } or not _has_expediter_runner_receipt(row):
            raise HTTPException(status_code=409, detail="Dish ingredient is not executable")
        claimed_at = (
            _aware_datetime(row.service_exec_claimed_at, stale_cutoff)
            if row.service_exec_claimed_at is not None
            else None
        )
        if claimed_at is not None and claimed_at >= stale_cutoff:
            raise HTTPException(status_code=409, detail="Dish ingredient already claimed")
        plugin_result = await db.execute(
            select(ServicePlugin).where(
                ServicePlugin.service_type == str(row.service_type or "").strip().lower()
            )
        )
        plugin = plugin_result.scalar_one_or_none()
        if not expediter_runner_claimable(row, plugin, now):
            raise HTTPException(
                status_code=409,
                detail="Service plugin is not currently callable; execution remains cached",
            )
        row.service_exec_status = "running"
        row.service_exec_claimed_at = now
        row.service_exec_claimed_by = req_id
        row.updated_at = now
    return DishIngredientResponse.model_validate(row)


@router.post(
    "/dish-ingredients/{dish_ingredient_id}/execution-release",
    response_model=DishIngredientResponse,
)
async def release_dish_ingredient_execution_claim(
    request: Request,
    dish_ingredient_id: int,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_service),
) -> DishIngredientResponse:
    """Release an expediter-runner claim after downstream provider work starts."""
    req_id = request.state.req_id
    async with _write_transaction(db):
        update_result = await db.execute(
            update(DishIngredient)
            .where(
                DishIngredient.id == dish_ingredient_id,
                DishIngredient.deleted.is_(False),
                DishIngredient.service_exec_claimed_by == req_id,
                DishIngredient.service_exec_status == "running",
            )
            .values(service_exec_claimed_at=None, service_exec_claimed_by=None)
        )
        if _rowcount(update_result) == 0:
            raise HTTPException(status_code=409, detail="Dish ingredient claim not owned")
        result = await db.execute(
            select(DishIngredient).where(DishIngredient.id == dish_ingredient_id)
        )
        row = result.scalars().first()
        if row is None:
            raise HTTPException(status_code=404, detail="Dish ingredient not found")
    return DishIngredientResponse.model_validate(row)


@router.post(
    "/dish-ingredients/{dish_ingredient_id}/poll-release",
    response_model=DishIngredientResponse,
)
async def release_dish_ingredient_poll_claim(
    request: Request,
    dish_ingredient_id: int,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_service),
) -> DishIngredientResponse:
    """Release a Timer poll claim when the plugin execution remains non-terminal."""
    req_id = request.state.req_id
    async with _write_transaction(db):
        update_result = await db.execute(
            update(DishIngredient)
            .where(
                DishIngredient.id == dish_ingredient_id,
                DishIngredient.deleted.is_(False),
                DishIngredient.service_exec_claimed_by == req_id,
                DishIngredient.service_exec_status == "running",
            )
            .values(service_exec_claimed_at=None, service_exec_claimed_by=None)
        )
        if _rowcount(update_result) == 0:
            raise HTTPException(status_code=409, detail="Dish ingredient claim not owned")
        result = await db.execute(
            select(DishIngredient).where(DishIngredient.id == dish_ingredient_id)
        )
        row = result.scalars().first()
        if row is None:
            raise HTTPException(status_code=404, detail="Dish ingredient not found")
    return DishIngredientResponse.model_validate(row)


@router.post(
    "/dish-ingredients/{dish_ingredient_id}/reconcile",
    response_model=DishIngredientResponse,
)
async def reconcile_dish_ingredient(
    request: Request,
    dish_ingredient_id: int,
    payload: DishIngredientUpsert,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_service),
) -> DishIngredientResponse:
    """Apply Timer reconciliation results to a service execution row."""
    async with _write_transaction(db):
        result = await db.execute(
            select(DishIngredient).where(DishIngredient.id == dish_ingredient_id).with_for_update()
        )
        row = result.scalars().first()
        if row is None or row.deleted:
            raise HTTPException(status_code=404, detail="Dish ingredient not found")
        _require_reconcile_owner(
            request,
            row,
            expected_service_type="timer",
            require_runner_receipt=False,
        )
        try:
            _apply_dish_ingredient_update(row, payload)
        except ServiceExecutionStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.refresh(row)
    return DishIngredientResponse.model_validate(row)


@router.post(
    "/dish-ingredients/{dish_ingredient_id}/execution-reconcile",
    response_model=DishIngredientResponse,
)
async def reconcile_executed_dish_ingredient(
    request: Request,
    dish_ingredient_id: int,
    payload: DishIngredientUpsert,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_service),
) -> DishIngredientResponse:
    """Apply expediter-runner execution results to a runner-dispatched runtime row."""
    async with _write_transaction(db):
        result = await db.execute(
            select(DishIngredient).where(DishIngredient.id == dish_ingredient_id).with_for_update()
        )
        row = result.scalars().first()
        if row is None or row.deleted:
            raise HTTPException(status_code=404, detail="Dish ingredient not found")
        _require_reconcile_owner(
            request,
            row,
            expected_service_type="expediter-runner",
            require_runner_receipt=True,
        )
        try:
            _apply_dish_ingredient_update(row, payload)
        except ServiceExecutionStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.refresh(row)
    return DishIngredientResponse.model_validate(row)


def _apply_dish_ingredient_update(row: DishIngredient, payload: DishIngredientUpsert) -> None:
    update_data = payload.model_dump(exclude_unset=True)
    update_data.pop("dish_id", None)
    requested_status = update_data.pop("service_exec_status", None)
    if requested_status is not None:
        row.service_exec_status = validate_execution_transition(
            row.service_exec_status,
            requested_status,
        )
    for key, value in update_data.items():
        if hasattr(row, key):
            setattr(row, key, value)
    row.service_exec_status = verdict_status(
        requested_status=row.service_exec_status,
        expected_outcome=row.service_exec_expected_outcome,
        actual_outcome=row.service_exec_actual_outcome,
    )
    if row.service_exec_run_time is None:
        row.service_exec_run_time = runtime_seconds(
            row.service_exec_start_time,
            row.service_exec_completed_time,
        )
    row.service_exec_sla_exceeded = sla_exceeded(
        row.service_exec_expected_secs,
        row.service_exec_run_time,
    )
    if row.service_exec_status in EXECUTION_TERMINAL_STATUSES:
        row.service_exec_claimed_at = None
        row.service_exec_claimed_by = None
    row.updated_at = datetime.now(timezone.utc)
