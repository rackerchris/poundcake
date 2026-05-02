"""Unit tests for Genestack Monitoring content sync."""

from __future__ import annotations

import asyncio
from pathlib import Path
from collections.abc import Iterator

import pytest
import yaml

from api.models.models import Ingredient, Recipe, RecipeIngredient
from api.plugins import catalog
from api.plugins.contract import validate_payload_schema
from api.plugins.genestack_monitoring import adapter as genestack_adapter
from api.plugins.genestack_monitoring import content_sync as genestack_content_sync
from api.plugins.genestack_monitoring.adapter import GenestackMonitoringExecutionAdapter
from api.plugins.genestack_monitoring.content_sync import (
    MANAGED_MARKER,
    _alert_names_from_content,
    _crd_name_for_alert,
    sync_genestack_monitoring_content,
)
from api.plugins.genestack_monitoring.remediation_profiles import (
    BLACKBOX_ALERT_GROUPS,
    CERTIFICATE_DIAGNOSTIC_GROUPS,
    DAEMONSET_DIAGNOSTIC_GROUPS,
    DAEMONSET_REMEDIATION_GROUPS,
    DEPLOYMENT_REMEDIATION_GROUPS,
    DIAGNOSTIC_ONLY_KUBERNETES_GROUPS,
    ETCD_ALERT_GROUPS,
    KUBERNETES_ALERT_GROUPS,
    NODE_TRIAGE_DIAGNOSTIC_GROUPS,
    PDB_HPA_DIAGNOSTIC_GROUPS,
    POD_REMEDIATION_GROUPS,
    PV_DIAGNOSTIC_GROUPS,
    PVC_DIAGNOSTIC_GROUPS,
    RemediationStepSpec,
    SERVICE_DIAGNOSTIC_GROUPS,
    STATEFULSET_DIAGNOSTIC_GROUPS,
    STATEFULSET_REMEDIATION_GROUPS,
    etcd_workflow_name_for_group,
    remediation_step_specs,
)
from api.plugins.github.client import GitHubClient
from api.plugins.genestack_monitoring.plugin import get_plugin
from api.plugins.genestack_monitoring.templates import (
    GENESTACK_MONITORING_INGREDIENT_TEMPLATES,
    GENESTACK_MONITORING_RECIPE_TEMPLATES,
    GENESTACK_MONITORING_SCHEDULED_TASKS,
)
from api.plugins.k8s.helper import KubernetesHelper
from api.plugins.manifest import ServicePlugin, ServicePluginManifestError, validate_service_plugin
from api.plugins.prometheus.helper import PrometheusAlertRuleHelper
from api.plugins.types import ExecutionContext
from api.schemas.schemas import IngredientCreate, ScheduledTaskCreate
from api.services import plugin_bootstrap
from api.services.plugin_bootstrap import PluginBootstrapError


def test_genestack_monitoring_manifest_is_community_tier() -> None:
    plugin = validate_service_plugin(get_plugin(), directory_name="genestack_monitoring")

    assert plugin.service_type == "genestack_monitoring"
    assert plugin.plugin_tier == "community"
    assert plugin.plugin_log_key is None
    assert plugin.bootstrap_factory is None


def test_bootstrap_factory_rejects_external_helper_dependencies() -> None:
    from api.plugins.github.plugin import get_plugin as get_github_plugin

    github = get_github_plugin()

    async def bootstrap(_db: object, _helpers: dict[str, object]) -> dict[str, object]:
        return {"processed": 1}

    plugin = ServicePlugin(
        service_type="consumer",
        adapter_factory=github.adapter_factory,
        required_helper_capabilities={"github": ("repo.read",)},
        bootstrap_factory=bootstrap,
        allow_directory_mismatch=True,
    )

    with pytest.raises(ServicePluginManifestError, match="external helper dependencies"):
        validate_service_plugin(plugin, directory_name="consumer")


def test_genestack_monitoring_templates_register_content_sync_task() -> None:
    assert {template["service_exec"] for template in GENESTACK_MONITORING_INGREDIENT_TEMPLATES} == {
        "content_sync",
        "health_check",
    }
    assert {recipe["name"] for recipe in GENESTACK_MONITORING_RECIPE_TEMPLATES} == {
        "plugin-content-sync:genestack_monitoring",
        "plugin-health-check:genestack_monitoring",
    }
    assert {task["task_key"] for task in GENESTACK_MONITORING_SCHEDULED_TASKS} == {
        "plugin-content-sync:genestack_monitoring",
        "plugin-health-check:genestack_monitoring",
    }
    catalog_task = next(
        task
        for task in GENESTACK_MONITORING_SCHEDULED_TASKS
        if task["task_key"] == "plugin-content-sync:genestack_monitoring"
    )
    assert catalog_task["task_type"] == "service_execution"
    assert catalog_task["service_exec"] == "content_sync"
    assert catalog_task["task_parameters"]["operation"] == "sync_content"
    for template in GENESTACK_MONITORING_INGREDIENT_TEMPLATES:
        validate_payload_schema(template["payload_schema"])


def test_genestack_monitoring_content_sync_scheduled_task_holds_plugin_contract() -> None:
    ingredients = [
        IngredientCreate.model_validate(template)
        for template in GENESTACK_MONITORING_INGREDIENT_TEMPLATES
    ]
    ingredient_rows = {
        plugin_bootstrap._ingredient_identity(ingredient.model_dump(mode="json")): ingredient
        for ingredient in ingredients
    }
    catalog_task = next(
        task
        for task in GENESTACK_MONITORING_SCHEDULED_TASKS
        if task["task_key"] == "plugin-content-sync:genestack_monitoring"
    )
    payload = ScheduledTaskCreate.model_validate(catalog_task)

    plugin_bootstrap._validate_scheduled_service_execution(payload, ingredient_rows)


