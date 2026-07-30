#  ___                        _  ____      _
# |  _ \ ___  _   _ _ __   __| |/ ___|__ _| | _____
# | |_) / _ \| | | | '_ \ / _` | |   / _` | |/ / _ \
# |  __/ (_) | |_| | | | | (_| | |__| (_| |   <  __/
# |_|   \___/ \__,_|_| |_|\__,_|\____\__,_|_|\_\___|
#
"""API routes for Order management."""

import asyncio
import random
from contextlib import asynccontextmanager
from typing import List, Literal
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, asc, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import selectinload

from api.api.auth import require_reader, require_service
from api.core.database import get_db
from api.core.logging import get_logger
from api.core.time import utc_now_db
from api.models.models import (
    Dish,
    DishIngredient,
    Order,
    Recipe,
    RecipeIngredient,
    ScheduledTask,
    ServicePlugin,
)
from api.schemas.schemas import (
    IncidentTimelineOrderResponse,
    IncidentTimelineEvent,
    IncidentTimelineResponse,
    OrderCreate,
    OrderDispatchResponse,
    OrderResponse,
    OrderStatusResponse,
    OrderUpdate,
)
from api.schemas.query_params import OrderQueryParams, validate_query_params
from api.services.communications_policy import (
    get_global_policy_recipe_for_dispatch,
    get_recipe_local_routes,
    global_policy_configured,
    policy_has_enabled_routes,
    sync_fallback_policy_recipe,
)
from api.core.config import get_settings as _get_settings
from api.services.dish_planner import (
    expected_run_secs_from_recipe_snapshot,
    seed_dish_ingredients_for_phase,
)
from api.services.order_types import (
    ensure_raw_data_order_type,
    require_order_type,
    order_matches_filters,
)
from api.types import (
    MANUAL_ORDER_TYPE,
    SCHEDULED_TASK_ORDER_TYPE,
    OrderScope,
    OrderType,
)

router = APIRouter()
logger = get_logger(__name__)
GLOBAL_COMMS_INHERIT_PHASES = {"firing", "resolving"}
MAX_COOK_DISPATCH_ATTEMPTS = 3
RETRYABLE_COOK_DB_ERROR_CODES = {1020, 1205, 1213}
COOK_DISPATCH_RETRY_BACKOFF_SECONDS = (0.05, 0.1, 0.2)


async def _get_global_policy_routes_for_fallback(db: AsyncSession) -> list:
    """Load global policy routes for fallback recipe sync.

    Returns a list of CommunicationRoute objects from the global policy
    recipe.  These routes are passed to ``sync_fallback_policy_recipe``.
    """
    from api.services.communications_policy import get_global_policy_routes

    return await get_global_policy_routes(db)


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


def _db_error_code(exc: OperationalError) -> int | None:
    args = getattr(getattr(exc, "orig", None), "args", ())
    if not args:
        return None
    try:
        return int(args[0])
    except (TypeError, ValueError):
        return None


def _is_retryable_cook_db_error(exc: OperationalError) -> bool:
    return _db_error_code(exc) in RETRYABLE_COOK_DB_ERROR_CODES


def _order_for_dispatch_query(order_id: int):
    return select(Order).where(Order.id == order_id).with_for_update()


def _active_recipe_query(recipe_name: str):
    return (
        select(Recipe)
        .options(selectinload(Recipe.recipe_ingredients).selectinload(RecipeIngredient.ingredient))
        .where(Recipe.name == recipe_name, Recipe.enabled.is_(True))
    )


def _active_firing_dish_query(order_id: int):
    return (
        select(Dish.id)
        .where(
            Dish.order_id == order_id,
            Dish.run_phase == "firing",
            Dish.processing_status.in_(("new", "processing", "finalizing")),
        )
        .order_by(Dish.created_at.desc())
        .with_for_update()
    )


def _active_phase_dish_query(order_id: int, run_phase: str):
    return (
        select(Dish)
        .where(
            Dish.order_id == order_id,
            Dish.run_phase == run_phase,
            Dish.processing_status.in_(("new", "processing", "finalizing")),
        )
        .order_by(Dish.created_at.desc())
        .with_for_update()
    )


def _dish_ingredients_for_seed_query(dish_id: int):
    return (
        select(DishIngredient)
        .where(DishIngredient.dish_id == dish_id, DishIngredient.deleted.is_(False))
        .with_for_update()
    )


