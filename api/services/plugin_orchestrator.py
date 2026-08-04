"""Unified execution orchestrator for all service plugins."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from pydantic import ValidationError

from api.core.logging import get_logger
from api.plugins.catalog import build_enabled_plugin_registry
from api.plugins.types import (
    ExecutionContext,
    ExecutionResult,
    PluginBootstrapResult,
    PluginHealthResult,
)

if TYPE_CHECKING:
    from api.plugins.registry import ExecutionAdapterRegistry

logger = get_logger(__name__)


def _raw_service_type(ctx: object) -> str:
    if isinstance(ctx, ExecutionContext):
        return ctx.service_type
    if isinstance(ctx, dict):
        value = ctx.get("service_type")
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return "unknown"


def _malformed_context_result(
    ctx: object,
    exc: ValidationError,
    *,
    service_exec_id: str | None = None,
) -> ExecutionResult:
    return ExecutionResult(
        service_type=_raw_service_type(ctx),
        status="errored",
        service_exec_id=service_exec_id,
        service_exec_error=f"Malformed ExecutionContext: {exc.errors()[0]['msg']}",
        retryable=False,
    )


class ExecutionOrchestrator:
    """Dispatches execution contexts to service_type-specific adapters with shared retries."""

    def __init__(self, registry: ExecutionAdapterRegistry) -> None:
        self._registry = registry

    def _adapter_for_context(self, ctx: ExecutionContext):
        adapter = self._registry.get(ctx.service_type)
        if adapter is None:
            return None
        config = ctx.context.get("operator_config") or ctx.context.get("plugin_config")
        if isinstance(config, dict):
            return adapter.with_operator_config(config)
        return adapter

    async def dispatch(self, ctx: ExecutionContext) -> ExecutionResult:
        try:
            ctx = ExecutionContext.model_validate(ctx)
        except ValidationError as exc:
            return _malformed_context_result(ctx, exc)
        try:
            adapter = self._adapter_for_context(ctx)
        except ValueError as exc:
            return ExecutionResult(
                service_type=(ctx.service_type or "").strip().lower(),
                status="errored",
                service_exec_error=str(exc),
                retryable=False,
            )
        if adapter is None:
            return ExecutionResult(
                service_type=(ctx.service_type or "").strip().lower() or "unknown",
                status="errored",
                service_exec_error=f"Unsupported service_type: {ctx.service_type}",
                retryable=False,
            )

        validation_error = adapter.validate(ctx)
        if validation_error:
            return ExecutionResult(
                service_type=(ctx.service_type or "").strip().lower(),
                status="errored",
                service_exec_error=validation_error,
                retryable=False,
            )

        attempts = max(1, int(ctx.retry_count) + 1)
        for attempt in range(1, attempts + 1):
            result = _validate_execution_result(
                await adapter.dispatch(ctx),
                service_type=ctx.service_type,
                operation="dispatch",
            )
            result.attempts = attempt
            if result.status not in {"failed", "errored"}:
                return result
            if not result.retryable or attempt >= attempts:
                return result
            delay = max(0, int(ctx.retry_delay))
            logger.warning(
                "Execution attempt failed; retrying",
                extra={
                    "req_id": ctx.req_id,
                    "service_type": ctx.service_type,
                    "target": ctx.service_exec,
                    "attempt": attempt,
                    "max_attempts": attempts,
                    "delay_seconds": delay,
                },
            )
            if delay > 0:
                await asyncio.sleep(delay)

        return ExecutionResult(
            service_type=(ctx.service_type or "").strip().lower(),
            status="errored",
            service_exec_error="Execution errored after retry loop",
            retryable=False,
            attempts=attempts,
        )

    async def poll(self, ctx: ExecutionContext, service_exec_id: str) -> ExecutionResult:
        try:
            ctx = ExecutionContext.model_validate(ctx)
        except ValidationError as exc:
            return _malformed_context_result(ctx, exc, service_exec_id=service_exec_id)
        try:
            adapter = self._adapter_for_context(ctx)
        except ValueError as exc:
            return ExecutionResult(
                service_type=(ctx.service_type or "").strip().lower(),
                status="errored",
                service_exec_id=service_exec_id,
                service_exec_error=str(exc),
                retryable=False,
            )
        if adapter is None:
            return ExecutionResult(
                service_type=(ctx.service_type or "").strip().lower() or "unknown",
                status="errored",
                service_exec_id=service_exec_id,
                service_exec_error=f"Unsupported service_type: {ctx.service_type}",
                retryable=False,
            )
        return _validate_execution_result(
            await adapter.poll(ctx, service_exec_id),
            service_type=ctx.service_type,
            operation="poll",
            service_exec_id=service_exec_id,
        )

    async def cancel(self, ctx: ExecutionContext, service_exec_id: str) -> ExecutionResult:
        try:
            ctx = ExecutionContext.model_validate(ctx)
        except ValidationError as exc:
            return _malformed_context_result(ctx, exc, service_exec_id=service_exec_id)
        try:
            adapter = self._adapter_for_context(ctx)
        except ValueError as exc:
            return ExecutionResult(
                service_type=(ctx.service_type or "").strip().lower(),
                status="errored",
                service_exec_id=service_exec_id,
                service_exec_error=str(exc),
                retryable=False,
            )
        if adapter is None:
            return ExecutionResult(
                service_type=(ctx.service_type or "").strip().lower() or "unknown",
                status="errored",
                service_exec_id=service_exec_id,
                service_exec_error=f"Unsupported service_type: {ctx.service_type}",
                retryable=False,
            )
        return _validate_execution_result(
            await adapter.cancel(ctx, service_exec_id),
            service_type=ctx.service_type,
            operation="cancel",
            service_exec_id=service_exec_id,
        )

    async def bootstrap_plugin(
        self,
        ctx: ExecutionContext,
        *,
        force: bool = False,
    ) -> PluginBootstrapResult | None:
        try:
            ctx = ExecutionContext.model_validate(ctx)
        except ValidationError as exc:
            return PluginBootstrapResult(
                service_type=_raw_service_type(ctx),
                status="failed",
                message=f"Malformed ExecutionContext: {exc.errors()[0]['msg']}",
                error_code="execution_contract_error",
            )
        adapter = self._registry.get(ctx.service_type)
        if adapter is None:
            return None
        result = PluginBootstrapResult.model_validate(
            await adapter.bootstrap_plugin(ctx, force=force)
        )
        if result.service_type != ctx.service_type:
            raise ValueError(
                "Adapter returned mismatched service_type for bootstrap_plugin: "
                f"{result.service_type}"
            )
        return result

    def health_check(self, service_type: str) -> PluginHealthResult | None:
        adapter = self._registry.get(service_type)
        if adapter is None:
            return None
        return PluginHealthResult.model_validate(adapter.health_check())


def _validate_execution_result(
    value: object,
    *,
    service_type: str,
    operation: str,
    service_exec_id: str | None = None,
) -> ExecutionResult:
    try:
        result = ExecutionResult.model_validate(value)
    except (TypeError, ValidationError, ValueError) as exc:
        logger.error(
            "Adapter returned invalid execution result",
            extra={
                "service_type": service_type,
                "operation": operation,
                "error": str(exc),
            },
        )
        return ExecutionResult(
            service_type=service_type,
            status="errored",
            service_exec_id=service_exec_id,
            service_exec_error=f"Adapter returned invalid ExecutionResult for {operation}",
            retryable=False,
        )
    if result.service_type != service_type.strip().lower():
        logger.error(
            "Adapter returned mismatched service_type",
            extra={
                "service_type": service_type,
                "returned_service_type": result.service_type,
                "operation": operation,
            },
        )
        return ExecutionResult(
            service_type=service_type,
            status="errored",
            service_exec_id=result.service_exec_id or service_exec_id,
            service_exec_error=(
                f"Adapter returned mismatched service_type for {operation}: "
                f"{result.service_type}"
            ),
            retryable=False,
        )
    return result


_orchestrator: ExecutionOrchestrator | None = None


def get_execution_orchestrator() -> ExecutionOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = ExecutionOrchestrator(build_enabled_plugin_registry())
    return _orchestrator
