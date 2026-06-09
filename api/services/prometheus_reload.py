"""Shared Prometheus rule reload orchestration helpers."""

from __future__ import annotations

from api.plugins.types import ExecutionContext, ExecutionResult
from api.services.plugin_orchestrator import ExecutionOrchestrator
from api.types import JSONObject


async def reload_prometheus_rules(
    *,
    orchestrator: ExecutionOrchestrator,
    req_id: str,
    operator_config: JSONObject | None = None,
) -> ExecutionResult:
    """Ask the Prometheus plugin to reload rule/config state."""

    context: JSONObject = {}
    if isinstance(operator_config, dict):
        context["operator_config"] = dict(operator_config)
    return await orchestrator.dispatch(
        ExecutionContext.model_validate(
            {
                "service_type": "prometheus",
                "service_exec": "reload_config",
                "service_payload": {},
                "service_exec_parameters": None,
                "retry_count": 0,
                "retry_delay": 0,
                "service_exec_timeout": 60,
                "context": context,
                "req_id": req_id,
            }
        )
    )
