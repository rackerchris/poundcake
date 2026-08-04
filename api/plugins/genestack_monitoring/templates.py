"""Genestack Monitoring plugin templates."""

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


_EMPTY_SCHEMA = _schema({})
_ALERT_EXPORT_PAYLOAD_SCHEMA = _schema(
    {
        "namespace": {"type": "string", "minLength": 1},
        "crd_name": {"type": "string", "minLength": 1},
        "group_name": {"type": "string", "minLength": 1},
        "rule_name": {"type": "string", "minLength": 1},
    },
    required=["crd_name", "group_name", "rule_name"],
)


GENESTACK_MONITORING_CONTENT_SYNC_OPERATION = "sync_content"
GENESTACK_MONITORING_ALERT_EXPORT_OPERATION = "export_alert_updates"
GENESTACK_MONITORING_CONTENT_SYNC_PARAMETERS: JSONObject = {
    "operation": GENESTACK_MONITORING_CONTENT_SYNC_OPERATION,
    "allowed_operations": [GENESTACK_MONITORING_CONTENT_SYNC_OPERATION],
    "operation_metadata": {
        GENESTACK_MONITORING_CONTENT_SYNC_OPERATION: {
            "label": "Sync content",
            "description": "Refresh PoundCake recipes from the Genestack Monitoring alert catalog.",
            "payload_schema": _EMPTY_SCHEMA,
        },
    },
}
GENESTACK_MONITORING_ALERT_EXPORT_PARAMETERS: JSONObject = {
    "operation": GENESTACK_MONITORING_ALERT_EXPORT_OPERATION,
    "allowed_operations": [GENESTACK_MONITORING_ALERT_EXPORT_OPERATION],
    "operation_metadata": {
        GENESTACK_MONITORING_ALERT_EXPORT_OPERATION: {
            "label": "Export alert updates",
            "description": (
                "Render the current Genestack-managed PrometheusRule update and create a GitHub PR."
            ),
            "payload_schema": _ALERT_EXPORT_PAYLOAD_SCHEMA,
        },
    },
}

GENESTACK_MONITORING_INGREDIENT_TEMPLATES: tuple[JSONObject, ...] = (
    {
        "service_type": "genestack_monitoring",
        "service_exec": "health_check",
        "destination_target": "genestack-monitoring",
        "task_key_template": "genestack-monitoring-health-check",
        "payload_schema": _EMPTY_SCHEMA,
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
        "service_type": "genestack_monitoring",
        "service_exec": "content_sync",
        "destination_target": "genestack-monitoring",
        "task_key_template": "genestack-monitoring-content-sync",
        "payload_schema": _EMPTY_SCHEMA,
        "service_payload_template": {},
        "service_exec_parameters": GENESTACK_MONITORING_CONTENT_SYNC_PARAMETERS,
        "default_expected_secs": 30,
        "default_timeout": 180,
        "service_exec_expected_outcome_default": {"success": True},
        "ingredient_purpose": "utility",
        "is_blocking": True,
        "retry_count": 0,
        "retry_delay": 0,
        "on_failure": "continue",
    },
    {
        "service_type": "genestack_monitoring",
        "service_exec": "repo_sync",
        "destination_target": "genestack-monitoring",
        "task_key_template": "genestack-monitoring-alert-export",
        "payload_schema": _ALERT_EXPORT_PAYLOAD_SCHEMA,
        "service_payload_template": {},
        "service_exec_parameters": GENESTACK_MONITORING_ALERT_EXPORT_PARAMETERS,
        "default_expected_secs": 30,
        "default_timeout": 180,
        "service_exec_expected_outcome_default": {"success": True},
        "ingredient_purpose": "utility",
        "is_blocking": True,
        "retry_count": 0,
        "retry_delay": 0,
        "on_failure": "continue",
    },
)


GENESTACK_MONITORING_RECIPE_TEMPLATES: tuple[JSONObject, ...] = (
    {
        "name": "plugin-health-check:genestack_monitoring",
        "description": "Scheduled health check for the Genestack Monitoring bootstrap plugin.",
        "enabled": True,
        "recipe_ingredients": [
            {
                "service_type": "genestack_monitoring",
                "service_exec": "health_check",
                "destination_target": "genestack-monitoring",
                "task_key_template": "genestack-monitoring-health-check",
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
    {
        "name": "plugin-content-sync:genestack_monitoring",
        "description": "Scheduled content sync for Genestack Monitoring alert recipe bindings.",
        "enabled": True,
        "recipe_ingredients": [
            {
                "service_type": "genestack_monitoring",
                "service_exec": "content_sync",
                "destination_target": "genestack-monitoring",
                "task_key_template": "genestack-monitoring-content-sync",
                "step_order": 1,
                "on_success": "continue",
                "parallel_group": 0,
                "depth": 0,
                "service_payload": {},
                "service_exec_parameters_override": GENESTACK_MONITORING_CONTENT_SYNC_PARAMETERS,
                "service_exec_expected_secs": 30,
                "service_exec_timeout": 180,
                "service_exec_expected_outcome": {"success": True},
                "run_phase": "firing",
                "run_condition": "always",
            }
        ],
    },
    {
        "name": "operator-action:genestack-monitoring:export-alert-updates",
        "description": "Operator-requested Genestack Monitoring alert export.",
        "enabled": True,
        "recipe_ingredients": [
            {
                "service_type": "genestack_monitoring",
                "service_exec": "repo_sync",
                "destination_target": "genestack-monitoring",
                "task_key_template": "genestack-monitoring-alert-export",
                "step_order": 1,
                "on_success": "continue",
                "parallel_group": 0,
                "depth": 0,
                "service_payload": {},
                "service_payload_from_order": True,
                "service_exec_parameters_override": GENESTACK_MONITORING_ALERT_EXPORT_PARAMETERS,
                "service_exec_expected_secs": 30,
                "service_exec_timeout": 180,
                "service_exec_expected_outcome": {"success": True},
                "run_phase": "firing",
                "run_condition": "always",
            }
        ],
    },
)


GENESTACK_MONITORING_SCHEDULED_TASKS: tuple[JSONObject, ...] = (
    {
        "task_key": "plugin-health-check:genestack_monitoring",
        "task_type": "plugin_health_check",
        "service_type": "genestack_monitoring",
        "service_exec": "health_check",
        "source": "plugin_manifest",
        "is_enabled": True,
        "run_interval_seconds": 300,
        "priority": 30,
        "timeout_seconds": 30,
        "task_payload": {},
        "task_parameters": health_check_operation_parameters(),
        "expected_outcome": {"success": True},
    },
    {
        "task_key": "plugin-content-sync:genestack_monitoring",
        "task_type": "service_execution",
        "service_type": "genestack_monitoring",
        "service_exec": "content_sync",
        "source": "plugin_manifest",
        "is_enabled": True,
        "run_interval_seconds": 300,
        "priority": 40,
        "timeout_seconds": 180,
        "task_payload": {},
        "task_parameters": GENESTACK_MONITORING_CONTENT_SYNC_PARAMETERS,
        "expected_outcome": {"success": True},
    },
)
