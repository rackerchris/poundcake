"""Cook router for order planning and dish advancement."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.api.orders import dispatch_order
from api.core.config import get_settings
from api.api.auth import require_service
from api.core.database import get_db
from api.core.logging import get_logger
from api.core.metrics import (
    record_dish_wall_time,
    record_dish_work_execution_time,
    record_order_lifetime,
)
from api.core.statuses import ORDER_TERMINAL_PROCESSING_STATUSES
from api.core.time import utc_now_db
from api.models.models import (
    AlertSuppression,
    AlertSuppressionMatcher,
    Dish,
    DishIngredient,
    Order,
    RecipeIngredient,
    ScheduledTask,
    ServicePlugin,
)
from api.plugins.state import (
    EXPEDITER_RUNNER_RECEIPT_PREFIX,
    EXPEDITER_RUNNER_SERVICE_TYPE,
    PLUGIN_CALLABLE_RUN_STATES,
    PLUGIN_RUN_STATE_FAILED,
    PLUGIN_RUN_STATE_HEALTHY,
    TERMINAL_EXECUTION_STATUSES,
    normalize_plugin_run_state,
    runtime_seconds,
)
from api.schemas.schemas import (
    CookAdvanceReadyItem,
    CookAdvanceResponse,
    CookDispatchedItem,
    CookSegmentMetadata,
    OrderDispatchResponse,
)
from api.services.suppression_service import (
    find_first_matching_suppression,
    normalize_utc_datetime,
    save_suppressed_event,
)
from api.services.suppression_sync import upsert_plugin_suppressions
from kitchen.execution_segments import has_in_flight_execution, next_pending_execution_segment

router = APIRouter()
logger = get_logger(__name__)


@router.post("/cook/orders/{order_id}", response_model=OrderDispatchResponse)
async def cook_order(
    request: Request,
    order_id: int,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_service),
) -> OrderDispatchResponse:
    """Expand a dispatchable order into a dish with hydrated service execution rows."""
    response = await dispatch_order(request=request, order_id=order_id, db=db)
    logger.info(
        "Cook order planned",
        extra={
            "req_id": request.state.req_id,
            "order_id": response.order_id,
            "dish_id": response.dish_id,
            "cook_status": response.status,
            "run_phase": response.run_phase,
            "recipe_id": response.recipe_id,
            "recipe_name": response.recipe_name,
        },
    )
    if response.dish_id is not None:
        suppressed_response = await _complete_suppressed_dish_if_matched(
            dish_id=response.dish_id,
            req_id=request.state.req_id,
            db=db,
        )
        if suppressed_response is not None:
            logger.info(
                "Cook dish suppressed",
                extra={
                    "req_id": request.state.req_id,
                    "order_id": response.order_id,
                    "dish_id": response.dish_id,
                    "run_phase": response.run_phase,
                    "reason": suppressed_response.blocked,
                },
            )
            return OrderDispatchResponse(
                status="complete",
                order_id=response.order_id,
                dish_id=response.dish_id,
                run_phase=response.run_phase,
                recipe_id=response.recipe_id,
                recipe_name=response.recipe_name,
                reason=suppressed_response.blocked,
            )
        await _advance_dish(
            dish_id=response.dish_id,
            req_id=request.state.req_id,
            db=db,
        )
    return response


async def _complete_suppressed_dish_if_matched(
    *,
    dish_id: int,
    req_id: str,
    db: AsyncSession,
) -> CookAdvanceResponse | None:
    settings = get_settings()
    if not settings.suppressions_enabled:
        return None

    result = await db.execute(
        select(Dish).options(selectinload(Dish.order)).where(Dish.id == dish_id)
    )
    dish = result.scalars().first()
    if dish is None or dish.order is None:
        return None
    order = dish.order
    if (dish.run_phase or "").lower() != "firing":
        return None
    if (order.alert_status or "").lower() != "firing":
        return None
    labels = order.labels if isinstance(order.labels, dict) else {}
    suppression = await find_first_matching_suppression(
        db=db,
        labels=labels,
        received_at=utc_now_db(),
    )
    if suppression is None:
        return None

    now = utc_now_db()
    raw_data = order.raw_data if isinstance(order.raw_data, dict) else {}
    await save_suppressed_event(
        db=db,
        suppression=suppression,
        alert_data=raw_data,
        req_id=req_id,
        received_at=now,
    )

    evidence = _suppression_decision_payload(
        suppression=suppression,
        order=order,
        decided_at=now,
    )
    dish.processing_status = "complete"
    dish.dish_exec_status = "succeeded"
    dish.started_at = dish.started_at or now
    dish.completed_at = now
    dish.run_time_secs = runtime_seconds(dish.started_at, now)
    dish.dish_actual_outcome = evidence
    dish.error_message = None
    dish.updated_at = now

    order.processing_status = "complete"
    order.remediation_outcome = "none"
    order.is_active = False
    order.auto_close_eligible = False
    order.clear_deadline_at = None
    order.clear_timed_out_at = None
    order.updated_at = now

    await db.execute(
        update(DishIngredient)
        .where(
            DishIngredient.dish_id == dish.id,
            DishIngredient.deleted.is_(False),
            DishIngredient.service_exec_status == "pending",
            DishIngredient.service_exec_start_time.is_(None),
        )
        .values(deleted=True, deleted_at=now, updated_at=now)
    )
    await db.commit()
    return CookAdvanceResponse(
        status="complete",
        dish_id=dish.id,
        order_id=order.id,
        blocked="suppressed",
        terminal=True,
    )


def _suppression_decision_payload(
    *,
    suppression: AlertSuppression,
    order: Order,
    decided_at: datetime,
) -> dict:
    return {
        "success": True,
        "decision": "suppressed",
        "reason": suppression.reason,
        "suppression": {
            "id": suppression.id,
            "name": suppression.name,
            "scope": suppression.scope,
            "source": suppression.source,
            "source_service_type": suppression.source_service_type,
            "source_ref": suppression.source_ref,
            "starts_at": _isoformat(normalize_utc_datetime(suppression.starts_at)),
            "ends_at": _isoformat(normalize_utc_datetime(suppression.ends_at)),
            "matchers": [_suppression_matcher_payload(item) for item in suppression.matchers],
        },
        "order": {
            "id": order.id,
            "req_id": order.req_id,
            "fingerprint": order.fingerprint,
            "alert_group_name": order.alert_group_name,
            "alert_status": order.alert_status,
            "labels": order.labels if isinstance(order.labels, dict) else {},
        },
        "decided_at": _isoformat(decided_at),
    }


def _suppression_matcher_payload(matcher: AlertSuppressionMatcher) -> dict:
    return {
        "label_key": matcher.label_key,
        "operator": matcher.operator,
        "value": matcher.value,
    }


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


@router.post("/cook/dishes/{dish_id}/advance", response_model=CookAdvanceResponse)
async def advance_dish(
    request: Request,
    dish_id: int,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_service),
) -> CookAdvanceResponse:
    """Mark the next runnable service segment for Expediter-owned execution."""
    return await _advance_dish(
        dish_id=dish_id,
        req_id=request.state.req_id,
        db=db,
    )


async def _advance_dish(
    *,
    dish_id: int,
    req_id: str,
    db: AsyncSession,
) -> CookAdvanceResponse:
    result = await db.execute(
        select(Dish).options(selectinload(Dish.recipe)).where(Dish.id == dish_id)
    )
    dish = result.scalars().first()
    if dish is None:
        raise HTTPException(status_code=404, detail="Dish not found")

    ingredients_result = await db.execute(
        select(DishIngredient)
        .where(DishIngredient.dish_id == dish_id, DishIngredient.deleted.is_(False))
        .order_by(DishIngredient.id.asc())
    )
    rows = list(ingredients_result.scalars().all())
    segment_input = [_dish_ingredient_runtime_dict(item) for item in rows]
    await db.commit()
    logger.info(
        "Cook dish advance evaluating",
        extra={
            "req_id": req_id,
            "order_id": dish.order_id,
            "dish_id": dish_id,
            "run_phase": dish.run_phase,
            "dish_status": dish.processing_status,
            "runtime_row_count": len(rows),
        },
    )

    segment = next_pending_execution_segment(
        {
            "id": dish.id,
            "recipe": {"recipe_ingredients": []},
        },
        segment_input,
    )
    if segment is None:
        if has_in_flight_execution(segment_input):
            logger.info(
                "Cook dish advance blocked by in-flight executions",
                extra={"req_id": req_id, "order_id": dish.order_id, "dish_id": dish_id},
            )
            return CookAdvanceResponse(
                status="blocked",
                dish_id=dish_id,
                order_id=dish.order_id,
                blocked="Dish has in-flight service executions",
            )
        if _has_pending_after_blocking_failure(segment_input):
            logger.info(
                "Cook dish advance blocked by pending rows after failure",
                extra={"req_id": req_id, "order_id": dish.order_id, "dish_id": dish_id},
            )
            return CookAdvanceResponse(
                status="blocked",
                dish_id=dish_id,
                order_id=dish.order_id,
                blocked="Dish has pending rows blocked by prior service execution failure",
            )
        terminal_status, dish_exec_status, error = _dish_terminal_state(segment_input)
        db_dish = await db.get(Dish, dish_id)
        dispatch_resolving = False
        if db_dish is not None:
            db_dish.processing_status = terminal_status
            db_dish.dish_exec_status = dish_exec_status
            completed_at = utc_now_db()
            db_dish.completed_at = completed_at
            db_dish.run_time_secs = runtime_seconds(db_dish.started_at, completed_at)
            db_dish.error_message = error
            await _apply_terminal_runtime_outputs(db, db_dish)
            group_name = await _dish_group_name(db, db_dish.order_id)
            work_execution_time = _total_work_execution_time_secs(segment_input)
            record_dish_wall_time(
                group_name,
                db_dish.run_phase,
                terminal_status,
                db_dish.run_time_secs,
            )
            record_dish_work_execution_time(
                group_name,
                db_dish.run_phase,
                terminal_status,
                work_execution_time,
            )
            dispatch_resolving = await _finalize_order_for_dish(db, db_dish, terminal_status)
            logger.info(
                "Cook dish terminal",
                extra={
                    "req_id": req_id,
                    "order_id": db_dish.order_id,
                    "dish_id": db_dish.id,
                    "run_phase": db_dish.run_phase,
                    "terminal_status": terminal_status,
                    "dish_exec_status": dish_exec_status,
                    "run_time_secs": db_dish.run_time_secs,
                    "work_execution_time_secs": work_execution_time,
                    "dispatch_resolving": dispatch_resolving,
                },
            )
            await db.commit()
        if dispatch_resolving and dish.order_id is not None:
            order_request = SimpleNamespace(state=SimpleNamespace(req_id=req_id))
            resolving_response = await dispatch_order(
                request=order_request,
                order_id=dish.order_id,
                db=db,
            )
            logger.info(
                "Cook resolving dish planned",
                extra={
                    "req_id": req_id,
                    "order_id": resolving_response.order_id,
                    "dish_id": resolving_response.dish_id,
                    "cook_status": resolving_response.status,
                    "run_phase": resolving_response.run_phase,
                    "recipe_id": resolving_response.recipe_id,
                    "recipe_name": resolving_response.recipe_name,
                },
            )
        return CookAdvanceResponse(
            status=(
                terminal_status
                if terminal_status in {"complete", "failed", "errored", "timeout", "canceled"}
                else "failed"
            ),
            dish_id=dish_id,
            order_id=dish.order_id,
            blocked=error,
            terminal=True,
        )

    ready = segment.rows
    dispatched: list[CookDispatchedItem] = []
    logger.info(
        "Cook marking execution segment ready",
        extra={
            "req_id": req_id,
            "order_id": dish.order_id,
            "dish_id": dish_id,
            "depth": segment.depth,
            "parallel_group": segment.parallel_group,
            "ready_count": len(ready),
            "service_types": ",".join(segment.service_types),
        },
    )
    for item in ready:
        row_id = int(item["id"])
        start_time = utc_now_db()
        claim_result = await db.execute(
            update(DishIngredient)
            .where(
                DishIngredient.id == row_id,
                DishIngredient.service_exec_status == "pending",
                DishIngredient.service_exec_start_time.is_(None),
                DishIngredient.service_exec_claimed_at.is_(None),
            )
            .values(
                service_exec_status="running",
                service_exec_start_time=start_time,
                service_exec_id=f"{EXPEDITER_RUNNER_RECEIPT_PREFIX}{row_id}",
                service_exec_actual_outcome={
                    "success": True,
                    "status": "running",
                    "receipt_owner": EXPEDITER_RUNNER_SERVICE_TYPE,
                    "dish_ingredient_id": row_id,
                },
                service_exec_claimed_at=None,
                service_exec_claimed_by=None,
                updated_at=start_time,
            )
        )
        await db.commit()
        if getattr(claim_result, "rowcount", 0) == 0:
            continue
        db_dish = await db.get(Dish, dish_id)
        if db_dish is not None:
            db_dish.processing_status = "processing"
            db_dish.started_at = db_dish.started_at or start_time
            db_dish.updated_at = start_time
        await db.commit()
        dispatched.append(
            CookDispatchedItem(
                dish_ingredient_id=row_id,
                req_id=str(item.get("req_id") or req_id),
                service_type=str(item["service_type"]),
                service_exec=str(item["service_exec"]),
                service_exec_id=f"{EXPEDITER_RUNNER_RECEIPT_PREFIX}{row_id}",
                service_exec_status="running",
                service_exec_error=None,
            )
        )
        logger.info(
            "Cook runtime row marked ready",
            extra={
                "req_id": str(item.get("req_id") or req_id),
                "order_id": dish.order_id,
                "dish_id": dish_id,
                "dish_ingredient_id": row_id,
                "service_type": str(item["service_type"]),
                "service_exec": str(item["service_exec"]),
                "service_exec_id": f"{EXPEDITER_RUNNER_RECEIPT_PREFIX}{row_id}",
                "service_exec_status": "running",
            },
        )

    return CookAdvanceResponse(
        status="dispatched",
        dish_id=dish_id,
        order_id=dish.order_id,
        segment=CookSegmentMetadata(
            depth=segment.depth,
            parallel_group=segment.parallel_group,
            service_types=list(segment.service_types),
        ),
        ready=[_cook_ready_item(item) for item in ready],
        dispatched=dispatched,
    )


def _dish_ingredient_runtime_dict(item: DishIngredient) -> dict:
    return {
        "id": item.id,
        "req_id": item.req_id,
        "dish_id": item.dish_id,
        "recipe_ingredient_id": item.recipe_ingredient_id,
        "task_key": item.task_key,
        "step_order": item.step_order,
        "parallel_group": item.parallel_group,
        "depth": item.depth,
        "service_type": item.service_type,
        "service_exec": item.service_exec,
        "destination_target": item.destination_target,
        "service_payload": item.service_payload,
        "service_exec_parameters": item.service_exec_parameters,
        "service_exec_expected_secs": item.service_exec_expected_secs,
        "service_exec_timeout": item.service_exec_timeout,
        "service_exec_expected_outcome": item.service_exec_expected_outcome,
        "retry_count": item.retry_count,
        "retry_delay": item.retry_delay,
        "on_failure": item.on_failure,
        "service_exec_id": item.service_exec_id,
        "service_exec_status": item.service_exec_status,
        "service_exec_run_time": item.service_exec_run_time,
        "service_exec_actual_outcome": item.service_exec_actual_outcome,
        "service_exec_error": item.service_exec_error,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def _runtime_bucket(item: dict) -> tuple[int, int]:
    depth = item.get("depth")
    if not isinstance(depth, int):
        depth = item.get("step_order")
    if not isinstance(depth, int):
        depth = 1_000_000
    parallel_group = item.get("parallel_group")
    if not isinstance(parallel_group, int):
        parallel_group = 0
    return depth, parallel_group


def _is_blocking_failure(item: dict) -> bool:
    status = str(item.get("service_exec_status") or "").strip().lower()
    on_failure = str(item.get("on_failure") or "stop").strip().lower()
    return status in {"failed", "errored", "timeout", "canceled"} and on_failure != "continue"


def _has_pending_after_blocking_failure(items: list[dict]) -> bool:
    blocking_buckets = [_runtime_bucket(item) for item in items if _is_blocking_failure(item)]
    if not blocking_buckets:
        return False
    first_blocking_bucket = min(blocking_buckets)
    return any(
        str(item.get("service_exec_status") or "").strip().lower() == "pending"
        and _runtime_bucket(item) > first_blocking_bucket
        for item in items
    )


def _cook_ready_item(item: dict) -> CookAdvanceReadyItem:
    return CookAdvanceReadyItem(
        id=int(item["id"]),
        req_id=str(item.get("req_id") or ""),
        dish_id=int(item["dish_id"]),
        recipe_ingredient_id=(
            int(item["recipe_ingredient_id"])
            if item.get("recipe_ingredient_id") is not None
            else None
        ),
        task_key=str(item["task_key"]) if item.get("task_key") is not None else None,
        step_order=int(item.get("step_order") or 1),
        parallel_group=int(item.get("parallel_group") or 0),
        depth=int(item.get("depth") or 0),
        service_type=str(item["service_type"]),
        service_exec=str(item["service_exec"]),
        service_exec_id=(
            str(item["service_exec_id"]) if item.get("service_exec_id") is not None else None
        ),
        service_exec_status=str(item.get("service_exec_status") or "pending"),
        on_failure=(str(item["on_failure"]) if item.get("on_failure") is not None else None),
        created_at=item.get("created_at"),
    )


def _dish_terminal_state(items: list[dict]) -> tuple[str, str, str | None]:
    for item in items:
        status = str(item.get("service_exec_status") or "").strip().lower()
        if status in {"pending", "dispatched", "running"}:
            return "processing", status or "pending", "Dish still has non-terminal work"
        if _is_no_remediation_guard_false(item):
            return (
                "canceled",
                status or "canceled",
                "Remediation skipped because Alertmanager no longer shows the alert firing",
            )
        if (
            status == "canceled"
            and str(item.get("on_failure") or "stop").strip().lower() != "continue"
        ):
            return "canceled", status, str(item.get("service_exec_error") or "Dish step canceled")
        if (
            status in {"failed", "errored", "timeout"}
            and str(item.get("on_failure") or "stop").strip().lower() != "continue"
        ):
            terminal = status if status in {"errored", "timeout"} else "failed"
            return terminal, status, str(item.get("service_exec_error") or "Dish step failed")
    return "complete", "succeeded", None


def _is_no_remediation_guard_false(item: dict) -> bool:
    params = item.get("service_exec_parameters")
    if not isinstance(params, dict):
        params = {}
    if params.get("guard_role") != "remediation_precondition":
        return False
    if params.get("false_outcome") != "cancel_downstream_no_remediation":
        return False
    outcome = item.get("service_exec_actual_outcome")
    if not isinstance(outcome, dict):
        return False
    return outcome.get("is_firing") is False


def _total_work_execution_time_secs(items: list[dict]) -> int | None:
    total = 0
    seen = False
    for item in items:
        run_time = item.get("service_exec_run_time")
        if run_time is None:
            continue
        total += int(run_time)
        seen = True
    return total if seen else None


async def _dish_group_name(db: AsyncSession, order_id: int | None) -> str:
    if order_id is None:
        return "unknown"
    order = await db.get(Order, order_id)
    if order is None:
        return "unknown"
    return order.alert_group_name or "unknown"


async def _apply_terminal_runtime_outputs(db: AsyncSession, dish: Dish) -> None:
    result = await db.execute(
        select(DishIngredient)
        .options(
            selectinload(DishIngredient.recipe_ingredient).selectinload(RecipeIngredient.ingredient)
        )
        .where(
            DishIngredient.dish_id == dish.id,
            DishIngredient.deleted.is_(False),
            DishIngredient.service_exec_status.in_(TERMINAL_EXECUTION_STATUSES),
        )
    )
    for row in result.scalars().all():
        recipe_ingredient = row.recipe_ingredient
        ingredient = recipe_ingredient.ingredient if recipe_ingredient else None
        purpose = str(getattr(ingredient, "ingredient_purpose", "") or "").strip().lower()
        outcome = (
            row.service_exec_actual_outcome
            if isinstance(row.service_exec_actual_outcome, dict)
            else {}
        )
        if purpose == "plugin_health":
            await _apply_plugin_health_result(db, row, dish, outcome)
        elif purpose == "suppression_sync":
            silences = outcome.get("silences") if isinstance(outcome, dict) else None
            if isinstance(silences, list):
                normalized = [item for item in silences if isinstance(item, dict)]
                sync_result = await upsert_plugin_suppressions(
                    db,
                    service_type=row.service_type,
                    suppressions=normalized,
                    synced_at=row.service_exec_completed_time or utc_now_db(),
                )
                row.service_exec_actual_outcome = {**outcome, "suppression_sync": sync_result}
        elif purpose == "suppression_lifecycle":
            suppression = outcome.get("suppression") if isinstance(outcome, dict) else None
            if isinstance(suppression, dict):
                sync_result = await upsert_plugin_suppressions(
                    db,
                    service_type=row.service_type,
                    suppressions=[dict(suppression)],
                    synced_at=row.service_exec_completed_time or utc_now_db(),
                )
                row.service_exec_actual_outcome = {
                    **outcome,
                    "suppression_lifecycle_sync": sync_result,
                }


async def _apply_plugin_health_result(
    db: AsyncSession,
    row: DishIngredient,
    dish: Dish,
    outcome: dict,
) -> None:
    result = await db.execute(
        select(ServicePlugin).where(ServicePlugin.service_type == row.service_type)
    )
    plugin = result.scalar_one_or_none()
    if plugin is None:
        return
    now = row.service_exec_completed_time or utc_now_db()
    if row.service_exec_status in {"failed", "errored", "timeout", "canceled"}:
        status = PLUGIN_RUN_STATE_FAILED
    else:
        status = str(outcome.get("status") or "").strip().lower()
    try:
        status = normalize_plugin_run_state(status)
    except ValueError:
        status = (
            PLUGIN_RUN_STATE_HEALTHY
            if row.service_exec_status == "succeeded"
            else PLUGIN_RUN_STATE_FAILED
        )
    plugin.health_status = status
    plugin.health_message = str(outcome.get("message") or row.service_exec_error or "") or None
    plugin.health_error_code = (
        str(outcome.get("error_code")) if outcome.get("error_code") is not None else None
    )
    plugin.health_latency_ms = (
        int(outcome["latency_ms"]) if isinstance(outcome.get("latency_ms"), int) else None
    )
    details = outcome.get("details") if isinstance(outcome.get("details"), dict) else outcome
    plugin.health_details = details
    plugin.last_health_check_at = now
    if status in PLUGIN_CALLABLE_RUN_STATES:
        plugin.last_success_at = now
        plugin.consecutive_failures = 0
    else:
        plugin.consecutive_failures = int(plugin.consecutive_failures or 0) + 1
    plugin.health_check_state = "idle"
    plugin.health_check_order_id = dish.order_id
    plugin.health_check_started_at = None
    plugin.health_check_grace_until = None
    plugin.updated_at = now


async def _finalize_order_for_dish(
    db: AsyncSession,
    dish: Dish,
    terminal_status: str,
) -> bool:
    if dish.order_id is None:
        return False
    order = await db.get(Order, dish.order_id)
    if order is None or order.processing_status in ORDER_TERMINAL_PROCESSING_STATUSES:
        return False

    active_result = await db.execute(
        select(Dish.id).where(
            Dish.order_id == order.id,
            Dish.id != dish.id,
            Dish.processing_status.in_(("new", "processing", "finalizing")),
        )
    )
    if active_result.first() is not None:
        return False

    now = utc_now_db()
    current_phase = (dish.run_phase or "").lower()
    alert_resolved = (order.alert_status or "").lower() == "resolved"
    if current_phase == "firing" and alert_resolved:
        if terminal_status == "complete":
            order.remediation_outcome = "succeeded"
        elif terminal_status == "canceled":
            order.remediation_outcome = "none"
        else:
            order.remediation_outcome = "failed"
        order.processing_status = "resolving"
        order.is_active = True
        order.auto_close_eligible = False
        order.clear_deadline_at = None
        order.clear_timed_out_at = None
        order.updated_at = now
        return True

    if current_phase == "firing" and terminal_status == "canceled":
        order.remediation_outcome = "none"
        order.processing_status = "complete"
        order.is_active = False
        order.auto_close_eligible = False
        order.clear_deadline_at = None
        order.clear_timed_out_at = None
        order.updated_at = now
        record_order_lifetime(
            order.alert_group_name,
            order.processing_status,
            runtime_seconds(order.created_at, now),
        )
        await _finalize_scheduled_task_for_order(db, order, terminal_status, now)
        return False

    if terminal_status == "complete":
        if current_phase == "firing":
            order.remediation_outcome = "succeeded"
        order.processing_status = "complete"
        order.is_active = False
    else:
        if current_phase == "firing":
            order.remediation_outcome = "failed"
        order.processing_status = (
            terminal_status if terminal_status in {"errored", "timeout", "canceled"} else "failed"
        )
        order.is_active = False
    order.auto_close_eligible = False
    order.clear_deadline_at = None
    order.clear_timed_out_at = None
    order.updated_at = now
    record_order_lifetime(
        order.alert_group_name,
        order.processing_status,
        runtime_seconds(order.created_at, now),
    )
    await _finalize_scheduled_task_for_order(db, order, terminal_status, now)
    return False


async def _finalize_scheduled_task_for_order(
    db: AsyncSession,
    order: Order,
    terminal_status: str,
    now: datetime,
) -> None:
    raw_data = order.raw_data if isinstance(order.raw_data, dict) else {}
    scheduled_task_id = raw_data.get("scheduled_task_id")
    if scheduled_task_id is None:
        return
    task = await db.get(ScheduledTask, int(scheduled_task_id))
    if task is None:
        return
    final_status = "succeeded" if terminal_status == "complete" else terminal_status
    task.status = "idle" if task.is_enabled else "disabled"
    task.last_status = final_status
    task.last_message = f"Scheduled task order {order.req_id} finished with {final_status}"
    task.last_order_id = order.id
    task.last_order_req_id = order.req_id
    task.last_completed_at = now
    if final_status == "succeeded":
        task.consecutive_failures = 0
    else:
        task.consecutive_failures = int(task.consecutive_failures or 0) + 1
    task.next_run_at = (
        now + timedelta(seconds=max(1, int(task.run_interval_seconds or 1)))
        if task.is_enabled
        else None
    )
    task.updated_at = now
