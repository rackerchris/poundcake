"""Composable dummy capability templates."""

from __future__ import annotations

from api.types import JSONObject


def load_dummy_capability_templates() -> tuple[JSONObject, ...]:
    """Advertise reusable dummy communication capabilities."""
    return (
        {
            "capability_id": "dummy.communication.open.default",
            "ingredient_ref": {
                "service_exec": "communication",
                "destination_target": "dummy",
                "task_key_template": "dummy-comms",
            },
            "operation": "open",
            "mode": "communication",
            "resource_kinds": ["thread", "communication"],
            "trigger_match": {"phase": "communicate"},
            "required_inputs": ["source", "context", "title", "description", "message"],
            "optional_inputs": [],
            "defaults": {
                "service_payload": {},
                "service_exec_parameters": {"operation": "open"},
                "expected_outcome": {"success": True},
                "expected_secs": 1,
                "timeout": 30,
                "role": "communicate",
            },
            "safety_class": "operator_guidance",
            "requires_evidence": False,
            "priority": 100,
        },
    )
