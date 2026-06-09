#  ___                        _  ____      _
# |  _ \ ___  _   _ _ __   __| |/ ___|__ _| | _____
# | |_) / _ \| | | | '_ \ / _` | |   / _` | |/ / _ \
# |  __/ (_) | |_| | | | | (_| | |__| (_| |   <  __/
# |_|   \___/ \__,_|_| |_|\__,_|\____\__,_|_|\_\___|
#
"""API routes for suppression windows and suppression observability."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.api.auth import require_operator, require_reader
from api.core.database import get_db
from api.core.logging import get_logger
from api.core.rate_limit import limiter
from api.core.config import get_settings
from api.models.models import (
    AlertSuppression,
    AlertSuppressionMatcher,
    Dish,
    Order,
    SuppressionSummary,
)
from api.schemas.query_params import (
    SuppressedActivityQueryParams,
    SuppressionQueryParams,
    validate_query_params,
)
from api.schemas.schemas import (
    ObservabilityFailuresSummary,
    ObservabilityHealthSummary,
    ObservabilityOverviewResponse,
    ObservabilityQueueSummary,
    ObservabilitySuppressionsSummary,
    ObservabilityTopError,
    SuppressedActivityResponse,
    SuppressionCreate,
    SuppressionDetailResponse,
    SuppressionMatcher,
    SuppressionResponse,
    SuppressionStatusResponse,
    SuppressionSummaryResponse,
    SuppressionStatsResponse,
    SuppressionUpdate,
)
from api.services.suppression_service import (
    compute_suppression_stats,
    count_active_suppressions,
    get_suppression,
    list_suppression_activity,
    list_suppressions,
    normalize_utc_datetime,
    suppression_status,
)
from api.services.alertmanager_suppressions import (
    SuppressionLifecycleError,
    create_alertmanager_suppression,
    expire_alertmanager_suppression,
    update_alertmanager_suppression,
)
from api.services.plugin_orchestrator import ExecutionOrchestrator
from api.services.plugin_orchestrator import get_execution_orchestrator
from api.types import SuppressionMatcherOperator, SuppressionScope, SuppressionStatus
from api.api.dishes import _sanitize_status_string

router = APIRouter()
logger = get_logger(__name__)


def _to_matcher_response(matchers: list[AlertSuppressionMatcher]) -> list[SuppressionMatcher]:
    return [
        SuppressionMatcher(
            label_key=m.label_key,
            operator=cast(SuppressionMatcherOperator, m.operator),
            value=m.value,
        )
        for m in matchers
    ]


def _to_suppression_response(item: AlertSuppression) -> SuppressionResponse:
    status = cast(SuppressionStatus, suppression_status(item))
    starts_at = normalize_utc_datetime(item.starts_at)
    ends_at = normalize_utc_datetime(item.ends_at)
    created_at = normalize_utc_datetime(item.created_at)
    updated_at = normalize_utc_datetime(item.updated_at)
    if starts_at is None or ends_at is None or created_at is None or updated_at is None:
        raise ValueError("Suppression timestamps must be present")
    return SuppressionResponse(
        id=item.id,
        name=item.name,
        reason=item.reason,
        scope=cast(SuppressionScope, item.scope),
        status=status,
        enabled=item.enabled,
        starts_at=starts_at,
        ends_at=ends_at,
        canceled_at=normalize_utc_datetime(item.canceled_at),
        created_by=item.created_by,
        summary_ticket_enabled=item.summary_ticket_enabled,
        source=item.source,
        source_service_type=item.source_service_type,
        source_ref=item.source_ref,
        source_payload=item.source_payload,
        last_synced_at=normalize_utc_datetime(item.last_synced_at),
        created_at=created_at,
        updated_at=updated_at,
        matchers=_to_matcher_response(item.matchers),
    )


def _to_suppression_status_response(item: AlertSuppression) -> SuppressionStatusResponse:
    status = cast(SuppressionStatus, suppression_status(item))
    starts_at = normalize_utc_datetime(item.starts_at)
    ends_at = normalize_utc_datetime(item.ends_at)
    created_at = normalize_utc_datetime(item.created_at)
    updated_at = normalize_utc_datetime(item.updated_at)
    if starts_at is None or ends_at is None or created_at is None or updated_at is None:
        raise ValueError("Suppression timestamps must be present")
    return SuppressionStatusResponse(
        id=item.id,
        name=item.name,
        reason=item.reason,
        scope=cast(SuppressionScope, item.scope),
        status=status,
        enabled=item.enabled,
        starts_at=starts_at,
        ends_at=ends_at,
        canceled_at=normalize_utc_datetime(item.canceled_at),
        source=item.source,
        source_service_type=item.source_service_type,
        source_ref=item.source_ref,
        last_synced_at=normalize_utc_datetime(item.last_synced_at),
        created_at=created_at,
        updated_at=updated_at,
    )


@router.get("/suppressions", response_model=list[SuppressionResponse])
@limiter.limit(get_settings().rate_limit_default)
async def get_suppressions(
    request: Request,
    params: SuppressionQueryParams = Depends(validate_query_params(SuppressionQueryParams)),
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_reader),
) -> list[SuppressionResponse]:
    req_id = request.state.req_id
    rows = await list_suppressions(
        db=db,
        status=params.status,
        enabled=params.enabled,
        scope=params.scope,
        limit=params.limit,
        offset=params.offset,
    )
    logger.debug("Fetched suppressions", extra={"req_id": req_id, "count": len(rows)})
    return [_to_suppression_response(row) for row in rows]


@router.get("/suppressions/status", response_model=list[SuppressionStatusResponse])
@limiter.limit(get_settings().rate_limit_default)
async def get_suppression_statuses(
    request: Request,
    params: SuppressionQueryParams = Depends(validate_query_params(SuppressionQueryParams)),
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_reader),
) -> list[SuppressionStatusResponse]:
    """List reader-safe suppression status rows without matcher or source payload data."""
    req_id = request.state.req_id
    rows = await list_suppressions(
        db=db,
        status=params.status,
        enabled=params.enabled,
        scope=params.scope,
        limit=params.limit,
        offset=params.offset,
    )
    logger.debug("Fetched suppression statuses", extra={"req_id": req_id, "count": len(rows)})
    return [_to_suppression_status_response(row) for row in rows]


@router.post("/suppressions", response_model=SuppressionResponse, status_code=201)
async def create_suppression(
    request: Request,
    payload: SuppressionCreate,
    db: AsyncSession = Depends(get_db),
    orchestrator: ExecutionOrchestrator = Depends(get_execution_orchestrator),
    _context: object = Depends(require_operator),
) -> SuppressionResponse:
    req_id = request.state.req_id
    if payload.ends_at <= payload.starts_at:
        raise HTTPException(status_code=400, detail="ends_at must be greater than starts_at")
    if not payload.matchers:
        raise HTTPException(status_code=400, detail="matchers are required")

    try:
        refreshed = await create_alertmanager_suppression(
            db=db,
            orchestrator=orchestrator,
            req_id=req_id,
            payload=payload,
        )
    except SuppressionLifecycleError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    logger.info(
        "Created suppression",
        extra={
            "req_id": req_id,
            "suppression_id": refreshed.id,
            "suppression_name": refreshed.name,
        },
    )
    return _to_suppression_response(refreshed)


@limiter.limit(get_settings().rate_limit_default)
@router.get("/suppressions/{suppression_id}", response_model=SuppressionDetailResponse)
async def get_suppression_by_id(
    request: Request,
    suppression_id: int,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_reader),
) -> SuppressionDetailResponse:
    req_id = request.state.req_id
    suppression = await get_suppression(db, suppression_id)
    if not suppression:
        raise HTTPException(status_code=404, detail="Suppression not found")

    stats = await compute_suppression_stats(db, suppression.id)
    response = _to_suppression_response(suppression)
    logger.debug(
        "Fetched suppression detail", extra={"req_id": req_id, "suppression_id": suppression_id}
    )
    return SuppressionDetailResponse(
        **response.model_dump(),
        counters=SuppressionStatsResponse(
            suppression_id=suppression.id,
            total_suppressed=stats["total_suppressed"],
            by_alertname=stats["by_alertname"],
            by_severity=stats["by_severity"],
            first_seen_at=stats["first_seen_at"],
            last_seen_at=stats["last_seen_at"],
        ),
        summary=(
            SuppressionSummaryResponse.model_validate(suppression.summary)
            if suppression.summary
            else None
        ),
    )


@router.patch("/suppressions/{suppression_id}", response_model=SuppressionResponse)
async def patch_suppression(
    request: Request,
    suppression_id: int,
    payload: SuppressionUpdate,
    db: AsyncSession = Depends(get_db),
    orchestrator: ExecutionOrchestrator = Depends(get_execution_orchestrator),
    _context: object = Depends(require_operator),
) -> SuppressionResponse:
    req_id = request.state.req_id
    suppression = await get_suppression(db, suppression_id)
    if not suppression:
        raise HTTPException(status_code=404, detail="Suppression not found")

    changes = payload.model_dump(exclude_unset=True)
    if "starts_at" in changes:
        updated_starts_at = normalize_utc_datetime(changes["starts_at"])
        ends_at = normalize_utc_datetime(changes.get("ends_at") or suppression.ends_at)
        if updated_starts_at is not None and ends_at is not None and ends_at <= updated_starts_at:
            raise HTTPException(status_code=400, detail="ends_at must be greater than starts_at")
    if "ends_at" in changes:
        updated_ends_at = normalize_utc_datetime(changes["ends_at"])
        starts_at = normalize_utc_datetime(changes.get("starts_at") or suppression.starts_at)
        if updated_ends_at is not None and starts_at is not None and updated_ends_at <= starts_at:
            raise HTTPException(status_code=400, detail="ends_at must be greater than starts_at")
    if "matchers" in changes and not payload.matchers:
        raise HTTPException(status_code=400, detail="matchers are required")

    try:
        refreshed = await update_alertmanager_suppression(
            db=db,
            orchestrator=orchestrator,
            req_id=req_id,
            suppression=suppression,
            payload=payload,
        )
    except SuppressionLifecycleError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    logger.info("Updated suppression", extra={"req_id": req_id, "suppression_id": suppression_id})
    return _to_suppression_response(refreshed)


@router.post("/suppressions/{suppression_id}/cancel", response_model=SuppressionResponse)
async def cancel_suppression(
    request: Request,
    suppression_id: int,
    db: AsyncSession = Depends(get_db),
    orchestrator: ExecutionOrchestrator = Depends(get_execution_orchestrator),
    _context: object = Depends(require_operator),
) -> SuppressionResponse:
    req_id = request.state.req_id
    suppression = await get_suppression(db, suppression_id)
    if not suppression:
        raise HTTPException(status_code=404, detail="Suppression not found")
    try:
        refreshed = await expire_alertmanager_suppression(
            db=db,
            orchestrator=orchestrator,
            req_id=req_id,
            suppression=suppression,
        )
    except SuppressionLifecycleError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    logger.info("Canceled suppression", extra={"req_id": req_id, "suppression_id": suppression_id})
    return _to_suppression_response(refreshed)


@limiter.limit(get_settings().rate_limit_default)
@router.get("/suppressions/{suppression_id}/stats", response_model=SuppressionStatsResponse)
async def get_suppression_stats(
    request: Request,
    suppression_id: int,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_reader),
) -> SuppressionStatsResponse:
    suppression = await get_suppression(db, suppression_id)
    if not suppression:
        raise HTTPException(status_code=404, detail="Suppression not found")
    stats = await compute_suppression_stats(db, suppression_id)
    return SuppressionStatsResponse(
        suppression_id=suppression_id,
        total_suppressed=stats["total_suppressed"],
        by_alertname=stats["by_alertname"],
        by_severity=stats["by_severity"],
        first_seen_at=stats["first_seen_at"],
        last_seen_at=stats["last_seen_at"],
    )


@limiter.limit(get_settings().rate_limit_default)
@router.get("/activity/suppressed", response_model=list[SuppressedActivityResponse])
async def get_suppressed_activity(
    request: Request,
    params: SuppressedActivityQueryParams = Depends(
        validate_query_params(SuppressedActivityQueryParams)
    ),
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_reader),
) -> list[SuppressedActivityResponse]:
    rows = await list_suppression_activity(
        db=db,
        suppression_id=params.suppression_id,
        limit=params.limit,
        offset=params.offset,
    )
    return [SuppressedActivityResponse.model_validate(row) for row in rows]


@limiter.limit(get_settings().rate_limit_default)
@router.get("/observability/overview", response_model=ObservabilityOverviewResponse)
async def get_observability_overview(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_reader),
) -> ObservabilityOverviewResponse:
    active_suppressions = await count_active_suppressions(db)

    order_new = await db.scalar(
        select(func.count(Order.id)).where(Order.processing_status == "new")
    )
    order_processing = await db.scalar(
        select(func.count(Order.id)).where(Order.processing_status == "processing")
    )
    failed_orders = await db.scalar(
        select(func.count(Order.id)).where(Order.processing_status == "failed")
    )
    failed_dishes = await db.scalar(
        select(func.count(Dish.id)).where(Dish.processing_status == "failed")
    )

    top_errors_result = await db.execute(
        select(Dish.error_message, func.count(Dish.id))
        .where(Dish.error_message.is_not(None), Dish.error_message != "")
        .group_by(Dish.error_message)
        .order_by(func.count(Dish.id).desc())
        .limit(5)
    )
    top_errors = [
        {"error": _sanitize_status_string(str(error)), "count": int(count)}
        for error, count in top_errors_result.all()
        if error is not None
    ]

    retrying_operations = await db.scalar(
        select(func.count(SuppressionSummary.id)).where(
            SuppressionSummary.state.in_(["pending", "created"])
        )
    )
    dead_letter_count = await db.scalar(
        select(func.count(SuppressionSummary.id)).where(SuppressionSummary.state == "failed")
    )
    runbook_hints: list[str] = []
    if int(failed_dishes or 0) > 0:
        runbook_hints.append(
            "Failed dishes detected. Check service execution errors in Incident Timeline."
        )
    if int(order_new or 0) > 20:
        runbook_hints.append(
            "Order queue backlog is high. Check prep-chef throughput and API latency."
        )

    return ObservabilityOverviewResponse(
        health=ObservabilityHealthSummary(status="ok"),
        queue=ObservabilityQueueSummary(
            orders_new=int(order_new or 0),
            orders_processing=int(order_processing or 0),
        ),
        failures=ObservabilityFailuresSummary(
            orders_failed=int(failed_orders or 0),
            dishes_failed=int(failed_dishes or 0),
            top_errors=[ObservabilityTopError.model_validate(item) for item in top_errors],
            runbook_hints=runbook_hints,
        ),
        suppressions=ObservabilitySuppressionsSummary(
            active=int(active_suppressions),
            retrying_operations=int(retrying_operations or 0),
            dead_letter=int(dead_letter_count or 0),
        ),
    )
