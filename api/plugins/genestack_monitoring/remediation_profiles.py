"""Managed Genestack remediation recipe profiles."""

from __future__ import annotations

import re
from dataclasses import dataclass

from api.types import JSONObject

MANAGED_REMEDIATION_MARKER = "genestack_monitoring"
GUARD_ROLE = "remediation_precondition"
GUARD_FALSE_OUTCOME = "cancel_downstream_no_remediation"
BLACKBOX_SERVICE_DOWN_GROUP = "blackbox-service-down"
GENERIC_CROSS_ADAPTER_DOMAINS: frozenset[str] = frozenset(
    {
        "billing",
        "hpraid",
        "imported",
        "mariadb",
        "megaraid",
        "node",
        "openstack",
        "ovn",
        "prometheus-stack",
        "rabbitmq",
    }
)

KUBERNETES_ALERT_GROUPS: frozenset[str] = frozenset(
    {
        "cpu-throttling-high",
        "kube-aggregated-api-down",
        "kube-aggregated-api-errors",
        "kube-api-down",
        "kube-api-error-budget-burn",
        "kube-api-terminated-requests",
        "kube-client-certificate-expiration",
        "kube-client-errors",
        "kube-container-waiting",
        "kube-controller-manager-down",
        "kube-cpu-overcommit",
        "kube-cpu-quota-overcommit",
        "kube-daemonset-misscheduled",
        "kube-daemonset-not-scheduled",
        "kube-daemonset-rollout-stuck",
        "kube-deployment-generation-mismatch",
        "kube-deployment-replicas-mismatch",
        "kube-deployment-rollout-stuck",
        "kube-hpa-maxed-out",
        "kube-hpa-replicas-mismatch",
        "kube-job-failed",
        "kube-job-not-completed",
        "kube-memory-overcommit",
        "kube-node-eviction",
        "kube-node-not-ready",
        "kube-node-pressure",
        "kube-node-readiness-flapping",
        "kube-node-unreachable",
        "kube-pdb-not-enough-healthy-pods",
        "kube-persistent-volume-errors",
        "kube-persistent-volume-filling-up",
        "kube-persistent-volume-inodes-filling-up",
        "kube-pod-container-restarts",
        "kube-pod-crash-looping",
        "kube-pod-not-ready",
        "kube-proxy-down",
        "kube-scheduler-down",
        "kube-statefulset-generation-mismatch",
        "kube-statefulset-replicas-mismatch",
        "kube-statefulset-update-not-rolled-out",
        "kube-version-mismatch",
        "kubelet-client-certificate-expiration",
        "kubelet-client-certificate-renewal-errors",
        "kubelet-down",
        "kubelet-pod-startup-latency-high",
        "kubelet-server-certificate-expiration",
        "kubelet-server-certificate-renewal-errors",
        "kubelet-too-many-pods",
        "target-down",
    }
)

BLACKBOX_ALERT_GROUPS: frozenset[str] = frozenset({BLACKBOX_SERVICE_DOWN_GROUP})