def _recipe_has_phase_remediation(recipe: Recipe, *, phase: str) -> bool:
    for item in recipe.recipe_ingredients or []:
        ingredient = item.ingredient
        if ingredient is None:
            continue
        run_phase = str(item.run_phase or "both").strip().lower()
        if phase == "firing" and run_phase not in {"firing", "both"}:
            continue
        if phase != "firing" and run_phase != phase:
            continue
        if str(ingredient.ingredient_purpose or "").strip().lower() == "remediation":
            return True
    return False


def _inactive_ingredients(recipe: Recipe) -> list[tuple[int, str]]:
    inactive: list[tuple[int, str]] = []
    for item in recipe.recipe_ingredients or []:
        ingredient = item.ingredient
        if ingredient is None or bool(getattr(ingredient, "is_active", True)):
            continue
        inactive.append(
            (
                int(item.ingredient_id),
                str(
                    getattr(ingredient, "task_key_template", "")
                    or f"ingredient-{item.ingredient_id}"
                ),
            )
        )
    return sorted(set(inactive))


def _serialize_order_status(order: Order) -> OrderStatusResponse:
    return OrderStatusResponse.model_validate(
        {
            "id": order.id,
            "req_id": order.req_id,
            "order_type": require_order_type(order.raw_data),
            "alert_status": order.alert_status,
            "alert_group_name": order.alert_group_name,
            "processing_status": order.processing_status,
            "is_active": order.is_active,
            "remediation_outcome": order.remediation_outcome,
            "clear_timeout_sec": order.clear_timeout_sec,
            "clear_deadline_at": order.clear_deadline_at,
            "clear_timed_out_at": order.clear_timed_out_at,
            "auto_close_eligible": order.auto_close_eligible,
            "severity": order.severity,
            "instance": order.instance,
            "correlation_key": order.correlation_key,
            "counter": order.counter,
            "starts_at": order.starts_at,
            "ends_at": order.ends_at,
            "order_lifetime_secs": order.order_lifetime_secs,
            "communication_route_count": len(getattr(order, "communications", []) or []),
            "created_at": order.created_at,
            "updated_at": order.updated_at,
        }
    )


def _serialize_timeline_order(order: Order) -> IncidentTimelineOrderResponse:
    payload = _serialize_order_status(order).model_dump(mode="json")
    payload["labels"] = order.labels if isinstance(order.labels, dict) else {}
    return IncidentTimelineOrderResponse.model_validate(payload)


def _effective_order_scope(
    *,
    order_scope: OrderScope,
    exclude_plugin_health_checks: bool,
) -> OrderScope:
    return "operator" if exclude_plugin_health_checks else order_scope


def _filter_orders(
    orders: list[Order],
    *,
    order_scope: OrderScope,
    order_type: OrderType | None,
) -> list[Order]:
    return [
        order
        for order in orders
        if order_matches_filters(
            raw_data=order.raw_data,
            order_scope=order_scope,
            order_type=order_type,
        )
    ]


@router.get("/orders", response_model=List[OrderResponse])
async def fetch_orders(
    request: Request,
    params: OrderQueryParams = Depends(validate_query_params(OrderQueryParams)),
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_service),
):
    """
    Get orders with optional filtering.

    Query Parameters:
    - processing_status: Filter by processing status (new/processing/resolving/complete/failed/errored/timeout/canceled)
    - alert_status: Filter by alert status (firing/resolved)
    - req_id: Filter by request ID
    - alert_group_name: Filter by alert group name
    - limit: Maximum number of results (default: 100, max: 1000)
    - offset: Number of results to skip (default: 0)

    Returns 422 Unprocessable Entity if unknown or invalid query parameters are provided.
    """
    request_id = request.state.req_id

    logger.debug(
        "Fetching orders",
        extra={
            "req_id": request_id,
            "processing_status": params.processing_status,
            "alert_status": params.alert_status,
            "filter_req_id": params.req_id,
            "alert_group_name": params.alert_group_name,
            "exclude_plugin_health_checks": params.exclude_plugin_health_checks,
            "limit": params.limit,
            "offset": params.offset,
        },
    )

    query = select(Order)

    if params.processing_status:
        query = query.where(Order.processing_status == params.processing_status)
    if params.alert_status:
        query = query.where(Order.alert_status == params.alert_status)
    if params.req_id:
        query = query.where(Order.req_id == params.req_id)
    if params.alert_group_name:
        query = query.where(Order.alert_group_name == params.alert_group_name)
    if params.processing_status and params.processing_status == "new":
        query = query.order_by(asc(Order.created_at))
    else:
        query = query.order_by(desc(Order.created_at))

    query = query.limit(1000)
    result = await db.execute(query)
    orders = _filter_orders(
        list(result.unique().scalars().all()),
        order_scope=_effective_order_scope(
            order_scope=params.order_scope,
            exclude_plugin_health_checks=params.exclude_plugin_health_checks,
        ),
        order_type=params.order_type,
    )[params.offset : params.offset + params.limit]

    logger.debug(
        "Orders fetched successfully",
        extra={"req_id": request_id, "count": len(orders)},
    )

    return orders


