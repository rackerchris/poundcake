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
            "required_inputs": ["source", "context"],
            "optional_inputs": [
                "title",
                "description",
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
                "service_payload": {},
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
    )