def test_genestack_kubernetes_remediation_profiles_cover_all_alert_groups() -> None:
    assert KUBERNETES_ALERT_GROUPS
    assert POD_REMEDIATION_GROUPS < KUBERNETES_ALERT_GROUPS
    assert DEPLOYMENT_REMEDIATION_GROUPS < KUBERNETES_ALERT_GROUPS
    assert STATEFULSET_REMEDIATION_GROUPS < KUBERNETES_ALERT_GROUPS
    assert STATEFULSET_DIAGNOSTIC_GROUPS < KUBERNETES_ALERT_GROUPS
    assert DAEMONSET_REMEDIATION_GROUPS < KUBERNETES_ALERT_GROUPS
    assert DAEMONSET_DIAGNOSTIC_GROUPS < KUBERNETES_ALERT_GROUPS
    assert CERTIFICATE_DIAGNOSTIC_GROUPS < KUBERNETES_ALERT_GROUPS
    assert DIAGNOSTIC_ONLY_KUBERNETES_GROUPS < KUBERNETES_ALERT_GROUPS

    for group in KUBERNETES_ALERT_GROUPS:
        specs = remediation_step_specs(f"{group}-critical", f"alerts/kubernetes/{group}.yaml")
        assert [spec.role for spec in specs] == [
            "verify_before_evidence",
            "gather_evidence",
            "verify_before_action",
            "action_alert",
            "communicate",
        ]
        assert specs[0].service_type == "alertmanager"
        assert specs[0].service_exec_parameters["guard_role"] == "remediation_precondition"
        assert specs[2].service_exec_parameters["false_outcome"] == (
            "cancel_downstream_no_remediation"
        )
        assert specs[-1].service_type == "bakery"


def test_genestack_kubernetes_remediation_profiles_choose_expected_adapter_boundaries() -> None:
    pod_specs = remediation_step_specs("kube-pod-crash-looping-critical")
    assert pod_specs[1].service_exec == "workload_triage"
    assert pod_specs[3].service_exec == "pod_action"
    assert pod_specs[3].service_exec_parameters["operation"] == "delete"

    deployment_specs = remediation_step_specs("kube-deployment-rollout-stuck-warning")
    assert deployment_specs[1].service_exec_parameters["operation"] == "workload_status"
    assert deployment_specs[3].service_exec == "deployment_action"
    assert deployment_specs[3].service_exec_parameters["operation"] == "rollout_restart"

    statefulset_specs = remediation_step_specs("kube-statefulset-update-not-rolled-out-critical")
    assert statefulset_specs[1].service_exec == "workload_triage"
    assert statefulset_specs[1].service_payload["kind"] == "StatefulSet"
    assert statefulset_specs[1].service_payload["name"] == "{{ order.labels.statefulset }}"
    assert statefulset_specs[3].service_type == "k8s"
    assert statefulset_specs[3].service_exec == "workload_action"
    assert statefulset_specs[3].service_payload["kind"] == "StatefulSet"
    assert statefulset_specs[3].service_exec_parameters["operation"] == "rollout_restart"

    daemonset_specs = remediation_step_specs("kube-daemonset-rollout-stuck-critical")
    assert daemonset_specs[1].service_exec == "workload_triage"
    assert daemonset_specs[1].service_payload["kind"] == "DaemonSet"
    assert daemonset_specs[1].service_payload["name"] == "{{ order.labels.daemonset }}"
    assert daemonset_specs[3].service_type == "k8s"
    assert daemonset_specs[3].service_exec == "workload_action"
    assert daemonset_specs[3].service_payload["kind"] == "DaemonSet"
    assert daemonset_specs[3].service_exec_parameters["operation"] == "rollout_restart"

    diagnostic_statefulset_specs = remediation_step_specs(
        "kube-statefulset-replicas-mismatch-critical"
    )
    assert diagnostic_statefulset_specs[1].service_exec == "workload_triage"
    assert diagnostic_statefulset_specs[1].service_payload["kind"] == "StatefulSet"
    assert diagnostic_statefulset_specs[3].service_type == "bakery"

    diagnostic_daemonset_specs = remediation_step_specs("kube-daemonset-misscheduled-critical")
    assert diagnostic_daemonset_specs[1].service_exec == "workload_triage"
    assert diagnostic_daemonset_specs[1].service_payload["kind"] == "DaemonSet"
    assert diagnostic_daemonset_specs[3].service_type == "bakery"

    certificate_specs = remediation_step_specs("kubelet-server-certificate-expiration-critical")
    assert certificate_specs[1].service_exec == "workload_triage"
    assert certificate_specs[1].service_exec_parameters["operation"] == "certificate_diagnostics"
    assert certificate_specs[1].service_payload == {"limit": 20}
    assert certificate_specs[3].service_type == "bakery"


def test_genestack_kubernetes_control_plane_profiles_are_diagnostics_only() -> None:
    assert DIAGNOSTIC_ONLY_KUBERNETES_GROUPS == {
        "kube-controller-manager-down",
        "kube-proxy-down",
        "kube-scheduler-down",
        "target-down",
    }

    for group in DIAGNOSTIC_ONLY_KUBERNETES_GROUPS - SERVICE_DIAGNOSTIC_GROUPS:
        specs = remediation_step_specs(f"{group}-critical")

        assert [spec.role for spec in specs] == [
            "verify_before_evidence",
            "gather_evidence",
            "verify_before_action",
            "action_alert",
            "communicate",
        ]
        assert [spec.service_type for spec in specs] == [
            "alertmanager",
            "k8s",
            "alertmanager",
            "bakery",
            "bakery",
        ]
        assert specs[1].service_exec == "node_triage"
        assert specs[1].service_exec_parameters["operation"] == "list_nodes"
        assert specs[3].service_exec_parameters["adapter_extension_candidate"] == "kubernetes"


