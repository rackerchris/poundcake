"""Run operator actions through the normal order execution workflow."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.api.cook import _advance_dish
from api.api.dishes import (
    claim_dish_ingredient_for_execution,
    reconcile_executed_dish_ingredient,
)
from api.api.expediter import execute_service_execution
from api.api.orders import dispatch_order
from api.core.time import utc_now_db
from api.models.models import DishIngredient, Order
from api.plugins.state import TERMINAL_EXECUTION_STATUSES
from api.schemas.schemas import DishIngredientUpsert
from api.services.plugin_orchestrator import ExecutionOrchestrator
from api.types import JSONValue, MANUAL_ORDER_TYPE


@dataclass(frozen=True)
class OperatorActionOrderResult:
    order_id: int
    dish_id: int
    dish_ingredient_id: int
    status: str
    outcome: dict[str, JSONValue]
    error: str | None = None


async def run_operator_action_order(
    *,
    db: AsyncSession,
    orchestrator: ExecutionOrchestrator,
    req_id: str,
    recipe_name: str,
    service_type: str,
    service_exec: str,
    task_key_template: str,
    service_payload: dict[str, JSONValue],
) -> OperatorActionOrderResult:
    """Create and synchronously advance a manual order through Cook and Expediter."""

    normalized_req_id = (req_id or "operator-action")[:100]
    normalized_service_type = service_type.strip().lower()
    normalized_service_exec = service_exec.strip()
    normalized_task_key = task_key_template.strip()
    order = Order(
        req_id=normalized_req_id,
        fingerprint=f"operator-action:{recipe_name}:{normalized_req_id}"[:255],
        alert_status="firing",
        processing_status="new",
        is_active=True,
        remediation_outcome="pending",
        alert_group_name=recipe_name,
        severity="operator",
        instance=normalized_service_type,
        correlation_key=None,
        labels={
            "alertname": recipe_name,
            "service_type": normalized_service_type,
            "service_exec": normalized_service_exec,
        },
        annotations={},
        raw_data={
            "order_type": MANUAL_ORDER_TYPE,
            "operator_action": True,
            "recipe_name": recipe_name,
            "service_type": normalized_service_type,
            "service_exec": normalized_service_exec,
            "task_key_template": normalized_task_key,
            "service_payload": dict(service_payload),
        },
        starts_at=utc_now_db(),
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)

    order_request = _service_request(normalized_req_id)
    dispatch_response = await dispatch_order(request=order_request, order_id=order.id, db=db)
    if dispatch_response.dish_id is None:
        raise HTTPException(
            status_code=409,
            detail=dispatch_response.reason or "Operator action order did not create a dish",
        )

    await _advance_dish(
        dish_id=dispatch_response.dish_id,
        req_id=normalized_req_id,
        db=db,
        orchestrator=orchestrator,
    )
    row = await _operator_action_runtime_row(
        db=db,
        dish_id=dispatch_response.dish_id,
        service_type=normalized_service_type,
        service_exec=normalized_service_exec,
        task_key=normalized_task_key,
    )

    runner_request = _service_request(normalized_req_id, service_type="expediter-runner")
    await claim_dish_ingredient_for_execution(
        request=runner_request,
        dish_ingredient_id=row.id,
        db=db,
        _context=object(),
    )
    envelope = await execute_service_execution(
        row.id,
        request=runner_request,
        db=db,
        orchestrator=orchestrator,
        _context=object(),
    )
    reconcile_status = (
        envelope.status if envelope.status in TERMINAL_EXECUTION_STATUSES else "failed"
    )
    outcome = (
        envelope.service_exec_actual_outcome
        if isinstance(envelope.service_exec_actual_outcome, dict)
        else {}
    )
    await reconcile_executed_dish_ingredient(
        request=runner_request,
        dish_ingredient_id=row.id,
        payload=DishIngredientUpsert(
            service_exec_id=envelope.service_exec_id,
            service_exec_status=reconcile_status,
            service_exec_actual_outcome=outcome,
            service_exec_error=envelope.service_exec_error,
            service_exec_completed_time=utc_now_db(),
        ),
        db=db,
        _context=object(),
    )
    await _advance_dish(
        dish_id=dispatch_response.dish_id,
        req_id=normalized_req_id,
        db=db,
        orchestrator=orchestrator,
    )
    await db.refresh(row)
    return OperatorActionOrderResult(
        order_id=order.id,
        dish_id=dispatch_response.dish_id,
        dish_ingredient_id=row.id,
        status=str(row.service_exec_status),
        outcome=(
            row.service_exec_actual_outcome
            if isinstance(row.service_exec_actual_outcome, dict)
            else {}
        ),
        error=row.service_exec_error,
    )


async def _operator_action_runtime_row(
    *,
    db: AsyncSession,
    dish_id: int,
    service_type: str,
    service_exec: str,
    task_key: str,
) -> DishIngredient:
    result = await db.execute(
        select(DishIngredient)
        .where(
            DishIngredient.dish_id == dish_id,
            DishIngredient.deleted.is_(False),
            DishIngredient.service_type == service_type,
            DishIngredient.service_exec == service_exec,
            or_(
                DishIngredient.task_key == task_key,
                DishIngredient.task_key.like(f"%_{task_key.replace('.', '_')}"),
            ),
        )
        .order_by(DishIngredient.id.asc())
    )
    row = result.scalars().first()
    if row is None:
        raise HTTPException(status_code=500, detail="Operator action runtime row was not seeded")
    return row


def _service_request(req_id: str, *, service_type: str = "operator-action") -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(
            req_id=req_id,
            auth_context=SimpleNamespace(service_type=service_type),
        )
    )
