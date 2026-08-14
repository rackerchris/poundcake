"""Contract tests for Genestack-managed recipe evidence metadata."""

from __future__ import annotations

from api.plugins.genestack_monitoring.remediation_profiles import remediation_step_specs


def _stackstorm_capabilities() -> list[dict[str, object]]:
    return [
        {
            "capability_id": "stackstorm.workflow.blackbox.blackbox-service-down.evidence",
            "service_type": "stackstorm",
            "ingredient_ref": {
                "service_exec": "workflow_execution",
                "task_key_template": "stackstorm-workflow-execution",
                "destination_target": "stackstorm",
            },
            "operation": "execute_workflow",
            "mode": "workflow",
            "trigger_match": {
                "domains": ["blackbox"],
                "alert_groups": ["blackbox-service-down"],
                "phase": "evidence",
            },
            "defaults": {
                "service_payload": {
                    "workflow_ref": "poundcake.blackbox_service_down_evidence",
                    "inputs": {"instance": "{{ order.labels.instance }}"},
                },
                "service_exec_parameters": {"operation": "execute_workflow"},
                "expected_outcome": {"status": "succeeded"},
                "expected_secs": 30,
                "timeout": 180,
                "role": "gather_endpoint_evidence",
            },
            "safety_class": "observe_only",
            "enabled": True,
        },
        {
            "capability_id": "stackstorm.workflow.blackbox.blackbox-service-down.remediation",
            "service_type": "stackstorm",
            "ingredient_ref": {
                "service_exec": "workflow_execution",
                "task_key_template": "stackstorm-workflow-execution",
                "destination_target": "stackstorm",
            },
            "operation": "execute_workflow",
            "mode": "workflow",
            "trigger_match": {
                "domains": ["blackbox"],
                "alert_groups": ["blackbox-service-down"],
                "phase": "remediation",
            },
            "defaults": {
                "service_payload": {
                    "workflow_ref": "poundcake.blackbox_service_down_remediation",
                    "inputs": {"instance": "{{ order.labels.instance }}"},
                },
                "service_exec_parameters": {"operation": "execute_workflow"},
                "expected_outcome": {"status": "succeeded"},
                "expected_secs": 30,
                "timeout": 300,
                "role": "action_alert",
            },
            "safety_class": "operator_guidance",
            "enabled": True,
        },
        {
            "capability_id": "stackstorm.workflow.etcd.etcd-members-down.evidence",
            "service_type": "stackstorm",
            "ingredient_ref": {
                "service_exec": "workflow_execution",
                "task_key_template": "stackstorm-workflow-execution",
                "destination_target": "stackstorm",
            },
            "operation": "execute_workflow",
            "mode": "workflow",
            "trigger_match": {
                "domains": ["etcd"],
                "alert_groups": ["etcd-members-down"],
                "phase": "evidence",
            },
            "defaults": {
                "service_payload": {
                    "workflow_ref": "poundcake.etcd_etcd_members_down_evidence",
                    "inputs": {"instance": "{{ order.labels.instance }}"},
                },
                "service_exec_parameters": {"operation": "execute_workflow"},
                "expected_outcome": {"status": "succeeded"},
                "expected_secs": 30,
                "timeout": 180,
                "role": "gather_etcd_evidence",
            },
            "safety_class": "observe_only",
            "enabled": True,
        },
        {
            "capability_id": "stackstorm.workflow.etcd.etcd-members-down.remediation",
            "service_type": "stackstorm",
            "ingredient_ref": {
                "service_exec": "workflow_execution",
                "task_key_template": "stackstorm-workflow-execution",
                "destination_target": "stackstorm",
            },
            "operation": "execute_workflow",
            "mode": "workflow",
            "trigger_match": {
                "domains": ["etcd"],
                "alert_groups": ["etcd-members-down"],
                "phase": "remediation",
            },
            "defaults": {
                "service_payload": {
                    "workflow_ref": "poundcake.etcd_etcd_members_down_remediation",
                    "inputs": {"instance": "{{ order.labels.instance }}"},
                },
                "service_exec_parameters": {"operation": "execute_workflow"},
                "expected_outcome": {"status": "succeeded"},
                "expected_secs": 30,
                "timeout": 300,
                "role": "action_alert",
            },
            "safety_class": "operator_guidance",
            "enabled": True,
        },
        {
            "capability_id": "stackstorm.workflow.kubernetes.host_down",
            "service_type": "stackstorm",
            "ingredient_ref": {
                "service_exec": "workflow_execution",
                "task_key_template": "stackstorm-workflow-execution",
                "destination_target": "stackstorm",
            },
            "operation": "execute_workflow",
            "mode": "workflow",
            "trigger_match": {
                "domains": ["kubernetes"],
                "alert_groups": ["kube-node-not-ready", "kube-node-unreachable", "kubelet-down"],
                "phase": "remediation",
            },
            "defaults": {
                "service_payload": {
                    "workflow_ref": "poundcake.host_down_remediation",
                    "inputs": {"host": "{{ order.labels.node }}"},
                },
                "service_exec_parameters": {"operation": "execute_workflow"},
                "expected_outcome": {"status": "succeeded"},
                "expected_secs": 60,
                "timeout": 600,
                "role": "action_alert",
            },
            "safety_class": "operator_guidance",
            "enabled": True,
        },
    ]