def test_genestack_node_profiles_use_targeted_node_pressure_evidence() -> None:
    assert NODE_TRIAGE_DIAGNOSTIC_GROUPS == {
        "kube-node-eviction",
        "kube-node-not-ready",
        "kube-node-pressure",
        "kube-node-readiness-flapping",
        "kube-node-unreachable",
        "kubelet-pod-startup-latency-high",
        "kubelet-too-many-pods",
    }

    for group in NODE_TRIAGE_DIAGNOSTIC_GROUPS:
        specs = remediation_step_specs(f"{group}-critical")

        assert specs[1].service_type == "k8s"
        assert specs[1].service_exec == "node_triage"
        assert specs[1].service_exec_parameters["operation"] == "node_pressure"
        assert specs[1].service_payload == {
            "node": "{{ order.labels.node }}",
            "limit": 20,
        }
        assert specs[3].role == "action_alert"


def test_genestack_storage_autoscaling_and_service_profiles_use_k8s_evidence() -> None:
    assert PVC_DIAGNOSTIC_GROUPS == {
        "kube-persistent-volume-filling-up",
        "kube-persistent-volume-inodes-filling-up",
    }
    assert PV_DIAGNOSTIC_GROUPS == {"kube-persistent-volume-errors"}
    assert PDB_HPA_DIAGNOSTIC_GROUPS == {
        "kube-hpa-maxed-out",
        "kube-hpa-replicas-mismatch",
        "kube-pdb-not-enough-healthy-pods",
    }
    assert SERVICE_DIAGNOSTIC_GROUPS == {"target-down"}

    pvc_specs = remediation_step_specs("kube-persistent-volume-filling-up-critical")
    assert pvc_specs[1].service_exec == "workload_triage"
    assert pvc_specs[1].service_exec_parameters["operation"] == "pvc_diagnostics"
    assert pvc_specs[1].service_payload == {
        "namespace": "{{ order.labels.namespace }}",
        "persistentvolumeclaim": "{{ order.labels.persistentvolumeclaim }}",
    }
    assert pvc_specs[3].service_type == "bakery"

    pv_specs = remediation_step_specs("kube-persistent-volume-errors-critical")
    assert pv_specs[1].service_exec_parameters["operation"] == "pvc_diagnostics"
    assert pv_specs[1].service_payload == {
        "persistentvolume": "{{ order.labels.persistentvolume }}",
    }
    assert pv_specs[3].service_type == "bakery"

    hpa_specs = remediation_step_specs("kube-hpa-maxed-out-critical")
    assert hpa_specs[1].service_exec_parameters["operation"] == "pdb_hpa_diagnostics"
    assert hpa_specs[1].service_payload == {
        "namespace": "{{ order.labels.namespace }}",
        "horizontalpodautoscaler": "{{ order.labels.horizontalpodautoscaler }}",
    }
    assert hpa_specs[3].service_type == "bakery"

    pdb_specs = remediation_step_specs("kube-pdb-not-enough-healthy-pods-critical")
    assert pdb_specs[1].service_exec_parameters["operation"] == "pdb_hpa_diagnostics"
    assert pdb_specs[1].service_payload == {
        "namespace": "{{ order.labels.namespace }}",
        "poddisruptionbudget": "{{ order.labels.poddisruptionbudget }}",
    }
    assert pdb_specs[3].service_type == "bakery"

    service_specs = remediation_step_specs("target-down-critical")
    assert service_specs[1].service_exec_parameters["operation"] == "service_diagnostics"
    assert service_specs[1].service_payload == {
        "namespace": "{{ order.labels.namespace }}",
        "service": "{{ order.labels.job }}",
    }
    assert service_specs[3].service_type == "bakery"


def test_genestack_blackbox_service_down_profile_uses_alertmanager_and_stackstorm() -> None:
    assert BLACKBOX_ALERT_GROUPS == {"blackbox-service-down"}

    specs = remediation_step_specs(
        "blackbox-service-down-critical",
        "alerts/blackbox/blackbox-service-down.yaml",
    )

    assert [spec.role for spec in specs] == [
        "verify_before_evidence",
        "gather_alertmanager_evidence",
        "gather_endpoint_evidence",
        "verify_before_action",
        "action_alert",
        "verify_recovery",
        "communicate",
    ]
    assert specs[0].service_type == "alertmanager"
    assert specs[1].task_key_template == "alertmanager-inspect"
    assert specs[1].service_exec_parameters["operation"] == "list_alerts"
    assert specs[2].service_type == "stackstorm"
    assert specs[2].service_payload["workflow_ref"] == ("poundcake.blackbox_service_down_evidence")
    assert specs[3].service_exec_parameters["false_outcome"] == ("cancel_downstream_no_remediation")
    assert specs[4].service_payload["workflow_ref"] == (
        "poundcake.blackbox_service_down_remediation"
    )
    assert specs[5].service_payload["workflow_ref"] == (
        "poundcake.blackbox_service_down_verify_recovery"
    )


