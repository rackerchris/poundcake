"""Unit tests for the Kubernetes service plugin."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from api.plugins.contract import validate_payload_schema
from api.plugins.k8s.adapter import KubernetesExecutionAdapter
from api.plugins.k8s.client import KubernetesClientConfig, KubernetesClientFactory
from api.plugins.k8s.helper import (
    KubernetesHelper,
    _certificate_secret_summary,
    _configmap_summary,
    _event_summary,
    _node_pod_summary,
)
from api.plugins.k8s.plugin import get_plugin
from api.plugins.k8s.templates import (
    K8S_INGREDIENT_TEMPLATES,
    K8S_RECIPE_TEMPLATES,
    K8S_SCHEDULED_TASKS,
)
from api.plugins.manifest import validate_service_plugin
from api.plugins.types import ExecutionContext


class _FakeKubernetesHelper:
    async def health_check(self) -> dict[str, object]:
        return {
            "success": True,
            "status": "healthy",
            "auth_mode": "adapter_credentials",
            "namespace": "monitoring",
        }

    async def list_prometheus_rules(self) -> list[dict[str, object]]:
        return [{"metadata": {"name": "demo-rules"}}]

    async def get_prometheus_rule(self, name: str) -> dict[str, object] | None:
        if name == "demo-rules":
            return {"metadata": {"name": name}}
        return None

    async def create_or_update_rule(
        self,
        *,
        rule_name: str,
        group_name: str,
        crd_name: str,
        rule_data: dict[str, object],
        source_metadata: object | None = None,
    ) -> dict[str, object]:
        return {
            "status": "success",
            "action": "updated",
            "rule_name": rule_name,
            "group_name": group_name,
            "crd_name": crd_name,
            "rule_data": rule_data,
        }

    async def delete_rule(
        self,
        rule_name: str,
        group_name: str,
        crd_name: str,
    ) -> dict[str, object]:
        return {
            "status": "success",
            "action": "updated",
            "rule_name": rule_name,
            "group_name": group_name,
            "crd_name": crd_name,
        }

    async def list_pods(
        self, *, namespace: str, label_selector: str = ""
    ) -> list[dict[str, object]]:
        return [
            {"metadata": {"name": "api-123", "namespace": namespace}, "selector": label_selector}
        ]

    async def get_pod(self, *, namespace: str, pod_name: str) -> dict[str, object] | None:
        if pod_name == "api-123":
            return {"metadata": {"name": pod_name, "namespace": namespace}}
        return None

    async def get_pod_logs(
        self,
        *,
        namespace: str,
        pod_name: str,
        label_selector: str = "",
        container: str = "",
        tail_lines: int | None = None,
        since_seconds: int | None = None,
        previous: bool = False,
    ) -> dict[str, object]:
        return {
            "success": True,
            "status": "succeeded",
            "pod_name": pod_name or "api-123",
            "namespace": namespace,
            "logs": "hello from pod",
            "tail_lines": tail_lines,
            "previous": previous,
            "selector": label_selector,
        }

    async def list_pod_events(self, *, namespace: str, pod_name: str) -> list[dict[str, object]]:
        return [{"metadata": {"name": "event-1", "namespace": namespace}, "pod_name": pod_name}]

    async def delete_pod(self, *, namespace: str, pod_name: str) -> dict[str, object]:
        return {
            "success": True,
            "status": "succeeded",
            "action": "deleted",
            "pod_name": pod_name,
            "namespace": namespace,
        }

    async def get_deployment(
        self,
        *,
        namespace: str,
        deployment_name: str,
    ) -> dict[str, object] | None:
        if deployment_name == "api":
            return {
                "metadata": {"name": deployment_name, "namespace": namespace},
                "spec": {"replicas": 2},
                "status": {"updatedReplicas": 2, "availableReplicas": 2},
            }
        return None

    async def scale_deployment(
        self,
        *,
        namespace: str,
        deployment_name: str,
        replicas: int,
    ) -> dict[str, object]:
        return {
            "success": True,
            "status": "succeeded",
            "action": "scaled",
            "deployment_name": deployment_name,
            "namespace": namespace,
            "replicas": replicas,
        }

    async def rollout_restart_deployment(
        self,
        *,
        namespace: str,
        deployment_name: str,
    ) -> dict[str, object]:
        return {
            "success": True,
            "status": "succeeded",
            "action": "rollout_restarted",
            "deployment_name": deployment_name,
            "namespace": namespace,
        }

    async def deployment_rollout_status(
        self,
        *,
        namespace: str,
        deployment_name: str,
    ) -> dict[str, object]:
        return {
            "success": True,
            "status": "succeeded",
            "deployment_name": deployment_name,
            "namespace": namespace,
            "desired_replicas": 2,
            "updated_replicas": 2,
            "available_replicas": 2,
        }

    async def controller_status(
        self,
        *,
        namespace: str,
        kind: str,
        name: str,
    ) -> dict[str, object]:
        return {
            "success": True,
            "status": "succeeded",
            "namespace": namespace,
            "kind": kind,
            "name": name,
            "rollout": {"ready": True, "desired_replicas": 3},
        }

    async def rollout_restart_controller(
        self,
        *,
        namespace: str,
        kind: str,
        name: str,
    ) -> dict[str, object]:
        return {
            "success": True,
            "status": "succeeded",
            "action": "rollout_restarted",
            "namespace": namespace,
            "kind": kind,
            "name": name,
        }

    async def controller_rollout_status(
        self,
        *,
        namespace: str,
        kind: str,
        name: str,
    ) -> dict[str, object]:
        return {
            "success": True,
            "status": "succeeded",
            "namespace": namespace,
            "kind": kind,
            "name": name,
            "rollout": {"ready": True, "desired_replicas": 3},
        }

    async def pod_diagnostics(
        self,
        *,
        namespace: str,
        pod_name: str,
        container: str = "",
        tail_lines: int | None = None,
        previous: bool = False,
    ) -> dict[str, object]:
        return {
            "success": True,
            "status": "succeeded",
            "namespace": namespace,
            "pod_name": pod_name,
            "container": container,
            "tail_lines": tail_lines,
            "previous": previous,
        }

    async def workload_status(self, *, namespace: str, kind: str, name: str) -> dict[str, object]:
        return {
            "success": True,
            "status": "succeeded",
            "namespace": namespace,
            "kind": kind,
            "name": name,
            "pods": [{"metadata": {"name": "api-123"}}],
        }

    async def workload_logs(
        self,
        *,
        namespace: str,
        kind: str = "",
        name: str = "",
        pod_name: str = "",
        label_selector: str = "",
        container: str = "",
        tail_lines: int | None = None,
        since_seconds: int | None = None,
        previous: bool = False,
        limit: int | None = None,
    ) -> dict[str, object]:
        return {
            "success": True,
            "status": "succeeded",
            "namespace": namespace,
            "kind": kind,
            "name": name,
            "pod_name": pod_name,
            "selector": label_selector,
            "tail_lines": tail_lines,
            "since_seconds": since_seconds,
            "previous": previous,
            "limit": limit,
            "items": [{"pod_name": pod_name or "api-123", "logs": "workload logs"}],
        }

    async def workload_events(
        self,
        *,
        namespace: str,
        kind: str = "",
        name: str = "",
        pod_name: str = "",
        label_selector: str = "",
        limit: int | None = None,
    ) -> dict[str, object]:
        return {
            "success": True,
            "status": "succeeded",
            "namespace": namespace,
            "kind": kind,
            "name": name,
            "pod_name": pod_name,
            "selector": label_selector,
            "limit": limit,
            "items": [{"metadata": {"name": "event-1"}}],
        }

    async def job_diagnostics(
        self,
        *,
        namespace: str,
        kind: str,
        name: str,
        tail_lines: int | None = None,
        previous: bool = False,
        limit: int | None = None,
    ) -> dict[str, object]:
        return {
            "success": True,
            "status": "succeeded",
            "namespace": namespace,
            "kind": kind,
            "name": name,
            "tail_lines": tail_lines,
            "previous": previous,
            "limit": limit,
        }

    async def list_nodes(
        self,
        *,
        label_selector: str = "",
        limit: int | None = None,
    ) -> list[dict[str, object]]:
        return [{"metadata": {"name": "worker-1"}, "selector": label_selector, "limit": limit}]

    async def node_diagnostics(
        self,
        *,
        node_name: str,
        limit: int | None = None,
    ) -> dict[str, object]:
        return {"success": True, "status": "succeeded", "node": node_name, "limit": limit}

    async def node_capacity(self, *, node_name: str) -> dict[str, object]:
        return {
            "success": True,
            "status": "succeeded",
            "node": node_name,
            "capacity": {"cpu": "4"},
        }

    async def node_pressure(
        self,
        *,
        node_name: str,
        limit: int | None = None,
    ) -> dict[str, object]:
        return {
            "success": True,
            "status": "succeeded",
            "node": node_name,
            "pressure_conditions": [],
            "limit": limit,
        }

    async def node_pods(
        self,
        *,
        node_name: str,
        namespace: str = "",
        label_selector: str = "",
        include_succeeded: bool = False,
        limit: int | None = None,
    ) -> dict[str, object]:
        return {
            "success": True,
            "status": "succeeded",
            "node": node_name,
            "namespace": namespace or None,
            "selector": label_selector,
            "include_succeeded": include_succeeded,
            "limit": limit,
            "items": [{"metadata": {"name": "api-123"}}],
        }

    async def node_events(
        self,
        *,
        node_name: str,
        limit: int | None = None,
    ) -> dict[str, object]:
        return {
            "success": True,
            "status": "succeeded",
            "node": node_name,
            "limit": limit,
            "items": [{"metadata": {"name": "node-event-1"}}],
        }

    async def cleanup_failed_job(self, *, namespace: str, job_name: str) -> dict[str, object]:
        return {
            "success": True,
            "status": "succeeded",
            "action": "deleted",
            "namespace": namespace,
            "job_name": job_name,
            "summary": {"failed": True},
        }

    async def remediate_resource_pressure_with_deployment_scale(
        self,
        *,
        namespace: str,
        deployment_name: str,
        replicas: int,
    ) -> dict[str, object]:
        return {
            "success": True,
            "status": "succeeded",
            "action": "scaled",
            "namespace": namespace,
            "deployment_name": deployment_name,
            "replicas": replicas,
        }

    async def remediate_resource_pressure_with_hpa_patch(
        self,
        *,
        namespace: str,
        hpa_name: str,
        min_replicas: int | None = None,
        max_replicas: int | None = None,
    ) -> dict[str, object]:
        return {
            "success": True,
            "status": "succeeded",
            "action": "patched_hpa_bounds",
            "namespace": namespace,
            "hpa_name": hpa_name,
            "applied_min_replicas": min_replicas,
            "applied_max_replicas": max_replicas,
        }

    async def service_probe(
        self,
        *,
        namespace: str,
        service_name: str,
        port: object,
        operation: str,
        path: str = "",
        scheme: str = "",
        timeout_seconds: int = 5,
        expected_status_codes: list[int] | None = None,
    ) -> dict[str, object]:
        return {
            "success": True,
            "status": "succeeded",
            "namespace": namespace,
            "service": service_name,
            "operation": operation,
            "port": port,
            "path": path or "/",
            "scheme": scheme or None,
            "timeout_seconds": timeout_seconds,
            "expected_status_codes": expected_status_codes,
        }

    async def pvc_diagnostics(
        self, *, namespace: str = "", pvc_name: str = "", pv_name: str = ""
    ) -> dict[str, object]:
        return {
            "success": True,
            "status": "succeeded",
            "namespace": namespace or None,
            "persistentvolumeclaim": pvc_name or None,
            "persistentvolume_name": pv_name or None,
        }

    async def service_diagnostics(self, *, namespace: str, service_name: str) -> dict[str, object]:
        return {
            "success": True,
            "status": "succeeded",
            "namespace": namespace,
            "service": service_name,
        }

    async def config_diagnostics(self, *, namespace: str, configmap_name: str) -> dict[str, object]:
        return {
            "success": True,
            "status": "succeeded",
            "namespace": namespace,
            "configmap": configmap_name,
            "summary": {"data_keys": ["config.yaml"]},
        }

    async def pdb_hpa_diagnostics(
        self,
        *,
        namespace: str,
        pdb_name: str = "",
        hpa_name: str = "",
        name: str = "",
    ) -> dict[str, object]:
        return {
            "success": True,
            "status": "succeeded",
            "namespace": namespace,
            "poddisruptionbudget": pdb_name or name,
            "horizontalpodautoscaler": hpa_name or name,
        }

    async def certificate_diagnostics(
        self,
        *,
        namespace: str = "",
        secret_name: str = "",
        label_selector: str = "",
        limit: int | None = None,
    ) -> dict[str, object]:
        return {
            "success": True,
            "status": "succeeded",
            "namespace": namespace or None,
            "secret": secret_name or None,
            "selector": label_selector,
            "limit": limit,
            "items": [
                {
                    "namespace": namespace or "kube-system",
                    "name": secret_name or "kubelet-serving-cert",
                    "certificates": [{"days_remaining": 12}],
                    "private_key_returned": False,
                }
            ],
        }


class _MissingPrometheusRuleCrdError(Exception):
    status = 404
    reason = "Not Found"
    body = '{"message":"the server could not find the requested resource prometheusrules.monitoring.coreos.com"}'


class _FakeCustomObjectsApi:
    def list_namespaced_custom_object(self, **_kwargs: object) -> dict[str, object]:
        raise _MissingPrometheusRuleCrdError()


class _FakeApiClient:
    def call_api(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        return {"gitVersion": "v1.30.0"}


class _FakeClientBundle:
    api_client = _FakeApiClient()
    apps_api = object()
    autoscaling_api = object()
    batch_api = object()
    custom_api = _FakeCustomObjectsApi()
    discovery_api = object()
    policy_api = object()
    namespace = "poundcake"
    auth_mode = "in_cluster"
    host = "https://kubernetes.default.svc"


class _FakeClientFactory:
    async def build(self) -> _FakeClientBundle:
        return _FakeClientBundle()


def _ctx(
    operation: str,
    payload: dict[str, object] | list[object] | None = None,
    service_exec: str = "prometheus_rule",
) -> ExecutionContext:
    return ExecutionContext(
        service_type="k8s",
        service_exec=service_exec,
        req_id="unit-test",
        service_payload={} if payload is None else payload,
        service_exec_parameters={
            "operation": operation,
            "allowed_operations": {
                "prometheus_rule": ["get", "list", "apply", "delete"],
                "pod_action": ["list", "get", "logs", "events", "delete"],
                "deployment_action": ["get", "scale", "rollout_restart", "rollout_status"],
                "workload_action": ["get", "rollout_restart", "rollout_status"],
                "workload_triage": [
                    "pod_diagnostics",
                    "workload_status",
                    "logs",
                    "events",
                    "job_diagnostics",
                    "node_diagnostics",
                    "pvc_diagnostics",
                    "service_diagnostics",
                    "config_diagnostics",
                    "pdb_hpa_diagnostics",
                    "certificate_diagnostics",
                ],
                "node_triage": [
                    "list_nodes",
                    "node_diagnostics",
                    "node_capacity",
                    "node_pressure",
                    "node_pods",
                    "node_events",
                ],
                "failed_job_cleanup": ["delete"],
                "resource_pressure_remediation": [
                    "scale_deployment",
                    "patch_hpa_bounds",
                ],
                "service_probe": ["dns", "tcp", "http"],
            }[service_exec],
        },
    )


def test_k8s_manifest_validates() -> None:
    plugin = get_plugin()
    validated = validate_service_plugin(plugin, directory_name="k8s")

    assert validated.service_type == "k8s"
    assert validated.plugin_tier == "community"
    assert validated.plugin_log_key is None
    assert validated.helper_factory is not None
    assert validated.helper_capabilities == (
        "k8s.cluster.connect",
        "k8s.deployments.manage",
        "k8s.deployments.read",
        "k8s.diagnostics.read",
        "k8s.nodes.read",
        "k8s.pods.manage",
        "k8s.pods.read",
        "k8s.prometheusrules.manage",
        "k8s.workloads.manage",
        "k8s.workloads.read",
    )
    capability_ids = {template["capability_id"] for template in validated.capability_templates}
    assert "k8s.remediation.kubernetes.kube-pod-crash-looping" in capability_ids
    assert "k8s.remediation.kubernetes.kube-deployment-rollout-stuck" in capability_ids
    assert "k8s.remediation.kubernetes.kube-daemonset-rollout-stuck" in capability_ids


def test_k8s_adapter_declares_optional_kubeconfig_credential() -> None:
    assert KubernetesExecutionAdapter(helper=_FakeKubernetesHelper()).credential_requirements() == [  # type: ignore[arg-type]
        {
            "credential_type": "kubernetes_kubeconfig",
            "credential_key_id": "default",
            "required": False,
            "usage": (
                "Optional kubeconfig for Kubernetes API access; falls back to "
                "in-cluster service account when absent."
            ),
        }
    ]


def test_k8s_adapter_rejects_non_object_service_payload() -> None:
    adapter = KubernetesExecutionAdapter(helper=_FakeKubernetesHelper())  # type: ignore[arg-type]
    ctx = _ctx("list", {}, service_exec="pod_action").model_copy(
        update={"service_payload": ["not", "an", "object"]}
    )

    assert (
        adapter.validate(ctx) == "service_payload must be an object when provided"
    )


def test_k8s_templates_are_valid_service_plugin_templates() -> None:
    assert {template["service_exec"] for template in K8S_INGREDIENT_TEMPLATES} == {
        "health_check",
        "deployment_action",
        "failed_job_cleanup",
        "resource_pressure_remediation",
        "pod_action",
        "prometheus_rule",
        "service_probe",
        "workload_action",
        "node_triage",
        "workload_triage",
    }
    prometheus_rule_template = next(
        template
        for template in K8S_INGREDIENT_TEMPLATES
        if template["service_exec"] == "prometheus_rule"
    )
    assert prometheus_rule_template["service_exec_parameters"]["allowed_operations"] == [
        "get",
        "list",
        "apply",
        "delete",
    ]
    pod_template = next(
        template
        for template in K8S_INGREDIENT_TEMPLATES
        if template["service_exec"] == "pod_action"
    )
    assert pod_template["service_payload_template"] == {}
    assert pod_template["service_exec_parameters"]["operation_metadata"]["delete"][
        "payload_schema"
    ]["required"] == ["namespace", "pod_name"]
    deployment_template = next(
        template
        for template in K8S_INGREDIENT_TEMPLATES
        if template["service_exec"] == "deployment_action"
    )
    assert deployment_template["service_exec_parameters"]["operation_metadata"]["scale"][
        "payload_schema"
    ]["required"] == ["namespace", "deployment_name", "replicas"]
    workload_action_template = next(
        template
        for template in K8S_INGREDIENT_TEMPLATES
        if template["service_exec"] == "workload_action"
    )
    assert workload_action_template["service_exec_parameters"]["allowed_operations"] == [
        "get",
        "rollout_restart",
        "rollout_status",
    ]
    assert workload_action_template["service_exec_parameters"]["operation_metadata"][
        "rollout_status"
    ]["payload_schema"]["required"] == ["namespace", "kind", "name"]
    triage_template = next(
        template
        for template in K8S_INGREDIENT_TEMPLATES
        if template["service_exec"] == "workload_triage"
    )
    assert triage_template["service_payload_template"] == {}
    assert triage_template["service_exec_parameters"]["operation_metadata"]["logs"][
        "payload_schema"
    ]["anyOf"] == [
        {"required": ["kind", "name"]},
        {"required": ["pod_name"]},
        {"required": ["label_selector"]},
    ]
    node_triage_template = next(
        template
        for template in K8S_INGREDIENT_TEMPLATES
        if template["service_exec"] == "node_triage"
    )
    assert node_triage_template["service_payload_template"] == {}
    assert node_triage_template["service_exec_parameters"]["operation_metadata"]["node_pods"][
        "payload_schema"
    ]["anyOf"] == [{"required": ["node"]}, {"required": ["name"]}]
    failed_job_cleanup_template = next(
        template
        for template in K8S_INGREDIENT_TEMPLATES
        if template["service_exec"] == "failed_job_cleanup"
    )
    assert failed_job_cleanup_template["service_exec_parameters"]["allowed_operations"] == [
        "delete"
    ]
    resource_pressure_template = next(
        template
        for template in K8S_INGREDIENT_TEMPLATES
        if template["service_exec"] == "resource_pressure_remediation"
    )
    assert resource_pressure_template["service_exec_parameters"]["allowed_operations"] == [
        "scale_deployment",
        "patch_hpa_bounds",
    ]
    service_probe_template = next(
        template
        for template in K8S_INGREDIENT_TEMPLATES
        if template["service_exec"] == "service_probe"
    )
    assert service_probe_template["service_exec_parameters"]["allowed_operations"] == [
        "dns",
        "tcp",
        "http",
    ]
    assert {recipe["name"] for recipe in K8S_RECIPE_TEMPLATES} == {
        "plugin-health-check:k8s",
        "operator-action:k8s:prometheus-rule-apply",
    }
    assert {task["task_key"] for task in K8S_SCHEDULED_TASKS} == {"plugin-health-check:k8s"}
    for template in K8S_INGREDIENT_TEMPLATES:
        assert template["service_type"] == "k8s"
        validate_payload_schema(template["payload_schema"])


def test_k8s_adapter_validates_prometheus_rule_payload() -> None:
    adapter = KubernetesExecutionAdapter(helper=_FakeKubernetesHelper())  # type: ignore[arg-type]

    assert (
        adapter.validate(
            _ctx(
                "apply",
                {
                    "rule_name": "DemoAlert",
                    "group_name": "demo",
                    "crd_name": "demo-rules",
                    "rule_data": {"alert": "DemoAlert", "expr": "vector(1)"},
                },
            )
        )
        is None
    )
    assert (
        adapter.validate(_ctx("apply", {"rule_name": "DemoAlert"}))
        == "k8s prometheus_rule apply requires service_payload.group_name"
    )
    assert (
        adapter.validate(_ctx("wat"))
        == "k8s prometheus_rule operation must be one of: apply, delete, get, list"
    )


def test_k8s_adapter_validates_pod_action_payload() -> None:
    adapter = KubernetesExecutionAdapter(helper=_FakeKubernetesHelper())  # type: ignore[arg-type]

    assert (
        adapter.validate(
            _ctx(
                "logs",
                {"namespace": "poundcake", "label_selector": "app=api"},
                service_exec="pod_action",
            )
        )
        is None
    )
    assert (
        adapter.validate(_ctx("logs", {"namespace": "poundcake"}, service_exec="pod_action"))
        == "k8s pod_action logs requires service_payload.pod_name or service_payload.label_selector"
    )
    assert (
        adapter.validate(_ctx("delete", {"namespace": "poundcake"}, service_exec="pod_action"))
        == "k8s pod_action delete requires service_payload.pod_name"
    )


def test_k8s_adapter_validates_deployment_action_payload() -> None:
    adapter = KubernetesExecutionAdapter(helper=_FakeKubernetesHelper())  # type: ignore[arg-type]

    assert (
        adapter.validate(
            _ctx(
                "rollout_restart",
                {"namespace": "poundcake", "deployment_name": "api"},
                service_exec="deployment_action",
            )
        )
        is None
    )
    assert (
        adapter.validate(
            _ctx(
                "scale",
                {"namespace": "poundcake", "deployment_name": "api"},
                service_exec="deployment_action",
            )
        )
        == "k8s deployment_action scale requires service_payload.replicas"
    )
    assert (
        adapter.validate(
            _ctx("rollout_restart", {"namespace": "poundcake"}, service_exec="deployment_action")
        )
        == "k8s deployment_action rollout_restart requires service_payload.deployment_name"
    )


def test_k8s_adapter_validates_workload_action_payload() -> None:
    adapter = KubernetesExecutionAdapter(helper=_FakeKubernetesHelper())  # type: ignore[arg-type]

    assert (
        adapter.validate(
            _ctx(
                "rollout_restart",
                {"namespace": "poundcake", "kind": "StatefulSet", "name": "mariadb"},
                service_exec="workload_action",
            )
        )
        is None
    )
    assert (
        adapter.validate(
            _ctx(
                "rollout_status",
                {"namespace": "poundcake", "kind": "Job", "name": "backup"},
                service_exec="workload_action",
            )
        )
        == "k8s workload_action rollout_status requires service_payload.kind "
        "to be one of: Deployment, StatefulSet, DaemonSet"
    )
    assert (
        adapter.validate(
            _ctx(
                "rollout_status",
                {"namespace": "poundcake", "kind": "DaemonSet"},
                service_exec="workload_action",
            )
        )
        == "k8s workload_action rollout_status requires service_payload.name"
    )


def test_k8s_adapter_validates_workload_triage_payload() -> None:
    adapter = KubernetesExecutionAdapter(helper=_FakeKubernetesHelper())  # type: ignore[arg-type]

    assert (
        adapter.validate(
            _ctx(
                "logs",
                {"namespace": "poundcake", "kind": "Deployment", "name": "api"},
                service_exec="workload_triage",
            )
        )
        is None
    )
    assert (
        adapter.validate(
            _ctx(
                "pod_diagnostics",
                {"namespace": "poundcake", "pod_name": "api-123"},
                service_exec="workload_triage",
            )
        )
        is None
    )
    assert (
        adapter.validate(_ctx("logs", {"namespace": "poundcake"}, service_exec="workload_triage"))
        == "k8s workload_triage logs requires service_payload.name, "
        "service_payload.pod_name, or service_payload.label_selector"
    )
    assert (
        adapter.validate(_ctx("node_diagnostics", {}, service_exec="workload_triage"))
        == "k8s workload_triage node_diagnostics requires service_payload.node or service_payload.name"
    )
    assert (
        adapter.validate(
            _ctx("pvc_diagnostics", {"namespace": "poundcake"}, service_exec="workload_triage")
        )
        == "k8s workload_triage pvc_diagnostics requires "
        "service_payload.persistentvolumeclaim, service_payload.persistentvolume, "
        "or service_payload.name"
    )
    assert (
        adapter.validate(
            _ctx(
                "pvc_diagnostics",
                {"persistentvolume": "pvc-123"},
                service_exec="workload_triage",
            )
        )
        is None
    )
    assert (
        adapter.validate(
            _ctx(
                "config_diagnostics",
                {"namespace": "prometheus", "configmap": "alertmanager-config"},
                service_exec="workload_triage",
            )
        )
        is None
    )
    assert (
        adapter.validate(
            _ctx(
                "certificate_diagnostics",
                {"namespace": "kube-system", "secret": "kubelet-serving-cert"},
                service_exec="workload_triage",
            )
        )
        is None
    )


def test_k8s_adapter_validates_node_triage_payload() -> None:
    adapter = KubernetesExecutionAdapter(helper=_FakeKubernetesHelper())  # type: ignore[arg-type]

    assert (
        adapter.validate(
            _ctx(
                "node_pressure",
                {"node": "worker-1", "limit": 10},
                service_exec="node_triage",
            )
        )
        is None
    )
    assert (
        adapter.validate(
            _ctx(
                "list_nodes",
                {"label_selector": "node-role.kubernetes.io/worker="},
                service_exec="node_triage",
            )
        )
        is None
    )
    assert (
        adapter.validate(_ctx("node_pods", {}, service_exec="node_triage"))
        == "k8s node_triage node_pods requires service_payload.node or service_payload.name"
    )
    assert (
        adapter.validate(_ctx("cordon", {"node": "worker-1"}, service_exec="node_triage"))
        == "k8s node_triage operation must be one of: "
        "list_nodes, node_capacity, node_diagnostics, node_events, node_pods, node_pressure"
    )


def test_k8s_adapter_validates_failed_job_cleanup_payload() -> None:
    adapter = KubernetesExecutionAdapter(helper=_FakeKubernetesHelper())  # type: ignore[arg-type]

    assert (
        adapter.validate(
            _ctx(
                "delete",
                {"namespace": "poundcake", "job_name": "backup-123"},
                service_exec="failed_job_cleanup",
            )
        )
        is None
    )
    assert (
        adapter.validate(
            _ctx("delete", {"namespace": "poundcake"}, service_exec="failed_job_cleanup")
        )
        == "k8s failed_job_cleanup delete requires service_payload.job_name"
    )


def test_k8s_adapter_validates_resource_pressure_remediation_payload() -> None:
    adapter = KubernetesExecutionAdapter(helper=_FakeKubernetesHelper())  # type: ignore[arg-type]

    assert (
        adapter.validate(
            _ctx(
                "scale_deployment",
                {"namespace": "poundcake", "deployment_name": "api", "replicas": 4},
                service_exec="resource_pressure_remediation",
            )
        )
        is None
    )
    assert (
        adapter.validate(
            _ctx(
                "patch_hpa_bounds",
                {"namespace": "poundcake", "hpa_name": "api", "min_replicas": 2},
                service_exec="resource_pressure_remediation",
            )
        )
        is None
    )
    assert (
        adapter.validate(
            _ctx(
                "patch_hpa_bounds",
                {"namespace": "poundcake", "hpa_name": "api", "min_replicas": 5, "max_replicas": 2},
                service_exec="resource_pressure_remediation",
            )
        )
        == "k8s resource_pressure_remediation patch_hpa_bounds requires "
        "service_payload.min_replicas <= service_payload.max_replicas"
    )


def test_k8s_adapter_validates_service_probe_payload() -> None:
    adapter = KubernetesExecutionAdapter(helper=_FakeKubernetesHelper())  # type: ignore[arg-type]

    assert (
        adapter.validate(
            _ctx(
                "dns",
                {"namespace": "poundcake", "service": "api"},
                service_exec="service_probe",
            )
        )
        is None
    )
    assert (
        adapter.validate(
            _ctx(
                "http",
                {"namespace": "poundcake", "service": "api", "port": 8080, "path": "/ready"},
                service_exec="service_probe",
            )
        )
        is None
    )
    assert (
        adapter.validate(
            _ctx("tcp", {"namespace": "poundcake", "service": "api"}, service_exec="service_probe")
        )
        == "k8s service_probe tcp requires service_payload.port"
    )


def test_k8s_certificate_secret_summary_omits_private_key_material() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "kubelet.test")]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "issuer.test")]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=12))
        .sign(key, hashes.SHA256())
    )
    secret = {
        "metadata": {"namespace": "kube-system", "name": "kubelet-serving-cert"},
        "type": "kubernetes.io/tls",
        "data": {
            "tls.crt": base64.b64encode(cert.public_bytes(serialization.Encoding.PEM)).decode(),
            "tls.key": base64.b64encode(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            ).decode(),
        },
    }

    summary = _certificate_secret_summary(secret)

    assert summary is not None
    assert summary["private_key_present"] is True
    assert summary["private_key_returned"] is False
    assert "tls.key" not in str(summary)
    assert summary["certificates"][0]["subject"] == "CN=kubelet.test"
    assert summary["certificates"][0]["days_remaining"] >= 11


def test_k8s_node_evidence_summaries_capture_capacity_pod_state_and_events() -> None:
    node = {"status": {"capacity": {"pods": "110"}}}
    pods = [
        {
            "metadata": {"namespace": "poundcake", "name": "api-1"},
            "status": {
                "phase": "Running",
                "containerStatuses": [{"name": "api", "restartCount": 2, "ready": True}],
            },
        },
        {
            "metadata": {"namespace": "poundcake", "name": "api-2"},
            "status": {
                "phase": "Pending",
                "containerStatuses": [
                    {
                        "name": "api",
                        "restartCount": 1,
                        "ready": False,
                        "state": {"waiting": {"reason": "ImagePullBackOff"}},
                    }
                ],
            },
        },
    ]
    events = [
        {"type": "Normal", "reason": "Started"},
        {"type": "Warning", "reason": "EvictionThresholdMet"},
        {"type": "Warning", "reason": "EvictionThresholdMet"},
    ]

    assert _node_pod_summary(node, pods) == {
        "scheduled_pod_count": 2,
        "pod_capacity": 110,
        "pod_capacity_usage_ratio": 0.0182,
        "by_phase": {"Running": 1, "Pending": 1},
        "waiting_reasons": {"ImagePullBackOff": 1},
        "restart_count_total": 3,
    }
    assert _event_summary(events) == {
        "event_count": 3,
        "by_type": {"Normal": 1, "Warning": 2},
        "warning_reasons": {"EvictionThresholdMet": 2},
    }


def test_k8s_configmap_summary_omits_config_values() -> None:
    summary = _configmap_summary(
        {
            "metadata": {
                "name": "alertmanager-config",
                "namespace": "prometheus",
                "resourceVersion": "123",
                "annotations": {
                    "kubectl.kubernetes.io/last-applied-configuration": (
                        '{"data":{"alertmanager.yaml":"route:\\n  receiver: webhook\\n"}}'
                    ),
                },
            },
            "data": {"alertmanager.yaml": "route:\n  receiver: webhook\n"},
            "binaryData": {"bundle": "YWJj"},
        }
    )

    assert summary["data_keys"] == ["alertmanager.yaml"]
    assert summary["binary_data_keys"] == ["bundle"]
    assert summary["data_fingerprints"]["alertmanager.yaml"]["length"] == 27
    assert summary["annotation_keys"] == ["kubectl.kubernetes.io/last-applied-configuration"]
    assert "receiver: webhook" not in str(summary)


@pytest.mark.asyncio
async def test_k8s_adapter_maps_helper_result_to_execution_result() -> None:
    adapter = KubernetesExecutionAdapter(helper=_FakeKubernetesHelper())  # type: ignore[arg-type]
    result = await adapter.dispatch(
        _ctx(
            "apply",
            {
                "rule_name": "DemoAlert",
                "group_name": "demo",
                "crd_name": "demo-rules",
                "rule_data": {"alert": "DemoAlert", "expr": "vector(1)"},
            },
        )
    )

    assert result.status == "succeeded"
    assert result.result["success"] is True
    assert result.result["action"] == "updated"
    assert result.service_exec_id is not None
    assert ":succeeded:" in result.service_exec_id


@pytest.mark.asyncio
async def test_k8s_adapter_dispatches_pod_actions() -> None:
    adapter = KubernetesExecutionAdapter(helper=_FakeKubernetesHelper())  # type: ignore[arg-type]

    logs = await adapter.dispatch(
        _ctx(
            "logs",
            {"namespace": "poundcake", "pod_name": "api-123", "tail_lines": 50, "previous": True},
            service_exec="pod_action",
        )
    )
    delete = await adapter.dispatch(
        _ctx(
            "delete",
            {"namespace": "poundcake", "pod_name": "api-123"},
            service_exec="pod_action",
        )
    )

    assert logs.status == "succeeded"
    assert logs.result["logs"] == "hello from pod"
    assert logs.result["tail_lines"] == 50
    assert logs.result["previous"] is True
    assert delete.status == "succeeded"
    assert delete.result["action"] == "deleted"


@pytest.mark.asyncio
async def test_k8s_adapter_dispatches_deployment_actions() -> None:
    adapter = KubernetesExecutionAdapter(helper=_FakeKubernetesHelper())  # type: ignore[arg-type]

    scaled = await adapter.dispatch(
        _ctx(
            "scale",
            {"namespace": "poundcake", "deployment_name": "api", "replicas": 3},
            service_exec="deployment_action",
        )
    )
    restarted = await adapter.dispatch(
        _ctx(
            "rollout_restart",
            {"namespace": "poundcake", "deployment_name": "api"},
            service_exec="deployment_action",
        )
    )
    rollout = await adapter.dispatch(
        _ctx(
            "rollout_status",
            {"namespace": "poundcake", "deployment_name": "api"},
            service_exec="deployment_action",
        )
    )

    assert scaled.status == "succeeded"
    assert scaled.result["action"] == "scaled"
    assert scaled.result["replicas"] == 3
    assert restarted.status == "succeeded"
    assert restarted.result["action"] == "rollout_restarted"
    assert rollout.status == "succeeded"
    assert rollout.result["available_replicas"] == 2


@pytest.mark.asyncio
async def test_k8s_adapter_dispatches_workload_actions() -> None:
    adapter = KubernetesExecutionAdapter(helper=_FakeKubernetesHelper())  # type: ignore[arg-type]

    restarted = await adapter.dispatch(
        _ctx(
            "rollout_restart",
            {"namespace": "poundcake", "kind": "StatefulSet", "name": "mariadb"},
            service_exec="workload_action",
        )
    )
    rollout = await adapter.dispatch(
        _ctx(
            "rollout_status",
            {"namespace": "poundcake", "kind": "DaemonSet", "name": "node-exporter"},
            service_exec="workload_action",
        )
    )

    assert restarted.status == "succeeded"
    assert restarted.result["kind"] == "StatefulSet"
    assert restarted.result["action"] == "rollout_restarted"
    assert rollout.status == "succeeded"
    assert rollout.result["kind"] == "DaemonSet"
    assert rollout.result["rollout"]["ready"] is True


@pytest.mark.asyncio
async def test_k8s_adapter_dispatches_workload_triage_actions() -> None:
    adapter = KubernetesExecutionAdapter(helper=_FakeKubernetesHelper())  # type: ignore[arg-type]

    logs = await adapter.dispatch(
        _ctx(
            "logs",
            {
                "namespace": "poundcake",
                "kind": "Deployment",
                "name": "api",
                "tail_lines": 25,
                "limit": 2,
            },
            service_exec="workload_triage",
        )
    )
    node = await adapter.dispatch(
        _ctx("node_diagnostics", {"node": "worker-1"}, service_exec="workload_triage")
    )
    pvc = await adapter.dispatch(
        _ctx(
            "pvc_diagnostics",
            {"namespace": "poundcake", "persistentvolumeclaim": "data-api-0"},
            service_exec="workload_triage",
        )
    )
    service = await adapter.dispatch(
        _ctx(
            "service_diagnostics",
            {"namespace": "poundcake", "service": "api"},
            service_exec="workload_triage",
        )
    )
    config = await adapter.dispatch(
        _ctx(
            "config_diagnostics",
            {"namespace": "prometheus", "configmap": "alertmanager-config"},
            service_exec="workload_triage",
        )
    )
    certificate = await adapter.dispatch(
        _ctx(
            "certificate_diagnostics",
            {"namespace": "kube-system", "secret": "kubelet-serving-cert", "limit": 1},
            service_exec="workload_triage",
        )
    )

    assert logs.status == "succeeded"
    assert logs.result["kind"] == "Deployment"
    assert logs.result["tail_lines"] == 25
    assert logs.result["limit"] == 2
    assert node.status == "succeeded"
    assert node.result["node"] == "worker-1"
    assert pvc.status == "succeeded"
    assert pvc.result["persistentvolumeclaim"] == "data-api-0"
    assert service.status == "succeeded"
    assert service.result["service"] == "api"
    assert config.status == "succeeded"
    assert config.result["configmap"] == "alertmanager-config"
    assert certificate.status == "succeeded"
    assert certificate.result["secret"] == "kubelet-serving-cert"
    assert certificate.result["items"][0]["private_key_returned"] is False


@pytest.mark.asyncio
async def test_k8s_adapter_dispatches_node_triage_actions() -> None:
    adapter = KubernetesExecutionAdapter(helper=_FakeKubernetesHelper())  # type: ignore[arg-type]

    nodes = await adapter.dispatch(
        _ctx(
            "list_nodes", {"label_selector": "role=worker", "limit": 3}, service_exec="node_triage"
        )
    )
    pressure = await adapter.dispatch(
        _ctx("node_pressure", {"node": "worker-1", "limit": 2}, service_exec="node_triage")
    )
    pods = await adapter.dispatch(
        _ctx(
            "node_pods",
            {
                "node": "worker-1",
                "namespace": "poundcake",
                "label_selector": "app=poundcake",
                "include_succeeded": True,
                "limit": 5,
            },
            service_exec="node_triage",
        )
    )
    capacity = await adapter.dispatch(
        _ctx("node_capacity", {"name": "worker-1"}, service_exec="node_triage")
    )

    assert nodes.status == "succeeded"
    assert nodes.result["items"][0]["selector"] == "role=worker"
    assert pressure.status == "succeeded"
    assert pressure.result["limit"] == 2
    assert pods.status == "succeeded"
    assert pods.result["namespace"] == "poundcake"
    assert pods.result["include_succeeded"] is True
    assert capacity.status == "succeeded"
    assert capacity.result["capacity"] == {"cpu": "4"}


@pytest.mark.asyncio
async def test_k8s_adapter_dispatches_failed_job_cleanup() -> None:
    adapter = KubernetesExecutionAdapter(helper=_FakeKubernetesHelper())  # type: ignore[arg-type]

    result = await adapter.dispatch(
        _ctx(
            "delete",
            {"namespace": "poundcake", "job_name": "backup-123"},
            service_exec="failed_job_cleanup",
        )
    )

    assert result.status == "succeeded"
    assert result.result["action"] == "deleted"
    assert result.result["job_name"] == "backup-123"


@pytest.mark.asyncio
async def test_k8s_adapter_dispatches_resource_pressure_remediation() -> None:
    adapter = KubernetesExecutionAdapter(helper=_FakeKubernetesHelper())  # type: ignore[arg-type]

    scale_result = await adapter.dispatch(
        _ctx(
            "scale_deployment",
            {"namespace": "poundcake", "deployment_name": "api", "replicas": 4},
            service_exec="resource_pressure_remediation",
        )
    )
    hpa_result = await adapter.dispatch(
        _ctx(
            "patch_hpa_bounds",
            {"namespace": "poundcake", "hpa_name": "api", "min_replicas": 2, "max_replicas": 6},
            service_exec="resource_pressure_remediation",
        )
    )

    assert scale_result.status == "succeeded"
    assert scale_result.result["action"] == "scaled"
    assert scale_result.result["replicas"] == 4
    assert hpa_result.status == "succeeded"
    assert hpa_result.result["action"] == "patched_hpa_bounds"
    assert hpa_result.result["applied_max_replicas"] == 6


@pytest.mark.asyncio
async def test_k8s_adapter_dispatches_service_probe() -> None:
    adapter = KubernetesExecutionAdapter(helper=_FakeKubernetesHelper())  # type: ignore[arg-type]

    dns_result = await adapter.dispatch(
        _ctx(
            "dns",
            {"namespace": "poundcake", "service": "api"},
            service_exec="service_probe",
        )
    )
    http_result = await adapter.dispatch(
        _ctx(
            "http",
            {
                "namespace": "poundcake",
                "service": "api",
                "port": 8080,
                "path": "/ready",
                "expected_status_codes": [200],
            },
            service_exec="service_probe",
        )
    )

    assert dns_result.status == "succeeded"
    assert dns_result.result["operation"] == "dns"
    assert dns_result.result["service"] == "api"
    assert http_result.status == "succeeded"
    assert http_result.result["operation"] == "http"
    assert http_result.result["expected_status_codes"] == [200]


@pytest.mark.asyncio
async def test_k8s_adapter_poll_preserves_terminal_receipt_status() -> None:
    adapter = KubernetesExecutionAdapter(helper=_FakeKubernetesHelper())  # type: ignore[arg-type]
    ctx = _ctx(
        "logs",
        {"namespace": "poundcake", "pod_name": "api-123"},
        service_exec="pod_action",
    )

    dispatch_result = await adapter.dispatch(ctx)
    poll_result = await adapter.poll(ctx, dispatch_result.service_exec_id or "")

    assert dispatch_result.status == "succeeded"
    assert poll_result.status == "succeeded"
    assert poll_result.result == {
        "success": True,
        "status": "succeeded",
        "service_exec": "pod_action",
        "operation": "logs",
        "message": "Kubernetes execution completed during dispatch with status=succeeded",
    }


@pytest.mark.asyncio
async def test_k8s_adapter_poll_preserves_failed_receipt_status() -> None:
    class _FailedPodActionHelper(_FakeKubernetesHelper):
        async def get_pod_logs(
            self,
            *,
            namespace: str,
            pod_name: str,
            label_selector: str = "",
            container: str = "",
            tail_lines: int | None = None,
            since_seconds: int | None = None,
            previous: bool = False,
        ) -> dict[str, object]:
            del namespace, pod_name, label_selector, container, tail_lines, since_seconds, previous
            return {"success": False, "status": "failed", "message": "pod logs unavailable"}

    adapter = KubernetesExecutionAdapter(helper=_FailedPodActionHelper())  # type: ignore[arg-type]
    ctx = _ctx(
        "logs",
        {"namespace": "poundcake", "pod_name": "api-123"},
        service_exec="pod_action",
    )

    dispatch_result = await adapter.dispatch(ctx)
    poll_result = await adapter.poll(ctx, dispatch_result.service_exec_id or "")

    assert dispatch_result.status == "failed"
    assert dispatch_result.service_exec_id is not None
    assert ":failed:" in dispatch_result.service_exec_id
    assert poll_result.status == "failed"
    assert poll_result.result == {
        "success": False,
        "status": "failed",
        "service_exec": "pod_action",
        "operation": "logs",
        "message": "Kubernetes execution completed during dispatch with status=failed",
    }


@pytest.mark.asyncio
async def test_k8s_adapter_cancel_returns_failed_unsupported_result() -> None:
    adapter = KubernetesExecutionAdapter(helper=_FakeKubernetesHelper())  # type: ignore[arg-type]
    ctx = _ctx(
        "logs",
        {"namespace": "poundcake", "pod_name": "api-123"},
        service_exec="pod_action",
    )

    dispatch_result = await adapter.dispatch(ctx)
    cancel_result = await adapter.cancel(ctx, dispatch_result.service_exec_id or "")

    assert cancel_result.status == "failed"
    assert cancel_result.result == {
        "success": False,
        "status": "unsupported",
        "service_exec": "pod_action",
        "operation": "logs",
        "message": "Cancellation is not supported by the Kubernetes plugin",
    }


def test_k8s_helper_accepts_explicit_client_config() -> None:
    helper = KubernetesHelper(
        client_factory=KubernetesClientFactory(
            config=KubernetesClientConfig(namespace="poundcake", allow_local_kubeconfig=True)
        )
    )

    assert helper.client_factory.config.namespace == "poundcake"
    assert helper.client_factory.config.allow_local_kubeconfig is True


@pytest.mark.asyncio
async def test_k8s_health_stays_healthy_when_prometheus_rule_crd_is_missing() -> None:
    helper = KubernetesHelper(client_factory=_FakeClientFactory())  # type: ignore[arg-type]

    result = await helper.health_check()

    assert result["success"] is True
    assert result["status"] == "healthy"
    assert result["version"] == "v1.30.0"
    assert result["capabilities"] == {
        "k8s.cluster.connect": "healthy",
        "k8s.prometheusrules.manage": "missing_crd",
    }