ETCD_ALERT_GROUPS: frozenset[str] = frozenset(
    {
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
)

POD_REMEDIATION_GROUPS: frozenset[str] = frozenset(
    {
        "kube-container-waiting",
        "kube-pod-container-restarts",
        "kube-pod-crash-looping",
        "kube-pod-not-ready",
    }
)

DEPLOYMENT_REMEDIATION_GROUPS: frozenset[str] = frozenset(
    {
        "kube-deployment-generation-mismatch",
        "kube-deployment-replicas-mismatch",
        "kube-deployment-rollout-stuck",
    }
)

STATEFULSET_REMEDIATION_GROUPS: frozenset[str] = frozenset(
    {
        "kube-statefulset-update-not-rolled-out",
    }
)

STATEFULSET_DIAGNOSTIC_GROUPS: frozenset[str] = frozenset(
    {
        "kube-statefulset-generation-mismatch",
        "kube-statefulset-replicas-mismatch",
    }
)

DAEMONSET_REMEDIATION_GROUPS: frozenset[str] = frozenset(
    {
        "kube-daemonset-rollout-stuck",
    }
)

DAEMONSET_DIAGNOSTIC_GROUPS: frozenset[str] = frozenset(
    {
        "kube-daemonset-misscheduled",
        "kube-daemonset-not-scheduled",
    }
)

CERTIFICATE_DIAGNOSTIC_GROUPS: frozenset[str] = frozenset(
    {
        "kube-client-certificate-expiration",
        "kubelet-client-certificate-expiration",
        "kubelet-client-certificate-renewal-errors",
        "kubelet-server-certificate-expiration",
        "kubelet-server-certificate-renewal-errors",
    }
)

NODE_TRIAGE_DIAGNOSTIC_GROUPS: frozenset[str] = frozenset(
    {
        "kube-node-eviction",
        "kube-node-not-ready",
        "kube-node-pressure",
        "kube-node-readiness-flapping",
        "kube-node-unreachable",
        "kubelet-pod-startup-latency-high",
        "kubelet-too-many-pods",
    }
)

PVC_DIAGNOSTIC_GROUPS: frozenset[str] = frozenset(
    {
        "kube-persistent-volume-filling-up",
        "kube-persistent-volume-inodes-filling-up",
    }
)

PV_DIAGNOSTIC_GROUPS: frozenset[str] = frozenset({"kube-persistent-volume-errors"})

PDB_HPA_DIAGNOSTIC_GROUPS: frozenset[str] = frozenset(
    {
        "kube-hpa-maxed-out",
        "kube-hpa-replicas-mismatch",
        "kube-pdb-not-enough-healthy-pods",
    }
)

SERVICE_DIAGNOSTIC_GROUPS: frozenset[str] = frozenset({"target-down"})

DIAGNOSTIC_ONLY_KUBERNETES_GROUPS: frozenset[str] = frozenset(
    {
        "kube-controller-manager-down",
        "kube-proxy-down",
        "kube-scheduler-down",
        "target-down",
    }
)


@dataclass(frozen=True)
class RemediationStepSpec:
    role: str
    service_type: str
    service_exec: str
    task_key_template: str
    service_payload: JSONObject
    service_exec_parameters: JSONObject
    expected_outcome: JSONObject
    expected_secs: int
    timeout: int
    destination_target: str | None = None
    run_phase: str = "firing"
    run_condition: str = "always"


def kubernetes_alert_group(alert_name: str, source_path: str = "") -> str | None:
    normalized = _alert_group_slug(alert_name)
    if normalized in KUBERNETES_ALERT_GROUPS:
        return normalized
    if "/kubernetes/" in f"/{source_path.strip('/')}":
        return normalized or None
    return None


def remediation_step_specs(
    alert_name: str,
    source_path: str = "",
    rule_data: JSONObject | None = None,
) -> list[RemediationStepSpec]:
    blackbox_group = blackbox_alert_group(alert_name, source_path)
    if blackbox_group == BLACKBOX_SERVICE_DOWN_GROUP:
        return [
            _guard_step("verify_before_evidence"),
            _alertmanager_evidence_step(),
            _blackbox_evidence_step(),
            _guard_step("verify_before_action"),
            _blackbox_action_step(),
            _blackbox_recovery_step(),
            _bakery_communication_step(),
        ]

    etcd_group = etcd_alert_group(alert_name, source_path)
    if etcd_group in ETCD_ALERT_GROUPS:
        return [
            _guard_step("verify_before_evidence"),
            _alertmanager_evidence_step(),
            _etcd_evidence_step(etcd_group),
            _guard_step("verify_before_action"),
            _etcd_action_step(etcd_group),
            _etcd_recovery_step(etcd_group),
            _bakery_communication_step(),
        ]

    group = kubernetes_alert_group(alert_name, source_path)
    if group and group in KUBERNETES_ALERT_GROUPS:
        if group in (
            DIAGNOSTIC_ONLY_KUBERNETES_GROUPS
            | STATEFULSET_DIAGNOSTIC_GROUPS
            | DAEMONSET_DIAGNOSTIC_GROUPS
            | CERTIFICATE_DIAGNOSTIC_GROUPS
            | PVC_DIAGNOSTIC_GROUPS
            | PV_DIAGNOSTIC_GROUPS
            | PDB_HPA_DIAGNOSTIC_GROUPS
            | SERVICE_DIAGNOSTIC_GROUPS
        ):
            return [
                _guard_step("verify_before_evidence"),
                _evidence_step(group),
                _guard_step("verify_before_action"),
                _operator_review_action_step(alert_name, source_path),
                _bakery_communication_step(),
            ]
        return [
            _guard_step("verify_before_evidence"),
            _evidence_step(group),
            _guard_step("verify_before_action"),
            _action_step(group, alert_name),
            _bakery_communication_step(),
        ]

    domain = alert_domain(source_path)
    if domain in GENERIC_CROSS_ADAPTER_DOMAINS:
        return [
            _guard_step("verify_before_evidence"),
            _alertmanager_evidence_step(),
            _prometheus_rule_evidence_step(alert_name, source_path, rule_data),
            _source_rule_evidence_step(source_path),
            _guard_step("verify_before_action"),
            _operator_review_action_step(alert_name, source_path),
            _bakery_communication_step(),
        ]
    return []


def blackbox_alert_group(alert_name: str, source_path: str = "") -> str | None:
    normalized = _alert_group_slug(alert_name)
    if normalized in BLACKBOX_ALERT_GROUPS:
        return normalized
    if "/blackbox/" in f"/{source_path.strip('/')}":
        return normalized or None
    return None


def etcd_alert_group(alert_name: str, source_path: str = "") -> str | None:
    normalized = _alert_group_slug(alert_name)
    if normalized in ETCD_ALERT_GROUPS:
        return normalized
    if "/etcd/" in f"/{source_path.strip('/')}":
        return normalized or None
    return None


def workflow_name_for_group(group: str) -> str:
    return f"kubernetes_{_slug(group).replace('-', '_')}_remediation"


def etcd_workflow_name_for_group(group: str, phase: str) -> str:
    return f"etcd_{_slug(group).replace('-', '_')}_{phase}"


def _guard_step(role: str) -> RemediationStepSpec:
    return RemediationStepSpec(
        role=role,
        service_type="alertmanager",
        service_exec="inspect",
        task_key_template="alertmanager-firing-guard",
        service_payload={
            "fingerprint": "{{ order.raw_data.fingerprint }}",
            "labels": "{{ order.labels }}",
            "active": True,
            "limit": 1,
        },
        service_exec_parameters={
            "operation": "verify_firing",
            "guard_role": GUARD_ROLE,
            "false_outcome": GUARD_FALSE_OUTCOME,
        },
        expected_outcome={"is_firing": True},
        expected_secs=5,
        timeout=30,
    )


def _alertmanager_evidence_step() -> RemediationStepSpec:
    return RemediationStepSpec(
        role="gather_alertmanager_evidence",
        service_type="alertmanager",
        service_exec="inspect",
        task_key_template="alertmanager-inspect",
        service_payload={
            "labels": "{{ order.labels }}",
            "matchers": [
                'alertname="{{ order.raw_data.alertname }}"',
                'group_name="{{ order.alert_group_name }}"',
                'instance="{{ order.labels.instance }}"',
            ],
            "active": True,
            "silenced": False,
            "inhibited": False,
            "limit": 20,
        },
        service_exec_parameters={"operation": "list_alerts"},
        expected_outcome={"success": True},
        expected_secs=10,
        timeout=60,
    )


def _blackbox_workflow_inputs() -> JSONObject:
    return {
        "alert_name": alert_name_template(),
        "alert_group_name": "{{ order.alert_group_name }}",
        "severity": "{{ order.labels.severity }}",
        "instance": "{{ order.labels.instance }}",
        "labels": "{{ order.labels }}",
        "annotations": "{{ order.annotations }}",
        "order_id": "{{ order.id }}",
        "req_id": "{{ order.req_id }}",
        "method": "HEAD",
        "timeout": 15,
        "evidence": {},
    }


def _blackbox_evidence_step() -> RemediationStepSpec:
    return RemediationStepSpec(
        role="gather_endpoint_evidence",
        service_type="stackstorm",
        service_exec="workflow_execution",
        task_key_template="stackstorm-workflow-execution",
        service_payload={
            "workflow_ref": "poundcake.blackbox_service_down_evidence",
            "inputs": _blackbox_workflow_inputs(),
        },
        service_exec_parameters={
            "operation": "execute_workflow",
            "mutation_family": "blackbox_probe_evidence",
        },
        expected_outcome={"status": "succeeded"},
        expected_secs=30,
        timeout=180,
    )


def _blackbox_action_step() -> RemediationStepSpec:
    inputs = _blackbox_workflow_inputs()
    return RemediationStepSpec(
        role="action_alert",
        service_type="stackstorm",
        service_exec="workflow_execution",
        task_key_template="stackstorm-workflow-execution",
        service_payload={
            "workflow_ref": "poundcake.blackbox_service_down_remediation",
            "inputs": inputs,
        },
        service_exec_parameters={
            "operation": "execute_workflow",
            "mutation_family": "blackbox_service_down",
        },
        expected_outcome={"status": "succeeded"},
        expected_secs=30,
        timeout=300,
    )


def _blackbox_recovery_step() -> RemediationStepSpec:
    inputs = _blackbox_workflow_inputs()
    inputs["method"] = "GET"
    return RemediationStepSpec(
        role="verify_recovery",
        service_type="stackstorm",
        service_exec="workflow_execution",
        task_key_template="stackstorm-workflow-execution",
        service_payload={
            "workflow_ref": "poundcake.blackbox_service_down_verify_recovery",
            "inputs": inputs,
        },
        service_exec_parameters={
            "operation": "execute_workflow",
            "mutation_family": "blackbox_recovery_probe",
        },
        expected_outcome={"status": "succeeded"},
        expected_secs=30,
        timeout=180,
    )


def _etcd_workflow_inputs() -> JSONObject:
    return {
        "alert_name": alert_name_template(),
        "alert_group_name": "{{ order.alert_group_name }}",
        "severity": "{{ order.labels.severity }}",
        "instance": "{{ order.labels.instance }}",
        "job": "{{ order.labels.job }}",
        "cluster": "{{ order.labels.cluster }}",
        "labels": "{{ order.labels }}",
        "annotations": "{{ order.annotations }}",
        "order_id": "{{ order.id }}",
        "req_id": "{{ order.req_id }}",
        "evidence": {},
    }


def _etcd_evidence_step(group: str) -> RemediationStepSpec:
    workflow = etcd_workflow_name_for_group(group, "evidence")
    return RemediationStepSpec(
        role="gather_etcd_evidence",
        service_type="stackstorm",
        service_exec="workflow_execution",
        task_key_template="stackstorm-workflow-execution",
        service_payload={
            "workflow_ref": f"poundcake.{workflow}",
            "inputs": _etcd_workflow_inputs(),
        },
        service_exec_parameters={
            "operation": "execute_workflow",
            "mutation_family": "etcd_evidence",
            "managed_role": "gather_etcd_evidence",
            "evidence_family": "etcd",
        },
        expected_outcome={"status": "succeeded"},
        expected_secs=30,
        timeout=180,
    )


def _etcd_action_step(group: str) -> RemediationStepSpec:
    workflow = etcd_workflow_name_for_group(group, "remediation")
    return RemediationStepSpec(
        role="action_alert",
        service_type="stackstorm",
        service_exec="workflow_execution",
        task_key_template="stackstorm-workflow-execution",
        service_payload={
            "workflow_ref": f"poundcake.{workflow}",
            "inputs": _etcd_workflow_inputs(),
        },
        service_exec_parameters={
            "operation": "execute_workflow",
            "mutation_family": "etcd_operator_review",
        },
        expected_outcome={"status": "succeeded"},
        expected_secs=30,
        timeout=300,
    )


def _etcd_recovery_step(group: str) -> RemediationStepSpec:
    workflow = etcd_workflow_name_for_group(group, "verify_recovery")
    return RemediationStepSpec(
        role="verify_recovery",
        service_type="stackstorm",
        service_exec="workflow_execution",
        task_key_template="stackstorm-workflow-execution",
        service_payload={
            "workflow_ref": f"poundcake.{workflow}",
            "inputs": _etcd_workflow_inputs(),
        },
        service_exec_parameters={
            "operation": "execute_workflow",
            "mutation_family": "etcd_recovery_evidence",
        },
        expected_outcome={"status": "succeeded"},
        expected_secs=30,
        timeout=180,
    )


def _evidence_step(group: str) -> RemediationStepSpec:
    if group in POD_REMEDIATION_GROUPS:
        return RemediationStepSpec(
            role="gather_evidence",
            service_type="k8s",
            service_exec="workload_triage",
            task_key_template="k8s-workload-triage-v2",
            service_payload={
                "namespace": "{{ order.labels.namespace }}",
                "pod_name": "{{ order.labels.pod }}",
                "container": "{{ order.labels.container }}",
                "tail_lines": 200,
                "previous": True,
            },
            service_exec_parameters={
                "operation": "pod_diagnostics",
                "managed_role": "gather_evidence",
                "evidence_family": "kubernetes",
            },
            expected_outcome={"success": True},
            expected_secs=20,
            timeout=180,
        )
    if group in DEPLOYMENT_REMEDIATION_GROUPS:
        return _controller_evidence_step(kind="Deployment", name_label="deployment")
    if group in STATEFULSET_REMEDIATION_GROUPS | STATEFULSET_DIAGNOSTIC_GROUPS:
        return _controller_evidence_step(kind="StatefulSet", name_label="statefulset")
    if group in DAEMONSET_REMEDIATION_GROUPS | DAEMONSET_DIAGNOSTIC_GROUPS:
        return _controller_evidence_step(kind="DaemonSet", name_label="daemonset")
    if group in CERTIFICATE_DIAGNOSTIC_GROUPS:
        return RemediationStepSpec(
            role="gather_evidence",
            service_type="k8s",
            service_exec="workload_triage",
            task_key_template="k8s-workload-triage-v2",
            service_payload={
                "limit": 20,
            },
            service_exec_parameters={
                "operation": "certificate_diagnostics",
                "managed_role": "gather_evidence",
                "evidence_family": "kubernetes",
            },
            expected_outcome={"success": True},
            expected_secs=20,
            timeout=180,
        )
    if group in NODE_TRIAGE_DIAGNOSTIC_GROUPS:
        return RemediationStepSpec(
            role="gather_evidence",
            service_type="k8s",
            service_exec="node_triage",
            task_key_template="k8s-node-triage",
            service_payload={
                "node": "{{ order.labels.node }}",
                "limit": 20,
            },
            service_exec_parameters={
                "operation": "node_pressure",
                "managed_role": "gather_evidence",
                "evidence_family": "kubernetes",
            },
            expected_outcome={"success": True},
            expected_secs=20,
            timeout=180,
        )
    if group in PVC_DIAGNOSTIC_GROUPS:
        return RemediationStepSpec(
            role="gather_evidence",
            service_type="k8s",
            service_exec="workload_triage",
            task_key_template="k8s-workload-triage-v2",
            service_payload={
                "namespace": "{{ order.labels.namespace }}",
                "persistentvolumeclaim": "{{ order.labels.persistentvolumeclaim }}",
            },
            service_exec_parameters={
                "operation": "pvc_diagnostics",
                "managed_role": "gather_evidence",
                "evidence_family": "kubernetes",
            },
            expected_outcome={"success": True},
            expected_secs=20,
            timeout=180,
        )
    if group in PV_DIAGNOSTIC_GROUPS:
        return RemediationStepSpec(
            role="gather_evidence",
            service_type="k8s",
            service_exec="workload_triage",
            task_key_template="k8s-workload-triage-v2",
            service_payload={
                "persistentvolume": "{{ order.labels.persistentvolume }}",
            },
            service_exec_parameters={
                "operation": "pvc_diagnostics",
                "managed_role": "gather_evidence",
                "evidence_family": "kubernetes",
            },
            expected_outcome={"success": True},
            expected_secs=20,
            timeout=180,
        )
    if group in PDB_HPA_DIAGNOSTIC_GROUPS:
        payload: JSONObject = {"namespace": "{{ order.labels.namespace }}"}
        if group == "kube-pdb-not-enough-healthy-pods":
            payload["poddisruptionbudget"] = "{{ order.labels.poddisruptionbudget }}"
        else:
            payload["horizontalpodautoscaler"] = "{{ order.labels.horizontalpodautoscaler }}"
        return RemediationStepSpec(
            role="gather_evidence",
            service_type="k8s",
            service_exec="workload_triage",
            task_key_template="k8s-workload-triage-v2",
            service_payload=payload,
            service_exec_parameters={
                "operation": "pdb_hpa_diagnostics",
                "managed_role": "gather_evidence",
                "evidence_family": "kubernetes",
            },
            expected_outcome={"success": True},
            expected_secs=20,
            timeout=180,
        )
    if group in SERVICE_DIAGNOSTIC_GROUPS:
        return RemediationStepSpec(
            role="gather_evidence",
            service_type="k8s",
            service_exec="workload_triage",
            task_key_template="k8s-workload-triage-v2",
            service_payload={
                "namespace": "{{ order.labels.namespace }}",
                "service": "{{ order.labels.job }}",
            },
            service_exec_parameters={
                "operation": "service_diagnostics",
                "managed_role": "gather_evidence",
                "evidence_family": "kubernetes",
            },
            expected_outcome={"success": True},
            expected_secs=20,
            timeout=180,
        )
    return RemediationStepSpec(
        role="gather_evidence",
        service_type="k8s",
        service_exec="node_triage",
        task_key_template="k8s-node-triage",
        service_payload={"limit": 50},
        service_exec_parameters={
            "operation": "list_nodes",
            "managed_role": "gather_evidence",
            "evidence_family": "kubernetes",
        },
        expected_outcome={"success": True},
        expected_secs=20,
        timeout=180,
    )


def _controller_evidence_step(*, kind: str, name_label: str) -> RemediationStepSpec:
    return RemediationStepSpec(
        role="gather_evidence",
        service_type="k8s",
        service_exec="workload_triage",
        task_key_template="k8s-workload-triage-v2",
        service_payload={
            "namespace": "{{ order.labels.namespace }}",
            "kind": kind,
            "name": f"{{{{ order.labels.{name_label} }}}}",
            "limit": 20,
        },
        service_exec_parameters={
            "operation": "workload_status",
            "managed_role": "gather_evidence",
            "evidence_family": "kubernetes",
        },
        expected_outcome={"success": True},
        expected_secs=20,
        timeout=180,
    )


def _action_step(group: str, alert_name: str) -> RemediationStepSpec:
    if group in POD_REMEDIATION_GROUPS:
        return RemediationStepSpec(
            role="action_alert",
            service_type="k8s",
            service_exec="pod_action",
            task_key_template="k8s-pod-action",
            service_payload={
                "namespace": "{{ order.labels.namespace }}",
                "pod_name": "{{ order.labels.pod }}",
            },
            service_exec_parameters={
                "operation": "delete",
                "mutation_family": "pod_delete",
                "require_controller_owned": True,
            },
            expected_outcome={"success": True},
            expected_secs=10,
            timeout=120,
        )
    if group in DEPLOYMENT_REMEDIATION_GROUPS:
        return RemediationStepSpec(
            role="action_alert",
            service_type="k8s",
            service_exec="deployment_action",
            task_key_template="k8s-deployment-action",
            service_payload={
                "namespace": "{{ order.labels.namespace }}",
                "deployment_name": "{{ order.labels.deployment }}",
            },
            service_exec_parameters={
                "operation": "rollout_restart",
                "mutation_family": "deployment_rollout_restart",
            },
            expected_outcome={"success": True},
            expected_secs=10,
            timeout=180,
        )
    if group in STATEFULSET_REMEDIATION_GROUPS:
        return _controller_rollout_restart_step(
            kind="StatefulSet",
            name_label="statefulset",
            mutation_family="statefulset_rollout_restart",
        )
    if group in DAEMONSET_REMEDIATION_GROUPS:
        return _controller_rollout_restart_step(
            kind="DaemonSet",
            name_label="daemonset",
            mutation_family="daemonset_rollout_restart",
        )
    workflow = workflow_name_for_group(group)
    return RemediationStepSpec(
        role="action_alert",
        service_type="stackstorm",
        service_exec="workflow_execution",
        task_key_template="stackstorm-workflow-execution",
        service_payload={
            "workflow_ref": f"poundcake.{workflow}",
            "inputs": {
                "alert_name": alert_name_template(),
                "alert_group_name": "{{ order.alert_group_name }}",
                "severity": "{{ order.labels.severity }}",
                "labels": "{{ order.labels }}",
                "annotations": "{{ order.annotations }}",
                "order_id": "{{ order.id }}",
                "req_id": "{{ order.req_id }}",
                "evidence": {},
            },
        },
        service_exec_parameters={
            "operation": "execute_workflow",
            "mutation_family": "stackstorm_workflow",
        },
        expected_outcome={"status": "succeeded"},
        expected_secs=60,
        timeout=600,
    )


def _controller_rollout_restart_step(
    *,
    kind: str,
    name_label: str,
    mutation_family: str,
) -> RemediationStepSpec:
    return RemediationStepSpec(
        role="action_alert",
        service_type="k8s",
        service_exec="workload_action",
        task_key_template="k8s-workload-action",
        service_payload={
            "namespace": "{{ order.labels.namespace }}",
            "kind": kind,
            "name": f"{{{{ order.labels.{name_label} }}}}",
        },
        service_exec_parameters={
            "operation": "rollout_restart",
            "mutation_family": mutation_family,
        },
        expected_outcome={"success": True},
        expected_secs=10,
        timeout=180,
    )


def _prometheus_rule_evidence_step(
    alert_name: str,
    source_path: str,
    rule_data: JSONObject | None,
) -> RemediationStepSpec:
    rule = rule_data if isinstance(rule_data, dict) else {}
    return RemediationStepSpec(
        role="gather_prometheus_evidence",
        service_type="prometheus",
        service_exec="inspect",
        task_key_template="prometheus-inspect",
        service_payload={
            "alert_name": alert_name,
            "query": str(rule.get("expr") or f'ALERTS{{alertname="{alert_name}"}}'),
            "labels": "{{ order.labels }}",
            "lookback_seconds": 3600,
            "step_seconds": 60,
        },
        service_exec_parameters={
            "operation": "alert_evidence",
            "evidence_family": alert_domain(source_path),
            "alert_name": alert_name,
        },
        expected_outcome={"success": True},
        expected_secs=15,
        timeout=90,
    )


def _source_rule_evidence_step(source_path: str) -> RemediationStepSpec:
    return RemediationStepSpec(
        role="gather_source_rule_evidence",
        service_type="github",
        service_exec="repo_read",
        task_key_template="github-repo-read",
        service_payload={
            "repo": "rackerlabs/genestack-monitoring",
            "ref": "main",
            "path": source_path,
        },
        service_exec_parameters={
            "operation": "read_file",
            "evidence_family": "alert_source",
        },
        expected_outcome={"success": True},
        expected_secs=5,
        timeout=60,
    )


def _operator_review_action_step(alert_name: str, source_path: str) -> RemediationStepSpec:
    domain = _alert_domain_for(alert_name, source_path)
    return RemediationStepSpec(
        role="action_alert",
        service_type="bakery",
        service_exec="communication",
        task_key_template="bakery-comms",
        service_payload={
            "source": "genestack_monitoring",
            "title": "PoundCake action review: {{ order.alert_group_name }}",
            "description": (
                "PoundCake validated the alert twice and gathered evidence. "
                "No safer native adapter action is configured for this alert family."
            ),
            "severity": "{{ order.labels.severity }}",
            "category": domain,
            "state": "open",
            "message": "Critical alert remains firing after evidence collection; operator action required.",
            "context": {
                "alert_name": alert_name_template(),
                "alert_group_name": "{{ order.alert_group_name }}",
                "labels": "{{ order.labels }}",
                "annotations": "{{ order.annotations }}",
                "order_id": "{{ order.id }}",
                "req_id": "{{ order.req_id }}",
                "source_path": source_path,
                "operator_review_required": True,
            },
        },
        service_exec_parameters={
            "operation": "open",
            "mutation_family": "operator_review",
            "adapter_extension_candidate": domain,
        },
        expected_outcome={"success": True},
        expected_secs=5,
        timeout=120,
    )


def _bakery_communication_step() -> RemediationStepSpec:
    return RemediationStepSpec(
        role="communicate",
        service_type="bakery",
        service_exec="communication",
        task_key_template="bakery-comms",
        service_payload={
            "source": "genestack_monitoring",
            "title": "PoundCake alert update: {{ order.alert_group_name }}",
            "description": (
                "PoundCake completed the managed critical-alert recipe and recorded "
                "the validation, evidence, and action-routing result."
            ),
            "severity": "{{ order.labels.severity }}",
            "category": "alert_remediation",
            "state": "updated",
            "message": "PoundCake completed alert validation, evidence gathering, and action routing.",
            "context": {
                "alert_name": alert_name_template(),
                "alert_group_name": "{{ order.alert_group_name }}",
                "labels": "{{ order.labels }}",
                "annotations": "{{ order.annotations }}",
                "order_id": "{{ order.id }}",
                "req_id": "{{ order.req_id }}",
            },
        },
        service_exec_parameters={"operation": "open"},
        expected_outcome={"success": True},
        expected_secs=5,
        timeout=120,
    )


def alert_name_template() -> str:
    return "{{ order.raw_data.alertname }}"


def alert_domain(source_path: str) -> str:
    parts = [part for part in source_path.strip("/").split("/") if part]
    if "alerts" in parts:
        index = parts.index("alerts")
        if index + 1 < len(parts):
            return parts[index + 1]
    if parts:
        return parts[0]
    return "unknown"


def _alert_domain_for(alert_name: str, source_path: str) -> str:
    domain = alert_domain(source_path)
    if domain != "unknown":
        return domain
    if kubernetes_alert_group(alert_name, source_path):
        return "kubernetes"
    if etcd_alert_group(alert_name, source_path):
        return "etcd"
    if blackbox_alert_group(alert_name, source_path):
        return "blackbox"
    return domain


def _alert_group_slug(alert_name: str) -> str:
    normalized = _slug(alert_name)
    for suffix in ("-warning", "-critical"):
        if normalized.endswith(suffix):
            return normalized[: -len(suffix)]
    return normalized


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower())
    return re.sub(r"-+", "-", slug).strip("-")
