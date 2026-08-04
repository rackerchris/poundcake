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
from collections.abc import Mapping
from dataclasses import dataclass

from api.plugins.genestack_monitoring.helper_contracts import (
    require_github_reader_helper,
    require_k8s_helper,
    require_prometheus_helper,
)
from api.plugins.genestack_monitoring.remediation_profiles import (
    MANAGED_REMEDIATION_MARKER,
    remediation_step_specs,
)
from api.plugins.contract import ServicePluginContractError
from api.services.alert_rule_repo import (
    AlertRuleSource,
    load_alert_rule_sources_from_annotations,
)
from api.services.plugin_bootstrap import PluginBootstrapError
from api.services.plugin_operations import (
    RecipePayload,
    RecipeStepPayload,
)
from api.types import JSONObject

DEFAULT_REPO = "rackerchris/genestack-monitoring"
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
    recipes_published: int
    recipes_degraded_to_review: int
    recipes_skipped_missing_capability: int
    recipes_skipped_missing_ingredient: int
    recipe_outcomes: dict[str, str]
    remediation_profiles_applied: int
    remediation_profiles_skipped_missing_ingredients: int
    processed: int


@dataclass(frozen=True)
class AlertExportPrepareResult:
    """Structured result for outbound Genestack alert export."""

    repo: str
    base_branch: str
    branch: str
    files: dict[str, str]
    message: str
    skipped: dict[str, int]
    warnings: list[str]
    selected_rule: str


