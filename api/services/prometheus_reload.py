"""Shared Prometheus rule reload order submission."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from api.services.order_intake import (
    OperatorActionOrderSubmission,
    submit_operator_action_order,
)
from api.types import JSONObject


async def reload_prometheus_rules(
    *,
    db: AsyncSession,
    req_id: str,
    operator_config: JSONObject | None = None,
) -> OperatorActionOrderSubmission:
    """Submit a Prometheus rule/config reload order."""

    _ = operator_config
    return await submit_operator_action_order(
        db=db,
        req_id=req_id,
        recipe_name="operator-action:prometheus:reload-config",
        service_type="prometheus",
        service_exec="reload_config",
        task_key_template="prometheus-reload-config",
        service_payload={},
    )
