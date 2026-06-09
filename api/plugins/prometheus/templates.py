"""Prometheus plugin templates."""

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


def _template(
    service_exec: str,
    *,
    payload_schema: JSONObject | None = None,
    payload_template: JSONObject | None = None,
    expected_secs: int = 5,
    timeout: int = 30,
    purpose: str = "utility",
) -> JSONObject:
    return {
        "service_type": "prometheus",
        "service_exec": service_exec,
        "destination_target": "prometheus",
        "task_key_template": f"prometheus-{service_exec.replace('_', '-')}",
        "payload_schema": payload_schema or _schema({}),
        "service_payload_template": payload_template or {},
        "service_exec_parameters": (
            health_check_operation_parameters() if service_exec == "health_check" else None
        ),
        "default_expected_secs": expected_secs,
        "default_timeout": timeout,
        "service_exec_expected_outcome_default": {"success": True},
        "ingredient_purpose": purpose,
        "is_blocking": True,
        "retry_count": 0,
        "retry_delay": 0,
        "on_failure": "continue" if purpose == "plugin_health" else "stop",
    }


PROMETHEUS_INGREDIENT_TEMPLATES: tuple[JSONObject, ...] = (
    _template("health_check", purpose="plugin_health"),
    _template(
        "inspect",
        payload_schema=_schema(
            {
                "metric": {"type": "string", "minLength": 1},
                "label_name": {"type": "string", "minLength": 1},
                "query": {"type": "string", "minLength": 1},
                "time": {"type": "string", "minLength": 1},
                "start": {"type": "string", "minLength": 1},
                "end": {"type": "string", "minLength": 1},
                "step": {"type": ["string", "integer"], "minLength": 1},
                "alert_name": {"type": "string", "minLength": 1},
                "labels": {"type": "object", "additionalProperties": True},
                "lookback_seconds": {"type": "integer", "minimum": 60},
                "step_seconds": {"type": "integer", "minimum": 15},
            }
        ),
        payload_template={},
        timeout=60,
    ),
    _template("reload_config", expected_secs=10, timeout=60),
)

PROMETHEUS_INGREDIENT_TEMPLATES[1]["service_exec_parameters"] = {
    "operation": "list_rules",
    "allowed_operations": [
        "alert_evidence",
        "list_rules",
        "list_rule_groups",
        "list_metrics",
        "list_labels",
        "list_label_values",
        "query",
        "range_query",
    ],
    "operation_metadata": {
        "alert_evidence": {
            "label": "Alert evidence",
            "description": "Evaluate an alert expression and collect current plus recent series evidence.",
        },
        "list_rules": {"label": "List rules", "description": "List alerting rules."},
        "list_rule_groups": {"label": "List rule groups", "description": "List rule groups."},
        "list_metrics": {"label": "List metrics", "description": "List metric names."},
        "list_labels": {"label": "List labels", "description": "List label names."},
        "list_label_values": {
            "label": "List label values",
            "description": "List values for a label.",
        },
        "query": {"label": "Query", "description": "Run an instant PromQL query."},
        "range_query": {"label": "Range query", "description": "Run a range PromQL query."},
    },
}
PROMETHEUS_INGREDIENT_TEMPLATES[2]["service_exec_parameters"] = {
    "operation": "reload_config",
    "allowed_operations": ["reload_config"],
    "operation_metadata": {
        "reload_config": {
            "label": "Reload config",
            "description": "Trigger Prometheus to reload its current rule and config state.",
        }
    },
}


PROMETHEUS_RECIPE_TEMPLATES: tuple[JSONObject, ...] = (
    {
        "name": "plugin-health-check:prometheus",
        "description": "Scheduled health check for the Prometheus service plugin.",
        "enabled": True,
        "recipe_ingredients": [
            {
                "service_type": "prometheus",
                "service_exec": "health_check",
                "destination_target": "prometheus",
                "task_key_template": "prometheus-health-check",
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


PROMETHEUS_SCHEDULED_TASKS: tuple[JSONObject, ...] = (
    {
        "task_key": "plugin-health-check:prometheus",
        "task_type": "plugin_health_check",
        "service_type": "prometheus",
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
