"""StackStorm capability templates derived from pack metadata."""

from __future__ import annotations

from api.plugins.capability_matrix import alert_group_provider_policy
from api.plugins.stackstorm.content_sync import (
    _content_pack_name,
    load_stackstorm_action_definitions,
    load_stackstorm_profile_metadata,
)
from api.types import JSONObject

HOST_DOWN_ALERT_GROUPS = {
    "kube-node-not-ready",
    "kube-node-unreachable",
    "kubelet-down",
}
BLACKBOX_PHASES = {
    "evidence": ("blackbox_service_down_evidence", "gather_endpoint_evidence", "observe_only"),
    "remediation": ("blackbox_service_down_remediation", "action_alert", "operator_guidance"),
    "verify_recovery": (
        "blackbox_service_down_verify_recovery",
        "verify_recovery",
        "observe_only",
    ),
}
ETCD_PHASES = {
    "evidence": ("gather_etcd_evidence", "observe_only"),
    "remediation": ("action_alert", "operator_guidance"),
    "verify_recovery": ("verify_recovery", "observe_only"),
}


def load_stackstorm_capability_templates() -> tuple[JSONObject, ...]:
    """Build capability templates from available StackStorm pack metadata."""
    action_definitions = load_stackstorm_action_definitions()
    available_refs = {
        f"{str(action.get('pack') or _content_pack_name()).strip()}.{str(action.get('name') or '').strip()}"
        for action in action_definitions
        if str(action.get("name") or "").strip()
    }
    templates: list[JSONObject] = []
    templates.extend(_explicit_profile_capabilities(available_refs))
    templates.extend(_blackbox_capabilities(available_refs))
    templates.extend(_etcd_capabilities(available_refs))
    templates.extend(_host_down_capability(available_refs))
    templates.extend(_profile_capabilities(available_refs))
    return tuple(_dedupe_capability_templates(templates))


def _blackbox_capabilities(available_refs: set[str]) -> list[JSONObject]:
    templates: list[JSONObject] = []
    for phase, (workflow_name, managed_role, safety_class) in BLACKBOX_PHASES.items():
        workflow_ref = f"poundcake.{workflow_name}"
        if workflow_ref not in available_refs:
            continue
        templates.append(
            {
                "capability_id": f"stackstorm.workflow.blackbox.blackbox-service-down.{phase}",
                "ingredient_ref": {
                    "service_exec": "workflow_execution",
                    "destination_target": "stackstorm",
                    "task_key_template": "stackstorm-workflow-execution",
                },
                "operation": "execute_workflow",
                "mode": "workflow",
                "resource_kinds": ["service", "endpoint"],
                "trigger_match": {
                    "domains": ["blackbox"],
                    "alert_groups": ["blackbox-service-down"],
                    "phase": phase,
                },
                "required_inputs": ["alert_name", "instance", "labels"],
                "optional_inputs": ["annotations", "severity", "order_id", "req_id", "evidence"],
                "defaults": {
                    "service_payload": {
                        "workflow_ref": workflow_ref,
                        "inputs": {
                            "alert_name": "{{ order.raw_data.alertname }}",
                            "alert_group_name": "{{ order.alert_group_name }}",
                            "severity": "{{ order.labels.severity }}",
                            "instance": "{{ order.labels.instance }}",
                            "labels": "{{ order.labels }}",
                            "annotations": "{{ order.annotations }}",
                            "order_id": "{{ order.id }}",
                            "req_id": "{{ order.req_id }}",
                            "method": "HEAD" if phase != "verify_recovery" else "GET",
                            "timeout": 15,
                            "evidence": {},
                        },
                    },
                    "service_exec_parameters": {"operation": "execute_workflow"},
                    "expected_outcome": {"status": "succeeded"},
                    "expected_secs": 30,
                    "timeout": 180 if phase != "remediation" else 300,
                    "role": managed_role,
                },
                "safety_class": safety_class,
                "requires_evidence": phase == "remediation",
            }
        )
    return templates


