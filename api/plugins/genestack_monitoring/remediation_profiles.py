"""Managed Genestack remediation recipe profiles."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass

from api.plugins.capability_matrix import (
    PROVIDER_SELECTION_PRECEDENCE,
    alert_group_provider_policy,
)
from api.types import JSONObject

MANAGED_REMEDIATION_MARKER = "managed-by:poundcake-genestack-monitoring"
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
    *,
    capabilities: list[JSONObject] | None = None,
) -> list[RemediationStepSpec]:
    capability_catalog = capabilities or []
    blackbox_group = blackbox_alert_group(alert_name, source_path)
    if blackbox_group == BLACKBOX_SERVICE_DOWN_GROUP:
        steps = [
            _guard_step(capability_catalog, role="verify_before_evidence"),
            _alertmanager_evidence_step(capability_catalog),
            _guard_step(capability_catalog, role="verify_before_action"),
        ]
        blackbox_evidence = _best_capability(
            capability_catalog,
            alert_group=blackbox_group,
            domain="blackbox",
            phase="evidence",
        )
        if blackbox_evidence is not None:
            steps.insert(2, _capability_step(blackbox_evidence))
        blackbox_action = _best_capability(
            capability_catalog,
            alert_group=blackbox_group,
            domain="blackbox",
            phase="remediation",
        )
        if blackbox_action is not None:
            steps.append(_capability_step(blackbox_action))
            blackbox_recovery = _best_capability(
                capability_catalog,
                alert_group=blackbox_group,
                domain="blackbox",
                phase="verify_recovery",
            )
            if blackbox_recovery is not None:
                steps.append(_capability_step(blackbox_recovery))
        else:
            steps.append(_operator_review_action_step(alert_name, source_path))
        steps.append(_communication_step(capability_catalog))
        return steps

    etcd_group = etcd_alert_group(alert_name, source_path)
    if etcd_group in ETCD_ALERT_GROUPS:
        steps = [
            _guard_step(capability_catalog, role="verify_before_evidence"),
            _alertmanager_evidence_step(capability_catalog),
            _guard_step(capability_catalog, role="verify_before_action"),
        ]
        etcd_evidence = _best_capability(
            capability_catalog,
            alert_group=etcd_group,
            domain="etcd",
            phase="evidence",
        )
        if etcd_evidence is not None:
            steps.insert(2, _capability_step(etcd_evidence))
        etcd_action = _best_capability(
            capability_catalog,
            alert_group=etcd_group,
            domain="etcd",
            phase="remediation",
        )
        if etcd_action is not None:
            steps.append(_capability_step(etcd_action))
            etcd_recovery = _best_capability(
                capability_catalog,
                alert_group=etcd_group,
                domain="etcd",
                phase="verify_recovery",
            )
            if etcd_recovery is not None:
                steps.append(_capability_step(etcd_recovery))
        else:
            steps.append(_operator_review_action_step(alert_name, source_path))
        steps.append(_communication_step(capability_catalog))
        return steps

    group = kubernetes_alert_group(alert_name, source_path)
    if group and group in KUBERNETES_ALERT_GROUPS:
        provider_policy = alert_group_provider_policy(domain="kubernetes", alert_group=group)
        remediation_capability = None
        if provider_policy not in {"operator_guidance_only", "defer_for_now"}:
            remediation_capability = _best_capability(
                capability_catalog,
                alert_group=group,
                domain="kubernetes",
                phase="remediation",
                preferred_service_type=_preferred_service_type_for_policy(provider_policy),
            )
        return [
            _guard_step(capability_catalog, role="verify_before_evidence"),
            _evidence_step(group),
            _guard_step(capability_catalog, role="verify_before_action"),
            _action_step(
                group,
                alert_name,
                source_path,
                remediation_capability=remediation_capability,
            ),
            _communication_step(capability_catalog),
        ]

    domain = alert_domain(source_path)
    if domain in GENERIC_CROSS_ADAPTER_DOMAINS:
        return [
            _guard_step(capability_catalog, role="verify_before_evidence"),
            _alertmanager_evidence_step(capability_catalog),
            _prometheus_rule_evidence_step(
                capability_catalog,
                alert_name,
                source_path,
                rule_data,
            ),
            _source_rule_evidence_step(capability_catalog, source_path),
            _guard_step(capability_catalog, role="verify_before_action"),
            _operator_review_action_step(alert_name, source_path),
            _communication_step(capability_catalog),
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


def _best_capability(
    capabilities: list[JSONObject],
    *,
    alert_group: str,
    domain: str,
    phase: str,
    preferred_service_type: str | None = None,
    required_mode: str | None = None,
) -> JSONObject | None:
    matches = [
        capability
        for capability in capabilities
        if _capability_matches(
            capability,
            alert_group=alert_group,
            domain=domain,
            phase=phase,
            preferred_service_type=preferred_service_type,
            required_mode=required_mode,
        )
    ]
    if not matches:
        return None
    return sorted(
        matches,
        key=lambda item: (
            _safety_rank(str(item.get("safety_class") or "")),
            PROVIDER_SELECTION_PRECEDENCE.get(
                str(item.get("service_type") or "").strip().lower(),
                99,
            ),
            -(int(item.get("priority") or 0)),
            str(item.get("capability_id") or ""),
        ),
    )[0]


def _preferred_service_type_for_policy(provider_policy: str) -> str | None:
    normalized = provider_policy.strip().lower()
    if normalized in {"k8s", "stackstorm"}:
        return normalized
    return None


def _capability_matches(
    capability: JSONObject,
    *,
    alert_group: str,
    domain: str,
    phase: str,
    preferred_service_type: str | None = None,
    required_mode: str | None = None,
) -> bool:
    if capability.get("enabled") is False:
        return False
    normalized_service_type = str(capability.get("service_type") or "").strip().lower()
    if preferred_service_type and normalized_service_type != preferred_service_type.strip().lower():
        return False
    normalized_mode = str(capability.get("mode") or "").strip().lower()
    if required_mode and normalized_mode != required_mode.strip().lower():
        return False
    trigger_match = capability.get("trigger_match")
    if not isinstance(trigger_match, dict):
        return False
    match_domains = {
        str(item).strip().lower() for item in trigger_match.get("domains", []) if str(item).strip()
    }
    if match_domains and domain.strip().lower() not in match_domains:
        return False
    match_groups = {
        str(item).strip().lower()
        for item in trigger_match.get("alert_groups", [])
        if str(item).strip()
    }
    if match_groups and alert_group.strip().lower() not in match_groups:
        return False
    match_phase = str(trigger_match.get("phase") or "").strip().lower()
    return not match_phase or match_phase == phase.strip().lower()


def _safety_rank(value: str) -> int:
    normalized = value.strip().lower()
    order = {
        "observe_only": 0,
        "safe_restart": 1,
        "bounded_scale": 2,
        "operator_guidance": 3,
        "destructive": 4,
    }
    return order.get(normalized, 99)


def _capability_step(
    capability: JSONObject,
    *,
    service_payload_overrides: JSONObject | None = None,
    service_exec_parameter_overrides: JSONObject | None = None,
    expected_outcome_overrides: JSONObject | None = None,
) -> RemediationStepSpec:
    defaults = capability.get("defaults")
    default_payload: JSONObject = defaults if isinstance(defaults, dict) else {}
    ingredient_ref = capability.get("ingredient_ref")
    if not isinstance(ingredient_ref, dict):
        raise ValueError("Capability ingredient_ref must be present")
    service_payload = copy.deepcopy(default_payload.get("service_payload"))
    if not isinstance(service_payload, dict):
        service_payload = {}
    if isinstance(service_payload_overrides, dict):
        service_payload.update(copy.deepcopy(service_payload_overrides))
    parameters = copy.deepcopy(default_payload.get("service_exec_parameters"))
    if not isinstance(parameters, dict):
        parameters = {}
    if isinstance(service_exec_parameter_overrides, dict):
        parameters.update(copy.deepcopy(service_exec_parameter_overrides))
    expected_outcome = copy.deepcopy(default_payload.get("expected_outcome"))
    if not isinstance(expected_outcome, dict):
        expected_outcome = {"success": True}
    if isinstance(expected_outcome_overrides, dict):
        expected_outcome.update(copy.deepcopy(expected_outcome_overrides))
    return RemediationStepSpec(
        role=str(default_payload.get("role") or "action_alert"),
        service_type=str(capability.get("service_type") or "").strip().lower(),
        service_exec=str(ingredient_ref.get("service_exec") or "").strip().lower(),
        task_key_template=str(ingredient_ref.get("task_key_template") or "").strip(),
        service_payload=service_payload,
        service_exec_parameters=parameters,
        expected_outcome=expected_outcome,
        expected_secs=max(1, int(default_payload.get("expected_secs") or 30)),
        timeout=max(1, int(default_payload.get("timeout") or 300)),
        destination_target=str(ingredient_ref.get("destination_target") or "").strip() or None,
        run_phase=str(default_payload.get("run_phase") or "firing").strip() or "firing",
        run_condition=str(default_payload.get("run_condition") or "always").strip() or "always",
    )


def _guard_step(capabilities: list[JSONObject], *, role: str) -> RemediationStepSpec:
    capability = _best_capability(
        capabilities,
        alert_group="",
        domain="",
        phase=role,
        preferred_service_type="alertmanager",
    )
    if capability is not None:
        return _capability_step(capability)
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


def _alertmanager_evidence_step(capabilities: list[JSONObject]) -> RemediationStepSpec:
    capability = _best_capability(
        capabilities,
        alert_group="",
        domain="",
        phase="evidence",
        preferred_service_type="alertmanager",
    )
    if capability is not None:
        return _capability_step(capability)
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
        service_exec_parameters={
            "operation": "list_alerts",
            "evidence_family": "alertmanager",
        },
        expected_outcome={"success": True},
        expected_secs=10,
        timeout=60,
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


def _action_step(
    group: str,
    alert_name: str,
    source_path: str,
    *,
    remediation_capability: JSONObject | None = None,
) -> RemediationStepSpec:
    if remediation_capability is not None:
        return _capability_step(remediation_capability)
    return _operator_review_action_step(alert_name, source_path)


def _prometheus_rule_evidence_step(
    capabilities: list[JSONObject],
    alert_name: str,
    source_path: str,
    _rule_data: JSONObject | None,
) -> RemediationStepSpec:
    capability = _best_capability(
        capabilities,
        alert_group="",
        domain="",
        phase="evidence",
        preferred_service_type="prometheus",
    )
    if capability is not None:
        return _capability_step(
            capability,
            service_payload_overrides={
                "alert_name": alert_name,
            },
            service_exec_parameter_overrides={
                "alert_name": alert_name,
                "evidence_family": alert_domain(source_path),
            },
        )
    return RemediationStepSpec(
        role="gather_prometheus_evidence",
        service_type="prometheus",
        service_exec="inspect",
        task_key_template="prometheus-inspect",
        service_payload={
            "alert_name": alert_name,
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


def _source_rule_evidence_step(
    capabilities: list[JSONObject],
    source_path: str,
) -> RemediationStepSpec:
    capability = _best_capability(
        capabilities,
        alert_group="",
        domain="",
        phase="evidence",
        preferred_service_type="github",
    )
    if capability is not None:
        return _capability_step(
            capability,
            service_payload_overrides={"path": source_path},
        )
    return RemediationStepSpec(
        role="gather_source_rule_evidence",
        service_type="github",
        service_exec="repo_read",
        task_key_template="github-repo-read",
        service_payload={
            "repo": "rackerchris/genestack-monitoring",
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
    return _communication_step([])


def _communication_step(capabilities: list[JSONObject]) -> RemediationStepSpec:
    capability = _best_capability(
        capabilities,
        alert_group="",
        domain="",
        phase="communicate",
        required_mode="communication",
    )
    if capability is not None:
        return _capability_step(
            capability,
            service_payload_overrides={
                "source": "genestack_monitoring",
                "title": "PoundCake alert update: {{ order.alert_group_name }}",
                "description": (
                    "PoundCake completed the managed critical-alert recipe and recorded "
                    "the validation, evidence, and action-routing result."
                ),
                "severity": "{{ order.labels.severity }}",
                "category": "alert_remediation",
                "state": "updated",
                "message": (
                    "PoundCake completed alert validation, evidence gathering, and "
                    "action routing."
                ),
                "context": {
                    "alert_name": alert_name_template(),
                    "alert_group_name": "{{ order.alert_group_name }}",
                    "labels": "{{ order.labels }}",
                    "annotations": "{{ order.annotations }}",
                    "order_id": "{{ order.id }}",
                    "req_id": "{{ order.req_id }}",
                },
            },
        )
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