def test_genestack_etcd_remediation_profiles_cover_all_alert_groups() -> None:
    assert ETCD_ALERT_GROUPS == {
        "etcd-database-high-fragmentation-ratio",
        "etcd-database-quota-low-space",
        "etcd-excessive-database-growth",
        "etcd-grpc-requests-slow",
        "etcd-high-commit-durations",
        "etcd-high-fsync-durations",
        "etcd-high-number-of-failed-grpc-requests",
        "etcd-high-number-of-failed-proposals",
        "etcd-high-number-of-leader-changes",
        "etcd-insufficient-members",
        "etcd-member-communication-slow",
        "etcd-members-down",
        "etcd-no-leader",
    }

    for group in ETCD_ALERT_GROUPS:
        specs = remediation_step_specs(f"{group}-critical", f"alerts/etcd/{group}.yaml")
        assert [spec.role for spec in specs] == [
            "verify_before_evidence",
            "gather_alertmanager_evidence",
            "gather_etcd_evidence",
            "verify_before_action",
            "action_alert",
            "verify_recovery",
            "communicate",
        ]
        assert [spec.service_type for spec in specs] == [
            "alertmanager",
            "alertmanager",
            "stackstorm",
            "alertmanager",
            "stackstorm",
            "stackstorm",
            "bakery",
        ]
        assert specs[1].service_exec_parameters["operation"] == "list_alerts"
        assert specs[2].task_key_template == "stackstorm-workflow-execution"
        assert specs[3].service_exec_parameters["false_outcome"] == (
            "cancel_downstream_no_remediation"
        )
        assert specs[4].service_exec_parameters["mutation_family"] == ("etcd_operator_review")


def test_genestack_generic_profiles_use_cross_adapter_evidence_and_bakery_action() -> None:
    specs = remediation_step_specs(
        "rabbitmq-memory-alarm-critical",
        "alerts/rabbitmq/rabbitmq-memory-alarm.yaml",
    )

    assert [spec.role for spec in specs] == [
        "verify_before_evidence",
        "gather_alertmanager_evidence",
        "gather_prometheus_evidence",
        "gather_source_rule_evidence",
        "verify_before_action",
        "action_alert",
        "communicate",
    ]
    assert [spec.service_type for spec in specs] == [
        "alertmanager",
        "alertmanager",
        "prometheus",
        "github",
        "alertmanager",
        "bakery",
        "bakery",
    ]
    assert specs[2].service_exec_parameters["operation"] == "alert_evidence"
    assert specs[2].service_payload["query"] == (
        'ALERTS{alertname="rabbitmq-memory-alarm-critical"}'
    )
    assert specs[5].service_exec_parameters["adapter_extension_candidate"] == "rabbitmq"


def test_genestack_generic_prometheus_evidence_uses_alert_expression() -> None:
    specs = remediation_step_specs(
        "rabbitmq-memory-alarm-critical",
        "alerts/rabbitmq/rabbitmq-memory-alarm.yaml",
        {"expr": 'rabbitmq_node_mem_alarm{job="rabbitmq"} > 0'},
    )

    assert specs[2].service_type == "prometheus"
    assert specs[2].service_exec_parameters["operation"] == "alert_evidence"
    assert specs[2].service_payload["query"] == 'rabbitmq_node_mem_alarm{job="rabbitmq"} > 0'


def test_genestack_local_alert_catalog_critical_rules_have_recipe_shape() -> None:
    root = Path("/Users/chris.breu/code/flex/genestack-monitoring/alerts")
    if not root.exists():
        pytest.skip("local Genestack Monitoring alert catalog is not checked out")

    missing: list[str] = []
    for path in sorted(root.rglob("*.y*ml")):
        document = yaml.safe_load(path.read_text()) or {}
        for rule in _alert_rules_from_document(document):
            labels = rule.get("labels") if isinstance(rule.get("labels"), dict) else {}
            if str(labels.get("severity") or "").lower() != "critical":
                continue
            alert_name = str(rule.get("alert") or "")
            specs = remediation_step_specs(alert_name, f"alerts/{path.relative_to(root)}")
            roles = [spec.role for spec in specs]
            if not specs or roles[0] != "verify_before_evidence" or "action_alert" not in roles:
                missing.append(alert_name)
                continue
            assert "verify_before_action" in roles
            assert roles[-1] == "communicate"
            assert specs[-1].service_type == "bakery"

    assert missing == []


def test_genestack_etcd_remediation_profiles_use_expected_workflow_refs() -> None:
    specs = remediation_step_specs("etcd-no-leader-critical", "alerts/etcd/etcd-no-leader.yaml")

    assert etcd_workflow_name_for_group("etcd-no-leader", "evidence") == (
        "etcd_etcd_no_leader_evidence"
    )
    assert specs[2].service_payload["workflow_ref"] == "poundcake.etcd_etcd_no_leader_evidence"
    assert specs[4].service_payload["workflow_ref"] == ("poundcake.etcd_etcd_no_leader_remediation")
    assert specs[5].service_payload["workflow_ref"] == (
        "poundcake.etcd_etcd_no_leader_verify_recovery"
    )

    quota_specs = remediation_step_specs(
        "etcd-database-quota-low-space-warning",
        "alerts/etcd/etcd-database-quota-low-space.yaml",
    )
    assert quota_specs[2].service_payload["workflow_ref"] == (
        "poundcake.etcd_etcd_database_quota_low_space_evidence"
    )


def _alert_rules_from_document(document: object) -> Iterator[dict[str, object]]:
    if isinstance(document, dict):
        if str(document.get("alert") or "").strip():
            yield document
        for value in document.values():
            yield from _alert_rules_from_document(value)
    elif isinstance(document, list):
        for value in document:
            yield from _alert_rules_from_document(value)


def test_genestack_monitoring_adapter_validates_content_sync_operation() -> None:
    adapter = GenestackMonitoringExecutionAdapter()

    assert (
        adapter.validate(
            ExecutionContext(
                service_type="genestack_monitoring",
                service_exec="content_sync",
                req_id="unit-test",
                service_payload={},
                service_exec_parameters={
                    "operation": "sync_content",
                    "allowed_operations": ["sync_content"],
                },
            )
        )
        is None
    )
    assert (
        adapter.validate(
            ExecutionContext(
                service_type="genestack_monitoring",
                service_exec="content_sync",
                req_id="unit-test",
                service_payload={},
                service_exec_parameters={
                    "operation": "write_alert_catalog",
                    "allowed_operations": ["sync_content"],
                },
            )
        )
        == "genestack_monitoring content_sync operation must be: sync_content"
    )


