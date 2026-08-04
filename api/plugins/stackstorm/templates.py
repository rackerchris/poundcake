"""StackStorm service plugin templates."""

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


_ACTION_EXECUTION_PROPS: JSONObject = {
    "action_ref": {"type": "string", "minLength": 1},
    "parameters": {"type": "object", "additionalProperties": True},
}
_WORKFLOW_EXECUTION_PROPS: JSONObject = {
    "workflow_ref": {"type": "string", "minLength": 1},
    "inputs": {"type": "object", "additionalProperties": True},
}

STACKSTORM_ACTION_OPERATIONS: list[str] = ["execute_action"]
STACKSTORM_ACTION_OPERATION_METADATA: JSONObject = {
    "execute_action": {
        "label": "Execute action",
        "description": "Start a StackStorm action execution.",
        "payload_schema": _schema(_ACTION_EXECUTION_PROPS, required=["action_ref"]),
    },
}
STACKSTORM_WORKFLOW_OPERATIONS: list[str] = ["execute_workflow"]
STACKSTORM_WORKFLOW_OPERATION_METADATA: JSONObject = {
    "execute_workflow": {
        "label": "Execute workflow",
        "description": "Start a StackStorm Orquesta workflow execution.",
        "payload_schema": _schema(_WORKFLOW_EXECUTION_PROPS, required=["workflow_ref"]),
    },
}
STACKSTORM_CONTENT_OPERATIONS: list[str] = ["sync_content"]
STACKSTORM_CONTENT_OPERATION_METADATA: JSONObject = {
    "sync_content": {
        "label": "Sync content",
        "description": "Sync PoundCake-owned StackStorm action metadata.",
        "payload_schema": _schema({}),
    },
}


STACKSTORM_INGREDIENT_TEMPLATES: tuple[JSONObject, ...] = (
    {
        "service_type": "stackstorm",
        "service_exec": "health_check",
        "destination_target": "stackstorm",
        "task_key_template": "stackstorm-health-check",
        "payload_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "service_payload_template": {},
        "service_exec_parameters": health_check_operation_parameters(),
        "default_expected_secs": 5,
        "default_timeout": 30,
        "service_exec_expected_outcome_default": {"status": "healthy"},
        "ingredient_purpose": "plugin_health",
        "is_blocking": True,
        "retry_count": 0,
        "retry_delay": 0,
        "on_failure": "continue",
    },
    {
        "service_type": "stackstorm",
        "service_exec": "action_execution",
        "destination_target": "stackstorm",
        "task_key_template": "stackstorm-action-execution",
        "payload_schema": _schema(_ACTION_EXECUTION_PROPS, required=["action_ref"]),
        "service_payload_template": {"action_ref": "", "parameters": {}},
        "service_exec_parameters": {
            "operation": "execute_action",
            "allowed_operations": STACKSTORM_ACTION_OPERATIONS,
            "operation_metadata": STACKSTORM_ACTION_OPERATION_METADATA,
        },
        "default_expected_secs": 30,
        "default_timeout": 300,
        "service_exec_expected_outcome_default": {"status": "succeeded"},
        "ingredient_purpose": "remediation",
        "is_blocking": True,
        "retry_count": 0,
        "retry_delay": 0,
        "on_failure": "stop",
    },
    {
        "service_type": "stackstorm",
        "service_exec": "workflow_execution",
        "destination_target": "stackstorm",
        "task_key_template": "stackstorm-workflow-execution",
        "payload_schema": _schema(_WORKFLOW_EXECUTION_PROPS, required=["workflow_ref"]),
        "service_payload_template": {"workflow_ref": "", "inputs": {}},
        "service_exec_parameters": {
            "operation": "execute_workflow",
            "allowed_operations": STACKSTORM_WORKFLOW_OPERATIONS,
            "operation_metadata": STACKSTORM_WORKFLOW_OPERATION_METADATA,
        },
        "default_expected_secs": 60,
        "default_timeout": 600,
        "service_exec_expected_outcome_default": {"status": "succeeded"},
        "ingredient_purpose": "remediation",
        "is_blocking": True,
        "retry_count": 0,
        "retry_delay": 0,
        "on_failure": "stop",
    },
    {
        "service_type": "stackstorm",
        "service_exec": "content_sync",
        "destination_target": "stackstorm",
        "task_key_template": "stackstorm-content-sync",
        "payload_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "service_payload_template": {},
        "service_exec_parameters": {
            "operation": "sync_content",
            "allowed_operations": STACKSTORM_CONTENT_OPERATIONS,
            "operation_metadata": STACKSTORM_CONTENT_OPERATION_METADATA,
        },
        "default_expected_secs": 10,
        "default_timeout": 120,
        "service_exec_expected_outcome_default": {"status": "succeeded"},
        "ingredient_purpose": "utility",
        "is_blocking": True,
        "retry_count": 1,
        "retry_delay": 5,
        "on_failure": "stop",
    },
)


STACKSTORM_RECIPE_TEMPLATES: tuple[JSONObject, ...] = (
    {
        "name": "plugin-health-check:stackstorm",
        "description": "Scheduled health check for the StackStorm service plugin.",
        "enabled": True,
        "recipe_ingredients": [
            {
                "service_type": "stackstorm",
                "service_exec": "health_check",
                "destination_target": "stackstorm",
                "task_key_template": "stackstorm-health-check",
                "step_order": 1,
                "on_success": "continue",
                "parallel_group": 0,
                "depth": 0,
                "service_payload": {},
                "service_exec_expected_secs": 5,
                "service_exec_timeout": 30,
                "service_exec_expected_outcome": {"status": "healthy"},
                "run_phase": "firing",
                "run_condition": "always",
            }
        ],
    },
    {
        "name": "plugin-content-sync:stackstorm",
        "description": "Scheduled content sync for PoundCake-owned StackStorm metadata.",
        "enabled": True,
        "recipe_ingredients": [
            {
                "service_type": "stackstorm",
                "service_exec": "content_sync",
                "destination_target": "stackstorm",
                "task_key_template": "stackstorm-content-sync",
                "step_order": 1,
                "on_success": "continue",
                "parallel_group": 0,
                "depth": 0,
                "service_payload": {},
                "service_exec_expected_secs": 10,
                "service_exec_timeout": 120,
                "service_exec_expected_outcome": {"status": "succeeded"},
                "run_phase": "firing",
                "run_condition": "always",
            }
        ],
    },
)


STACKSTORM_SCHEDULED_TASKS: tuple[JSONObject, ...] = (
    {
        "task_key": "plugin-health-check:stackstorm",
        "task_type": "plugin_health_check",
        "service_type": "stackstorm",
        "service_exec": "health_check",
        "source": "plugin_manifest",
        "is_enabled": True,
        "run_interval_seconds": 60,
        "priority": 20,
        "timeout_seconds": 30,
        "task_payload": {},
        "task_parameters": health_check_operation_parameters(),
        "expected_outcome": {"status": "healthy"},
    },
    {
        "task_key": "plugin-content-sync:stackstorm",
        "task_type": "service_execution",
        "service_type": "stackstorm",
        "service_exec": "content_sync",
        "source": "plugin_manifest",
        "is_enabled": True,
        "run_interval_seconds": 300,
        "priority": 30,
        "timeout_seconds": 120,
        "task_payload": {},
        "task_parameters": {
            "operation": "sync_content",
            "allowed_operations": STACKSTORM_CONTENT_OPERATIONS,
            "operation_metadata": STACKSTORM_CONTENT_OPERATION_METADATA,
        },
        "expected_outcome": {"status": "succeeded"},
    },
)
