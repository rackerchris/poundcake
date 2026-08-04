"""Service-layer helpers for short-lived adapter/bootstrap runtime work."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select, text

from api.core.database import SessionLocal, dispose_async_engines
from api.models.models import Dish, DishIngredient, Order, ServicePlugin
from api.types import JSONObject


async def dispose_adapter_runtime_resources() -> None:
    """Dispose adapter/runtime resources without exposing database internals."""

    await dispose_async_engines()


async def check_database_health() -> JSONObject:
    """Return a minimal database health snapshot for adapter diagnostics."""

    async with SessionLocal() as db:
        try:
            await db.execute(text("SELECT 1"))
            return {"status": "healthy", "message": "Connected"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "unhealthy", "message": str(exc)}


async def get_service_plugin_state(service_type: str) -> JSONObject | None:
    """Return the persisted service plugin state for adapter diagnostics."""

    async with SessionLocal() as db:
        result = await db.execute(
            select(ServicePlugin).where(ServicePlugin.service_type == service_type)
        )
        plugin = result.scalars().first()

    if plugin is None:
        return None
    return {
        "service_type": plugin.service_type,
        "plugin_short_id": plugin.plugin_short_id,
        "enabled": plugin.enabled,
        "health_status": plugin.health_status,
        "health_message": plugin.health_message,
        "health_error_code": plugin.health_error_code,
        "credential_status": plugin.credential_status,
        "credential_error": plugin.credential_error,
        "consecutive_failures": plugin.consecutive_failures,
        "last_health_check_at": plugin.last_health_check_at,
        "last_success_at": plugin.last_success_at,
        "last_credential_bootstrap_at": plugin.last_credential_bootstrap_at,
        "updated_at": plugin.updated_at,
    }


async def get_bakery_ticket_context(
    *,
    order_id: int | None,
    req_id: str | None,
    bakery_ticket_id: str | None,
    limit: int,
) -> JSONObject:
    """Collect Bakery ticket context from PoundCake-owned models."""

    normalized_req_id = str(req_id or "").strip()
    normalized_ticket_id = str(bakery_ticket_id or "").strip()
    criteria = {
        "order_id": order_id,
        "req_id": normalized_req_id or None,
        "bakery_ticket_id": normalized_ticket_id or None,
        "limit": limit,
    }

    async with SessionLocal() as db:
        order_query = select(Order)
        if order_id is not None:
            order_query = order_query.where(Order.id == order_id)
        if normalized_req_id:
            order_query = order_query.where(Order.req_id == normalized_req_id)
        if normalized_ticket_id:
            order_query = order_query.where(Order.fingerprint == normalized_ticket_id)
        orders = (
            (await db.execute(order_query.order_by(Order.updated_at.desc()).limit(limit)))
            .scalars()
            .all()
        )

        order_ids = [item.id for item in orders]
        req_ids = [item.req_id for item in orders]
        ingredient_req_ids = req_ids or ([normalized_req_id] if normalized_req_id else [])

        ingredients: list[DishIngredient] = []
        if ingredient_req_ids:
            ingredient_query = (
                select(DishIngredient)
                .where(DishIngredient.req_id.in_(ingredient_req_ids))
                .order_by(DishIngredient.updated_at.desc())
            )
            ingredients = (await db.execute(ingredient_query.limit(limit))).scalars().all()

        dishes: list[Dish] = []
        has_scoping_criteria = any([order_id is not None, normalized_req_id, normalized_ticket_id])
        if order_ids or normalized_req_id or not has_scoping_criteria:
            dish_query = select(Dish)
            if order_ids:
                dish_query = dish_query.where(
                    or_(Dish.order_id.in_(order_ids), Dish.req_id.in_(req_ids))
                )
            elif normalized_req_id:
                dish_query = dish_query.where(Dish.req_id == normalized_req_id)
            dishes = (
                (await db.execute(dish_query.order_by(Dish.updated_at.desc()).limit(limit)))
                .scalars()
                .all()
            )

    return {
        "criteria": criteria,
        "orders": [
            {
                "id": item.id,
                "req_id": item.req_id,
                "alert_group_name": item.alert_group_name,
                "alert_status": item.alert_status,
                "processing_status": item.processing_status,
                "remediation_outcome": item.remediation_outcome,
                "fingerprint": item.fingerprint,
                "is_active": item.is_active,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
            }
            for item in orders
        ],
        "ingredients": [
            {
                "id": item.id,
                "req_id": item.req_id,
                "dish_id": item.dish_id,
                "task_key": item.task_key,
                "service_type": item.service_type,
                "service_exec": item.service_exec,
                "destination_target": item.destination_target,
                "service_exec_id": item.service_exec_id,
                "service_exec_status": item.service_exec_status,
                "service_exec_error": item.service_exec_error,
                "updated_at": item.updated_at,
            }
            for item in ingredients
        ],
        "dishes": [
            {
                "id": item.id,
                "req_id": item.req_id,
                "order_id": item.order_id,
                "recipe_id": item.recipe_id,
                "run_phase": item.run_phase,
                "processing_status": item.processing_status,
                "dish_exec_status": item.dish_exec_status,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
            }
            for item in dishes
        ],
    }


async def reconcile_bakery_active_orders(*, req_id: str, limit: int) -> JSONObject:
    """Run Bakery incident reconciliation inside the service DB boundary."""

    from api.plugins.bakery.incident_reconciliation import reconcile_active_orders

    async with SessionLocal() as db:
        async with db.begin():
            return await reconcile_active_orders(db, req_id=req_id, limit=limit)


async def check_prometheus_watchdog_heartbeat_once() -> JSONObject:
    """Run a single Prometheus watchdog heartbeat check inside the service layer."""

    from api.plugins.prometheus.watchdog import check_watchdog_heartbeat_once

    async with SessionLocal() as db:
        async with db.begin():
            return await check_watchdog_heartbeat_once(db)


async def process_release_update_notification(
    *,
    oci_repository: str,
    current_app_version: str,
    current_chart_version: str,
    available_app_version: str,
    available_chart_version: str,
    available_created_at: datetime | None = None,
    req_id: str,
) -> JSONObject:
    """Persist and dispatch release update notifications inside the service layer."""

    from api.plugins.release.delivery import process_release_notification

    async with SessionLocal() as db:
        async with db.begin():
            return await process_release_notification(
                db,
                oci_repository=oci_repository,
                current_app_version=current_app_version,
                current_chart_version=current_chart_version,
                available_app_version=available_app_version,
                available_chart_version=available_chart_version,
                available_created_at=available_created_at,
                req_id=req_id,
            )