def _etcd_capabilities(available_refs: set[str]) -> list[JSONObject]:
    profiles = load_stackstorm_profile_metadata()
    etcd_profile = next(
        (
            profile
            for profile in profiles.get("profiles", [])
            if isinstance(profile, dict) and str(profile.get("domain") or "") == "etcd"
        ),
        {},
    )
    workflow_prefix = str(etcd_profile.get("workflow_prefix") or "poundcake.etcd_").strip()
    phases = [
        str(item).strip() for item in etcd_profile.get("workflow_phases", []) if str(item).strip()
    ] or ["evidence", "remediation", "verify_recovery"]
    templates: list[JSONObject] = []
    for raw_group in etcd_profile.get("alert_groups", []):
        group = str(raw_group or "").strip().lower()
        if not group:
            continue
        for phase in phases:
            workflow_ref = f"{workflow_prefix}{group.replace('-', '_')}_{phase}"
            if workflow_ref not in available_refs:
                continue
            role, safety_class = ETCD_PHASES.get(phase, ("action_alert", "operator_guidance"))
            templates.append(
                {
                    "capability_id": f"stackstorm.workflow.etcd.{group}.{phase}",
                    "ingredient_ref": {
                        "service_exec": "workflow_execution",
                        "destination_target": "stackstorm",
                        "task_key_template": "stackstorm-workflow-execution",
                    },
                    "operation": "execute_workflow",
                    "mode": "workflow",
                    "resource_kinds": ["etcd_member", "etcd_cluster"],
                    "trigger_match": {
                        "domains": ["etcd"],
                        "alert_groups": [group],
                        "phase": phase,
                    },
                    "required_inputs": ["alert_name", "labels"],
                    "optional_inputs": [
                        "alert_group_name",
                        "severity",
                        "annotations",
                        "instance",
                        "job",
                        "cluster",
                        "order_id",
                        "req_id",
                        "evidence",
                    ],
                    "defaults": {
                        "service_payload": {
                            "workflow_ref": workflow_ref,
                            "inputs": {
                                "alert_name": "{{ order.raw_data.alertname }}",
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
                            },
                        },
                        "service_exec_parameters": {"operation": "execute_workflow"},
                        "expected_outcome": {"status": "succeeded"},
                        "expected_secs": 30,
                        "timeout": 180 if phase != "remediation" else 300,
                        "role": role,
                    },
                    "safety_class": safety_class,
                    "requires_evidence": phase == "remediation",
                }
            )
    return templates


def _host_down_capability(available_refs: set[str]) -> list[JSONObject]:
    workflow_ref = "poundcake.host_down_remediation"
    if workflow_ref not in available_refs:
        return []
    return [
        {
            "capability_id": "stackstorm.workflow.kubernetes.host_down",
            "ingredient_ref": {
                "service_exec": "workflow_execution",
                "destination_target": "stackstorm",
                "task_key_template": "stackstorm-workflow-execution",
            },
            "operation": "execute_workflow",
            "mode": "workflow",
            "resource_kinds": ["node", "host"],
            "trigger_match": {
                "domains": ["kubernetes"],
                "alert_groups": sorted(HOST_DOWN_ALERT_GROUPS),
                "phase": "remediation",
            },
            "required_inputs": ["host"],
            "optional_inputs": ["alert_group_name", "order_id"],
            "defaults": {
                "service_payload": {
                    "workflow_ref": workflow_ref,
                    "inputs": {
                        "host": "{{ order.labels.node }}",
                        "alert_group_name": "{{ order.alert_group_name }}",
                        "order_id": "{{ order.id }}",
                    },
                },
                "service_exec_parameters": {"operation": "execute_workflow"},
                "expected_outcome": {"status": "succeeded"},
                "expected_secs": 60,
                "timeout": 600,
                "role": "action_alert",
            },
            "safety_class": "operator_guidance",
            "requires_evidence": True,
            "priority": 100,
        }
    ]


def _profile_capabilities(available_refs: set[str]) -> list[JSONObject]:
    profiles = load_stackstorm_profile_metadata()
    templates: list[JSONObject] = []
    for profile in profiles.get("profiles", []):
        if not isinstance(profile, dict):
            continue
        domain = str(profile.get("domain") or "").strip().lower()
        if domain != "kubernetes":
            continue
        prefix = str(profile.get("workflow_prefix") or "").strip()
        suffix = str(profile.get("workflow_suffix") or "").strip()
        if not prefix:
            continue
        for raw_group in profile.get("alert_groups", []):
            group = str(raw_group or "").strip().lower()
            if not group or group in HOST_DOWN_ALERT_GROUPS:
                continue
            if alert_group_provider_policy(domain=domain, alert_group=group) != "stackstorm":
                continue
            workflow_ref = f"{prefix}{group.replace('-', '_')}{suffix}"
            if workflow_ref not in available_refs:
                continue
            templates.append(
                {
                    "capability_id": f"stackstorm.workflow.kubernetes.{group}",
                    "ingredient_ref": {
                        "service_exec": "workflow_execution",
                        "destination_target": "stackstorm",
                        "task_key_template": "stackstorm-workflow-execution",
                    },
                    "operation": "execute_workflow",
                    "mode": "workflow",
                    "resource_kinds": _resource_kinds_for_group(group),
                    "trigger_match": {
                        "domains": ["kubernetes"],
                        "alert_groups": [group],
                        "phase": "remediation",
                    },
                    "required_inputs": ["alert_name", "alert_group_name", "labels"],
                    "optional_inputs": [
                        "annotations",
                        "severity",
                        "order_id",
                        "req_id",
                        "evidence",
                    ],
                    "defaults": {
                        "service_payload": {
                            "workflow_ref": workflow_ref,
                            "inputs": {
                                "alert_name": "{{ order.raw_data.alertname }}",
                                "alert_group_name": "{{ order.alert_group_name }}",
                                "severity": "{{ order.labels.severity }}",
                                "labels": "{{ order.labels }}",
                                "annotations": "{{ order.annotations }}",
                                "order_id": "{{ order.id }}",
                                "req_id": "{{ order.req_id }}",
                                "evidence": {},
                            },
                        },
                        "service_exec_parameters": {"operation": "execute_workflow"},
                        "expected_outcome": {"status": "succeeded"},
                        "expected_secs": 60,
                        "timeout": 600,
                        "role": "action_alert",
                    },
                    "safety_class": "operator_guidance",
                    "requires_evidence": True,
                }
            )
    return templates