@pytest.mark.asyncio
async def test_genestack_content_sync_dispatch_starts_work_and_poll_only_reads_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    async def bootstrap(db: object, helpers: dict[str, object]) -> dict[str, object]:
        calls.append((db, helpers))
        await asyncio.sleep(0)
        return {"processed": 1, "crds_applied": 1}

    monkeypatch.setattr(genestack_adapter, "SessionLocal", lambda: _FakeSession())
    adapter = GenestackMonitoringExecutionAdapter(
        bootstrap_func=bootstrap,
        helper_factory=lambda: {"helpers": "loaded"},
    )
    ctx = ExecutionContext(
        service_type="genestack_monitoring",
        service_exec="content_sync",
        req_id="unit-test",
        service_payload={},
        service_exec_parameters={"operation": "sync_content"},
    )

    dispatched = await adapter.dispatch(ctx)
    early_status = await adapter.poll(ctx, dispatched.service_exec_id or "")

    assert dispatched.status == "running"
    assert early_status.status == "running"

    task = adapter._content_sync_tasks[dispatched.service_exec_id or ""]
    await task
    completed_status = await adapter.poll(ctx, dispatched.service_exec_id or "")

    assert len(calls) == 1
    assert completed_status.status == "succeeded"
    assert completed_status.raw == {
        "success": True,
        "status": "succeeded",
        "message": "Genestack Monitoring content sync complete",
        "service_exec": "content_sync",
        "service_exec_id": dispatched.service_exec_id,
        "stats": {"processed": 1, "crds_applied": 1},
    }


@pytest.mark.asyncio
async def test_genestack_content_sync_poll_does_not_run_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def bootstrap(_db: object, _helpers: dict[str, object]) -> dict[str, object]:
        raise AssertionError("poll must not perform content sync work")

    monkeypatch.setattr(genestack_adapter, "SessionLocal", lambda: _FakeSession())
    adapter = GenestackMonitoringExecutionAdapter(
        bootstrap_func=bootstrap,
        helper_factory=lambda: {"helpers": "loaded"},
    )
    ctx = ExecutionContext(
        service_type="genestack_monitoring",
        service_exec="content_sync",
        req_id="unit-test",
        service_payload={},
        service_exec_parameters={"operation": "sync_content"},
    )

    result = await adapter.poll(ctx, "genestack_monitoring:content_sync:missing")

    assert result.status == "errored"
    assert (
        result.service_exec_error == "Unknown Genestack Monitoring content_sync execution receipt"
    )


def test_alert_names_from_content_supports_group_documents() -> None:
    names = _alert_names_from_content(
        """
groups:
  - name: demo
    rules:
      - alert: FirstAlert
        expr: vector(1)
      - record: ignored_record
        expr: vector(1)
""",
        "alerts/demo.yaml",
    )
    assert names == {"FirstAlert"}


class _FakeResult:
    def __init__(self, row: object | None = None, rows: list[object] | None = None) -> None:
        self.rows = rows if rows is not None else ([] if row is None else [row])

    def scalars(self) -> "_FakeResult":
        return self

    def first(self) -> object | None:
        return self.rows[0] if self.rows else None

    def __iter__(self) -> Iterator[object]:
        return iter(self.rows)

    def all(self) -> list[object]:
        return self.rows


class _FakeDb:
    def __init__(
        self,
        existing: dict[str, Recipe] | None = None,
        ingredients: list[Ingredient] | None = None,
        recipe_ingredients: list[RecipeIngredient] | None = None,
    ) -> None:
        self.existing = existing or {}
        self.ingredients = ingredients or []
        self.recipe_ingredients = recipe_ingredients or []
        self.added: list[Recipe] = []
        self.added_recipe_ingredients: list[RecipeIngredient] = []
        self.deleted: list[object] = []

    async def execute(self, statement: object) -> _FakeResult:
        text = str(statement.compile(compile_kwargs={"literal_binds": True}))
        if text.startswith("DELETE FROM recipe_ingredients"):
            self.recipe_ingredients = []
            return _FakeResult()
        if "FROM ingredients" in text:
            for ingredient in self.ingredients:
                if (
                    f"'{ingredient.service_type}'" in text
                    and f"'{ingredient.service_exec}'" in text
                    and f"'{ingredient.task_key_template}'" in text
                ):
                    return _FakeResult(ingredient)
            return _FakeResult(None)
        if "FROM recipe_ingredients" in text:
            if "SELECT recipe_ingredients.id" in text:
                return _FakeResult(rows=[step.id for step in self.recipe_ingredients])
            return _FakeResult(rows=list(self.recipe_ingredients))
        for name, recipe in self.existing.items():
            if f"'{name}'" in text:
                return _FakeResult(recipe)
        return _FakeResult(None)

    def add(self, row: object) -> None:
        if isinstance(row, Recipe):
            self.added.append(row)
        elif isinstance(row, RecipeIngredient):
            self.added_recipe_ingredients.append(row)

    async def delete(self, row: object) -> None:
        self.deleted.append(row)
        if isinstance(row, Recipe):
            self.existing.pop(str(row.name), None)
            self.added = [recipe for recipe in self.added if recipe is not row]

    async def flush(self) -> None:
        next_id = 100
        for recipe in self.added:
            if recipe.id is None:
                next_id += 1
                recipe.id = next_id


class _FakeTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_args: object) -> None:
        return None


class _FakeSession(_FakeDb):
    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def begin(self) -> _FakeTransaction:
        return _FakeTransaction()


