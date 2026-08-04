"""Composable Bakery capability templates."""

from __future__ import annotations

from api.plugins.bakery.templates import _active_provider
from api.types import JSONObject


def load_bakery_capability_templates() -> tuple[JSONObject, ...]:
    """Advertise reusable Bakery communication capabilities."""
    provider = _active_provider()
    return (
        {
            "capability_id": "bakery.communication.open.default",
            "ingredient_ref": {
                "service_exec": "communication",
                "destination_target": provider,
                "task_key_template": "bakery-comms",
            },
            "operation": "open",
            "mode": "communication",
            "resource_kinds": ["ticket", "thread", "communication"],
            "trigger_match": {"phase": "communicate"},
            "required_inputs": ["title", "description", "source", "context"],
            "optional_inputs": [
                "message",
                "severity",
                "category",
                "state",
                "resolution_code",
                "resolution_notes",
                "visibility",
                "ticket_id",
                "comment",
            ],
            "defaults": {
                "service_payload": {
                    "title": "PoundCake communication",
                    "description": "PoundCake opened a Bakery communication.",
                    "source": "poundcake",
                    "context": {},
                },
                "service_exec_parameters": {"operation": "open"},
                "expected_outcome": {"success": True},
                "expected_secs": 5,
                "timeout": 120,
                "role": "communicate",
            },
            "safety_class": "operator_guidance",
            "requires_evidence": False,
            "priority": 200,
        },
        {
            "capability_id": "bakery.incident_reconcile",
            "ingredient_ref": {
                "service_exec": "incident_reconcile",
                "destination_target": "bakery",
                "task_key_template": "bakery-incident-reconcile",
            },
            "operation": "reconcile",
            "mode": "inspection",
            "resource_kinds": ["incident", "alert", "ticket"],
            "trigger_match": {"phase": "both"},
            "required_inputs": [],
            "optional_inputs": ["limit"],
            "defaults": {
                "service_payload": {},
                "service_exec_parameters": {"operation": "reconcile"},
                "expected_outcome": {"success": True},
                "expected_secs": 30,
                "timeout": 300,
                "role": "reconcile",
            },
            "safety_class": "observe_only",
            "requires_evidence": False,
            "priority": 100,
        },
        {
            "capability_id": "bakery.collect.monitor_diagnostics",
            "ingredient_ref": {
                "service_exec": "collect",
                "destination_target": "bakery",
                "task_key_template": "bakery-collect",
            },
            "operation": "monitor_diagnostics",
            "mode": "inspection",
            "resource_kinds": ["plugin_state", "health"],
            "trigger_match": {"phase": "both"},
            "required_inputs": [],
            "optional_inputs": [],
            "defaults": {
                "service_payload": {},
                "service_exec_parameters": {"operation": "monitor_diagnostics"},
                "expected_outcome": {"success": True},
                "expected_secs": 5,
                "timeout": 30,
                "role": "collect",
            },
            "safety_class": "observe_only",
            "requires_evidence": False,
            "priority": 150,
        },
        {
            "capability_id": "bakery.collect.cluster_inventory",
            "ingredient_ref": {
                "service_exec": "collect",
                "destination_target": "bakery",
                "task_key_template": "bakery-collect",
            },
            "operation": "cluster_inventory",
            "mode": "inspection",
            "resource_kinds": ["k8s_cluster", "nodes", "workloads", "storage"],
            "trigger_match": {"phase": "both"},
            "required_inputs": [],
            "optional_inputs": ["namespace", "limit"],
            "defaults": {
                "service_payload": {},
                "service_exec_parameters": {"operation": "cluster_inventory"},
                "expected_outcome": {"success": True},
                "expected_secs": 30,
                "timeout": 120,
                "role": "collect",
            },
            "safety_class": "observe_only",
            "requires_evidence": False,
            "priority": 150,
        },
        {
            "capability_id": "bakery.collect.ticket_context",
            "ingredient_ref": {
                "service_exec": "collect",
                "destination_target": "bakery",
                "task_key_template": "bakery-collect",
            },
            "operation": "ticket_context",
            "mode": "inspection",
            "resource_kinds": ["order", "dish", "ingredient"],
            "trigger_match": {"phase": "both"},
            "required_inputs": [],
            "optional_inputs": ["order_id", "req_id", "bakery_ticket_id", "limit"],
            "defaults": {
                "service_payload": {},
                "service_exec_parameters": {"operation": "ticket_context"},
                "expected_outcome": {"success": True},
                "expected_secs": 5,
                "timeout": 30,
                "role": "collect",
            },
            "safety_class": "observe_only",
            "requires_evidence": False,
            "priority": 150,
        },
    )
