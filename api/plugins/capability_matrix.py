"""Provider ownership matrix for capability publication and selection."""

from __future__ import annotations

from api.types import JSONObject

NATIVE_K8S_PREFERRED_ALERT_GROUPS: frozenset[str] = frozenset(
    {
        "kube-pod-container-restarts",
        "kube-pod-crash-looping",
        "kube-deployment-generation-mismatch",
        "kube-deployment-replicas-mismatch",
        "kube-deployment-rollout-stuck",
        "kube-daemonset-rollout-stuck",
        "kube-statefulset-update-not-rolled-out",
    }
)

OPERATOR_GUIDANCE_ONLY_ALERT_GROUPS: frozenset[str] = frozenset(
    {
        "kube-node-not-ready",
        "kube-node-unreachable",
        "kubelet-down",
    }
)

DEFER_FOR_NOW_ALERT_GROUPS: frozenset[str] = frozenset(
    {
        "kube-container-waiting",
        "kube-pod-not-ready",
    }
)

STACKSTORM_PREFERRED_DOMAINS: frozenset[str] = frozenset({"blackbox", "etcd"})

PLUGIN_CAPABILITY_OWNERSHIP_MATRIX: dict[str, JSONObject] = {
    "k8s": {
        "category": "bounded_native_mutation",
        "capabilities": [
            "pod_action.delete",
            "deployment_action.rollout_restart",
            "workload_action.rollout_restart",
            "failed_job_cleanup.delete",
            "resource_pressure_remediation.scale_deployment",
            "resource_pressure_remediation.patch_hpa_bounds",
        ],
    },
    "stackstorm": {
        "category": "workflow_orchestration",
        "capabilities": [
            "workflow_execution.execute_workflow",
            "content_sync.sync_content",
        ],
    },
    "alertmanager": {
        "category": "evidence_inspection",
        "capabilities": [
            "inspect.list_alerts",
            "inspect.verify_firing",
        ],
    },
    "prometheus": {
        "category": "evidence_inspection",
        "capabilities": [
            "inspect.alert_evidence",
            "helper.alert_rules.parse",
            "reload_config",
        ],
    },
    "github": {
        "category": "evidence_inspection",
        "capabilities": [
            "repo_read.read_file",
            "helper.repo.list",
        ],
    },
    "git": {
        "category": "utility",
        "capabilities": [
            "repo_write.commit",
        ],
    },
    "bakery": {
        "category": "communication",
        "capabilities": [
            "communication.open",
            "communication.notify",
            "communication.update",
            "communication.close",
        ],
    },
    "dummy": {
        "category": "communication",
        "capabilities": [
            "communication.open",
            "communication.notify",
            "communication.update",
            "communication.close",
        ],
    },
}

PROVIDER_SELECTION_PRECEDENCE: dict[str, int] = {
    "k8s": 0,
    "stackstorm": 1,
    "alertmanager": 2,
    "prometheus": 3,
    "github": 4,
    "git": 5,
    "bakery": 6,
    "dummy": 7,
}


def alert_group_provider_policy(*, domain: str, alert_group: str) -> str:
    """Return the preferred provider policy for one alert group."""
    normalized_domain = domain.strip().lower()
    normalized_group = alert_group.strip().lower()
    if normalized_domain in STACKSTORM_PREFERRED_DOMAINS:
        return "stackstorm"
    if normalized_group in NATIVE_K8S_PREFERRED_ALERT_GROUPS:
        return "k8s"
    if normalized_group in OPERATOR_GUIDANCE_ONLY_ALERT_GROUPS:
        return "operator_guidance_only"
    if normalized_group in DEFER_FOR_NOW_ALERT_GROUPS:
        return "defer_for_now"
    return "review"