class _FakeGitHubHelper(GitHubClient):
    def __init__(self) -> None:
        pass

    async def list_files(self, **_kwargs: object) -> dict[str, object]:
        return {"files": [{"path": "alerts/demo.yaml"}, {"path": "alerts/README.md"}]}

    async def read_file(self, **_kwargs: object) -> dict[str, object]:
        return {"content": """
groups:
  - name: demo
    rules:
      - alert: DemoAlert
        expr: vector(1)
"""}


class _MultiAlertGitHubHelper(GitHubClient):
    def __init__(self) -> None:
        pass

    async def list_files(self, **_kwargs: object) -> dict[str, object]:
        return {
            "files": [
                {"path": "alerts/control-plane.yaml"},
                {"path": "alerts/dataplane.yaml"},
                {"path": "alerts/README.md"},
            ]
        }

    async def read_file(self, **kwargs: object) -> dict[str, object]:
        path = str(kwargs.get("path") or "")
        content_by_path = {
            "alerts/control-plane.yaml": """
groups:
  - name: control-plane
    rules:
      - alert: ApiDown
        expr: vector(1)
""",
            "alerts/dataplane.yaml": """
groups:
  - name: dataplane
    rules:
      - alert: NeutronAgentsDown
        expr: vector(1)
      - alert: NovaComputeDown
        expr: vector(1)
""",
        }
        return {"content": content_by_path[path]}


class _WarningCriticalGitHubHelper(GitHubClient):
    def __init__(self) -> None:
        pass

    async def list_files(self, **_kwargs: object) -> dict[str, object]:
        return {"files": [{"path": "alerts/demo.yaml"}]}

    async def read_file(self, **_kwargs: object) -> dict[str, object]:
        return {"content": """
groups:
  - name: demo
    rules:
      - alert: AdvisoryByLabel
        expr: vector(1)
        labels:
          severity: WARNING
      - alert: CriticalByLabel
        expr: vector(1)
        labels:
          severity: critical
"""}


class _WarningSuffixGitHubHelper(GitHubClient):
    def __init__(self) -> None:
        pass

    async def list_files(self, **_kwargs: object) -> dict[str, object]:
        return {"files": [{"path": "alerts/demo.yaml"}]}

    async def read_file(self, **_kwargs: object) -> dict[str, object]:
        return {"content": """
groups:
  - name: demo
    rules:
      - alert: suffix-only-warning
        expr: vector(1)
      - alert: suffix-only-critical
        expr: vector(1)
"""}


class _FakeK8sHelper(KubernetesHelper):
    def __init__(self) -> None:
        self.applied: list[dict[str, object]] = []

    async def create_or_update_rule(
        self,
        *,
        rule_name: str,
        group_name: str,
        crd_name: str,
        rule_data: dict[str, object],
        source_metadata: object | None = None,
    ) -> dict[str, object]:
        self.applied.append(
            {
                "rule_name": rule_name,
                "group_name": group_name,
                "crd_name": crd_name,
                "rule_data": rule_data,
                "source_metadata": source_metadata,
            }
        )
        return {"status": "success", "action": "updated", "crd_name": crd_name}


def _helpers(
    github: GitHubClient | None = None,
    k8s: _FakeK8sHelper | None = None,
) -> dict[str, object]:
    helper = k8s or _FakeK8sHelper()
    return {
        "github": github or _FakeGitHubHelper(),
        "k8s": helper,
        "prometheus": PrometheusAlertRuleHelper(),
    }


@pytest.mark.asyncio
async def test_genestack_content_sync_creates_managed_recipes() -> None:
    db = _FakeDb()
    stats = await sync_genestack_monitoring_content(
        db,
        _helpers(),
    )

    assert stats["created"] == 1
    assert db.added[0].name == "DemoAlert"
    assert MANAGED_MARKER in str(db.added[0].description)


@pytest.mark.asyncio
async def test_genestack_content_sync_creates_one_recipe_per_alertname_in_alerts() -> None:
    db = _FakeDb()
    k8s = _FakeK8sHelper()
    stats = await sync_genestack_monitoring_content(
        db,
        _helpers(github=_MultiAlertGitHubHelper(), k8s=k8s),
    )

    assert stats["created"] == 3
    assert stats["files_scanned"] == 2
    assert stats["crds_applied"] == 3
    assert {recipe.name for recipe in db.added} == {
        "ApiDown",
        "NeutronAgentsDown",
        "NovaComputeDown",
    }
    assert {item["crd_name"] for item in k8s.applied} == {
        "genestack-monitoring-api-down",
        "genestack-monitoring-neutron-agents-down",
        "genestack-monitoring-nova-compute-down",
    }


@pytest.mark.asyncio
async def test_genestack_content_sync_applies_one_prometheus_rule_crd_per_alert() -> None:
    db = _FakeDb()
    k8s = _FakeK8sHelper()

    await sync_genestack_monitoring_content(
        db,
        _helpers(github=_MultiAlertGitHubHelper(), k8s=k8s),
    )

    applied_by_rule = {str(item["rule_name"]): item for item in k8s.applied}
    assert applied_by_rule["ApiDown"]["group_name"] == "control-plane"
    assert applied_by_rule["ApiDown"]["crd_name"] == "genestack-monitoring-api-down"
    assert applied_by_rule["ApiDown"]["rule_data"] == {
        "alert": "ApiDown",
        "expr": "vector(1)",
    }
    source = applied_by_rule["ApiDown"]["source_metadata"]
    assert source is not None
    assert source.relative_path == "alerts/control-plane.yaml"
    assert source.source_format == "groups"
    assert source.wrapper_key is None


