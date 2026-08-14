"""Bakery plugin capabilities translated into PoundCake templates."""

from __future__ import annotations

import os

from api.plugins.contract import health_check_operation_parameters
from api.types import JSONObject


def _active_provider() -> str:
    return (
        os.getenv("POUNDCAKE_BAKERY_ACTIVE_PROVIDER", "rackspace_core").strip() or "rackspace_core"
    )


def _route_label(provider: str) -> str:
    return f"Bakery {provider.replace('_', ' ').title()}"


TICKET_ID_CONTEXT_KEYS = ("ticket_id", "bakery_ticket_id", "bakery_comms_id", "communication_id")


BAKERY_HEALTH_INGREDIENT: JSONObject = {
    "service_type": "bakery",
    "service_exec": "health_check",
    "destination_target": "bakery",
    "task_key_template": "bakery-health-check",
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
}


def _ticket_context_schema(*, require_ticket_id: bool = False) -> JSONObject:
    schema: JSONObject = {
        "type": "object",
        "properties": {
            **{key: {"type": "string", "minLength": 1} for key in TICKET_ID_CONTEXT_KEYS},
            "source": {"type": "string", "minLength": 1},
            "route_label": {"type": "string", "minLength": 1},
            "destination_target": {"type": "string"},
            "provider_config": {"type": "object"},
            "semantic_text": {"type": "object"},
            "poundcake_policy": {"type": "object"},
            "execution_target": {"type": "string"},
            "provider_type": {"type": "string"},
            "alert_name": {"type": "string", "minLength": 1},
            "alert_group_name": {"type": "string", "minLength": 1},
            "labels": {"type": "object"},
            "annotations": {"type": "object"},
            "order_id": {"type": "integer", "minimum": 1},
            "req_id": {"type": "string", "minLength": 1},
            "source_path": {"type": "string", "minLength": 1},
            "operator_review_required": {"type": "boolean"},
            "evidence": {"type": "array"},
            "execution_context": {"type": "object"},
            "dish": {"type": "object"},
        },
        "additionalProperties": False,
    }
    if require_ticket_id:
        schema["anyOf"] = [{"required": [key]} for key in TICKET_ID_CONTEXT_KEYS]
    return schema


def _comms_schema(*, required: list[str] | None = None) -> JSONObject:
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string", "minLength": 1},
            "description": {"type": "string", "minLength": 1},
            "message": {"type": "string", "minLength": 1},
            "comment": {"type": "string", "minLength": 1},
            "severity": {"type": "string"},
            "category": {"type": "string"},
            "source": {"type": "string", "minLength": 1},
            "state": {"type": "string"},
            "resolution_code": {"type": "string"},
            "resolution_notes": {"type": "string"},
            "visibility": {"type": "string"},
            "ticket_id": {"type": "string", "minLength": 1},
            "context": _ticket_context_schema(),
        },
        "required": ["source", "context"] if required is None else required,
        "additionalProperties": False,
    }


def _collect_schema() -> JSONObject:
    return {
        "type": "object",
        "properties": {
            "order_id": {"type": "integer", "minimum": 1},
            "req_id": {"type": "string", "minLength": 1},
            "bakery_ticket_id": {"type": "string", "minLength": 1},
            "namespace": {"type": "string", "minLength": 1},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        },
        "additionalProperties": False,
    }


def _ticket_mutation_schema() -> JSONObject:
    schema = _comms_schema(required=[])
    schema["anyOf"] = [
        {"required": ["ticket_id"]},
        {
            "required": ["context"],
            "properties": {
                "context": _ticket_context_schema(require_ticket_id=True),
            },
        },
        {
            "required": ["context"],
            "properties": {
                "context": {
                    **_ticket_context_schema(require_ticket_id=False),
                    "required": ["poundcake_policy"],
                },
            },
        },
    ]
    return schema


def _comms_template(provider: str) -> JSONObject:
    ticket_create_schema = _comms_schema(required=["title", "description", "source", "context"])
    ticket_mutation_schema = _ticket_mutation_schema()
    ticket_create_payload = {
        "title": "PoundCake communication",
        "description": "PoundCake opened a Bakery communication.",
        "source": "poundcake",
        "context": {},
    }
    return {
        "service_type": "bakery",
        "service_exec": "communication",
        "destination_target": provider,
        "task_key_template": "bakery-comms",
        "payload_schema": _comms_schema(required=[]),
        "service_payload_template": ticket_create_payload,
        "service_exec_parameters": {
            "operation": "open",
            "allowed_operations": ["open", "notify", "update", "close"],
            "operation_metadata": {
                "open": {
                    "label": "Open",
                    "description": "Create a ticket or thread.",
                    "payload_schema": ticket_create_schema,
                },
                "notify": {
                    "label": "Notify",
                    "description": "Add a comment or notification.",
                    "payload_schema": ticket_mutation_schema,
                },
                "update": {
                    "label": "Update",
                    "description": "Update an existing ticket.",
                    "payload_schema": ticket_mutation_schema,
                },
                "close": {
                    "label": "Close",
                    "description": "Close an existing ticket.",
                    "payload_schema": ticket_mutation_schema,
                },
            },
        },
        "default_expected_secs": 5,
        "default_timeout": 120,
        "service_exec_expected_outcome_default": {"success": True},
        "ingredient_purpose": "comms",
        "is_blocking": False,
        "retry_count": 1,
        "retry_delay": 5,
        "on_failure": "continue",
    }