@router.get("/orders/status", response_model=List[OrderStatusResponse])
async def fetch_order_statuses(
    request: Request,
    params: OrderQueryParams = Depends(validate_query_params(OrderQueryParams)),
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_reader),
) -> list[OrderStatusResponse]:
    """Get redacted order status rows for reporting views."""
    request_id = request.state.req_id
    query = select(Order)

    if params.processing_status:
        query = query.where(Order.processing_status == params.processing_status)
    if params.alert_status:
        query = query.where(Order.alert_status == params.alert_status)
    if params.req_id:
        query = query.where(Order.req_id == params.req_id)
    if params.alert_group_name:
        query = query.where(Order.alert_group_name == params.alert_group_name)
    if params.processing_status and params.processing_status == "new":
        query = query.order_by(asc(Order.created_at))
    else:
        query = query.order_by(desc(Order.created_at))

    query = query.limit(1000)
    result = await db.execute(query)
    orders = _filter_orders(
        list(result.unique().scalars().all()),
        order_scope=_effective_order_scope(
            order_scope=params.order_scope,
            exclude_plugin_health_checks=params.exclude_plugin_health_checks,
        ),
        order_type=params.order_type,
    )[params.offset : params.offset + params.limit]
    logger.debug(
        "Order statuses fetched successfully",
        extra={"req_id": request_id, "count": len(orders)},
    )
    return [_serialize_order_status(order) for order in orders]


@router.post("/orders", response_model=OrderResponse, status_code=201)
async def create_order(
    request: Request,
    payload: OrderCreate,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_service),
):
    """Create an order manually (non-Alertmanager ingestion)."""
    req_id = request.state.req_id
    if payload.processing_status != "new":
        raise HTTPException(
            status_code=400,
            detail="orders must enter the control plane with processing_status='new'",
        )

    logger.info(
        "Creating order",
        extra={
            "req_id": req_id,
            "order_req_id": payload.req_id,
            "alert_status": payload.alert_status,
            "group_name": payload.alert_group_name,
        },
    )

    create_data = payload.model_dump()
    # Generated by DB; ignore user input to avoid writes to computed column.
    create_data.pop("fingerprint_when_active", None)
    try:
        create_data["raw_data"] = ensure_raw_data_order_type(
            create_data.get("raw_data"),
            MANUAL_ORDER_TYPE,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    order = Order(**create_data)
    db.add(order)
    await db.flush()
    raw_data = payload.raw_data if isinstance(payload.raw_data, dict) else {}
    scheduled_task_id = raw_data.get("scheduled_task_id")
    now = utc_now_db()
    if scheduled_task_id is not None:
        task = await db.get(ScheduledTask, int(scheduled_task_id))
        if task is not None:
            task.status = "queued"
            task.last_order_id = order.id
            task.last_order_req_id = order.req_id
            task.updated_at = now
    task_type = str(raw_data.get("task_type") or "").strip().lower()
    service_type = str(raw_data.get("service_type") or "").strip().lower()
    if task_type == "plugin_health_check" and service_type:
        result = await db.execute(
            select(ServicePlugin).where(ServicePlugin.service_type == service_type)
        )
        plugin_row = result.scalar_one_or_none()
        if plugin_row is not None:
            timeout = int(raw_data.get("timeout_seconds") or 60)
            plugin_row.health_check_state = "queued"
            plugin_row.health_check_order_id = order.id
            plugin_row.health_check_started_at = now
            plugin_row.health_check_grace_until = now + timedelta(seconds=max(timeout, 60))
            plugin_row.updated_at = now
    await db.commit()
    await db.refresh(order)
    result = await db.execute(select(Order).where(Order.id == order.id))
    order = result.unique().scalars().first() or order

    logger.info(
        "Order created successfully",
        extra={"req_id": req_id, "order_id": order.id},
    )

    return order


@router.get("/orders/{order_id}/status", response_model=OrderStatusResponse)
async def get_order_status(
    request: Request,
    order_id: int,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_reader),
) -> OrderStatusResponse:
    """Retrieve redacted status for a specific order."""
    req_id = request.state.req_id
    logger.debug("Fetching order status by ID", extra={"req_id": req_id, "order_id": order_id})

    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.unique().scalars().first()
    if not order:
        logger.warning("Order not found", extra={"req_id": req_id, "order_id": order_id})
        raise HTTPException(status_code=404, detail="Order not found")
    return _serialize_order_status(order)