@pytest.mark.asyncio
async def test_genestack_content_sync_skips_warning_recipes_but_applies_crds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ingredient = Ingredient(
        id=17,
        service_type="stackstorm",
        service_exec="workflow_execution",
        destination_target="stackstorm",
        task_key_template="stackstorm-workflow-execution",
        default_expected_secs=10,
        default_timeout=60,
    )

    def fake_remediation_step_specs(
        alert_name: str,
        source_path: str = "",
        rule_data: dict[str, object] | None = None,
    ) -> list[RemediationStepSpec]:
        if alert_name != "CriticalByLabel":
            return []
        return [
            RemediationStepSpec(
                role="remediate",
                service_type="stackstorm",
                service_exec="workflow_execution",
                task_key_template="stackstorm-workflow-execution",
                service_payload={"workflow_ref": "poundcake.demo"},
                service_exec_parameters={"operation": "run"},
                expected_outcome={"success": True},
                expected_secs=10,
                timeout=60,
            )
        ]

    monkeypatch.setattr(
        genestack_content_sync,
        "remediation_step_specs",
        fake_remediation_step_specs,
    )
    db = _FakeDb(ingredients=[ingredient])
    k8s = _FakeK8sHelper()

    stats = await sync_genestack_monitoring_content(
        db,
        _helpers(github=_WarningCriticalGitHubHelper(), k8s=k8s),
    )

    assert stats["processed"] == 2
    assert stats["crds_applied"] == 2
    assert stats["created"] == 1
    assert stats["warning_recipes_skipped"] == 1
    assert stats["warning_recipes_deleted"] == 0
    assert stats["warning_recipes_preserved_nonmanaged"] == 0
    assert stats["remediation_profiles_applied"] == 1
    assert {item["rule_name"] for item in k8s.applied} == {
        "AdvisoryByLabel",
        "CriticalByLabel",
    }
    assert [recipe.name for recipe in db.added] == ["CriticalByLabel"]
    assert len(db.added_recipe_ingredients) == 1
    assert db.added_recipe_ingredients[0].ingredient_id == 17


@pytest.mark.asyncio
async def test_genestack_content_sync_skips_warning_recipe_by_alert_name_suffix() -> None:
    db = _FakeDb()
    k8s = _FakeK8sHelper()

    stats = await sync_genestack_monitoring_content(
        db,
        _helpers(github=_WarningSuffixGitHubHelper(), k8s=k8s),
    )

    assert stats["processed"] == 2
    assert stats["crds_applied"] == 2
    assert stats["created"] == 1
    assert stats["warning_recipes_skipped"] == 1
    assert [recipe.name for recipe in db.added] == ["suffix-only-critical"]
    assert {item["rule_name"] for item in k8s.applied} == {
        "suffix-only-warning",
        "suffix-only-critical",
    }


@pytest.mark.asyncio
async def test_genestack_content_sync_disables_existing_managed_warning_recipe() -> None:
    step = RecipeIngredient(id=44, recipe_id=7, ingredient_id=11, step_order=1)
    existing = Recipe(
        id=7,
        name="AdvisoryByLabel",
        description=f"{MANAGED_MARKER} warning binding",
        enabled=True,
        recipe_ingredients=[step],
    )
    db = _FakeDb({"AdvisoryByLabel": existing}, recipe_ingredients=[step])

    stats = await sync_genestack_monitoring_content(
        db,
        _helpers(github=_WarningCriticalGitHubHelper()),
    )

    assert stats["warning_recipes_skipped"] == 1
    assert stats["warning_recipes_deleted"] == 0
    assert stats["warning_recipes_disabled"] == 1
    assert stats["warning_recipes_preserved_nonmanaged"] == 0
    assert db.deleted == []
    assert existing.enabled is False
    assert existing.deleted is False
    assert existing.deleted_at is None
    assert db.recipe_ingredients == [step]


@pytest.mark.asyncio
async def test_genestack_content_sync_preserves_existing_nonmanaged_warning_recipe() -> None:
    existing = Recipe(
        id=7,
        name="AdvisoryByLabel",
        description="operator-owned warning recipe",
        enabled=True,
    )
    db = _FakeDb({"AdvisoryByLabel": existing})

    stats = await sync_genestack_monitoring_content(
        db,
        _helpers(github=_WarningCriticalGitHubHelper()),
    )

    assert stats["warning_recipes_skipped"] == 1
    assert stats["warning_recipes_deleted"] == 0
    assert stats["warning_recipes_preserved_nonmanaged"] == 1
    assert db.deleted == []
    assert existing.description == "operator-owned warning recipe"


@pytest.mark.asyncio
async def test_genestack_content_sync_skips_existing_nonmanaged_recipe() -> None:
    existing = Recipe(name="DemoAlert", description="user owned", enabled=True)
    db = _FakeDb({"DemoAlert": existing})
    k8s = _FakeK8sHelper()
    stats = await sync_genestack_monitoring_content(
        db,
        _helpers(k8s=k8s),
    )

    assert stats["skipped_existing"] == 1
    assert stats["crds_applied"] == 1
    assert k8s.applied[0]["rule_name"] == "DemoAlert"
    assert existing.description == "user owned"


@pytest.mark.asyncio
async def test_genestack_content_sync_preserves_existing_managed_recipe_steps() -> None:
    existing_step = RecipeIngredient(recipe_id=7, ingredient_id=11, step_order=1)
    existing = Recipe(
        id=7,
        name="DemoAlert",
        description=f"{MANAGED_MARKER} existing binding",
        enabled=False,
        recipe_ingredients=[existing_step],
    )
    db = _FakeDb({"DemoAlert": existing})
    stats = await sync_genestack_monitoring_content(
        db,
        _helpers(),
    )

    assert stats["updated"] == 1
    assert existing.enabled is True
    assert existing.recipe_ingredients == [existing_step]


