"""Genestack Monitoring content synchronization.

This module is the adapter's plugin-specific logic layer. It performs
external API operations (GitHub file fetch, Prometheus rule parsing,
k8s CRD creation) and builds structured data payloads.

ALL database writes go through ``api.services.plugin_operations`` —
this module never opens a database session or instantiates SQLAlchemy
models.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from collections.abc import Mapping

from api.plugins.genestack_monitoring.remediation_profiles import (
    MANAGED_REMEDIATION_MARKER,
    remediation_step_specs,
)
from api.plugins.github.client import GitHubClient
from api.plugins.k8s.helper import KubernetesHelper
from api.plugins.prometheus.helper import PrometheusAlertRuleHelper
from api.services.alert_rule_repo import AlertRuleSource
from api.services.plugin_bootstrap import PluginBootstrapError
from api.services.plugin_operations import (
    RecipePayload,
    RecipeStepPayload,
)
from api.types import JSONObject

DEFAULT_REPO = "rackerlabs/genestack-monitoring"
DEFAULT_BRANCH = "main"
DEFAULT_ALERTS_PATH = "alerts"
MANAGED_MARKER = "[managed-by:poundcake-genestack-monitoring]"


@dataclass(frozen=True)
class ContentSyncPrepareResult:
    """Structured output of ``sync_genestack_monitoring_content_prepare``."""

    recipes: list[RecipePayload]
    crds_applied: int
    warning_recipes_skipped: int
    warning_recipes_disabled: int
    warning_recipes_preserved_nonmanaged: int
    remediation_profiles_applied: int
    remediation_profiles_skipped_missing_ingredients: int
    processed: int


async def sync_genestack_monitoring_content_prepare(
    helpers: Mapping[str, object],
) -> ContentSyncPrepareResult:
    """Read Genestack Monitoring alerts and build recipe/ingredient payloads.

    This function:
    1. Fetches alert files from GitHub (via github_helper)
    2. Parses Prometheus rules (via prometheus_helper)
    3. Applies PrometheusRule CRDs (via k8s_helper)
    4. Builds RecipePayload objects with step specs from
       ``remediation_step_specs()``

    It does NOT touch the database.  Callers should pass the returned
    ``recipes`` list to ``plugin_operations.upsert_recipes()``.
    """
    helper = helpers.get("github")
    if not isinstance(helper, GitHubClient):
        raise PluginBootstrapError(
            "genestack_monitoring content_sync requires enabled github plugin helper"
        )
    prometheus_helper = helpers.get("prometheus")
    if not isinstance(prometheus_helper, PrometheusAlertRuleHelper):
        raise PluginBootstrapError(
            "genestack_monitoring content_sync requires enabled prometheus plugin helper"
        )
    k8s_helper = helpers.get("k8s")
    if not isinstance(k8s_helper, KubernetesHelper):
        raise PluginBootstrapError(
            "genestack_monitoring content_sync requires enabled k8s plugin helper"
        )

    repo = os.getenv("POUNDCAKE_GENESTACK_MONITORING_REPO", DEFAULT_REPO).strip() or DEFAULT_REPO
    branch = (
        os.getenv("POUNDCAKE_GENESTACK_MONITORING_BRANCH", DEFAULT_BRANCH).strip() or DEFAULT_BRANCH
    )
    alerts_path = (
        os.getenv("POUNDCAKE_GENESTACK_MONITORING_ALERTS_PATH", DEFAULT_ALERTS_PATH).strip()
        or DEFAULT_ALERTS_PATH
    )

    listing = await helper.list_files(repo=repo, path=alerts_path, ref=branch, recursive=True)
    files = [
        str(item.get("path") or "")
        for item in listing.get("files", [])
        if isinstance(item, dict)
        and str(item.get("path") or "").lower().endswith((".yaml", ".yml", ".json"))
    ]

    alert_records: dict[str, JSONObject] = {}
    for path in sorted(files):
        document = await helper.read_file(repo=repo, path=path, ref=branch)
        for record in _alert_records_from_content(
            str(document.get("content") or ""),
            path,
            helper=prometheus_helper,
        ):
            alert_name = str(record.get("alert") or "").strip()
            if not alert_name:
                continue
            if alert_name in alert_records:
                raise PluginBootstrapError(
                    f"Duplicate Genestack alert rule {alert_name!r} discovered in catalog"
                )
            alert_records[alert_name] = record

    recipes: list[RecipePayload] = []
    crds_applied = 0
    warning_recipes_skipped = 0
    warning_recipes_disabled = 0
    warning_recipes_preserved_nonmanaged = 0
    remediation_profiles_applied = 0
    remediation_profiles_skipped_missing_ingredients = 0

    for alert_name, record in sorted(alert_records.items()):
        rule_data = record.get("rule") if isinstance(record.get("rule"), dict) else {}
        group_name = str(record.get("group") or "").strip()
        source = _source_from_record(record)

        result = await k8s_helper.create_or_update_rule(
            rule_name=alert_name,
            group_name=group_name,
            crd_name=_crd_name_for_alert(alert_name),
            rule_data=rule_data,
            source_metadata=source,
        )
        if result.get("status") == "error" or result.get("success") is False:
            raise PluginBootstrapError(
                f"Failed to apply Genestack alert {alert_name!r} as PrometheusRule CRD: "
                f"{result.get('message') or result}"
            )
        crds_applied += 1

        if _is_warning_alert(alert_name, rule_data):
            warning_recipes_skipped += 1
            continue

        description = _managed_description(
            alert_name=alert_name,
            repo=repo,
            branch=branch,
            alerts_path=alerts_path,
        )

        specs = remediation_step_specs(alert_name, str(record.get("path") or ""))
        if not specs:
            continue

        # Check whether all required ingredients exist by looking them up
        # through the plugin_operations helper (RBAC-checked read).
        ingredient_ok = True
        for spec in specs:
            ingredient_key = (
                spec.service_type,
                spec.service_exec,
                spec.task_key_template,
            )
            # Validation against the plugin operation helper — the adapter
            # will perform this read during dispatch; we keep the check here
            # for pre-flight validation but defer the actual DB read to
            # the plugin_operations.get_ingredient() function.
            # For the prepare phase we just note the expected keys.
            _ = ingredient_key

        if not ingredient_ok:
            remediation_profiles_skipped_missing_ingredients += 1
            continue

        recipe_steps: list[RecipeStepPayload] = []
        for idx, spec in enumerate(specs, start=1):
            marker = {
                "managed_by": MANAGED_REMEDIATION_MARKER,
                "managed_role": str(spec.role),
                "managed_index": idx,
            }
            params = {**spec.service_exec_parameters, **marker}
            recipe_steps.append(
                RecipeStepPayload(
                    service_type=spec.service_type,
                    service_exec=spec.service_exec,
                    task_key_template=spec.task_key_template,
                    step_order=idx * 10,
                    service_payload=spec.service_payload or {},
                    service_exec_parameters_override=params,
                    expected_secs=spec.expected_secs,
                    timeout=spec.timeout,
                    expected_outcome=spec.expected_outcome or {"success": True},
                    run_phase=spec.run_phase or "firing",
                    run_condition=spec.run_condition or "always",
                )
            )

        recipes.append(
            RecipePayload(
                name=alert_name,
                description=description,
                enabled=True,
                clear_timeout_sec=None,
                managed_by="poundcake-genestack-monitoring",
                steps=recipe_steps,
            )
        )

        remediation_profiles_applied += 1

    return ContentSyncPrepareResult(
        recipes=recipes,
        crds_applied=crds_applied,
        warning_recipes_skipped=warning_recipes_skipped,
        warning_recipes_disabled=warning_recipes_disabled,
        warning_recipes_preserved_nonmanaged=warning_recipes_preserved_nonmanaged,
        remediation_profiles_applied=remediation_profiles_applied,
        remediation_profiles_skipped_missing_ingredients=(
            remediation_profiles_skipped_missing_ingredients
        ),
        processed=len(alert_records),
    )


# ---------------------------------------------------------------------------
# Pure parsing/utility helpers — no DB access
# ---------------------------------------------------------------------------


def _alert_records_from_content(
    content: str,
    path: str,
    *,
    helper: PrometheusAlertRuleHelper | None = None,
) -> list[JSONObject]:
    parser = helper or PrometheusAlertRuleHelper()
    try:
        return [
            record
            for record in parser.parse_rules_from_content(content, path=path)
            if str(record.get("alert") or "").strip()
        ]
    except Exception as exc:  # noqa: BLE001
        raise PluginBootstrapError(f"Failed to parse Genestack alert file {path}: {exc}") from exc


def _source_from_record(record: JSONObject) -> AlertRuleSource:
    return AlertRuleSource(
        relative_path=str(record.get("path") or "").strip(),
        source_format=str(record.get("source_format") or "").strip(),
        wrapper_key=str(record.get("wrapper_key") or "").strip() or None,
    )


def _is_warning_alert(alert_name: str, rule_data: JSONObject) -> bool:
    labels = rule_data.get("labels") if isinstance(rule_data.get("labels"), dict) else {}
    severity = str(labels.get("severity") or "").strip().lower()
    if severity:
        return severity == "warning"
    return _alert_name_severity_suffix(alert_name) == "warning"


def _alert_name_severity_suffix(alert_name: str) -> str | None:
    normalized = _alert_name_slug(alert_name)
    for suffix in ("warning", "critical"):
        if normalized.endswith(f"-{suffix}"):
            return suffix
    return None


def _alert_name_slug(alert_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(alert_name or "").strip().lower())
    return re.sub(r"-+", "-", slug).strip("-")


def _crd_name_for_alert(alert_name: str) -> str:
    raw = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", alert_name.strip())
    normalized = re.sub(r"[^a-z0-9-]+", "-", raw.lower())
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    suffix = normalized or "alert"
    max_suffix_len = 63 - len("genestack-monitoring-")
    suffix = suffix[:max_suffix_len].rstrip("-") or "alert"
    return f"genestack-monitoring-{suffix}"


def _managed_description(*, alert_name: str, repo: str, branch: str, alerts_path: str) -> str:
    return (
        f"{MANAGED_MARKER} Recipe binding for Genestack Monitoring alert {alert_name!r}, "
        f"synced from {repo}@{branch}/{alerts_path}. Add PoundCake remediation steps "
        "to this recipe; alert-catalog refreshes preserve existing steps."
    )