@router.get("/orders/{order_id}", response_model=OrderResponse)
async def get_order(
    request: Request,
    order_id: int,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_service),
):
    """Retrieve a specific order by ID."""
    req_id = request.state.req_id

    logger.debug("Fetching order by ID", extra={"req_id": req_id, "order_id": order_id})

    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.unique().scalars().first()

    if not order:
        logger.warning("Order not found", extra={"req_id": req_id, "order_id": order_id})
        raise HTTPException(status_code=404, detail="Order not found")

    return order


@router.put("/orders/{order_id}", response_model=OrderResponse)
async def update_order(
    request: Request,
    order_id: int,
    payload: OrderUpdate,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_service),
):
    """Update order lifecycle metadata."""
    req_id = request.state.req_id

    logger.info("Updating order", extra={"req_id": req_id, "order_id": order_id})

    order: Order | None = None
    async with _write_transaction(db):
        result = await db.execute(select(Order).where(Order.id == order_id).with_for_update())
        order = result.unique().scalars().first()
        if not order:
            logger.warning(
                "Order not found for update",
                extra={"req_id": req_id, "order_id": order_id},
            )
            raise HTTPException(status_code=404, detail="Order not found")

        update_data = payload.model_dump(exclude_unset=True)
        # Generated by DB; ignore user input to avoid writes to computed column.
        update_data.pop("fingerprint_when_active", None)

        lifecycle_fields = {
            "processing_status",
            "remediation_outcome",
            "clear_deadline_at",
            "clear_timed_out_at",
            "auto_close_eligible",
            "is_active",
        }
        blocked = sorted(lifecycle_fields.intersection(update_data))
        if blocked:
            raise HTTPException(
                status_code=400,
                detail=(
                    "order lifecycle fields are owned by Prep-Chef, Cook, and Timer: "
                    + ", ".join(blocked)
                ),
            )

        # Apply updates after validation
        for key, value in update_data.items():
            setattr(order, key, value)

        order.updated_at = utc_now_db()

    if order is None:
        raise HTTPException(status_code=500, detail="Order update failed")
    await db.refresh(order)
    result = await db.execute(select(Order).where(Order.id == order.id))
    order = result.unique().scalars().first() or order

    logger.info(
        "Order updated successfully",
        extra={
            "req_id": req_id,
            "order_id": order_id,
            "fields_updated": len(update_data),
            "new_status": order.processing_status,
        },
    )

    return order


async def dispatch_order(
    request: Request,
    order_id: int,
    db: AsyncSession = Depends(get_db),
) -> OrderDispatchResponse:
    """Create/seed a phase-scoped dish for a dispatchable order."""
    req_id = request.state.req_id
    for attempt in range(1, MAX_COOK_DISPATCH_ATTEMPTS + 1):
        try:
            return await _dispatch_order_once(request=request, order_id=order_id, db=db)
        except OperationalError as exc:
            if not _is_retryable_cook_db_error(exc):
                raise
            await db.rollback()
            error_code = _db_error_code(exc)
            if attempt >= MAX_COOK_DISPATCH_ATTEMPTS:
                logger.error(
                    "Cook dispatch retryable database error exhausted",
                    extra={
                        "req_id": req_id,
                        "order_id": order_id,
                        "attempt": attempt,
                        "max_attempts": MAX_COOK_DISPATCH_ATTEMPTS,
                        "db_error_code": error_code,
                    },
                )
                raise
            logger.warning(
                "Cook dispatch retrying database error",
                extra={
                    "req_id": req_id,
                    "order_id": order_id,
                    "attempt": attempt,
                    "max_attempts": MAX_COOK_DISPATCH_ATTEMPTS,
                    "db_error_code": error_code,
                },
            )
            backoff = COOK_DISPATCH_RETRY_BACKOFF_SECONDS[
                min(attempt - 1, len(COOK_DISPATCH_RETRY_BACKOFF_SECONDS) - 1)
            ]
            await asyncio.sleep(backoff + random.uniform(0, 0.025))
    raise RuntimeError("cook dispatch retry loop exited unexpectedly")


