"""Kubernetes plugin templates."""

from __future__ import annotations

from api.plugins.contract import health_check_operation_parameters
from api.types import JSONObject


def _schema(properties: JSONObject, required: list[str] | None = None) -> JSONObject:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


_PROMETHEUS_RULE_PROPS: JSONObject = {
    "rule_name": {"type": "string", "minLength": 1},
    "group_name": {"type": "string", "minLength": 1},
    "crd_name": {"type": "string", "minLength": 1},
    "rule_data": {"type": "object"},
}

_POD_ACTION_PROPS: JSONObject = {
    "namespace": {"type": "string", "minLength": 1},
    "pod_name": {"type": "string", "minLength": 1},
    "label_selector": {"type": "string", "minLength": 1},
    "container": {"type": "string", "minLength": 1},
    "tail_lines": {"type": "integer", "minimum": 1},
    "since_seconds": {"type": "integer", "minimum": 1},
    "previous": {"type": "boolean"},
}

_DEPLOYMENT_ACTION_PROPS: JSONObject = {
    "namespace": {"type": "string", "minLength": 1},
    "deployment_name": {"type": "string", "minLength": 1},
    "replicas": {"type": "integer", "minimum": 0},
    "timeout_seconds": {"type": "integer", "minimum": 1},
}

_CONTROLLER_KINDS = ["Deployment", "StatefulSet", "DaemonSet"]

_WORKLOAD_ACTION_PROPS: JSONObject = {
    "namespace": {"type": "string", "minLength": 1},
    "kind": {"type": "string", "enum": _CONTROLLER_KINDS},
    "name": {"type": "string", "minLength": 1},
    "timeout_seconds": {"type": "integer", "minimum": 1},
}

_WORKLOAD_KINDS = [
    "Pod",
    "Deployment",
    "StatefulSet",
    "DaemonSet",
    "ReplicaSet",
    "Job",
    "CronJob",
    "Service",
]

_WORKLOAD_TRIAGE_PROPS: JSONObject = {
    "namespace": {"type": "string", "minLength": 1},
    "kind": {"type": "string", "enum": _WORKLOAD_KINDS},
    "name": {"type": "string", "minLength": 1},
    "pod_name": {"type": "string", "minLength": 1},
    "container": {"type": "string", "minLength": 1},
    "label_selector": {"type": "string", "minLength": 1},
    "tail_lines": {"type": "integer", "minimum": 1},
    "since_seconds": {"type": "integer", "minimum": 1},
    "previous": {"type": "boolean"},
    "limit": {"type": "integer", "minimum": 1},
    "node": {"type": "string", "minLength": 1},
    "persistentvolumeclaim": {"type": "string", "minLength": 1},
    "persistentvolume": {"type": "string", "minLength": 1},
    "service": {"type": "string", "minLength": 1},
    "configmap": {"type": "string", "minLength": 1},
    "secret": {"type": "string", "minLength": 1},
    "poddisruptionbudget": {"type": "string", "minLength": 1},
    "horizontalpodautoscaler": {"type": "string", "minLength": 1},
}

_NODE_TRIAGE_PROPS: JSONObject = {
    "node": {"type": "string", "minLength": 1},
    "name": {"type": "string", "minLength": 1},
    "label_selector": {"type": "string", "minLength": 1},
    "namespace": {"type": "string", "minLength": 1},
    "include_succeeded": {"type": "boolean"},
    "limit": {"type": "integer", "minimum": 1},
}

_FAILED_JOB_CLEANUP_PROPS: JSONObject = {
    "namespace": {"type": "string", "minLength": 1},
    "job_name": {"type": "string", "minLength": 1},
    "name": {"type": "string", "minLength": 1},
}

_RESOURCE_PRESSURE_REMEDIATION_PROPS: JSONObject = {
    "namespace": {"type": "string", "minLength": 1},
    "deployment_name": {"type": "string", "minLength": 1},
    "hpa_name": {"type": "string", "minLength": 1},
    "name": {"type": "string", "minLength": 1},
    "replicas": {"type": "integer", "minimum": 0},
    "min_replicas": {"type": "integer", "minimum": 0},
    "max_replicas": {"type": "integer", "minimum": 1},
}

