"""Alertmanager plugin capabilities translated into PoundCake templates."""

from __future__ import annotations

from api.plugins.contract import health_check_operation_parameters
from api.types import JSONObject

ALERTMANAGER_INSPECT_OPERATIONS = (
    "list_alerts",
    "list_groups",
    "find_inhibited_by_source",
    "verify_firing",
)

ALERTMANAGER_INSPECT_OPERATION_METADATA: JSONObject = {
    "list_alerts": {
        "label": "List alerts",
        "description": "Read active, silenced, and inhibited alerts from Alertmanager.",
    },
    "list_groups": {
        "label": "List alert groups",
        "description": "Read grouped alerts and route mute evidence from Alertmanager.",
    },
    "find_inhibited_by_source": {
        "label": "Find alerts inhibited by source",
        "description": "Find Alertmanager alerts inhibited by the current source alert fingerprint.",
    },
    "verify_firing": {
        "label": "Verify alert is firing",
        "description": "Read Alertmanager alerts and verify the source alert is still active.",
    },
}

ALERTMANAGER_INSPECT_PAYLOAD_SCHEMA: JSONObject = {
    "type": "object",
    "properties": {
        "fingerprint": {"type": "string"},
        "labels": {
            "type": "object",
            "additionalProperties": {"type": ["string", "number", "boolean", "null"]},
        },
        "matchers": {
            "type": "array",
            "items": {"type": "string"},
        },
        "receiver": {"type": "string"},
        "active": {"type": "boolean"},
        "silenced": {"type": "boolean"},
        "inhibited": {"type": "boolean"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
    },
    "additionalProperties": False,
}

ALERTMANAGER_FIND_INHIBITED_PAYLOAD_SCHEMA: JSONObject = {
    **ALERTMANAGER_INSPECT_PAYLOAD_SCHEMA,
    "required": ["fingerprint"],
}

ALERTMANAGER_SUPPRESSION_PAYLOAD_SCHEMA: JSONObject = {
    "type": "object",
    "properties": {
        "source_ref": {"type": "string"},
        "name": {"type": "string"},
        "reason": {"type": ["string", "null"]},
        "starts_at": {"type": "string"},
        "ends_at": {"type": "string"},
        "created_by": {"type": ["string", "null"]},
        "summary_ticket_enabled": {"type": "boolean"},
        "matchers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label_key": {"type": "string"},
                    "operator": {
                        "type": "string",
                        "enum": ["eq", "neq", "regex", "nregex", "exists", "not_exists"],
                    },
                    "value": {"type": ["string", "null"]},
                },
                "required": ["label_key", "operator"],
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}

_SUPPRESSION_CREATE_PROPS: JSONObject = {
    **ALERTMANAGER_SUPPRESSION_PAYLOAD_SCHEMA["properties"],
    "matchers": {
        **ALERTMANAGER_SUPPRESSION_PAYLOAD_SCHEMA["properties"]["matchers"],
        "minItems": 1,
    },
}

ALERTMANAGER_SUPPRESSION_CREATE_PAYLOAD_SCHEMA: JSONObject = {
    **ALERTMANAGER_SUPPRESSION_PAYLOAD_SCHEMA,
    "properties": _SUPPRESSION_CREATE_PROPS,
    "required": ["matchers", "name", "starts_at", "ends_at"],
}

ALERTMANAGER_SUPPRESSION_UPDATE_PAYLOAD_SCHEMA: JSONObject = {
    **ALERTMANAGER_SUPPRESSION_PAYLOAD_SCHEMA,
    "properties": _SUPPRESSION_CREATE_PROPS,
    "required": ["source_ref", "matchers", "name", "starts_at", "ends_at"],
}

ALERTMANAGER_SUPPRESSION_SOURCE_REF_PAYLOAD_SCHEMA: JSONObject = {
    "type": "object",
    "properties": {
        "source_ref": {"type": "string", "minLength": 1},
    },
    "required": ["source_ref"],
    "additionalProperties": False,
}

ALERTMANAGER_INSPECT_OPERATION_METADATA["list_alerts"][
    "payload_schema"
] = ALERTMANAGER_INSPECT_PAYLOAD_SCHEMA
ALERTMANAGER_INSPECT_OPERATION_METADATA["list_groups"][
    "payload_schema"
] = ALERTMANAGER_INSPECT_PAYLOAD_SCHEMA
ALERTMANAGER_INSPECT_OPERATION_METADATA["verify_firing"][
    "payload_schema"
] = ALERTMANAGER_INSPECT_PAYLOAD_SCHEMA
ALERTMANAGER_INSPECT_OPERATION_METADATA["find_inhibited_by_source"][
    "payload_schema"
] = ALERTMANAGER_FIND_INHIBITED_PAYLOAD_SCHEMA

ALERTMANAGER_INGREDIENT_TEMPLATES: tuple[JSONObject, ...] = (
    {
        "service_type": "alertmanager",
        "service_exec": "health_check",
        "destination_target": "alertmanager",
        "task_key_template": "alertmanager-health-check",
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
        "service_type": "alertmanager",
        "service_exec": "inspect",
        "destination_target": "alertmanager",
        "task_key_template": "alertmanager-inspect",
        "payload_schema": ALERTMANAGER_INSPECT_PAYLOAD_SCHEMA,
        "service_payload_template": {},
        "service_exec_parameters": {
            "operation": "list_alerts",
            "allowed_operations": list(ALERTMANAGER_INSPECT_OPERATIONS),
            "operation_metadata": ALERTMANAGER_INSPECT_OPERATION_METADATA,
        },
        "default_expected_secs": 10,
        "default_timeout": 60,
        "service_exec_expected_outcome_default": {"success": True},
        "ingredient_purpose": "utility",
        "is_blocking": False,
        "retry_count": 0,
        "retry_delay": 0,
        "on_failure": "continue",
    },
    {
        "service_type": "alertmanager",
        "service_exec": "inspect",
        "destination_target": "alertmanager",
        "task_key_template": "alertmanager-firing-guard",
        "payload_schema": ALERTMANAGER_INSPECT_PAYLOAD_SCHEMA,
        "service_payload_template": {},
        "service_exec_parameters": {
            "operation": "verify_firing",
            "allowed_operations": ["verify_firing"],
            "operation_metadata": {
                "verify_firing": ALERTMANAGER_INSPECT_OPERATION_METADATA["verify_firing"],
            },
        },
        "default_expected_secs": 5,
        "default_timeout": 30,
        "service_exec_expected_outcome_default": {"is_firing": True},
        "ingredient_purpose": "utility",
        "is_blocking": True,
        "retry_count": 0,
        "retry_delay": 0,
        "on_failure": "stop",
    },
    {
        "service_type": "alertmanager",
        "service_exec": "sync_silences",
        "destination_target": "alertmanager",
        "task_key_template": "alertmanager-sync-silences",
        "payload_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "service_payload_template": {},
        "default_expected_secs": 10,
        "default_timeout": 60,
        "service_exec_expected_outcome_default": {"success": True},
        "ingredient_purpose": "suppression_sync",
        "is_blocking": True,
        "retry_count": 0,
        "retry_delay": 0,
        "on_failure": "continue",
    },
    {
        "service_type": "alertmanager",
        "service_exec": "suppression",
        "destination_target": "alertmanager",
        "task_key_template": "alertmanager-create-suppression",
        "payload_schema": ALERTMANAGER_SUPPRESSION_PAYLOAD_SCHEMA,
        "service_payload_template": {},
        "service_exec_parameters": {
            "operation": "create",
            "allowed_operations": ["create"],
            "operation_metadata": {
                "create": {
                    "label": "Create suppression",
                    "description": "Create an Alertmanager silence through PoundCake.",
                    "payload_schema": ALERTMANAGER_SUPPRESSION_CREATE_PAYLOAD_SCHEMA,
                }
            },
        },
        "default_expected_secs": 10,
        "default_timeout": 60,
        "service_exec_expected_outcome_default": {"success": True},
        "ingredient_purpose": "suppression_lifecycle",
        "is_blocking": True,
        "retry_count": 0,
        "retry_delay": 0,
        "on_failure": "stop",
    },
    {
        "service_type": "alertmanager",
        "service_exec": "suppression",
        "destination_target": "alertmanager",
        "task_key_template": "alertmanager-expire-suppression",
        "payload_schema": ALERTMANAGER_SUPPRESSION_PAYLOAD_SCHEMA,
        "service_payload_template": {},
        "service_exec_parameters": {
            "operation": "expire",
            "allowed_operations": ["expire"],
            "operation_metadata": {
                "expire": {
                    "label": "Expire suppression",
                    "description": "Expire an existing Alertmanager silence through PoundCake.",
                    "payload_schema": ALERTMANAGER_SUPPRESSION_SOURCE_REF_PAYLOAD_SCHEMA,
                }
            },
        },
        "default_expected_secs": 10,
        "default_timeout": 60,
        "service_exec_expected_outcome_default": {"success": True},
        "ingredient_purpose": "suppression_lifecycle",
        "is_blocking": True,
        "retry_count": 0,
        "retry_delay": 0,
        "on_failure": "stop",
    },
    {
        "service_type": "alertmanager",
        "service_exec": "suppression",
        "destination_target": "alertmanager",
        "task_key_template": "alertmanager-update-suppression",
        "payload_schema": ALERTMANAGER_SUPPRESSION_PAYLOAD_SCHEMA,
        "service_payload_template": {},
        "service_exec_parameters": {
            "operation": "update",
            "allowed_operations": ["update"],
            "operation_metadata": {
                "update": {
                    "label": "Update suppression",
                    "description": "Update an existing Alertmanager silence through PoundCake.",
                    "payload_schema": ALERTMANAGER_SUPPRESSION_UPDATE_PAYLOAD_SCHEMA,
                }
            },
        },
        "default_expected_secs": 10,
        "default_timeout": 60,
        "service_exec_expected_outcome_default": {"success": True},
        "ingredient_purpose": "suppression_lifecycle",
        "is_blocking": True,
        "retry_count": 0,
        "retry_delay": 0,
        "on_failure": "stop",
    },
)


ALERTMANAGER_RECIPE_TEMPLATES: tuple[JSONObject, ...] = (
    {
        "name": "plugin-health-check:alertmanager",
        "description": "Scheduled health check for the Alertmanager service plugin.",
        "enabled": True,
        "recipe_ingredients": [
            {
                "service_type": "alertmanager",
                "service_exec": "health_check",
                "destination_target": "alertmanager",
                "task_key_template": "alertmanager-health-check",
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
        "name": "alertmanager-sync-silences",
        "description": "Synchronize Alertmanager silences into local PoundCake suppressions.",
        "enabled": True,
        "recipe_ingredients": [
            {
                "service_type": "alertmanager",
                "service_exec": "sync_silences",
                "destination_target": "alertmanager",
                "task_key_template": "alertmanager-sync-silences",
                "step_order": 1,
                "on_success": "continue",
                "parallel_group": 0,
                "depth": 0,
                "service_payload": {},
                "service_exec_expected_secs": 10,
                "service_exec_timeout": 60,
                "service_exec_expected_outcome": {"success": True},
                "run_phase": "firing",
                "run_condition": "always",
            }
        ],
    },
    {
        "name": "operator-action:alertmanager:create-suppression",
        "description": "Operator-requested Alertmanager suppression creation.",
        "enabled": True,
        "recipe_ingredients": [
            {
                "service_type": "alertmanager",
                "service_exec": "suppression",
                "destination_target": "alertmanager",
                "task_key_template": "alertmanager-create-suppression",
                "step_order": 1,
                "service_payload": {},
                "service_payload_from_order": True,
                "service_exec_parameters_override": {
                    "operation": "create",
                    "allowed_operations": ["create"],
                },
                "service_exec_expected_secs": 10,
                "service_exec_timeout": 60,
                "service_exec_expected_outcome": {"success": True},
                "run_phase": "firing",
                "run_condition": "always",
            }
        ],
    },
    {
        "name": "operator-action:alertmanager:update-suppression",
        "description": "Operator-requested Alertmanager suppression update.",
        "enabled": True,
        "recipe_ingredients": [
            {
                "service_type": "alertmanager",
                "service_exec": "suppression",
                "destination_target": "alertmanager",
                "task_key_template": "alertmanager-update-suppression",
                "step_order": 1,
                "service_payload": {},
                "service_payload_from_order": True,
                "service_exec_parameters_override": {
                    "operation": "update",
                    "allowed_operations": ["update"],
                },
                "service_exec_expected_secs": 10,
                "service_exec_timeout": 60,
                "service_exec_expected_outcome": {"success": True},
                "run_phase": "firing",
                "run_condition": "always",
            }
        ],
    },
    {
        "name": "operator-action:alertmanager:expire-suppression",
        "description": "Operator-requested Alertmanager suppression expiration.",
        "enabled": True,
        "recipe_ingredients": [
            {
                "service_type": "alertmanager",
                "service_exec": "suppression",
                "destination_target": "alertmanager",
                "task_key_template": "alertmanager-expire-suppression",
                "step_order": 1,
                "service_payload": {},
                "service_payload_from_order": True,
                "service_exec_parameters_override": {
                    "operation": "expire",
                    "allowed_operations": ["expire"],
                },
                "service_exec_expected_secs": 10,
                "service_exec_timeout": 60,
                "service_exec_expected_outcome": {"success": True},
                "run_phase": "firing",
                "run_condition": "always",
            }
        ],
    },
)


ALERTMANAGER_SCHEDULED_TASKS: tuple[JSONObject, ...] = (
    {
        "task_key": "plugin-health-check:alertmanager",
        "task_type": "plugin_health_check",
        "service_type": "alertmanager",
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
        "task_key": "alertmanager-sync-silences",
        "task_type": "service_execution",
        "service_type": "alertmanager",
        "service_exec": "sync_silences",
        "source": "plugin_manifest",
        "is_enabled": True,
        "run_interval_seconds": 300,
        "priority": 80,
        "timeout_seconds": 60,
        "task_payload": {},
        "task_parameters": {},
        "expected_outcome": {"success": True},
    },
)