async def _dispatch_order_once(
    request: Request,
    order_id: int,
    db: AsyncSession,
) -> OrderDispatchResponse:
    now = utc_now_db()

    response: OrderDispatchResponse | None = None
    async with _write_transaction(db):
        global_policy_is_configured = await global_policy_configured(db)
        result = await db.execute(_order_for_dispatch_query(order_id))
        order = result.scalars().first()
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        run_phase: Literal["firing", "resolving"]
        if order.processing_status == "new":
            run_phase = "firing"
        elif order.processing_status == "resolving":
            run_phase = "resolving"
        else:
            raise HTTPException(
                status_code=409,
                detail=f"Order is not dispatchable (status={order.processing_status})",
            )

        scheduled_order = (
            isinstance(order.raw_data, dict)
            and str(order.raw_data.get("order_type") or "").strip().lower()
            == SCHEDULED_TASK_ORDER_TYPE
        )

        if run_phase == "resolving" and (order.remediation_outcome or "").lower() == "pending":
            active_firing_result = await db.execute(_active_firing_dish_query(order.id))
            if active_firing_result.first() is not None:
                response = OrderDispatchResponse(
                    status="skipped",
                    order_id=order.id,
                    reason="Resolving dispatch is waiting for firing remediation to finish",
                )
                order.updated_at = now
                return response

        recipe_result = await db.execute(_active_recipe_query(order.alert_group_name))
        recipe = recipe_result.unique().scalars().first()

        extra_policy_steps: list[RecipeIngredient] = []
        if recipe and not scheduled_order:
            local_routes = get_recipe_local_routes(recipe)
            has_local_policy = policy_has_enabled_routes(local_routes)
            if not has_local_policy and not global_policy_is_configured:
                recipe = None
            elif run_phase in GLOBAL_COMMS_INHERIT_PHASES and not has_local_policy:
                global_policy_recipe = await get_global_policy_recipe_for_dispatch(db)
                extra_policy_steps = (
                    list(global_policy_recipe.recipe_ingredients) if global_policy_recipe else []
                )

        if not recipe:
            # Attempt to fall back to the fallback recipe
            settings = _get_settings()
            fallback_recipe_name = str(settings.catch_all_recipe_name or "").strip()
            if fallback_recipe_name:
                fallback_result = await db.execute(
                    select(Recipe)
                    .options(
                        selectinload(Recipe.recipe_ingredients).selectinload(
                            RecipeIngredient.ingredient
                        )
                    )
                    .where(Recipe.name == fallback_recipe_name, Recipe.enabled.is_(True))
                )
                fallback_recipe = fallback_result.unique().scalars().first()
                if fallback_recipe:
                    # Ensure the fallback recipe is in sync with current global routes
                    global_routes = await _get_global_policy_routes_for_fallback(db)
                    await sync_fallback_policy_recipe(db, routes=global_routes)
                    # Reload the fallback recipe with updated ingredients
                    fallback_result = await db.execute(
                        select(Recipe)
                        .options(
                            selectinload(Recipe.recipe_ingredients).selectinload(
                                RecipeIngredient.ingredient
                            )
                        )
                        .where(Recipe.name == fallback_recipe_name, Recipe.enabled.is_(True))
                    )
                    fallback_recipe = fallback_result.unique().scalars().first()
                    if fallback_recipe:
                        recipe = fallback_recipe
            if not recipe:
                order.processing_status = "resolving"
                order.remediation_outcome = "none"
                order.clear_timeout_sec = None
                order.clear_deadline_at = None
                order.clear_timed_out_at = None
                order.auto_close_eligible = False
                order.is_active = True
                order.updated_at = now
                response = OrderDispatchResponse(
                    status="skipped",
                    order_id=order.id,
                    reason=f"No recipe for {order.alert_group_name}",
                )
        else:
            inactive_ingredients = _inactive_ingredients(recipe)
            if inactive_ingredients:
                inactive_labels = ", ".join(
                    f"{ingredient_id}:{task_name}"
                    for ingredient_id, task_name in inactive_ingredients
                )
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Recipe references inactive ingredients and cannot execute until updated: "
                        f"{inactive_labels}"
                    ),
                )
            if run_phase == "firing":
                order.processing_status = "processing"
                if _recipe_has_phase_remediation(recipe, phase="firing"):
                    order.remediation_outcome = "pending"
                    order.clear_timeout_sec = recipe.clear_timeout_sec
                    order.clear_deadline_at = None
                    order.clear_timed_out_at = None
                    order.auto_close_eligible = False
                else:
                    order.remediation_outcome = "none"
                    order.clear_timeout_sec = None
                    order.auto_close_eligible = False
                    order.clear_deadline_at = None
                    order.clear_timed_out_at = None
                order.updated_at = now

            dish_result = await db.execute(_active_phase_dish_query(order.id, run_phase))
            dish = dish_result.scalars().first()
            if dish is None:
                expected_run_secs = expected_run_secs_from_recipe_snapshot(
                    recipe=recipe,
                    phase=run_phase,
                    extra_recipe_ingredients=extra_policy_steps,
                )
                dish = Dish(
                    req_id=order.req_id,
                    order_id=order.id,
                    recipe_id=recipe.id,
                    run_phase=run_phase,
                    processing_status="new",
                    expected_run_secs=expected_run_secs,
                )
                db.add(dish)
                await db.flush()

            existing_result = await db.execute(_dish_ingredients_for_seed_query(dish.id))
            existing_by_recipe_ingredient_id = {
                row.recipe_ingredient_id: row
                for row in existing_result.scalars().all()
                if row.recipe_ingredient_id is not None
            }

            seeded_rows = seed_dish_ingredients_for_phase(
                dish_id=dish.id,
                recipe=recipe,
                phase=run_phase,
                order=order,
                existing_by_recipe_ingredient_id=existing_by_recipe_ingredient_id,
                extra_recipe_ingredients=extra_policy_steps,
            )
            for row in seeded_rows:
                db.add(row)
                if row.recipe_ingredient_id is not None:
                    existing_by_recipe_ingredient_id[row.recipe_ingredient_id] = row

            response = OrderDispatchResponse(
                status="dispatched",
                order_id=order.id,
                dish_id=dish.id,
                run_phase=run_phase,
                recipe_id=recipe.id,
                recipe_name=recipe.name,
            )

    if response is None:
        raise HTTPException(status_code=500, detail="Dispatch failed")
    return response


