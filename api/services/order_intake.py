"""Shared order intake boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.time import utc_now_db
from api.models.models import Order, ScheduledTask, ServicePlugin
from api.schemas.schemas import OrderCreate
from api.services.order_types import ensure_raw_data_order_type
from api.types import JSONValue, MANUAL_ORDER_TYPE


@dataclass(frozen=True)
class OperatorActionOrderSubmission:
    order_id: int
    order_req_id: str
    service_type: str
    service_exec: str
    submitted_at: datetime


async def create_manual_order(
    *,
    db: AsyncSession,
    payload: OrderCreate,
) -> Order:
    """Create a manual order through the shared control-plane intake path."""

    if payload.processing_status != "new":
        raise HTTPException(
            status_code=400,
            detail="orders must enter the control plane with processing_status='new'",
        )

    create_data = payload.model_dump()
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
    return result.unique().scalars().first() or order


async def submit_operator_action_order(
    *,
    db: AsyncSession,
    req_id: str,
    recipe_name: str,
    service_type: str,
    service_exec: str,
    task_key_template: str,
    service_payload: dict[str, JSONValue],
) -> OperatorActionOrderSubmission:
    """Submit a provider-mutating operator action through order intake."""

    normalized_req_id = (req_id or "operator-action")[:100]
    normalized_service_type = service_type.strip().lower()
    normalized_service_exec = service_exec.strip()
    normalized_task_key = task_key_template.strip()
    payload = OrderCreate(
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
    order = await create_manual_order(db=db, payload=payload)
    return OperatorActionOrderSubmission(
        order_id=order.id,
        order_req_id=order.req_id,
        service_type=normalized_service_type,
        service_exec=normalized_service_exec,
        submitted_at=order.starts_at,
    )
