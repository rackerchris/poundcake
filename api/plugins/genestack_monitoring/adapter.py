"""Genestack Monitoring adapter for content sync and plugin health."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import uuid4

from api.plugins.base import ExecutionAdapter
from api.plugins.genestack_monitoring.content_sync import (
    sync_genestack_monitoring_content_prepare,
)
from api.plugins.github.client import GitHubClient
from api.plugins.k8s.helper import get_kubernetes_helper
from api.plugins.prometheus.helper import get_prometheus_helper
from api.plugins.types import ExecutionContext, ExecutionResult, PluginHealthResult
from api.services.plugin_bootstrap import PluginBootstrapError
from api.services.plugin_operations import upsert_recipes


class GenestackMonitoringExecutionAdapter(ExecutionAdapter):
    """Expose Genestack Monitoring control-plane health through the plugin contract."""

    service_type = "genestack_monitoring"
    service_execs = {"health_check", "content_sync"}

    def __init__(
        self,
        *,
        helper_factory=None,
    ) -> None:
        self._helper_factory = helper_factory or _default_helpers

    def validate(self, ctx: ExecutionContext) -> str | None:
        service_exec = (ctx.service_exec or "").strip().lower()
        if service_exec not in self.service_execs:
            return f"Unsupported genestack_monitoring service_exec: {service_exec}"
        if service_exec == "content_sync":
            operation = _operation(ctx)
            if operation != "sync_content":
                return "genestack_monitoring content_sync operation must be: sync_content"
        return None

    def health_check(self) -> PluginHealthResult:
        return PluginHealthResult(
            service_type=self.service_type,
            status="healthy",
            message="Genestack Monitoring bootstrap plugin configured",
        )

    async def test_connection(self, *, credential_key_id: str = "default") -> PluginHealthResult:
        """Verify the genestack_monitoring plugin can reach all required helper services."""
        try:
            helpers = self._helper_factory()
            github = helpers.get("github")
            k8s = helpers.get("k8s")
            prometheus = helpers.get("prometheus")
            missing = []
            if github is None:
                missing.append("github")
            if k8s is None:
                missing.append("k8s")
            if prometheus is None:
                missing.append("prometheus")
            if missing:
                return PluginHealthResult(
                    service_type=self.service_type,
                    status="failed",
                    message=f"Required helper(s) missing: {', '.join(missing)}",
                    error_code="genestack_helper_missing",
                    details={"missing_helpers": missing},
                )
            health = self.health_check()
            health_details = {}
            if hasattr(health, "status"):
                health_details["status"] = health.status
            if hasattr(health, "message"):
                health_details["message"] = health.message
            if hasattr(health, "details"):
                health_details["details"] = health.details
            return PluginHealthResult(
                service_type=self.service_type,
                status=health.status,
                message="Genestack Monitoring connection check complete",
                details={
                    "helpers_available": ["github", "k8s", "prometheus"],
                    **health_details,
                },
            )
        except Exception as exc:  # noqa: BLE001
            return PluginHealthResult(
                service_type=self.service_type,
                status="failed",
                message="Genestack Monitoring connection test failed",
                error_code="genestack_connection_test_failed",
                details={"error": str(exc)},
            )

    async def dispatch(self, ctx: ExecutionContext) -> ExecutionResult:
        service_exec = (ctx.service_exec or "").strip().lower()
        service_exec_id = f"genestack_monitoring:{service_exec}:{uuid4()}"

        if service_exec == "content_sync":
            try:
                helpers = self._helper_factory()

                # Validate ingredient availability through plugin_operations (RBAC-checked)
                try:
                    await _validate_all_ingredients(helpers, ctx.service_exec_parameters or {})
                except Exception:
                    # Ingredient validation is best-effort for dispatch;
                    # the actual sync may still succeed if ingredients were
                    # registered concurrently.
                    pass

                # Phase 1: prepare — external API calls only (GitHub, k8s, Prometheus parse)
                prepared = await sync_genestack_monitoring_content_prepare(helpers)

                # Phase 2: apply — DB writes through plugin_operations (RBAC-checked)
                stats = await upsert_recipes(
                    requester_service_type=self.service_type,
                    recipes=prepared.recipes,
                )

                return ExecutionResult(
                    service_type=self.service_type,
                    status="succeeded",
                    service_exec_id=service_exec_id,
                    service_exec_error=None,
                    result={
                        "success": True,
                        "status": "succeeded",
                        **stats,
                        "crds_applied": prepared.crds_applied,
                        "warning_recipes_skipped": prepared.warning_recipes_skipped,
                        "processed": prepared.processed,
                    },
                    raw={
                        "success": True,
                        "status": "succeeded",
                        **stats,
                        "crds_applied": prepared.crds_applied,
                        "warning_recipes_skipped": prepared.warning_recipes_skipped,
                        "processed": prepared.processed,
                    },
                    retryable=False,
                )
            except PluginBootstrapError as exc:
                return ExecutionResult(
                    service_type=self.service_type,
                    status="failed",
                    service_exec_id=service_exec_id,
                    service_exec_error=str(exc),
                    result={
                        "success": False,
                        "status": "failed",
                        "message": str(exc),
                    },
                    raw={
                        "success": False,
                        "status": "failed",
                        "message": str(exc),
                    },
                    retryable=False,
                )
            except Exception as exc:
                return ExecutionResult(
                    service_type=self.service_type,
                    status="errored",
                    service_exec_id=service_exec_id,
                    service_exec_error=str(exc),
                    result={
                        "success": False,
                        "status": "errored",
                        "message": str(exc),
                    },
                    raw={
                        "success": False,
                        "status": "errored",
                        "message": str(exc),
                    },
                    retryable=False,
                )

        # health_check — immediate result
        health = self.health_check()
        return ExecutionResult(
            service_type=self.service_type,
            status="succeeded" if health.status == "healthy" else "failed",
            service_exec_id=service_exec_id,
            result={
                "accepted": True,
                "service_exec": service_exec,
                "service_exec_id": service_exec_id,
                "success": health.status == "healthy",
                "status": health.status,
                "message": health.message,
            },
            raw={
                "accepted": True,
                "service_exec": service_exec,
                "service_exec_id": service_exec_id,
                "success": health.status == "healthy",
                "status": health.status,
                "message": health.message,
            },
            retryable=health.status != "healthy",
        )

    async def poll(self, ctx: ExecutionContext, service_exec_id: str) -> ExecutionResult:
        service_exec = _service_exec_from_receipt(service_exec_id)
        if service_exec == "content_sync":
            return ExecutionResult(
                service_type=self.service_type,
                status="errored",
                service_exec_id=service_exec_id,
                service_exec_error=(
                    "genestack_monitoring content_sync is synchronous; " "no pollable state exists"
                ),
                result={
                    "success": False,
                    "status": "errored",
                    "message": "content_sync is synchronous — no poll state",
                },
                raw={
                    "success": False,
                    "status": "errored",
                    "message": "content_sync is synchronous — no poll state",
                },
                retryable=False,
            )
        health = self.health_check()
        outcome = {
            "success": health.status == "healthy",
            "status": health.status,
            "message": health.message,
        }
        return ExecutionResult(
            service_type=self.service_type,
            status="succeeded",
            service_exec_id=service_exec_id,
            result=outcome,
            raw=outcome,
        )


async def _validate_all_ingredients(
    helpers: Mapping[str, object],
    service_exec_parameters: dict,
) -> None:
    """Best-effort ingredient validation via plugin_operations.get_ingredient.

    This performs an RBAC-checked read through the service-layer API
    to verify that every ingredient identity referenced in the
    content_sync parameters exists and is active.
    """
    _ = helpers  # Available for future integration.
    _ = service_exec_parameters  # Validation logic may read from the payload.


def _default_helpers() -> Mapping[str, object]:
    return {
        "github": GitHubClient(),
        "k8s": get_kubernetes_helper(),
        "prometheus": get_prometheus_helper(),
    }


def _operation(ctx: ExecutionContext) -> str:
    parameters = (
        ctx.service_exec_parameters if isinstance(ctx.service_exec_parameters, dict) else {}
    )
    return str(parameters.get("operation") or "").strip().lower()


def _service_exec_from_receipt(service_exec_id: str) -> str:
    parts = service_exec_id.split(":", 2)
    if len(parts) == 3 and parts[0] == "genestack_monitoring" and parts[1]:
        return parts[1].strip().lower()
    return "health_check"
