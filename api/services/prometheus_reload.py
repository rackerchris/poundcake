"""Shared Prometheus rule reload orchestration helpers."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from api.plugins.types import ExecutionResult
from api.services.operator_action_orders import run_operator_action_order
from api.services.plugin_orchestrator import ExecutionOrchestrator
from api.types import JSONObject


async def reload_prometheus_rules(
    *,
    db: AsyncSession,
    orchestrator: ExecutionOrchestrator,
    req_id: str,
    operator_config: JSONObject | None = None,
) -> ExecutionResult:
    """Ask the Prometheus plugin to reload rule/config state."""

    _ = operator_config
    result = await run_operator_action_order(
        db=db,
        orchestrator=orchestrator,
        req_id=req_id,
        recipe_name="operator-action:prometheus:reload-config",
        service_type="prometheus",
        service_exec="reload_config",
        task_key_template="prometheus-reload-config",
        service_payload={},
    )
    return ExecutionResult(
        service_type="prometheus",
        status=result.status,
        service_exec_id=str(result.dish_ingredient_id),
        service_exec_error=result.error,
        result=result.outcome,
        raw=result.outcome,
        retryable=False,
    )