def _alertmanager_capabilities() -> list[dict[str, object]]:
    return [
        {
            "capability_id": "alertmanager.inspect.verify-firing.before-evidence",
            "service_type": "alertmanager",
            "ingredient_ref": {
                "service_exec": "inspect",
                "task_key_template": "alertmanager-firing-guard",
                "destination_target": "alertmanager",
            },
            "operation": "verify_firing",
            "mode": "inspection",
            "trigger_match": {
                "phase": "verify_before_evidence",
            },
            "defaults": {
                "service_payload": {
                    "fingerprint": "{{ order.raw_data.fingerprint }}",
                    "labels": "{{ order.labels }}",
                    "active": True,
                    "limit": 1,
                },
                "service_exec_parameters": {
                    "operation": "verify_firing",
                    "guard_role": "remediation_precondition",
                    "false_outcome": "cancel_downstream_no_remediation",
                },
                "expected_outcome": {"is_firing": True},
                "expected_secs": 5,
                "timeout": 30,
                "role": "verify_before_evidence",
            },
            "safety_class": "observe_only",
            "enabled": True,
        },
        {
            "capability_id": "alertmanager.inspect.verify-firing.before-action",
            "service_type": "alertmanager",
            "ingredient_ref": {
                "service_exec": "inspect",
                "task_key_template": "alertmanager-firing-guard",
                "destination_target": "alertmanager",
            },
            "operation": "verify_firing",
            "mode": "inspection",
            "trigger_match": {
                "phase": "verify_before_action",
            },
            "defaults": {
                "service_payload": {
                    "fingerprint": "{{ order.raw_data.fingerprint }}",
                    "labels": "{{ order.labels }}",
                    "active": True,
                    "limit": 1,
                },
                "service_exec_parameters": {
                    "operation": "verify_firing",
                    "guard_role": "remediation_precondition",
                    "false_outcome": "cancel_downstream_no_remediation",
                },
                "expected_outcome": {"is_firing": True},
                "expected_secs": 5,
                "timeout": 30,
                "role": "verify_before_action",
            },
            "safety_class": "observe_only",
            "enabled": True,
        },
        {
            "capability_id": "alertmanager.inspect.active-alerts.evidence",
            "service_type": "alertmanager",
            "ingredient_ref": {
                "service_exec": "inspect",
                "task_key_template": "alertmanager-inspect",
                "destination_target": "alertmanager",
            },
            "operation": "list_alerts",
            "mode": "inspection",
            "trigger_match": {
                "phase": "evidence",
            },
            "defaults": {
                "service_payload": {
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
                "service_exec_parameters": {
                    "operation": "list_alerts",
                    "managed_role": "gather_alertmanager_evidence",
                    "evidence_family": "alertmanager",
                },
                "expected_outcome": {"success": True},
                "expected_secs": 10,
                "timeout": 60,
                "role": "gather_alertmanager_evidence",
            },
            "safety_class": "observe_only",
            "enabled": True,
        },
    ]


def _prometheus_capabilities() -> list[dict[str, object]]:
    return [
        {
            "capability_id": "prometheus.inspect.alert-evidence.generic",
            "service_type": "prometheus",
            "ingredient_ref": {
                "service_exec": "inspect",
                "task_key_template": "prometheus-inspect",
                "destination_target": "prometheus",
            },
            "operation": "alert_evidence",
            "mode": "inspection",
            "trigger_match": {
                "phase": "evidence",
            },
            "defaults": {
                "service_payload": {
                    "alert_name": "{{ order.raw_data.alertname }}",
                    "labels": "{{ order.labels }}",
                    "lookback_seconds": 3600,
                    "step_seconds": 60,
                },
                "service_exec_parameters": {
                    "operation": "alert_evidence",
                    "managed_role": "gather_prometheus_evidence",
                    "evidence_family": "prometheus",
                    "alert_name": "{{ order.raw_data.alertname }}",
                },
                "expected_outcome": {"success": True},
                "expected_secs": 15,
                "timeout": 90,
                "role": "gather_prometheus_evidence",
            },
            "safety_class": "observe_only",
            "enabled": True,
        }
    ]


def _github_capabilities() -> list[dict[str, object]]:
    return [
        {
            "capability_id": "github.repo.read.genestack-source-rule",
            "service_type": "github",
            "ingredient_ref": {
                "service_exec": "repo_read",
                "task_key_template": "github-repo-read",
                "destination_target": "github",
            },
            "operation": "read_file",
            "mode": "inspection",
            "trigger_match": {
                "phase": "evidence",
            },
            "defaults": {
                "service_payload": {
                    "repo": "rackerchris/genestack-monitoring",
                    "ref": "main",
                    "path": "",
                },
                "service_exec_parameters": {
                    "operation": "read_file",
                    "managed_role": "gather_source_rule_evidence",
                    "evidence_family": "alert_source",
                },
                "expected_outcome": {"success": True},
                "expected_secs": 5,
                "timeout": 60,
                "role": "gather_source_rule_evidence",
            },
            "safety_class": "observe_only",
            "enabled": True,
        }
    ]


def _communication_capabilities() -> list[dict[str, object]]:
    return [
        {
            "capability_id": "bakery.communication.open.default",
            "service_type": "bakery",
            "ingredient_ref": {
                "service_exec": "communication",
                "task_key_template": "bakery-comms",
                "destination_target": "rackspace_core",
            },
            "operation": "open",
            "mode": "communication",
            "trigger_match": {
                "phase": "communicate",
            },
            "defaults": {
                "service_payload": {},
                "service_exec_parameters": {
                    "operation": "open",
                },
                "expected_outcome": {"success": True},
                "expected_secs": 5,
                "timeout": 120,
                "role": "communicate",
            },
            "safety_class": "operator_guidance",
            "enabled": True,
            "priority": 200,
        },
        {
            "capability_id": "dummy.communication.open.default",
            "service_type": "dummy",
            "ingredient_ref": {
                "service_exec": "communication",
                "task_key_template": "dummy-comms",
                "destination_target": "dummy",
            },
            "operation": "open",
            "mode": "communication",
            "trigger_match": {
                "phase": "communicate",
            },
            "defaults": {
                "service_payload": {},
                "service_exec_parameters": {
                    "operation": "open",
                },
                "expected_outcome": {"success": True},
                "expected_secs": 1,
                "timeout": 30,
                "role": "communicate",
            },
            "safety_class": "operator_guidance",
            "enabled": True,
            "priority": 100,
        },
    ]


def _k8s_capabilities() -> list[dict[str, object]]:
    return [
        {
            "capability_id": "k8s.remediation.kubernetes.kube-pod-crash-looping",
            "service_type": "k8s",
            "ingredient_ref": {
                "service_exec": "pod_action",
                "task_key_template": "k8s-pod-action",
                "destination_target": "kubernetes",
            },
            "operation": "delete",
            "mode": "action",
            "trigger_match": {
                "domains": ["kubernetes"],
                "alert_groups": ["kube-pod-crash-looping"],
                "phase": "remediation",
            },
            "defaults": {
                "service_payload": {
                    "namespace": "{{ order.labels.namespace }}",
                    "pod_name": "{{ order.labels.pod }}",
                },
                "service_exec_parameters": {
                    "operation": "delete",
                    "mutation_family": "pod_delete",
                    "require_controller_owned": True,
                },
                "expected_outcome": {"success": True},
                "expected_secs": 10,
                "timeout": 120,
                "role": "action_alert",
            },
            "safety_class": "safe_restart",
            "enabled": True,
            "priority": 200,
        },
        {
            "capability_id": "k8s.remediation.kubernetes.kube-deployment-rollout-stuck",
            "service_type": "k8s",
            "ingredient_ref": {
                "service_exec": "deployment_action",
                "task_key_template": "k8s-deployment-action",
                "destination_target": "kubernetes",
            },
            "operation": "rollout_restart",
            "mode": "action",
            "trigger_match": {
                "domains": ["kubernetes"],
                "alert_groups": ["kube-deployment-rollout-stuck"],
                "phase": "remediation",
            },
            "defaults": {
                "service_payload": {
                    "namespace": "{{ order.labels.namespace }}",
                    "deployment_name": "{{ order.labels.deployment }}",
                },
                "service_exec_parameters": {
                    "operation": "rollout_restart",
                    "mutation_family": "deployment_rollout_restart",
                },
                "expected_outcome": {"success": True},
                "expected_secs": 10,
                "timeout": 180,
                "role": "action_alert",
            },
            "safety_class": "safe_restart",
            "enabled": True,
            "priority": 200,
        },
    ]


def _all_capabilities() -> list[dict[str, object]]:
    return (
        _alertmanager_capabilities()
        + _prometheus_capabilities()
        + _github_capabilities()
        + _communication_capabilities()
        + _k8s_capabilities()
        + _stackstorm_capabilities()
    )


def _assert_recipe_has_classifiable_evidence_before_final_communication(
    *,
    alert_name: str,
    source_path: str,
) -> None:
    specs = remediation_step_specs(
        alert_name,
        source_path,
        {},
        capabilities=_all_capabilities(),
    )

    assert specs

    communication_index = next(idx for idx, spec in enumerate(specs) if spec.role == "communicate")
    evidence_before_bakery = []
    for spec in specs[:communication_index]:
        params = spec.service_exec_parameters or {}
        role = str(params.get("managed_role") or spec.role or "").strip().lower()
        evidence_family = str(params.get("evidence_family") or "").strip().lower()
        if role.startswith("gather_") or bool(evidence_family):
            evidence_before_bakery.append(spec)

    assert evidence_before_bakery
    assert all(
        str((spec.service_exec_parameters or {}).get("evidence_family") or "").strip()
        or str(spec.role or "").strip().lower().startswith("gather_")
        for spec in evidence_before_bakery
    )


def test_blackbox_recipe_has_classifiable_evidence_before_bakery() -> None:
    _assert_recipe_has_classifiable_evidence_before_final_communication(
        alert_name="blackbox-service-down-critical",
        source_path="alerts/blackbox/http.yaml",
    )


def test_etcd_recipe_has_classifiable_evidence_before_bakery() -> None:
    _assert_recipe_has_classifiable_evidence_before_final_communication(
        alert_name="etcd-members-down-critical",
        source_path="alerts/etcd/cluster.yaml",
    )


def test_kubernetes_diagnostic_recipe_has_classifiable_evidence_before_bakery() -> None:
    _assert_recipe_has_classifiable_evidence_before_final_communication(
        alert_name="kube-persistent-volume-errors-critical",
        source_path="alerts/kubernetes/storage.yaml",
    )


def test_kubernetes_remediation_recipe_has_classifiable_evidence_before_bakery() -> None:
    _assert_recipe_has_classifiable_evidence_before_final_communication(
        alert_name="kube-pod-crash-looping-critical",
        source_path="alerts/kubernetes/pods.yaml",
    )


def test_generic_cross_adapter_recipe_has_classifiable_evidence_before_bakery() -> None:
    _assert_recipe_has_classifiable_evidence_before_final_communication(
        alert_name="openstack-api-down-critical",
        source_path="alerts/openstack/control-plane.yaml",
    )


def test_kubernetes_single_step_remediation_prefers_native_k8s_capability() -> None:
    specs = remediation_step_specs(
        "kube-pod-crash-looping-critical",
        "alerts/kubernetes/pods.yaml",
        {},
        capabilities=_all_capabilities(),
    )

    action_step = next(spec for spec in specs if spec.role == "action_alert")

    assert action_step.service_type == "k8s"
    assert action_step.service_exec == "pod_action"
    assert action_step.service_exec_parameters["operation"] == "delete"


def test_kubernetes_single_step_remediation_degrades_to_review_without_catalog_capability() -> None:
    specs = remediation_step_specs(
        "kube-pod-crash-looping-critical",
        "alerts/kubernetes/pods.yaml",
        {},
        capabilities=[],
    )

    action_step = next(spec for spec in specs if spec.role == "action_alert")

    assert action_step.service_type == "bakery"
    assert action_step.service_payload["context"]["operator_review_required"] is True


def test_blackbox_recipe_prefers_stackstorm_workflow_capability() -> None:
    specs = remediation_step_specs(
        "blackbox-service-down-critical",
        "alerts/blackbox/http.yaml",
        {},
        capabilities=_all_capabilities(),
    )

    action_step = next(spec for spec in specs if spec.role == "action_alert")

    assert action_step.service_type == "stackstorm"
    assert action_step.service_exec == "workflow_execution"


def test_etcd_recipe_prefers_stackstorm_workflow_capability() -> None:
    specs = remediation_step_specs(
        "etcd-members-down-critical",
        "alerts/etcd/cluster.yaml",
        {},
        capabilities=_all_capabilities(),
    )

    action_step = next(spec for spec in specs if spec.role == "action_alert")

    assert action_step.service_type == "stackstorm"
    assert action_step.service_exec == "workflow_execution"


def test_node_not_ready_recipe_remains_evidence_and_review_even_with_stackstorm_present() -> None:
    specs = remediation_step_specs(
        "kube-node-not-ready-critical",
        "alerts/kubernetes/nodes.yaml",
        {},
        capabilities=_all_capabilities(),
    )

    action_step = next(spec for spec in specs if spec.role == "action_alert")

    assert action_step.service_type == "bakery"
    assert action_step.service_payload["context"]["operator_review_required"] is True


def test_node_pressure_recipe_degrades_to_operator_review_until_workflow_exists() -> None:
    specs = remediation_step_specs(
        "kube-node-pressure-critical",
        "alerts/kubernetes/nodes.yaml",
        {},
    )

    action_step = next(spec for spec in specs if spec.role == "action_alert")

    assert action_step.service_type == "bakery"
    assert action_step.service_payload["context"]["operator_review_required"] is True


def test_daemonset_rollout_recipe_degrades_to_review_without_catalog_capability() -> None:
    specs = remediation_step_specs(
        "kube-daemonset-rollout-stuck-critical",
        "alerts/kubernetes/daemonsets.yaml",
        {},
        capabilities=[],
    )

    action_step = next(spec for spec in specs if spec.role == "action_alert")

    assert action_step.service_type == "bakery"
    assert action_step.service_payload["context"]["operator_review_required"] is True


def test_generic_recipe_uses_alert_identity_for_prometheus_evidence() -> None:
    specs = remediation_step_specs(
        "openstack-api-down-critical",
        "alerts/openstack/control-plane.yaml",
        {"expr": "sum(rate(http_requests_total[5m])) > 0"},
        capabilities=_all_capabilities(),
    )

    evidence_step = next(spec for spec in specs if spec.role == "gather_prometheus_evidence")

    assert "query" not in evidence_step.service_payload
    assert evidence_step.service_payload["alert_name"] == "openstack-api-down-critical"


def test_blackbox_recipe_uses_alertmanager_guard_capabilities() -> None:
    specs = remediation_step_specs(
        "blackbox-service-down-critical",
        "alerts/blackbox/http.yaml",
        {},
        capabilities=_all_capabilities(),
    )

    verify_roles = [spec.role for spec in specs if spec.service_type == "alertmanager"]

    assert "verify_before_evidence" in verify_roles
    assert "verify_before_action" in verify_roles


def test_generic_recipe_uses_prometheus_capability_for_evidence() -> None:
    specs = remediation_step_specs(
        "openstack-api-down-critical",
        "alerts/openstack/control-plane.yaml",
        {"expr": "sum(rate(http_requests_total[5m])) > 0"},
        capabilities=_all_capabilities(),
    )

    evidence_step = next(spec for spec in specs if spec.role == "gather_prometheus_evidence")

    assert evidence_step.service_type == "prometheus"
    assert evidence_step.service_exec_parameters["operation"] == "alert_evidence"
    assert evidence_step.service_exec_parameters["evidence_family"] == "openstack"


def test_generic_recipe_uses_github_capability_for_source_evidence() -> None:
    specs = remediation_step_specs(
        "openstack-api-down-critical",
        "alerts/openstack/control-plane.yaml",
        {"expr": "sum(rate(http_requests_total[5m])) > 0"},
        capabilities=_all_capabilities(),
    )

    evidence_step = next(spec for spec in specs if spec.role == "gather_source_rule_evidence")

    assert evidence_step.service_type == "github"
    assert evidence_step.service_exec == "repo_read"
    assert evidence_step.service_exec_parameters["operation"] == "read_file"
    assert evidence_step.service_payload["path"] == "alerts/openstack/control-plane.yaml"


def test_final_communication_prefers_bakery_capability_by_default() -> None:
    specs = remediation_step_specs(
        "kube-pod-crash-looping-critical",
        "alerts/kubernetes/pods.yaml",
        {},
        capabilities=_all_capabilities(),
    )

    communication_step = next(spec for spec in specs if spec.role == "communicate")

    assert communication_step.service_type == "bakery"
    assert communication_step.service_exec == "communication"
    assert communication_step.service_exec_parameters["operation"] == "open"


def test_final_communication_falls_back_to_dummy_when_bakery_disabled() -> None:
    capabilities = []
    for capability in _all_capabilities():
        cloned = dict(capability)
        if cloned["capability_id"] == "bakery.communication.open.default":
            cloned["enabled"] = False
        capabilities.append(cloned)

    specs = remediation_step_specs(
        "kube-pod-crash-looping-critical",
        "alerts/kubernetes/pods.yaml",
        {},
        capabilities=capabilities,
    )

    communication_step = next(spec for spec in specs if spec.role == "communicate")

    assert communication_step.service_type == "dummy"
    assert communication_step.service_exec == "communication"
    assert communication_step.service_exec_parameters["operation"] == "open"


def test_final_communication_ignores_non_communication_capabilities() -> None:
    capabilities = _all_capabilities() + [
        {
            "capability_id": "github.repo.read.communicate-trap",
            "service_type": "github",
            "ingredient_ref": {
                "service_exec": "repo_read",
                "task_key_template": "github-repo-read",
                "destination_target": "github",
            },
            "operation": "list_files",
            "mode": "inspection",
            "trigger_match": {
                "phase": "communicate",
            },
            "defaults": {
                "service_payload": {"repo": "rackerchris/genestack-monitoring", "ref": "main"},
                "service_exec_parameters": {"operation": "list_files"},
                "expected_outcome": {"success": True},
                "expected_secs": 5,
                "timeout": 60,
                "role": "communicate",
            },
            "safety_class": "observe_only",
            "enabled": True,
            "priority": 999,
        }
    ]

    specs = remediation_step_specs(
        "kube-pod-crash-looping-critical",
        "alerts/kubernetes/pods.yaml",
        {},
        capabilities=capabilities,
    )

    communication_step = next(spec for spec in specs if spec.role == "communicate")

    assert communication_step.service_type == "bakery"
    assert communication_step.service_exec == "communication"
