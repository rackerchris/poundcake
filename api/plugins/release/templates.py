"""Release update plugin templates."""

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
    expected_secs: int = 10,
    timeout: int = 60,
    purpose: str = "utility",
) -> JSONObject:
    return {
        "service_type": "release",
        "service_exec": service_exec,
        "destination_target": "release",
        "task_key_template": f"release-{service_exec.replace('_', '-')}",
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


RELEASE_INGREDIENT_TEMPLATES: tuple[JSONObject, ...] = (
    _template("health_check", purpose="plugin_health"),
    _template(
        "check_updates",
        payload_schema=_schema({}),
        payload_template={},
        expected_secs=30,
        timeout=120,
        purpose="monitoring",
    ),
)

RELEASE_INGREDIENT_TEMPLATES[1]["service_exec_parameters"] = {
    "operation": "check_updates",
    "allowed_operations": ["check_updates"],
    "operation_metadata": {
        "check_updates": {
            "label": "Check for updates",
            "description": "Query the OCI registry for newer Helm chart releases and create notification records if an update is available.",
            "payload_schema": _schema({}),
        },
    },
}


RELEASE_RECIPE_TEMPLATES: tuple[JSONObject, ...] = (
    {
        "name": "plugin-health-check:release",
        "description": "Scheduled health check for the release update service plugin.",
        "enabled": True,
        "recipe_ingredients": [
            {
                "service_type": "release",
                "service_exec": "health_check",
                "destination_target": "release",
                "task_key_template": "release-health-check",
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


RELEASE_SCHEDULED_TASKS: tuple[JSONObject, ...] = (
    {
        "task_key": "plugin-health-check:release",
        "task_type": "plugin_health_check",
        "service_type": "release",
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
    {
        "task_key": "check-release-updates",
        "task_type": "service_execution",
        "service_type": "release",
        "service_exec": "check_updates",
        "source": "plugin_manifest",
        "is_enabled": True,
        "run_interval_seconds": 21600,
        "priority": 50,
        "timeout_seconds": 120,
        "task_payload": {},
        "task_parameters": {
            "operation": "check_updates",
        },
        "expected_outcome": {"success": True},
    },
)