def _incident_reconcile_ingredient() -> JSONObject:
    reconcile_schema: JSONObject = {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "minimum": 1},
        },
        "additionalProperties": False,
    }
    return {
        "service_type": "bakery",
        "service_exec": "incident_reconcile",
        "destination_target": "bakery",
        "task_key_template": "bakery-incident-reconcile",
        "payload_schema": reconcile_schema,
        "service_payload_template": {},
        "service_exec_parameters": {
            "operation": "reconcile",
            "allowed_operations": ["reconcile"],
            "operation_metadata": {
                "reconcile": {
                    "label": "Reconcile",
                    "description": "Reconcile active orders against Prometheus alerts and Bakery ticket state.",
                    "payload_schema": reconcile_schema,
                },
            },
        },
        "default_expected_secs": 30,
        "default_timeout": 300,
        "service_exec_expected_outcome_default": {"success": True},
        "ingredient_purpose": "scheduled_reconciliation",
        "is_blocking": False,
        "retry_count": 0,
        "retry_delay": 0,
        "on_failure": "continue",
    }


def _collect_ingredient() -> JSONObject:
    collect_schema = _collect_schema()
    return {
        "service_type": "bakery",
        "service_exec": "collect",
        "destination_target": "bakery",
        "task_key_template": "bakery-collect",
        "payload_schema": collect_schema,
        "service_payload_template": {},
        "service_exec_parameters": {
            "operation": "monitor_diagnostics",
            "allowed_operations": ["monitor_diagnostics", "cluster_inventory", "ticket_context"],
            "operation_metadata": {
                "monitor_diagnostics": {
                    "label": "Monitor Diagnostics",
                    "description": "Return plugin health, configuration, and credential status.",
                    "payload_schema": collect_schema,
                },
                "cluster_inventory": {
                    "label": "Cluster Inventory",
                    "description": "Collect Kubernetes cluster topology and workload inventory.",
                    "payload_schema": collect_schema,
                },
                "ticket_context": {
                    "label": "Ticket Context",
                    "description": "Query orders, dishes, and ingredients by order_id, req_id, or bakery_ticket_id.",
                    "payload_schema": collect_schema,
                },
            },
        },
        "default_expected_secs": 15,
        "default_timeout": 120,
        "service_exec_expected_outcome_default": {"success": True},
        "ingredient_purpose": "collection",
        "is_blocking": False,
        "retry_count": 0,
        "retry_delay": 0,
        "on_failure": "continue",
    }


def ingredient_templates() -> tuple[JSONObject, ...]:
    provider = _active_provider()
    return (
        BAKERY_HEALTH_INGREDIENT,
        _comms_template(provider),
        _incident_reconcile_ingredient(),
        _collect_ingredient(),
    )


def recipe_templates() -> tuple[JSONObject, ...]:
    return (
        {
            "name": "plugin-health-check:bakery",
            "description": "Scheduled health check for the Bakery service plugin.",
            "enabled": True,
            "recipe_ingredients": [
                {
                    "service_type": "bakery",
                    "service_exec": "health_check",
                    "destination_target": "bakery",
                    "task_key_template": "bakery-health-check",
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
    )


def communication_routes() -> tuple[JSONObject, ...]:
    provider = _active_provider()
    return (
        {
            "id": "bakery-global-comms",
            "label": _route_label(provider),
            "service_type": "bakery",
            "destination_target": provider,
            "provider_config": {},
            "enabled": True,
            "position": 1,
        },
    )


BAKERY_SCHEDULED_TASKS: tuple[JSONObject, ...] = (
    {
        "task_key": "plugin-health-check:bakery",
        "task_type": "plugin_health_check",
        "service_type": "bakery",
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
        "task_key": "incident-reconcile:bakery",
        "task_type": "service_execution",
        "service_type": "bakery",
        "service_exec": "incident_reconcile",
        "source": "plugin_manifest",
        "is_enabled": True,
        "run_interval_seconds": 60,
        "priority": 10,
        "timeout_seconds": 300,
        "task_payload": {},
        "task_parameters": {
            "operation": "reconcile",
            "allowed_operations": ["reconcile"],
            "operation_metadata": {
                "reconcile": {
                    "label": "Reconcile",
                    "description": "Reconcile active orders against Prometheus alerts and Bakery ticket state.",
                },
            },
        },
        "expected_outcome": {"success": True},
    },
)
