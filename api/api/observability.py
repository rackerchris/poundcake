#  ___                        _  ____      _
# |  _ \ ___  _   _ _ __   __| |/ ___|__ _| | _____
# | |_) / _ \| | | | '_ \ / _` | |   / _` | |/ / _ \
# |  __/ (_) | |_| | | | | (_| | |__| (_| |   <  __/
# |_|   \___/ \__,_|_| |_|\__,_|\____\__,_|_|\_\___|
#
"""Observability and communication activity endpoints for the mission-control UI."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from api.api.auth import require_reader
from api.core.database import get_db
from api.core.config import get_settings
from api.core.rate_limit import limiter
from api.models.models import (
    AlertSuppression,
    Dish,
    DishIngredient,
    Ingredient,
    Order,
    RecipeIngredient,
)
from api.schemas.query_params import (
    CommunicationActivityQueryParams,
    ObservabilityActivityQueryParams,
    validate_query_params,
)
from api.schemas.schemas import (
    CommunicationActivityRecord,
    CommunicationActivityStatusRecord,
    ObservabilityActivityRecord,
    ObservabilityActivityStatusRecord,
)
from api.services.communications import (
    normalize_destination_target,
    normalize_destination_type,
)
from api.services.order_types import order_matches_filters
from api.services.suppression_service import normalize_utc_datetime, suppression_status
from api.types import OrderScope, OrderType

router = APIRouter()


def _epoch() -> datetime:
    return datetime(1970, 1, 1, tzinfo=timezone.utc)


def _destination_label(*, service_type: str | None, destination_target: str | None) -> str:
    channel = normalize_destination_type(service_type)
    destination = normalize_destination_target(destination_target)
    if destination:
        return f"{channel}:{destination}"
    return channel


def _effective_order_scope(
    *,
    order_scope: OrderScope,
    exclude_plugin_health_checks: bool,
) -> OrderScope:
    return "operator" if exclude_plugin_health_checks else order_scope


def _order_matches_activity_scope(
    order: Order,
    *,
    order_scope: OrderScope,
    order_type: OrderType | None,
) -> bool:
    return order_matches_filters(
        raw_data=order.raw_data,
        labels=order.labels,
        order_scope=order_scope,
        order_type=order_type,
    )


async def _load_communication_activity(
    db: AsyncSession,
    *,
    status: str | None = None,
    channel: str | None = None,
    exclude_plugin_health_checks: bool = False,
    order_scope: OrderScope = "all",
    order_type: OrderType | None = None,
    limit: int = 100,
) -> list[CommunicationActivityRecord]:
    normalized_channel = normalize_destination_type(channel) if channel else None
    effective_scope = _effective_order_scope(
        order_scope=order_scope,
        exclude_plugin_health_checks=exclude_plugin_health_checks,
    )
    rows: list[CommunicationActivityRecord] = []
    # This feed should show communication-path work only. Keep comms rows even when
    # the parent order is a plugin health check so down-plugin ticket routes remain visible.

    runtime_query = (
        select(DishIngredient, Dish, Order)
        .join(Dish, Dish.id == DishIngredient.dish_id)
        .join(Order, Order.id == Dish.order_id)
        .join(RecipeIngredient, RecipeIngredient.id == DishIngredient.recipe_ingredient_id)
        .join(Ingredient, Ingredient.id == RecipeIngredient.ingredient_id)
        .where(DishIngredient.deleted.is_(False))
        .where(Ingredient.ingredient_purpose == "comms")
        .order_by(DishIngredient.updated_at.desc())
        .limit(limit)
    )
    runtime_result = await db.execute(runtime_query)
    for dish_ingredient, _dish, order in runtime_result.all():
        if not _order_matches_activity_scope(
            order,
            order_scope=effective_scope,
            order_type=order_type,
        ):
            continue
        current_channel = normalize_destination_type(dish_ingredient.service_type)
        if normalized_channel and current_channel != normalized_channel:
            continue
        if status and status not in {
            dish_ingredient.service_exec_status or "",
            order.processing_status or "",
        }:
            continue
        outcome = dish_ingredient.service_exec_actual_outcome or {}
        ticket_id = outcome.get("ticket_id") if isinstance(outcome, dict) else None
        provider_reference_id = (
            outcome.get("provider_reference_id") if isinstance(outcome, dict) else None
        )
        operation_id = dish_ingredient.service_exec_id
        rows.append(
            CommunicationActivityRecord(
                communication_id=str(dish_ingredient.id),
                reference_type="incident",
                reference_id=str(order.id),
                reference_name=order.alert_group_name,
                channel=current_channel,
                destination=_destination_label(
                    service_type=dish_ingredient.service_type,
                    destination_target=dish_ingredient.destination_target,
                ),
                ticket_id=str(ticket_id) if ticket_id else None,
                provider_reference_id=str(provider_reference_id) if provider_reference_id else None,
                operation_id=operation_id,
                lifecycle_state=dish_ingredient.service_exec_status,
                remote_state=None,
                last_error=dish_ingredient.service_exec_error,
                writable=None,
                reopenable=None,
                updated_at=dish_ingredient.updated_at,
            )
        )

    rows.sort(key=lambda item: item.updated_at or _epoch(), reverse=True)
    return rows


@limiter.limit(get_settings().rate_limit_default)
@router.get("/communications/activity", response_model=list[CommunicationActivityRecord])
async def get_communication_activity(
    request: Request,
    params: CommunicationActivityQueryParams = Depends(
        validate_query_params(CommunicationActivityQueryParams)
    ),
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_reader),
) -> list[CommunicationActivityRecord]:
    _ = request.state.req_id
    rows = await _load_communication_activity(
        db,
        status=params.status,
        channel=params.channel,
        order_scope="all",
        limit=params.limit + params.offset,
    )
    return rows[params.offset : params.offset + params.limit]


@limiter.limit(get_settings().rate_limit_default)
@router.get(
    "/communications/activity/status",
    response_model=list[CommunicationActivityStatusRecord],
)
async def get_communication_activity_status(
    request: Request,
    params: CommunicationActivityQueryParams = Depends(
        validate_query_params(CommunicationActivityQueryParams)
    ),
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_reader),
) -> list[CommunicationActivityStatusRecord]:
    rows = await _load_communication_activity(
        db,
        status=params.status,
        channel=params.channel,
        order_scope="all",
        limit=params.limit + params.offset,
    )
    return [
        CommunicationActivityStatusRecord(
            communication_id=row.communication_id,
            reference_type=row.reference_type,
            reference_id=row.reference_id,
            reference_name=row.reference_name,
            channel=row.channel,
            destination=row.destination,
            lifecycle_state=row.lifecycle_state,
            remote_state=row.remote_state,
            updated_at=row.updated_at,
        )
        for row in rows[params.offset : params.offset + params.limit]
    ]


@limiter.limit(get_settings().rate_limit_default)
@router.get("/observability/activity", response_model=list[ObservabilityActivityRecord])
async def get_observability_activity(
    request: Request,
    params: ObservabilityActivityQueryParams = Depends(
        validate_query_params(ObservabilityActivityQueryParams)
    ),
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_reader),
) -> list[ObservabilityActivityRecord]:
    _ = request.state.req_id
    records: list[ObservabilityActivityRecord] = []
    fetch_count = params.limit + params.offset
    effective_scope = _effective_order_scope(
        order_scope=params.order_scope,
        exclude_plugin_health_checks=params.exclude_plugin_health_checks,
    )

    order_query = select(Order).order_by(Order.updated_at.desc()).limit(1000)
    order_result = await db.execute(order_query)
    for order in order_result.scalars().all():
        if not _order_matches_activity_scope(
            order,
            order_scope=effective_scope,
            order_type=params.order_type,
        ):
            continue
        instance = f" on {order.instance}" if order.instance else ""
        severity = order.severity or "unknown severity"
        records.append(
            ObservabilityActivityRecord(
                type="order",
                status=order.processing_status,
                title=order.alert_group_name,
                summary=f"{order.alert_status} | {severity}{instance}",
                timestamp=normalize_utc_datetime(order.updated_at),
                target_kind="order",
                target_id=str(order.id),
                link_hint=f"/orders/{order.id}",
                metadata={
                    "severity": order.severity,
                    "instance": order.instance,
                    "counter": order.counter,
                },
            )
        )

    dish_query = (
        select(Dish)
        .options(joinedload(Dish.recipe), joinedload(Dish.order))
        .order_by(Dish.updated_at.desc())
        .limit(1000)
    )
    dish_result = await db.execute(dish_query)
    for dish in dish_result.unique().scalars().all():
        order = getattr(dish, "order", None)
        if order is not None and not _order_matches_activity_scope(
            order,
            order_scope=effective_scope,
            order_type=params.order_type,
        ):
            continue
        recipe_name = dish.recipe.name if dish.recipe else f"Recipe #{dish.recipe_id}"
        target_link = f"/execution-activity?dish={dish.id}"
        if dish.order_id:
            target_link = f"/orders/{dish.order_id}?dish={dish.id}"
        records.append(
            ObservabilityActivityRecord(
                type="dish",
                status=dish.processing_status,
                title=f"{recipe_name} run",
                summary=f"{dish.run_phase} phase | {dish.dish_exec_status or 'pending'}",
                timestamp=normalize_utc_datetime(dish.updated_at),
                target_kind="dish",
                target_id=str(dish.id),
                link_hint=target_link,
                metadata={
                    "order_id": dish.order_id,
                    "run_phase": dish.run_phase,
                    "dish_exec_status": dish.dish_exec_status,
                },
            )
        )

    communication_rows = await _load_communication_activity(
        db,
        exclude_plugin_health_checks=params.exclude_plugin_health_checks,
        order_scope=params.order_scope,
        order_type=params.order_type,
        limit=fetch_count,
    )
    for item in communication_rows:
        link_hint = "/communication-routes"
        if item.reference_type == "incident":
            link_hint = f"/orders/{item.reference_id}?communication={item.communication_id}"
        elif item.reference_type == "suppression":
            link_hint = f"/suppressions?suppression={item.reference_id}"
        reference_name = item.reference_name or item.reference_id
        records.append(
            ObservabilityActivityRecord(
                type="communication",
                status=item.remote_state or item.lifecycle_state or "unknown",
                title=f"{item.channel} route for {reference_name}",
                summary=item.destination or item.ticket_id or item.provider_reference_id,
                timestamp=normalize_utc_datetime(item.updated_at),
                target_kind="communication",
                target_id=item.communication_id,
                link_hint=link_hint,
                metadata={
                    "reference_type": item.reference_type,
                    "reference_id": item.reference_id,
                    "ticket_id": item.ticket_id,
                    "provider_reference_id": item.provider_reference_id,
                    "last_error": item.last_error,
                },
            )
        )

    suppression_result = await db.execute(
        select(AlertSuppression).order_by(AlertSuppression.updated_at.desc()).limit(fetch_count)
    )
    for suppression in suppression_result.scalars().all():
        status = suppression_status(suppression)
        ends_at = normalize_utc_datetime(suppression.ends_at)
        records.append(
            ObservabilityActivityRecord(
                type="suppression",
                status=status,
                title=suppression.name,
                summary=suppression.reason
                or f"{suppression.scope} suppression window until {ends_at.isoformat() if ends_at else 'unknown'}",
                timestamp=normalize_utc_datetime(suppression.updated_at),
                target_kind="suppression",
                target_id=str(suppression.id),
                link_hint=f"/suppressions?suppression={suppression.id}",
                metadata={
                    "scope": suppression.scope,
                    "summary_ticket_enabled": suppression.summary_ticket_enabled,
                },
            )
        )

    if params.type:
        requested_type = params.type.strip().lower()
        records = [item for item in records if item.type.lower() == requested_type]

    records.sort(key=lambda item: item.timestamp or _epoch(), reverse=True)
    return records[params.offset : params.offset + params.limit]


@limiter.limit(get_settings().rate_limit_default)
@router.get(
    "/observability/activity/status",
    response_model=list[ObservabilityActivityStatusRecord],
)
async def get_observability_activity_status(
    request: Request,
    params: ObservabilityActivityQueryParams = Depends(
        validate_query_params(ObservabilityActivityQueryParams)
    ),
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_reader),
) -> list[ObservabilityActivityStatusRecord]:
    _ = request.state.req_id
    records = await get_observability_activity(params=params, db=db)
    return [
        ObservabilityActivityStatusRecord(
            type=row.type,
            status=row.status,
            title=row.title,
            summary=row.summary,
            timestamp=row.timestamp,
            target_kind=row.target_kind,
            target_id=row.target_id,
            link_hint=row.link_hint,
        )
        for row in records
    ]
