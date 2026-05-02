"""Dummy execution adapter used to prove the service-plugin API contract."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from uuid import uuid4

from api.plugins.base import ExecutionAdapter
from api.plugins.types import ExecutionContext, ExecutionResult, PluginHealthResult
from api.types import JSONObject

DUMMY_COMMUNICATION_OPERATIONS = {"open", "notify", "update", "close"}
DUMMY_RECEIPT_PREFIX = "dummy"


@dataclass(frozen=True, slots=True)
class DummyOperatorConfig:
    """Non-secret operator configuration for the dummy plugin."""

    simulate_health_failures: bool = False


class DummyExecutionAdapter(ExecutionAdapter):
    """Return async-style receipts and terminal poll outcomes for contract validation."""

    service_type = "dummy"

    def __init__(
        self,
        *,
        operator_config: DummyOperatorConfig | None = None,
    ) -> None:
        self._operator_config = operator_config

    def credential_requirements(self) -> list[JSONObject]:
        return []

    def operator_config_schema(self) -> JSONObject:
        return {
            "type": "object",
            "properties": {
                "simulate_health_failures": {
                    "type": "boolean",
                    "title": "Simulate Health Failures",
                    "description": "When true, health_check returns unhealthy status for contract testing.",
                },
            },
            "required": [],
            "additionalProperties": False,
        }

    def default_operator_config(self) -> JSONObject:
        return {"simulate_health_failures": False}

    def normalize_operator_config(self, config: JSONObject | None) -> JSONObject:
        raw = dict(config or {})
        simulate = bool(raw.get("simulate_health_failures", False))
        return {"simulate_health_failures": simulate}

    def with_operator_config(self, config: JSONObject | None) -> "DummyExecutionAdapter":
        raw = dict(config or {})
        simulate = bool(raw.get("simulate_health_failures", False))
        op_config = DummyOperatorConfig(simulate_health_failures=simulate)
        return DummyExecutionAdapter(operator_config=op_config)

    @staticmethod
    def _parse_receipt_parts(receipt_id: str) -> tuple[str, str | None, str | None]:
        """Parse an opaque receipt into (service_exec, operation, extra).

        Receipt formats:
            dummy:<service_exec>:<uuid>                          (3 parts, no op)
            dummy:<service_exec>:<ready_at>                      (3 parts, sleep_10)
            dummy:<service_exec>:<operation>:<uuid>             (4 parts, has op)
            dummy:<service_exec>:<operation>:<extra>            (4+ parts, has op)

        For 3-part receipts, the third part is examined: if it's a valid
        operation name it falls back to being ``extra``; otherwise it is
        treated as ``extra`` so that sleep_10 timestamps survive the parse.
        """
        parts = receipt_id.split(":")
        if len(parts) < 2 or parts[0] != DUMMY_RECEIPT_PREFIX:
            return "".join(parts).strip().lower(), None, None
        service_exec = parts[1].strip().lower()
        operation: str | None = None
        extra: str | None = None
        if len(parts) == 3:
            # dummy:<service_exec>:<extra>  (e.g. sleep_10:ready_at or plain UUID)
            extra = parts[2].strip() if parts[2].strip() else None
        elif len(parts) >= 4:
            third = parts[2].strip().lower()
            if third in DUMMY_COMMUNICATION_OPERATIONS:
                operation = third
            if len(parts) >= 4:
                fourth = parts[3].strip() if parts[3].strip() else None
                if operation is None and not fourth.isdigit():
                    # 3rd part was not an op and 4th part isn't a plain
                    # timestamp → treat fourth as the actual extra / UUID.
                    extra = fourth
                else:
                    extra = fourth
        return service_exec, operation, extra

    def validate(self, ctx: ExecutionContext) -> str | None:
        service_exec = self._normalize_service_exec(ctx.service_exec)
        if service_exec not in {
            "positive_result",
            "negative_result",
            "slow_result",
            "sleep_10",
            "health_check",
            "communication",
        }:
            return f"Unsupported dummy service_exec: {ctx.service_exec}"
        if service_exec == "communication":
            parameters = (
                ctx.service_exec_parameters if isinstance(ctx.service_exec_parameters, dict) else {}
            )
            operation = str(parameters.get("operation") or "").strip().lower()
            raw_allowed = parameters.get("allowed_operations", [])
            raw_allowed = raw_allowed if isinstance(raw_allowed, list) else []
            allowed = {str(item).strip().lower() for item in raw_allowed if str(item).strip()}
            if not allowed:
                allowed = DUMMY_COMMUNICATION_OPERATIONS
            if operation not in allowed:
                return (
                    "dummy communication operation must be one of: " f"{', '.join(sorted(allowed))}"
                )
        return None

    def health_check(self) -> PluginHealthResult:
        if self._operator_config and self._operator_config.simulate_health_failures:
            simulate_override = True
        else:
            raw_status = os.getenv("POUNDCAKE_DUMMY_HEALTH_STATUS", None)
            simulate_override = raw_status is not None

        if simulate_override:
            from api.plugins.state import (
                PLUGIN_RUN_STATE_FAILED,
                PLUGIN_RUN_STATE_HEALTHY,
                normalize_plugin_run_state,
            )

            if raw_status is None:
                raw_status = "failed"
            try:
                status = normalize_plugin_run_state(raw_status)
            except ValueError:
                status = PLUGIN_RUN_STATE_FAILED
            message = os.getenv("POUNDCAKE_DUMMY_HEALTH_MESSAGE", "Dummy plugin ready")
            if status != PLUGIN_RUN_STATE_HEALTHY and message == "Dummy plugin ready":
                message = "Dummy plugin health failure simulated by environment"
            details: JSONObject = {
                "mode": "async-contract-validation",
                "simulated": True,
            }
        else:
            status = "healthy"
            message = "Dummy plugin ready"
            details = {
                "mode": "async-contract-validation",
                "simulated": False,
            }

        return PluginHealthResult(
            service_type=self.service_type,
            status=status,
            message=message,
            details=details,
        )

    async def dispatch(self, ctx: ExecutionContext) -> ExecutionResult:
        service_exec = self._normalize_service_exec(ctx.service_exec)
        operation = self._communication_operation(ctx) if service_exec == "communication" else None

        # Build opaque receipt_id: dummy:<service_exec>[:<operation>]:<extra>
        # For sleep_10, extra = ready_at timestamp so poll can detect completion.
        # For all other operations, extra is a UUID; receipts are opaque.
        if service_exec == "sleep_10":
            ready_at = int(time.time()) + 10
            if operation:
                service_exec_id = f"{DUMMY_RECEIPT_PREFIX}:{service_exec}:{operation}:{ready_at}"
            else:
                service_exec_id = f"{DUMMY_RECEIPT_PREFIX}:{service_exec}:{ready_at}"
            outcome: JSONObject = {
                "accepted": True,
                "status_code": 202,
                "status": "running",
                "service_exec": service_exec,
                "service_exec_id": service_exec_id,
                "work_execution_id": service_exec_id,
                "ready_at": ready_at,
            }
        elif operation:
            service_exec_id = f"{DUMMY_RECEIPT_PREFIX}:{service_exec}:{operation}:{uuid4()}"
            outcome = {
                "accepted": True,
                "status_code": 202,
                "status": "running",
                "service_exec": service_exec,
                "service_exec_id": service_exec_id,
                "work_execution_id": service_exec_id,
                "operation": operation,
            }
        else:
            service_exec_id = f"{DUMMY_RECEIPT_PREFIX}:{service_exec}:{uuid4()}"
            outcome = {
                "accepted": True,
                "status_code": 202,
                "status": "running",
                "service_exec": service_exec,
                "service_exec_id": service_exec_id,
                "work_execution_id": service_exec_id,
            }
            if operation:
                outcome["operation"] = operation

        return ExecutionResult(
            service_type=self.service_type,
            status="running",
            service_exec_id=service_exec_id,
            result=outcome,
            raw=outcome,
            retryable=False,
        )

    async def poll(self, ctx: ExecutionContext, service_exec_id: str) -> ExecutionResult:
        service_exec, operation, extra = self._parse_receipt_parts(service_exec_id)
        operation = operation if operation else None

        if service_exec == "slow_result":
            outcome: JSONObject = {
                "success": None,
                "status": "running",
                "message": "dummy slow execution still running",
                "service_exec": service_exec,
                "service_exec_id": service_exec_id,
            }
            if operation:
                outcome["operation"] = operation
            return ExecutionResult(
                service_type=self.service_type,
                status="running",
                service_exec_id=service_exec_id,
                result=outcome,
                raw=outcome,
                retryable=False,
            )

        if service_exec == "sleep_10":
            # Read ready_at from extra part of receipt (for local dev use only)
            ready_at = None
            if extra:
                try:
                    ready_at = int(extra)
                except ValueError:
                    pass
            if ready_at is not None and time.time() < ready_at:
                outcome = {
                    "success": None,
                    "status": "running",
                    "message": "dummy sleep execution still running",
                    "service_exec": service_exec,
                    "service_exec_id": service_exec_id,
                }
                if operation:
                    outcome["operation"] = operation
                return ExecutionResult(
                    service_type=self.service_type,
                    status="running",
                    service_exec_id=service_exec_id,
                    result=outcome,
                    raw=outcome,
                    retryable=False,
                )
            outcome = {
                "success": True,
                "status": "succeeded",
                "message": "dummy sleep execution completed",
                "service_exec": service_exec,
                "service_exec_id": service_exec_id,
            }
            if operation:
                outcome["operation"] = operation
            return ExecutionResult(
                service_type=self.service_type,
                status="succeeded",
                service_exec_id=service_exec_id,
                result=outcome,
                raw=outcome,
                retryable=False,
            )

        if service_exec == "health_check":
            health = self.health_check()
            callable_states = {"healthy", "initializing", "degraded"}
            success = health.status in callable_states
            outcome: JSONObject = {
                "success": success,
                "status": health.status,
                "message": health.message or "dummy plugin health checked",
                "service_type": self.service_type,
                "details": health.details or {},
            }
            return ExecutionResult(
                service_type=self.service_type,
                status="succeeded" if success else "failed",
                service_exec_id=service_exec_id,
                result=outcome,
                raw=outcome,
                retryable=False,
            )

        success = service_exec != "negative_result"
        status = "succeeded" if success else "failed"
        outcome: JSONObject = {
            "success": success,
            "status": status,
            "message": f"dummy {status} poll result",
            "service_exec": service_exec,
            "service_exec_id": service_exec_id,
        }
        if operation:
            outcome["operation"] = operation
        return ExecutionResult(
            service_type=self.service_type,
            status=status,
            service_exec_id=service_exec_id,
            result=outcome,
            raw=outcome,
            retryable=False,
        )

    async def cancel(self, ctx: ExecutionContext, service_exec_id: str) -> ExecutionResult:
        service_exec, operation, _ = self._parse_receipt_parts(service_exec_id)
        outcome: JSONObject = {
            "success": False,
            "status": "canceled",
            "message": "dummy execution canceled",
            "service_exec": service_exec,
            "service_exec_id": service_exec_id,
        }
        if operation:
            outcome["operation"] = operation
        return ExecutionResult(
            service_type=self.service_type,
            status="canceled",
            service_exec_id=service_exec_id,
            result=outcome,
            raw=outcome,
            retryable=False,
        )

    @staticmethod
    def _normalize_service_exec(service_exec: str | None) -> str:
        return (service_exec or "").strip().lower()

    @staticmethod
    def _communication_operation(ctx: ExecutionContext) -> str | None:
        parameters = (
            ctx.service_exec_parameters if isinstance(ctx.service_exec_parameters, dict) else {}
        )
        operation = str(parameters.get("operation") or "").strip().lower()
        return operation if operation in DUMMY_COMMUNICATION_OPERATIONS else None