async def sync_genestack_monitoring_content_prepare(
    helpers: Mapping[str, object],
    *,
    capabilities: list[JSONObject] | None = None,
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
    helper = require_github_reader_helper(
        helpers,
        operation="genestack_monitoring content_sync",
    )
    prometheus_helper = require_prometheus_helper(
        helpers,
        operation="genestack_monitoring content_sync",
    )
    k8s_helper = require_k8s_helper(
        helpers,
        operation="genestack_monitoring content_sync",
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
    recipes_published = 0
    recipes_degraded_to_review = 0
    recipes_skipped_missing_capability = 0
    recipes_skipped_missing_ingredient = 0
    recipe_outcomes: dict[str, str] = {}
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

        specs = remediation_step_specs(
            alert_name,
            str(record.get("path") or ""),
            rule_data,
            capabilities=capabilities or [],
        )
        if not specs:
            recipes_skipped_missing_capability += 1
            recipe_outcomes[alert_name] = "skipped_missing_capability"
            continue

        recipe_steps: list[RecipeStepPayload] = []
        for idx, spec in enumerate(specs, start=1):
            if spec.service_payload is not None and not isinstance(spec.service_payload, dict):
                raise ServicePluginContractError(
                    "service_payload must be an object when provided"
                )
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
                    service_payload={} if spec.service_payload is None else spec.service_payload,
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

        outcome = _recipe_publication_outcome_for_specs(specs)
        recipe_outcomes[alert_name] = outcome
        if outcome == "published_managed_recipe":
            recipes_published += 1
        else:
            recipes_degraded_to_review += 1
        remediation_profiles_applied += 1

    return ContentSyncPrepareResult(
        recipes=recipes,
        crds_applied=crds_applied,
        warning_recipes_skipped=warning_recipes_skipped,
        warning_recipes_disabled=warning_recipes_disabled,
        warning_recipes_preserved_nonmanaged=warning_recipes_preserved_nonmanaged,
        recipes_published=recipes_published,
        recipes_degraded_to_review=recipes_degraded_to_review,
        recipes_skipped_missing_capability=recipes_skipped_missing_capability,
        recipes_skipped_missing_ingredient=recipes_skipped_missing_ingredient,
        recipe_outcomes=recipe_outcomes,
        remediation_profiles_applied=remediation_profiles_applied,
        remediation_profiles_skipped_missing_ingredients=(
            remediation_profiles_skipped_missing_ingredients
        ),
        processed=len(alert_records),
    )


async def export_genestack_alert_updates_prepare(
    helpers: Mapping[str, object],
    *,
    crd_name: str,
    group_name: str,
    rule_name: str,
    namespace: str = "",
) -> AlertExportPrepareResult:
    """Build an in-memory repo update for one Genestack-managed alert rule."""
    require_github_reader_helper(helpers, operation="genestack_monitoring alert export")
    prometheus_helper = require_prometheus_helper(
        helpers,
        operation="genestack_monitoring alert export",
    )
    k8s_helper = require_k8s_helper(
        helpers,
        operation="genestack_monitoring alert export",
    )

    repo = os.getenv("POUNDCAKE_GENESTACK_MONITORING_REPO", DEFAULT_REPO).strip() or DEFAULT_REPO
    base_branch = (
        os.getenv("POUNDCAKE_GENESTACK_MONITORING_BRANCH", DEFAULT_BRANCH).strip() or DEFAULT_BRANCH
    )
    alerts_path = (
        os.getenv("POUNDCAKE_GENESTACK_MONITORING_ALERTS_PATH", DEFAULT_ALERTS_PATH).strip()
        or DEFAULT_ALERTS_PATH
    ).strip("/")

    rule_records = _rule_records_from_crds(await k8s_helper.list_prometheus_rules())
    selected = next(
        (
            record
            for record in rule_records
            if record["crd_name"] == crd_name
            and record["group_name"] == group_name
            and record["rule_name"] == rule_name
        ),
        None,
    )
    if selected is None:
        raise PluginBootstrapError(
            f"PrometheusRule {crd_name}/{group_name}/{rule_name} was not found in current CRD state"
        )

    selected_namespace = str(selected.get("namespace") or "").strip()
    if namespace.strip() and selected_namespace and selected_namespace != namespace.strip():
        raise PluginBootstrapError(
            f"Selected rule is in namespace {selected_namespace}, not requested namespace {namespace}"
        )

    source = selected.get("source")
    if not isinstance(source, AlertRuleSource):
        raise PluginBootstrapError(
            f"Rule {rule_name!r} does not have Genestack source metadata and cannot be exported"
        )
    relative_path = source.relative_path.strip()
    if not relative_path or not (
        relative_path == alerts_path or relative_path.startswith(f"{alerts_path}/")
    ):
        raise PluginBootstrapError(
            f"Rule {rule_name!r} is not mapped to the configured Genestack alerts path"
        )

    records_for_file = [
        (
            str(record["group_name"]),
            dict(record["rule_data"]),
            record["source"],
        )
        for record in rule_records
        if isinstance(record.get("source"), AlertRuleSource)
        and record["source"].relative_path == relative_path
    ]
    if not records_for_file:
        raise PluginBootstrapError(
            f"No current CRD-backed rules were found for Genestack source file {relative_path}"
        )

    document = prometheus_helper.render_document(records_for_file, relative_path=relative_path)
    content = prometheus_helper.dump_document(document, relative_path=relative_path)
    branch = _export_branch_name(rule_name)
    warnings: list[str] = []
    skipped = {
        "missing_source_metadata": len(
            [record for record in rule_records if record.get("source") is None]
        ),
        "non_genestack_rules": len(
            [
                record
                for record in rule_records
                if isinstance(record.get("source"), AlertRuleSource)
                and not (
                    record["source"].relative_path == alerts_path
                    or record["source"].relative_path.startswith(f"{alerts_path}/")
                )
            ]
        ),
    }
    if skipped["missing_source_metadata"]:
        warnings.append("Some live rules were skipped because they do not have source metadata.")

    return AlertExportPrepareResult(
        repo=repo,
        base_branch=base_branch,
        branch=branch,
        files={relative_path: content},
        message=(
            f"Prepared Genestack alert update for {rule_name} from {selected_namespace or namespace or 'configured namespace'}."
        ),
        skipped=skipped,
        warnings=warnings,
        selected_rule=rule_name,
    )


# ---------------------------------------------------------------------------
# Pure parsing/utility helpers — no DB access
# ---------------------------------------------------------------------------


def _alert_records_from_content(
    content: str,
    path: str,
    *,
    helper: object,
) -> list[JSONObject]:
    parser = require_prometheus_helper(
        {"prometheus": helper},
        operation=f"genestack_monitoring parse {path}",
    )
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


def _rule_records_from_crds(crds: list[JSONObject]) -> list[JSONObject]:
    records: list[JSONObject] = []
    for crd in crds:
        metadata = crd.get("metadata") if isinstance(crd.get("metadata"), dict) else {}
        spec = crd.get("spec") if isinstance(crd.get("spec"), dict) else {}
        groups = spec.get("groups") if isinstance(spec.get("groups"), list) else []
        sources = load_alert_rule_sources_from_annotations(metadata.get("annotations"))
        for group in groups:
            if not isinstance(group, dict):
                continue
            group_name = str(group.get("name") or "").strip()
            rules = group.get("rules") if isinstance(group.get("rules"), list) else []
            for raw_rule in rules:
                if not isinstance(raw_rule, dict):
                    continue
                rule_name = str(raw_rule.get("alert") or raw_rule.get("record") or "").strip()
                if not rule_name:
                    continue
                records.append(
                    {
                        "namespace": str(metadata.get("namespace") or "").strip(),
                        "crd_name": str(metadata.get("name") or "").strip(),
                        "group_name": group_name,
                        "rule_name": rule_name,
                        "rule_data": dict(raw_rule),
                        "source": sources.get(rule_name),
                    }
                )
    return records


def _export_branch_name(rule_name: str) -> str:
    slug = _alert_name_slug(rule_name)[:48].strip("-") or "alert-rule"
    return f"poundcake/genestack-alert-update-{slug}"


def _recipe_publication_outcome_for_specs(specs: list[RemediationStepSpec]) -> str:
    action_steps = [spec for spec in specs if str(spec.role) == "action_alert"]
    if not action_steps:
        return "degraded_to_review"
    if all(spec.service_type == "bakery" and spec.service_exec == "communication" for spec in action_steps):
        return "degraded_to_review"
    return "published_managed_recipe"


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