@pytest.mark.asyncio
async def test_genestack_content_sync_requires_github_helper() -> None:
    with pytest.raises(PluginBootstrapError, match="requires enabled github plugin helper"):
        await sync_genestack_monitoring_content(_FakeDb(), {})


@pytest.mark.asyncio
async def test_genestack_content_sync_requires_k8s_helper() -> None:
    with pytest.raises(PluginBootstrapError, match="requires enabled k8s plugin helper"):
        await sync_genestack_monitoring_content(
            _FakeDb(),
            {
                "github": _FakeGitHubHelper(),
                "prometheus": PrometheusAlertRuleHelper(),
            },  # type: ignore[arg-type]
        )


def test_crd_name_for_alert_uses_kubernetes_safe_dns_label() -> None:
    assert _crd_name_for_alert("NovaComputeDown") == "genestack-monitoring-nova-compute-down"
    assert _crd_name_for_alert("API Down!") == "genestack-monitoring-api-down"
    assert len(_crd_name_for_alert("A" * 120)) <= 63


def test_genestack_helper_dependency_fails_when_github_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.plugins.genestack_monitoring.plugin import get_plugin

    monkeypatch.setattr(catalog, "get_enabled_plugins", lambda: [get_plugin()])

    with pytest.raises(ServicePluginManifestError, match="genestack_monitoring requires github"):
        catalog.validate_enabled_plugin_helper_dependencies()


def test_genestack_helper_dependency_fails_when_prometheus_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.plugins.genestack_monitoring.plugin import get_plugin as get_genestack_plugin
    from api.plugins.github.plugin import get_plugin as get_github_plugin
    from api.plugins.k8s.plugin import get_plugin as get_k8s_plugin

    monkeypatch.setattr(
        catalog,
        "get_enabled_plugins",
        lambda: [get_github_plugin(), get_k8s_plugin(), get_genestack_plugin()],
    )

    with pytest.raises(
        ServicePluginManifestError, match="genestack_monitoring requires prometheus"
    ):
        catalog.validate_enabled_plugin_helper_dependencies()


def test_genestack_helper_dependency_fails_when_k8s_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.plugins.genestack_monitoring.plugin import get_plugin as get_genestack_plugin
    from api.plugins.github.plugin import get_plugin as get_github_plugin
    from api.plugins.prometheus.plugin import get_plugin as get_prometheus_plugin

    monkeypatch.setattr(
        catalog,
        "get_enabled_plugins",
        lambda: [get_github_plugin(), get_prometheus_plugin(), get_genestack_plugin()],
    )

    with pytest.raises(ServicePluginManifestError, match="genestack_monitoring requires k8s"):
        catalog.validate_enabled_plugin_helper_dependencies()


def test_genestack_helper_dependency_fails_when_k8s_capability_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.plugins.genestack_monitoring.plugin import get_plugin as get_genestack_plugin
    from api.plugins.github.plugin import get_plugin as get_github_plugin
    from api.plugins.k8s.plugin import get_plugin as get_k8s_plugin
    from api.plugins.prometheus.plugin import get_plugin as get_prometheus_plugin

    k8s = get_k8s_plugin()
    incomplete_k8s = ServicePlugin(
        service_type=k8s.service_type,
        adapter_factory=k8s.adapter_factory,
        ingredient_templates=k8s.ingredient_templates,
        recipe_templates=k8s.recipe_templates,
        scheduled_tasks=k8s.scheduled_tasks,
        helper_factory=k8s.helper_factory,
        helper_capabilities=("k8s.cluster.connect",),
    )
    monkeypatch.setattr(
        catalog,
        "get_enabled_plugins",
        lambda: [
            get_github_plugin(),
            incomplete_k8s,
            get_prometheus_plugin(),
            get_genestack_plugin(),
        ],
    )

    with pytest.raises(ServicePluginManifestError, match="k8s.prometheusrules.manage"):
        catalog.validate_enabled_plugin_helper_dependencies()


def test_helper_dependency_fails_when_capability_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.plugins.github.plugin import get_plugin as get_github_plugin

    github = get_github_plugin()
    incomplete_github = ServicePlugin(
        service_type=github.service_type,
        adapter_factory=github.adapter_factory,
        ingredient_templates=github.ingredient_templates,
        recipe_templates=github.recipe_templates,
        scheduled_tasks=github.scheduled_tasks,
        helper_factory=github.helper_factory,
        helper_capabilities=("repo.read",),
    )
    consumer = ServicePlugin(
        service_type="consumer",
        adapter_factory=github.adapter_factory,
        scheduled_tasks=(
            {
                "task_key": "plugin-health-check:consumer",
                "task_type": "plugin_health_check",
                "service_type": "consumer",
                "service_exec": "health_check",
            },
        ),
        required_helper_capabilities={"github": ("repo.read", "repo.list")},
        allow_directory_mismatch=True,
    )
    monkeypatch.setattr(catalog, "get_enabled_plugins", lambda: [incomplete_github, consumer])

    with pytest.raises(ServicePluginManifestError, match="consumer requires github"):
        catalog.validate_enabled_plugin_helper_dependencies()


def test_genestack_helper_dependency_passes_when_github_capabilities_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.plugins.genestack_monitoring.plugin import get_plugin as get_genestack_plugin
    from api.plugins.github.plugin import get_plugin as get_github_plugin
    from api.plugins.k8s.plugin import get_plugin as get_k8s_plugin
    from api.plugins.prometheus.plugin import get_plugin as get_prometheus_plugin

    monkeypatch.setattr(
        catalog,
        "get_enabled_plugins",
        lambda: [
            get_github_plugin(),
            get_k8s_plugin(),
            get_prometheus_plugin(),
            get_genestack_plugin(),
        ],
    )

    catalog.validate_enabled_plugin_helper_dependencies()