def _explicit_profile_capabilities(available_refs: set[str]) -> list[JSONObject]:
    profiles = load_stackstorm_profile_metadata()
    templates: list[JSONObject] = []
    for raw_capability in profiles.get("capabilities", []):
        if not isinstance(raw_capability, dict):
            continue
        workflow_ref = str(raw_capability.get("workflow_ref") or "").strip()
        if not workflow_ref or workflow_ref not in available_refs:
            continue
        capability_id = str(raw_capability.get("capability_id") or "").strip().lower()
        if not capability_id:
            continue
        alert_groups = [
            str(item).strip().lower()
            for item in raw_capability.get("alert_groups", [])
            if str(item).strip()
        ]
        domain = str(raw_capability.get("domain") or "").strip().lower()
        phase = str(raw_capability.get("phase") or "remediation").strip().lower()
        templates.append(
            {
                "capability_id": capability_id,
                "ingredient_ref": {
                    "service_exec": "workflow_execution",
                    "destination_target": "stackstorm",
                    "task_key_template": "stackstorm-workflow-execution",
                },
                "operation": "execute_workflow",
                "mode": "workflow",
                "resource_kinds": [
                    str(item).strip().lower()
                    for item in raw_capability.get("resource_kinds", [])
                    if str(item).strip()
                ],
                "trigger_match": {
                    "domains": [domain] if domain else [],
                    "alert_groups": alert_groups,
                    "phase": phase,
                },
                "required_inputs": [
                    str(item).strip()
                    for item in raw_capability.get("required_inputs", [])
                    if str(item).strip()
                ],
                "optional_inputs": [
                    str(item).strip()
                    for item in raw_capability.get("optional_inputs", [])
                    if str(item).strip()
                ],
                "defaults": {
                    "service_payload": {
                        "workflow_ref": workflow_ref,
                        "inputs": dict(raw_capability.get("defaults") or {}),
                    },
                    "service_exec_parameters": {"operation": "execute_workflow"},
                    "expected_outcome": {"status": "succeeded"},
                    "expected_secs": int(raw_capability.get("expected_secs") or 60),
                    "timeout": int(raw_capability.get("timeout") or 600),
                    "role": str(raw_capability.get("role") or "action_alert"),
                },
                "safety_class": str(raw_capability.get("safety_class") or "operator_guidance"),
                "requires_evidence": bool(raw_capability.get("requires_evidence", True)),
                "priority": int(raw_capability.get("priority") or 0),
            }
        )
    return templates


def _dedupe_capability_templates(templates: list[JSONObject]) -> list[JSONObject]:
    deduped: list[JSONObject] = []
    seen_capability_ids: set[str] = set()
    for template in templates:
        capability_id = str(template.get("capability_id") or "").strip().lower()
        if not capability_id or capability_id in seen_capability_ids:
            continue
        seen_capability_ids.add(capability_id)
        deduped.append(template)
    return deduped


def _resource_kinds_for_group(group: str) -> list[str]:
    if "daemonset" in group:
        return ["daemonset"]
    if "statefulset" in group:
        return ["statefulset"]
    if "deployment" in group:
        return ["deployment"]
    if "pod" in group or "container" in group:
        return ["pod"]
    if "node" in group or "kubelet" in group:
        return ["node"]
    if "persistent-volume" in group:
        return ["persistentvolume", "persistentvolumeclaim"]
    return ["kubernetes_resource"]
