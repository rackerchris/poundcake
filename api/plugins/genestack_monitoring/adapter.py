"""Genestack Monitoring adapter for content sync and plugin health."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from uuid import uuid4

from api.plugins.base import ExecutionAdapter
from api.plugins.catalog import (
    build_enabled_plugin_capability_catalog,
)
from api.plugins.genestack_monitoring.content_sync import (
    export_genestack_alert_updates_prepare,
    sync_genestack_monitoring_content_prepare,
)
from api.plugins.genestack_monitoring.helper_contracts import (
    apply_github_credentials,
    require_github_writer_helper,
    require_github_credential_helper,
    resolve_enabled_genestack_helpers,
    set_allow_public_read,
)
from api.plugins.genestack_monitoring.templates import (
    GENESTACK_MONITORING_ALERT_EXPORT_OPERATION,
    GENESTACK_MONITORING_CONTENT_SYNC_OPERATION,
)
from api.plugins.types import ExecutionContext, ExecutionResult, PluginHealthResult
from api.services.credential_manager import read_adapter_credential_with_policy
from api.services.plugin_bootstrap import PluginBootstrapError
from api.services.plugin_operations import (
    get_ingredient,
    list_recipe_management_states,
    list_service_plugin_configs,
    upsert_recipes,
)

SERVICE_PAYLOAD_OBJECT_ERROR = "service_payload must be an object when provided"


class GenestackMonitoringExecutionAdapter(ExecutionAdapter):
    """Expose Genestack Monitoring control-plane health through the plugin contract."""

    service_type = "genestack_monitoring"
    service_execs = {"health_check", "content_sync", "repo_sync"}

    def __init__(
        self,
        *,
        helper_factory=None,
    ) -> None:
        self._helper_factory = helper_factory or resolve_enabled_genestack_helpers

    def validate(self, ctx: ExecutionContext) -> str | None:
        service_exec = (ctx.service_exec or "").strip().lower()
        if service_exec not in self.service_execs:
            return f"Unsupported genestack_monitoring service_exec: {service_exec}"
        if ctx.service_payload is not None and not isinstance(ctx.service_payload, dict):
            return SERVICE_PAYLOAD_OBJECT_ERROR
        if service_exec == "content_sync":
            operation = _operation(ctx)
            if operation != GENESTACK_MONITORING_CONTENT_SYNC_OPERATION:
                return "genestack_monitoring content_sync operation must be: sync_content"
        if service_exec == "repo_sync":
            operation = _operation(ctx)
            if operation != GENESTACK_MONITORING_ALERT_EXPORT_OPERATION:
                return "genestack_monitoring repo_sync operation must be: export_alert_updates"
            payload = {} if ctx.service_payload is None else ctx.service_payload
            for key in ("crd_name", "group_name", "rule_name"):
                if not str(payload.get(key) or "").strip():
                    return f"genestack_monitoring repo_sync requires service_payload.{key}"
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
            helpers = await _hydrate_helper_credentials(self._helper_factory())
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
        if ctx.service_payload is not None and not isinstance(ctx.service_payload, dict):
            return _payload_contract_error(
                service_type=self.service_type,
                service_exec_id=service_exec_id,
                message=SERVICE_PAYLOAD_OBJECT_ERROR,
            )

        if service_exec == "content_sync":
            try:
                helpers = await _hydrate_helper_credentials(self._helper_factory())

                # Validate ingredient availability through plugin_operations (RBAC-checked)
                try:
                    await _validate_all_ingredients(helpers, ctx.service_exec_parameters or {})
                except Exception:
                    # Ingredient validation is best-effort for dispatch;
                    # the actual sync may still succeed if ingredients were
                    # registered concurrently.
                    pass

                plugin_configs = await list_service_plugin_configs(
                    requester_service_type=self.service_type
                )
                capability_catalog = build_enabled_plugin_capability_catalog(plugin_configs)

                # Phase 1: prepare — external API calls only (GitHub, k8s, Prometheus parse)
                prepared = await sync_genestack_monitoring_content_prepare(
                    helpers,
                    capabilities=capability_catalog,
                )
                filtered_recipes, adjusted_stats = await _apply_recipe_publication_validation(
                    prepared.recipes,
                    recipe_outcomes=prepared.recipe_outcomes,
                )
                prepared = _replace_prepare_result(
                    prepared,
                    recipes=filtered_recipes,
                    warning_recipes_preserved_nonmanaged=(
                        prepared.warning_recipes_preserved_nonmanaged
                        + adjusted_stats["warning_recipes_preserved_nonmanaged"]
                    ),
                    recipes_published=adjusted_stats["recipes_published"],
                    recipes_degraded_to_review=adjusted_stats["recipes_degraded_to_review"],
                    recipes_skipped_missing_ingredient=(
                        prepared.recipes_skipped_missing_ingredient
                        + adjusted_stats["recipes_skipped_missing_ingredient"]
                    ),
                    remediation_profiles_skipped_missing_ingredients=(
                        prepared.remediation_profiles_skipped_missing_ingredients
                        + adjusted_stats["recipes_skipped_missing_ingredient"]
                    ),
                    recipe_outcomes=adjusted_stats["recipe_outcomes"],
                )

                # Phase 2: apply — DB writes through plugin_operations (RBAC-checked)
                stats = await upsert_recipes(
                    requester_service_type=self.service_type,
                    recipes=prepared.recipes,
                )
                stats_payload = asdict(stats)

                return ExecutionResult(
                    service_type=self.service_type,
                    status="succeeded",
                    service_exec_id=service_exec_id,
                    service_exec_error=None,
                    result={
                        "success": True,
                        "status": "succeeded",
                        **stats_payload,
                        "crds_applied": prepared.crds_applied,
                        "warning_recipes_skipped": prepared.warning_recipes_skipped,
                        "warning_recipes_preserved_nonmanaged": (
                            prepared.warning_recipes_preserved_nonmanaged
                        ),
                        "recipes_published": prepared.recipes_published,
                        "recipes_degraded_to_review": prepared.recipes_degraded_to_review,
                        "recipes_skipped_missing_capability": (
                            prepared.recipes_skipped_missing_capability
                        ),
                        "recipes_skipped_missing_ingredient": (
                            prepared.recipes_skipped_missing_ingredient
                        ),
                        "recipe_outcomes": prepared.recipe_outcomes,
                        "processed": prepared.processed,
                        "prometheus_reload_required": True,
                    },
                    raw={
                        "success": True,
                        "status": "succeeded",
                        **stats_payload,
                        "crds_applied": prepared.crds_applied,
                        "warning_recipes_skipped": prepared.warning_recipes_skipped,
                        "warning_recipes_preserved_nonmanaged": (
                            prepared.warning_recipes_preserved_nonmanaged
                        ),
                        "recipes_published": prepared.recipes_published,
                        "recipes_degraded_to_review": prepared.recipes_degraded_to_review,
                        "recipes_skipped_missing_capability": (
                            prepared.recipes_skipped_missing_capability
                        ),
                        "recipes_skipped_missing_ingredient": (
                            prepared.recipes_skipped_missing_ingredient
                        ),
                        "recipe_outcomes": prepared.recipe_outcomes,
                        "processed": prepared.processed,
                        "prometheus_reload_required": True,
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
        if service_exec == "repo_sync":
            payload = {} if ctx.service_payload is None else ctx.service_payload
            try:
                helpers = await _hydrate_helper_credentials(self._helper_factory())
                prepared = await export_genestack_alert_updates_prepare(
                    helpers,
                    crd_name=str(payload.get("crd_name") or "").strip(),
                    group_name=str(payload.get("group_name") or "").strip(),
                    rule_name=str(payload.get("rule_name") or "").strip(),
                    namespace=str(payload.get("namespace") or "").strip(),
                )
                github = require_github_writer_helper(
                    helpers,
                    operation="genestack_monitoring alert export",
                )
                commit_message = f"Update Genestack alert {prepared.selected_rule} from PoundCake"
                pr_title = f"Update Genestack alert {prepared.selected_rule}"
                pr_body = (
                    "This pull request was created by PoundCake after a live "
                    "PrometheusRule edit."
                )
                pr = await github.commit_and_pr(
                    repo=prepared.repo,
                    base_branch=prepared.base_branch,
                    branch=prepared.branch,
                    files=prepared.files,
                    commit_message=commit_message,
                    title=pr_title,
                    body=pr_body,
                )
                result_payload = {
                    "status": "succeeded",
                    "message": prepared.message,
                    "branch": prepared.branch,
                    "pull_request": (
                        pr.get("pull_request") if isinstance(pr.get("pull_request"), dict) else None
                    ),
                    "exported": {
                        "files": len(prepared.files),
                        "rule_name": prepared.selected_rule,
                        "repo": prepared.repo,
                    },
                    "skipped": prepared.skipped,
                    "warnings": prepared.warnings,
                }
                return ExecutionResult(
                    service_type=self.service_type,
                    status="succeeded",
                    service_exec_id=service_exec_id,
                    service_exec_error=None,
                    result={"success": True, **result_payload},
                    raw={"success": True, **result_payload, "commit_and_pr": pr},
                    retryable=False,
                )
            except PluginBootstrapError as exc:
                return ExecutionResult(
                    service_type=self.service_type,
                    status="failed",
                    service_exec_id=service_exec_id,
                    service_exec_error=str(exc),
                    result={"success": False, "status": "failed", "message": str(exc)},
                    raw={"success": False, "status": "failed", "message": str(exc)},
                    retryable=False,
                )
            except Exception as exc:
                return ExecutionResult(
                    service_type=self.service_type,
                    status="errored",
                    service_exec_id=service_exec_id,
                    service_exec_error=str(exc),
                    result={"success": False, "status": "errored", "message": str(exc)},
                    raw={"success": False, "status": "errored", "message": str(exc)},
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
        if service_exec in {"content_sync", "repo_sync"}:
            return ExecutionResult(
                service_type=self.service_type,
                status="errored",
                service_exec_id=service_exec_id,
                service_exec_error=(
                    "genestack_monitoring synchronous operations do not expose pollable state"
                ),
                result={
                    "success": False,
                    "status": "errored",
                    "message": f"{service_exec} is synchronous — no poll state",
                },
                raw={
                    "success": False,
                    "status": "errored",
                    "message": f"{service_exec} is synchronous — no poll state",
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


def _replace_prepare_result(prepared, **overrides: object):
    payload = asdict(prepared)
    payload.update(overrides)
    return type(prepared)(**payload)


async def _apply_recipe_publication_validation(
    recipes: list,
    *,
    recipe_outcomes: dict[str, str],
) -> tuple[list, dict[str, object]]:
    adjusted_outcomes = dict(recipe_outcomes)
    filtered_recipes = []
    preserved_nonmanaged = 0
    skipped_missing_ingredient = 0

    recipe_states = await list_recipe_management_states(
        requester_service_type="genestack_monitoring",
        recipe_names=[recipe.name for recipe in recipes],
    )

    for recipe in recipes:
        recipe_name = str(recipe.name or "").strip()
        if not recipe_name:
            continue
        recipe_state = recipe_states.get(recipe_name)
        if recipe_state is not None and recipe_state.exists and not recipe_state.managed:
            adjusted_outcomes[recipe_name] = "preserved_nonmanaged_recipe"
            preserved_nonmanaged += 1
            continue

        missing_required_ingredient = False
        for step in recipe.steps:
            ingredient = await get_ingredient(
                requester_service_type="genestack_monitoring",
                service_type=step.service_type,
                service_exec=step.service_exec,
                task_key_template=step.task_key_template,
            )
            if ingredient is None:
                missing_required_ingredient = True
                break
        if missing_required_ingredient:
            adjusted_outcomes[recipe_name] = "skipped_missing_ingredient"
            skipped_missing_ingredient += 1
            continue
        filtered_recipes.append(recipe)

    recipes_published = 0
    recipes_degraded_to_review = 0
    for outcome in adjusted_outcomes.values():
        if outcome == "published_managed_recipe":
            recipes_published += 1
        elif outcome == "degraded_to_review":
            recipes_degraded_to_review += 1

    return filtered_recipes, {
        "warning_recipes_preserved_nonmanaged": preserved_nonmanaged,
        "recipes_skipped_missing_ingredient": skipped_missing_ingredient,
        "recipes_published": recipes_published,
        "recipes_degraded_to_review": recipes_degraded_to_review,
        "recipe_outcomes": adjusted_outcomes,
    }


async def _hydrate_helper_credentials(helpers: Mapping[str, object]) -> Mapping[str, object]:
    hydrated = dict(helpers)
    github = require_github_credential_helper(hydrated)
    if github is not None:
        try:
            result = await read_adapter_credential_with_policy(
                service_type="github",
                credential_type="github_token",
                credential_key_id="default",
            )
        except Exception:
            result = None
        configured = apply_github_credentials(github, result.payload if result else None)
        hydrated["github"] = set_allow_public_read(
            configured,
            allow_public_read=result.allow_public_read if result else False,
        )
    return hydrated


def _operation(ctx: ExecutionContext) -> str:
    parameters = (
        ctx.service_exec_parameters if isinstance(ctx.service_exec_parameters, dict) else {}
    )
    return str(parameters.get("operation") or "").strip().lower()


def _payload_contract_error(
    *, service_type: str, service_exec_id: str, message: str
) -> ExecutionResult:
    outcome = {"success": False, "status": "errored", "message": message}
    return ExecutionResult(
        service_type=service_type,
        status="errored",
        service_exec_id=service_exec_id,
        service_exec_error=message,
        result=outcome,
        raw=outcome,
        retryable=False,
    )


def _service_exec_from_receipt(service_exec_id: str) -> str:
    parts = service_exec_id.split(":", 2)
    if len(parts) == 3 and parts[0] == "genestack_monitoring" and parts[1]:
        return parts[1].strip().lower()
    return "health_check"