@router.get("/orders/{order_id}/timeline", response_model=IncidentTimelineResponse)
async def get_order_timeline(
    request: Request,
    order_id: int,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_reader),
) -> IncidentTimelineResponse:
    req_id = request.state.req_id
    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalars().first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    events: list[IncidentTimelineEvent] = [
        IncidentTimelineEvent(
            timestamp=order.created_at,
            event_type="order",
            status=order.processing_status,
            title=f"Order {order.id} received",
            details={
                "alert_group_name": order.alert_group_name,
                "alert_status": order.alert_status,
                "counter": order.counter,
            },
            correlation_ids={
                "req_id": order.req_id,
            },
        )
    ]

    dishes_result = await db.execute(
        select(Dish).where(Dish.order_id == order.id).order_by(Dish.created_at.asc())
    )
    dishes = dishes_result.scalars().all()
    for dish in dishes:
        events.append(
            IncidentTimelineEvent(
                timestamp=dish.started_at or dish.created_at,
                event_type="dish",
                status=dish.processing_status,
                title=f"Dish {dish.id} {dish.processing_status}",
                details={
                    "status": dish.dish_exec_status,
                },
                correlation_ids={
                    "dish_id": str(dish.id),
                },
            )
        )
        ingredient_result = await db.execute(
            select(DishIngredient)
            .where(DishIngredient.dish_id == dish.id, DishIngredient.deleted.is_(False))
            .order_by(DishIngredient.created_at.asc())
        )
        for ingredient in ingredient_result.scalars().all():
            events.append(
                IncidentTimelineEvent(
                    timestamp=ingredient.service_exec_start_time or ingredient.created_at,
                    event_type="task",
                    status=ingredient.service_exec_status or "unknown",
                    title=f"Task {ingredient.task_key or 'unknown'}",
                    details={},
                    correlation_ids={
                        "dish_ingredient_id": str(ingredient.id),
                    },
                )
            )

    events.sort(
        key=lambda item: item.timestamp or datetime(1970, 1, 1, tzinfo=timezone.utc),
        reverse=False,
    )
    logger.debug(
        "Built incident timeline",
        extra={"req_id": req_id, "order_id": order_id, "event_count": len(events)},
    )
    return IncidentTimelineResponse(order=_serialize_timeline_order(order), events=events)