_SERVICE_PROBE_PROPS: JSONObject = {
    "namespace": {"type": "string", "minLength": 1},
    "service": {"type": "string", "minLength": 1},
    "name": {"type": "string", "minLength": 1},
    "port": {"oneOf": [{"type": "integer", "minimum": 1}, {"type": "string", "minLength": 1}]},
    "scheme": {"type": "string", "enum": ["http", "https"]},
    "path": {"type": "string", "minLength": 1},
    "timeout_seconds": {"type": "integer", "minimum": 1},
    "expected_status_codes": {
        "type": "array",
        "items": {"type": "integer", "minimum": 100, "maximum": 599},
        "minItems": 1,
    },
}

_POD_TARGET_SCHEMA: JSONObject = {
    **_schema(_POD_ACTION_PROPS),
    "required": ["namespace"],
    "anyOf": [{"required": ["pod_name"]}, {"required": ["label_selector"]}],
}

_TRIAGE_LOGS_EVENTS_SCHEMA: JSONObject = {
    **_schema(_WORKLOAD_TRIAGE_PROPS),
    "required": ["namespace"],
    "anyOf": [
        {"required": ["kind", "name"]},
        {"required": ["pod_name"]},
        {"required": ["label_selector"]},
    ],
}


K8S_INGREDIENT_TEMPLATES: tuple[JSONObject, ...] = (
    {
        "service_type": "k8s",
        "service_exec": "health_check",
        "destination_target": "kubernetes",
        "task_key_template": "k8s-health-check",
        "payload_schema": _schema({}),
        "service_payload_template": {},
        "service_exec_parameters": health_check_operation_parameters(),
        "default_expected_secs": 5,
        "default_timeout": 30,
        "service_exec_expected_outcome_default": {"success": True},
        "ingredient_purpose": "plugin_health",
        "is_blocking": True,
        "retry_count": 0,
        "retry_delay": 0,
        "on_failure": "continue",
    },
    {
        "service_type": "k8s",
        "service_exec": "prometheus_rule",
        "destination_target": "kubernetes",
        "task_key_template": "k8s-prometheus-rule",
        "payload_schema": _schema(
            _PROMETHEUS_RULE_PROPS,
            required=["rule_name", "group_name", "crd_name"],
        ),
        "service_payload_template": {},
        "service_exec_parameters": {
            "operation": "get",
            "allowed_operations": ["get", "list", "apply", "delete"],
            "operation_metadata": {
                "get": {"label": "Get", "description": "Read one PrometheusRule CRD."},
                "list": {"label": "List", "description": "List PrometheusRule CRDs."},
                "apply": {"label": "Apply", "description": "Create or update an alert rule."},
                "delete": {"label": "Delete", "description": "Delete an alert rule."},
            },
        },
        "default_expected_secs": 5,
        "default_timeout": 60,
        "service_exec_expected_outcome_default": {"success": True},
        "ingredient_purpose": "utility",
        "is_blocking": True,
        "retry_count": 1,
        "retry_delay": 2,
        "on_failure": "stop",
    },
    {
        "service_type": "k8s",
        "service_exec": "pod_action",
        "destination_target": "kubernetes",
        "task_key_template": "k8s-pod-action",
        "payload_schema": _schema(_POD_ACTION_PROPS),
        "service_payload_template": {},
        "service_exec_parameters": {
            "operation": "logs",
            "allowed_operations": ["list", "get", "logs", "events", "delete"],
            "operation_metadata": {
                "list": {
                    "label": "List pods",
                    "description": "List pods in a namespace.",
                    "payload_schema": _schema(
                        {
                            "namespace": _POD_ACTION_PROPS["namespace"],
                            "label_selector": _POD_ACTION_PROPS["label_selector"],
                        },
                        required=["namespace"],
                    ),
                },
                "get": {
                    "label": "Get pod",
                    "description": "Read one pod.",
                    "payload_schema": _schema(
                        {
                            "namespace": _POD_ACTION_PROPS["namespace"],
                            "pod_name": _POD_ACTION_PROPS["pod_name"],
                        },
                        required=["namespace", "pod_name"],
                    ),
                },
                "logs": {
                    "label": "Get pod logs",
                    "description": "Read pod logs.",
                    "payload_schema": _POD_TARGET_SCHEMA,
                },
                "events": {
                    "label": "Get pod events",
                    "description": "List events for one pod.",
                    "payload_schema": _schema(
                        {
                            "namespace": _POD_ACTION_PROPS["namespace"],
                            "pod_name": _POD_ACTION_PROPS["pod_name"],
                        },
                        required=["namespace", "pod_name"],
                    ),
                },
                "delete": {
                    "label": "Delete pod",
                    "description": "Delete one pod by exact name.",
                    "payload_schema": _schema(
                        {
                            "namespace": _POD_ACTION_PROPS["namespace"],
                            "pod_name": _POD_ACTION_PROPS["pod_name"],
                        },
                        required=["namespace", "pod_name"],
                    ),
                },
            },
        },
        "default_expected_secs": 10,
        "default_timeout": 120,
        "service_exec_expected_outcome_default": {"success": True},
        "ingredient_purpose": "utility",
        "is_blocking": True,
        "retry_count": 0,
        "retry_delay": 0,
        "on_failure": "stop",
    },
    {
        "service_type": "k8s",
        "service_exec": "deployment_action",
        "destination_target": "kubernetes",
        "task_key_template": "k8s-deployment-action",
        "payload_schema": _schema(_DEPLOYMENT_ACTION_PROPS),
        "service_payload_template": {"namespace": ""},
        "service_exec_parameters": {
            "operation": "get",
            "allowed_operations": ["get", "scale", "rollout_restart", "rollout_status"],
            "operation_metadata": {
                "get": {
                    "label": "Get deployment",
                    "description": "Read one deployment.",
                    "payload_schema": _schema(
                        {
                            "namespace": _DEPLOYMENT_ACTION_PROPS["namespace"],
                            "deployment_name": _DEPLOYMENT_ACTION_PROPS["deployment_name"],
                        },
                        required=["namespace", "deployment_name"],
                    ),
                },
                "scale": {
                    "label": "Scale deployment",
                    "description": "Set deployment replicas.",
                    "payload_schema": _schema(
                        {
                            "namespace": _DEPLOYMENT_ACTION_PROPS["namespace"],
                            "deployment_name": _DEPLOYMENT_ACTION_PROPS["deployment_name"],
                            "replicas": _DEPLOYMENT_ACTION_PROPS["replicas"],
                        },
                        required=["namespace", "deployment_name", "replicas"],
                    ),
                },
                "rollout_restart": {
                    "label": "Rollout restart",
                    "description": "Restart deployment pods through a template annotation patch.",
                    "payload_schema": _schema(
                        {
                            "namespace": _DEPLOYMENT_ACTION_PROPS["namespace"],
                            "deployment_name": _DEPLOYMENT_ACTION_PROPS["deployment_name"],
                        },
                        required=["namespace", "deployment_name"],
                    ),
                },
                "rollout_status": {
                    "label": "Rollout status",
                    "description": "Check deployment rollout status.",
                    "payload_schema": _schema(
                        {
                            "namespace": _DEPLOYMENT_ACTION_PROPS["namespace"],
                            "deployment_name": _DEPLOYMENT_ACTION_PROPS["deployment_name"],
                            "timeout_seconds": _DEPLOYMENT_ACTION_PROPS["timeout_seconds"],
                        },
                        required=["namespace", "deployment_name"],
                    ),
                },
            },
        },
        "default_expected_secs": 10,
        "default_timeout": 180,
        "service_exec_expected_outcome_default": {"success": True},
        "ingredient_purpose": "remediation",
        "is_blocking": True,
        "retry_count": 0,
        "retry_delay": 0,
        "on_failure": "stop",
    },
    {
        "service_type": "k8s",
        "service_exec": "workload_action",
        "destination_target": "kubernetes",
        "task_key_template": "k8s-workload-action",
        "payload_schema": _schema(_WORKLOAD_ACTION_PROPS),
        "service_payload_template": {},
        "service_exec_parameters": {
            "operation": "get",
            "allowed_operations": ["get", "rollout_restart", "rollout_status"],
            "operation_metadata": {
                "get": {
                    "label": "Get workload",
                    "description": "Read one Deployment, StatefulSet, or DaemonSet.",
                    "payload_schema": _schema(
                        {
                            "namespace": _WORKLOAD_ACTION_PROPS["namespace"],
                            "kind": _WORKLOAD_ACTION_PROPS["kind"],
                            "name": _WORKLOAD_ACTION_PROPS["name"],
                        },
                        required=["namespace", "kind", "name"],
                    ),
                },
                "rollout_restart": {
                    "label": "Rollout restart",
                    "description": "Restart a Deployment, StatefulSet, or DaemonSet through a template annotation patch.",
                    "payload_schema": _schema(
                        {
                            "namespace": _WORKLOAD_ACTION_PROPS["namespace"],
                            "kind": _WORKLOAD_ACTION_PROPS["kind"],
                            "name": _WORKLOAD_ACTION_PROPS["name"],
                        },
                        required=["namespace", "kind", "name"],
                    ),
                },
                "rollout_status": {
                    "label": "Rollout status",
                    "description": "Check rollout status for a Deployment, StatefulSet, or DaemonSet.",
                    "payload_schema": _schema(
                        {
                            "namespace": _WORKLOAD_ACTION_PROPS["namespace"],
                            "kind": _WORKLOAD_ACTION_PROPS["kind"],
                            "name": _WORKLOAD_ACTION_PROPS["name"],
                            "timeout_seconds": _WORKLOAD_ACTION_PROPS["timeout_seconds"],
                        },
                        required=["namespace", "kind", "name"],
                    ),
                },
            },
        },
        "default_expected_secs": 10,
        "default_timeout": 180,
        "service_exec_expected_outcome_default": {"success": True},
        "ingredient_purpose": "remediation",
        "is_blocking": True,
        "retry_count": 0,
        "retry_delay": 0,
        "on_failure": "stop",
    },
    {
        "service_type": "k8s",
        "service_exec": "workload_triage",
        "destination_target": "kubernetes",
        "task_key_template": "k8s-workload-triage-v2",
        "payload_schema": _schema(_WORKLOAD_TRIAGE_PROPS),
        "service_payload_template": {},
        "service_exec_parameters": {
            "operation": "pod_diagnostics",
            "allowed_operations": [
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
            "operation_metadata": {
                "pod_diagnostics": {
                    "label": "Pod diagnostics",
                    "description": "Read pod status, container states, logs, and events.",
                    "payload_schema": _schema(
                        {
                            "namespace": _WORKLOAD_TRIAGE_PROPS["namespace"],
                            "pod_name": _WORKLOAD_TRIAGE_PROPS["pod_name"],
                            "name": _WORKLOAD_TRIAGE_PROPS["name"],
                            "container": _WORKLOAD_TRIAGE_PROPS["container"],
                            "tail_lines": _WORKLOAD_TRIAGE_PROPS["tail_lines"],
                            "previous": _WORKLOAD_TRIAGE_PROPS["previous"],
                        },
                        required=["namespace"],
                    )
                    | {"anyOf": [{"required": ["pod_name"]}, {"required": ["name"]}]},
                },
                "workload_status": {
                    "label": "Workload status",
                    "description": "Read workload status, conditions, related pods, and events.",
                    "payload_schema": _schema(
                        {
                            "namespace": _WORKLOAD_TRIAGE_PROPS["namespace"],
                            "kind": _WORKLOAD_TRIAGE_PROPS["kind"],
                            "name": _WORKLOAD_TRIAGE_PROPS["name"],
                            "limit": _WORKLOAD_TRIAGE_PROPS["limit"],
                        },
                        required=["namespace", "kind", "name"],
                    ),
                },
                "logs": {
                    "label": "Workload logs",
                    "description": "Read logs for a pod, selector, or related workload pods.",
                    "payload_schema": _TRIAGE_LOGS_EVENTS_SCHEMA,
                },
                "events": {
                    "label": "Workload events",
                    "description": "Read events for a pod, selector, or related workload pods.",
                    "payload_schema": _TRIAGE_LOGS_EVENTS_SCHEMA,
                },
                "job_diagnostics": {
                    "label": "Job diagnostics",
                    "description": "Read Job or CronJob status, pods, logs, and events.",
                    "payload_schema": _schema(
                        {
                            "namespace": _WORKLOAD_TRIAGE_PROPS["namespace"],
                            "kind": {
                                "type": "string",
                                "enum": ["Job", "CronJob"],
                            },
                            "name": _WORKLOAD_TRIAGE_PROPS["name"],
                            "tail_lines": _WORKLOAD_TRIAGE_PROPS["tail_lines"],
                            "previous": _WORKLOAD_TRIAGE_PROPS["previous"],
                            "limit": _WORKLOAD_TRIAGE_PROPS["limit"],
                        },
                        required=["namespace", "kind", "name"],
                    ),
                },
                "node_diagnostics": {
                    "label": "Node diagnostics",
                    "description": "Read node conditions, taints, capacity, pods, and events.",
                    "payload_schema": _schema(
                        {
                            "node": _WORKLOAD_TRIAGE_PROPS["node"],
                            "name": _WORKLOAD_TRIAGE_PROPS["name"],
                        },
                    )
                    | {"anyOf": [{"required": ["node"]}, {"required": ["name"]}]},
                },
                "pvc_diagnostics": {
                    "label": "PVC diagnostics",
                    "description": "Read PVC/PV status, mounted pods, and events.",
                    "payload_schema": _schema(
                        {
                            "namespace": _WORKLOAD_TRIAGE_PROPS["namespace"],
                            "persistentvolumeclaim": _WORKLOAD_TRIAGE_PROPS[
                                "persistentvolumeclaim"
                            ],
                            "persistentvolume": _WORKLOAD_TRIAGE_PROPS["persistentvolume"],
                            "name": _WORKLOAD_TRIAGE_PROPS["name"],
                        },
                    )
                    | {
                        "anyOf": [
                            {"required": ["persistentvolumeclaim"]},
                            {"required": ["persistentvolume"]},
                            {"required": ["name"]},
                        ]
                    },
                },
                "service_diagnostics": {
                    "label": "Service diagnostics",
                    "description": "Read Service selectors, pods, Endpoints, and EndpointSlices.",
                    "payload_schema": _schema(
                        {
                            "namespace": _WORKLOAD_TRIAGE_PROPS["namespace"],
                            "service": _WORKLOAD_TRIAGE_PROPS["service"],
                            "name": _WORKLOAD_TRIAGE_PROPS["name"],
                        },
                        required=["namespace"],
                    )
                    | {"anyOf": [{"required": ["service"]}, {"required": ["name"]}]},
                },
                "config_diagnostics": {
                    "label": "ConfigMap diagnostics",
                    "description": "Read ConfigMap metadata and safe key summaries without returning values.",
                    "payload_schema": _schema(
                        {
                            "namespace": _WORKLOAD_TRIAGE_PROPS["namespace"],
                            "configmap": _WORKLOAD_TRIAGE_PROPS["configmap"],
                            "name": _WORKLOAD_TRIAGE_PROPS["name"],
                        },
                        required=["namespace"],
                    )
                    | {"anyOf": [{"required": ["configmap"]}, {"required": ["name"]}]},
                },
                "pdb_hpa_diagnostics": {
                    "label": "PDB/HPA diagnostics",
                    "description": "Read PodDisruptionBudget and HPA status.",
                    "payload_schema": _schema(
                        {
                            "namespace": _WORKLOAD_TRIAGE_PROPS["namespace"],
                            "poddisruptionbudget": _WORKLOAD_TRIAGE_PROPS["poddisruptionbudget"],
                            "horizontalpodautoscaler": _WORKLOAD_TRIAGE_PROPS[
                                "horizontalpodautoscaler"
                            ],
                            "name": _WORKLOAD_TRIAGE_PROPS["name"],
                        },
                        required=["namespace"],
                    )
                    | {
                        "anyOf": [
                            {"required": ["poddisruptionbudget"]},
                            {"required": ["horizontalpodautoscaler"]},
                            {"required": ["name"]},
                        ]
                    },
                },
                "certificate_diagnostics": {
                    "label": "Certificate diagnostics",
                    "description": "Inspect Kubernetes TLS Secret metadata and public certificate expiry data without returning private keys.",
                    "payload_schema": _schema(
                        {
                            "namespace": _WORKLOAD_TRIAGE_PROPS["namespace"],
                            "secret": _WORKLOAD_TRIAGE_PROPS["secret"],
                            "name": _WORKLOAD_TRIAGE_PROPS["name"],
                            "label_selector": _WORKLOAD_TRIAGE_PROPS["label_selector"],
                            "limit": _WORKLOAD_TRIAGE_PROPS["limit"],
                        },
                    ),
                },
            },
        },
        "default_expected_secs": 10,
        "default_timeout": 180,
        "service_exec_expected_outcome_default": {"success": True},
        "ingredient_purpose": "utility",
        "is_blocking": True,
        "retry_count": 0,
        "retry_delay": 0,
        "on_failure": "stop",
    },
    {
        "service_type": "k8s",
        "service_exec": "node_triage",
        "destination_target": "kubernetes",
        "task_key_template": "k8s-node-triage",
        "payload_schema": _schema(_NODE_TRIAGE_PROPS),
        "service_payload_template": {},
        "service_exec_parameters": {
            "operation": "node_diagnostics",
            "allowed_operations": [
                "list_nodes",
                "node_diagnostics",
                "node_capacity",
                "node_pressure",
                "node_pods",
                "node_events",
            ],
            "operation_metadata": {
                "list_nodes": {
                    "label": "List nodes",
                    "description": "List cluster nodes with optional label filtering.",
                    "payload_schema": _schema(
                        {
                            "label_selector": _NODE_TRIAGE_PROPS["label_selector"],
                            "limit": _NODE_TRIAGE_PROPS["limit"],
                        },
                    ),
                },
                "node_diagnostics": {
                    "label": "Node diagnostics",
                    "description": "Read node conditions, capacity, taints, pods, and events.",
                    "payload_schema": _schema(
                        {
                            "node": _NODE_TRIAGE_PROPS["node"],
                            "name": _NODE_TRIAGE_PROPS["name"],
                            "limit": _NODE_TRIAGE_PROPS["limit"],
                        },
                    )
                    | {"anyOf": [{"required": ["node"]}, {"required": ["name"]}]},
                },
                "node_capacity": {
                    "label": "Node capacity",
                    "description": "Read node capacity, allocatable resources, taints, and system info.",
                    "payload_schema": _schema(
                        {
                            "node": _NODE_TRIAGE_PROPS["node"],
                            "name": _NODE_TRIAGE_PROPS["name"],
                        },
                    )
                    | {"anyOf": [{"required": ["node"]}, {"required": ["name"]}]},
                },
                "node_pressure": {
                    "label": "Node pressure",
                    "description": "Read pressure conditions, warning events, and non-running pods on a node.",
                    "payload_schema": _schema(
                        {
                            "node": _NODE_TRIAGE_PROPS["node"],
                            "name": _NODE_TRIAGE_PROPS["name"],
                            "limit": _NODE_TRIAGE_PROPS["limit"],
                        },
                    )
                    | {"anyOf": [{"required": ["node"]}, {"required": ["name"]}]},
                },
                "node_pods": {
                    "label": "Node pods",
                    "description": "List pods scheduled to a node, optionally scoped by namespace or labels.",
                    "payload_schema": _schema(
                        {
                            "node": _NODE_TRIAGE_PROPS["node"],
                            "name": _NODE_TRIAGE_PROPS["name"],
                            "namespace": _NODE_TRIAGE_PROPS["namespace"],
                            "label_selector": _NODE_TRIAGE_PROPS["label_selector"],
                            "include_succeeded": _NODE_TRIAGE_PROPS["include_succeeded"],
                            "limit": _NODE_TRIAGE_PROPS["limit"],
                        },
                    )
                    | {"anyOf": [{"required": ["node"]}, {"required": ["name"]}]},
                },
                "node_events": {
                    "label": "Node events",
                    "description": "List events associated with a node.",
                    "payload_schema": _schema(
                        {
                            "node": _NODE_TRIAGE_PROPS["node"],
                            "name": _NODE_TRIAGE_PROPS["name"],
                            "limit": _NODE_TRIAGE_PROPS["limit"],
                        },
                    )
                    | {"anyOf": [{"required": ["node"]}, {"required": ["name"]}]},
                },
            },
        },
        "default_expected_secs": 10,
        "default_timeout": 120,
        "service_exec_expected_outcome_default": {"success": True},
        "ingredient_purpose": "utility",
        "is_blocking": True,
        "retry_count": 0,
        "retry_delay": 0,
        "on_failure": "stop",
    },
    {
        "service_type": "k8s",
        "service_exec": "failed_job_cleanup",
        "destination_target": "kubernetes",
        "task_key_template": "k8s-failed-job-cleanup",
        "payload_schema": _schema(_FAILED_JOB_CLEANUP_PROPS),
        "service_payload_template": {},
        "service_exec_parameters": {
            "operation": "delete",
            "allowed_operations": ["delete"],
            "operation_metadata": {
                "delete": {
                    "label": "Delete failed Job",
                    "description": "Delete one failed Job by exact namespace and name after bounded diagnostics.",
                    "payload_schema": _schema(
                        {
                            "namespace": _FAILED_JOB_CLEANUP_PROPS["namespace"],
                            "job_name": _FAILED_JOB_CLEANUP_PROPS["job_name"],
                        },
                        required=["namespace", "job_name"],
                    ),
                }
            },
        },
        "default_expected_secs": 10,
        "default_timeout": 120,
        "service_exec_expected_outcome_default": {"success": True},
        "ingredient_purpose": "remediation",
        "is_blocking": True,
        "retry_count": 0,
        "retry_delay": 0,
        "on_failure": "stop",
    },
    {
        "service_type": "k8s",
        "service_exec": "resource_pressure_remediation",
        "destination_target": "kubernetes",
        "task_key_template": "k8s-resource-pressure-remediation",
        "payload_schema": _schema(_RESOURCE_PRESSURE_REMEDIATION_PROPS),
        "service_payload_template": {},
        "service_exec_parameters": {
            "operation": "scale_deployment",
            "allowed_operations": ["scale_deployment", "patch_hpa_bounds"],
            "operation_metadata": {
                "scale_deployment": {
                    "label": "Scale deployment",
                    "description": "Set an exact Deployment replica count as a bounded remediation action.",
                    "payload_schema": _schema(
                        {
                            "namespace": _RESOURCE_PRESSURE_REMEDIATION_PROPS["namespace"],
                            "deployment_name": _RESOURCE_PRESSURE_REMEDIATION_PROPS[
                                "deployment_name"
                            ],
                            "replicas": _RESOURCE_PRESSURE_REMEDIATION_PROPS["replicas"],
                        },
                        required=["namespace", "deployment_name", "replicas"],
                    ),
                },
                "patch_hpa_bounds": {
                    "label": "Patch HPA bounds",
                    "description": "Update min/max replicas for one HorizontalPodAutoscaler by exact name.",
                    "payload_schema": _schema(
                        {
                            "namespace": _RESOURCE_PRESSURE_REMEDIATION_PROPS["namespace"],
                            "hpa_name": _RESOURCE_PRESSURE_REMEDIATION_PROPS["hpa_name"],
                            "min_replicas": _RESOURCE_PRESSURE_REMEDIATION_PROPS["min_replicas"],
                            "max_replicas": _RESOURCE_PRESSURE_REMEDIATION_PROPS["max_replicas"],
                        },
                        required=["namespace", "hpa_name"],
                    )
                    | {
                        "anyOf": [
                            {"required": ["min_replicas"]},
                            {"required": ["max_replicas"]},
                        ]
                    },
                },
            },
        },
        "default_expected_secs": 10,
        "default_timeout": 120,
        "service_exec_expected_outcome_default": {"success": True},
        "ingredient_purpose": "remediation",
        "is_blocking": True,
        "retry_count": 0,
        "retry_delay": 0,
        "on_failure": "stop",
    },
    {
        "service_type": "k8s",
        "service_exec": "service_probe",
        "destination_target": "kubernetes",
        "task_key_template": "k8s-service-probe",
        "payload_schema": _schema(_SERVICE_PROBE_PROPS),
        "service_payload_template": {},
        "service_exec_parameters": {
            "operation": "dns",
            "allowed_operations": ["dns", "tcp", "http"],
            "operation_metadata": {
                "dns": {
                    "label": "DNS probe",
                    "description": "Resolve the in-cluster Service DNS name.",
                    "payload_schema": _schema(
                        {
                            "namespace": _SERVICE_PROBE_PROPS["namespace"],
                            "service": _SERVICE_PROBE_PROPS["service"],
                            "timeout_seconds": _SERVICE_PROBE_PROPS["timeout_seconds"],
                        },
                        required=["namespace", "service"],
                    ),
                },
                "tcp": {
                    "label": "TCP probe",
                    "description": "Attempt a bounded TCP connection to a resolved Service target.",
                    "payload_schema": _schema(
                        {
                            "namespace": _SERVICE_PROBE_PROPS["namespace"],
                            "service": _SERVICE_PROBE_PROPS["service"],
                            "port": _SERVICE_PROBE_PROPS["port"],
                            "timeout_seconds": _SERVICE_PROBE_PROPS["timeout_seconds"],
                        },
                        required=["namespace", "service", "port"],
                    ),
                },
                "http": {
                    "label": "HTTP probe",
                    "description": "Run a bounded HTTP GET against a resolved Service target.",
                    "payload_schema": _schema(
                        {
                            "namespace": _SERVICE_PROBE_PROPS["namespace"],
                            "service": _SERVICE_PROBE_PROPS["service"],
                            "port": _SERVICE_PROBE_PROPS["port"],
                            "scheme": _SERVICE_PROBE_PROPS["scheme"],
                            "path": _SERVICE_PROBE_PROPS["path"],
                            "timeout_seconds": _SERVICE_PROBE_PROPS["timeout_seconds"],
                            "expected_status_codes": _SERVICE_PROBE_PROPS["expected_status_codes"],
                        },
                        required=["namespace", "service", "port"],
                    ),
                },
            },
        },
        "default_expected_secs": 10,
        "default_timeout": 60,
        "service_exec_expected_outcome_default": {"success": True},
        "ingredient_purpose": "utility",
        "is_blocking": True,
        "retry_count": 0,
        "retry_delay": 0,
        "on_failure": "stop",
    },
)


K8S_RECIPE_TEMPLATES: tuple[JSONObject, ...] = (
    {
        "name": "plugin-health-check:k8s",
        "description": "Scheduled health check for the Kubernetes service plugin.",
        "enabled": True,
        "recipe_ingredients": [
            {
                "service_type": "k8s",
                "service_exec": "health_check",
                "destination_target": "kubernetes",
                "task_key_template": "k8s-health-check",
                "step_order": 1,
                "service_payload": {},
                "service_exec_expected_secs": 5,
                "service_exec_timeout": 30,
                "service_exec_expected_outcome": {"success": True},
                "run_phase": "firing",
                "run_condition": "always",
            }
        ],
    },
)


K8S_SCHEDULED_TASKS: tuple[JSONObject, ...] = (
    {
        "task_key": "plugin-health-check:k8s",
        "task_type": "plugin_health_check",
        "service_type": "k8s",
        "service_exec": "health_check",
        "source": "plugin_manifest",
        "is_enabled": True,
        "run_interval_seconds": 60,
        "priority": 20,
        "timeout_seconds": 30,
        "task_payload": {},
        "task_parameters": health_check_operation_parameters(),
        "expected_outcome": {"success": True},
    },
)
