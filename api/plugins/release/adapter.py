"""Release update execution adapter."""

from __future__ import annotations

from uuid import uuid4

from api.plugins.base import ExecutionAdapter
from api.plugins.state import PLUGIN_RUN_STATE_HEALTHY
from api.plugins.types import ExecutionContext, ExecutionResult, PluginHealthResult
from api.types import JSONObject

RELEASE_SERVICE_EXECS = {
    "health_check",
    "check_updates",
}
SERVICE_PAYLOAD_OBJECT_ERROR = "service_payload must be an object when provided"


class ReleaseExecutionAdapter(ExecutionAdapter):
    """Expose release update operations through the order execution boundary."""

    service_type = "release"

    def validate(self, ctx: ExecutionContext) -> str | None:
        service_exec = (ctx.service_exec or "").strip().lower()
        if service_exec not in RELEASE_SERVICE_EXECS:
            return f"Unsupported release service_exec: {ctx.service_exec}"
        if ctx.service_payload is not None and not isinstance(ctx.service_payload, dict):
            return SERVICE_PAYLOAD_OBJECT_ERROR
        return None

    def health_check(self) -> PluginHealthResult:
        from api.core.config import get_settings

        settings = get_settings()
        enabled = settings.release_update_enabled
        oci_repo = settings.release_update_oci_repository or ""

        return PluginHealthResult(
            service_type=self.service_type,
            status=PLUGIN_RUN_STATE_HEALTHY,
            message=(
                "Release update plugin configured"
                if enabled
                else "Release update notifications are disabled"
            ),
            details={
                "enabled": enabled,
                "oci_repository": oci_repo,
                "check_interval_seconds": settings.release_update_check_interval_seconds,
                "include_prereleases": settings.release_update_include_prereleases,
                "current_app_version": settings.app_version,
                "current_chart_version": settings.chart_version,
            },
        )

    async def dispatch(self, ctx: ExecutionContext) -> ExecutionResult:
        service_exec = (ctx.service_exec or "").strip().lower()
        service_exec_id = f"release:{service_exec}:{uuid4()}"
        if ctx.service_payload is not None and not isinstance(ctx.service_payload, dict):
            return _payload_contract_error(
                service_type=self.service_type,
                service_exec_id=service_exec_id,
                message=SERVICE_PAYLOAD_OBJECT_ERROR,
            )

        try:
            if service_exec == "health_check":
                health = self.health_check()
                return ExecutionResult(
                    service_type=self.service_type,
                    status="succeeded",
                    service_exec_id=service_exec_id,
                    result={
                        "success": True,
                        "status": health.status,
                        "message": health.message,
                        "details": health.details,
                    },
                    raw={
                        "success": True,
                        "status": health.status,
                        "message": health.message,
                        "details": health.details,
                    },
                    retryable=False,
                )

            if service_exec == "check_updates":
                from api.plugins.release.checker import check_once

                result = await check_once()
                status = (
                    "succeeded"
                    if result.get("status")
                    not in {"disabled", "errored"}
                    else "succeeded"
                )
                return ExecutionResult(
                    service_type=self.service_type,
                    status=status,
                    service_exec_id=service_exec_id,
                    result=result,
                    raw=result,
                    retryable=False,
                )

            raise ValueError(f"Unknown release operation: {service_exec}")

        except Exception as exc:  # noqa: BLE001
            outcome: JSONObject = {
                "success": False,
                "status": "errored",
                "message": str(exc),
            }
            return ExecutionResult(
                service_type=self.service_type,
                status="errored",
                service_exec_id=service_exec_id,
                service_exec_error=str(exc),
                result=outcome,
                raw=outcome,
                retryable=False,
            )

    async def poll(self, ctx: ExecutionContext, service_exec_id: str) -> ExecutionResult:
        message = "Release executions complete during dispatch; no pollable state exists"
        return ExecutionResult(
            service_type=self.service_type,
            status="succeeded",
            service_exec_id=service_exec_id,
            result={
                "success": True,
                "status": "succeeded",
                "message": message,
            },
            raw={
                "success": True,
                "status": "succeeded",
                "message": message,
            },
            retryable=False,
        )


def _payload_contract_error(
    *, service_type: str, service_exec_id: str, message: str
) -> ExecutionResult:
    outcome: JSONObject = {"success": False, "status": "errored", "message": message}
    return ExecutionResult(
        service_type=service_type,
        status="errored",
        service_exec_id=service_exec_id,
        service_exec_error=message,
        result=outcome,
        raw=outcome,
        retryable=False,
    )
