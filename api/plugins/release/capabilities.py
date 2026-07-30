"""Composable release update capability templates."""

from __future__ import annotations

from api.types import JSONObject


def load_release_capability_templates() -> tuple[JSONObject, ...]:
    """Advertise reusable release update check capabilities."""
    return (
        {
            "capability_id": "release.check_updates.registry",
            "ingredient_ref": {
                "service_exec": "check_updates",
                "destination_target": "release",
                "task_key_template": "release-check-updates",
            },
            "operation": "check_updates",
            "mode": "inspection",
            "resource_kinds": ["helm_chart", "oci_registry"],
            "trigger_match": {"phase": "inspection"},
            "required_inputs": [],
            "optional_inputs": [],
            "defaults": {
                "service_payload": {},
                "service_exec_parameters": {
                    "operation": "check_updates",
                },
                "expected_outcome": {"success": True},
                "expected_secs": 30,
                "timeout": 120,
                "role": "check_release_updates",
            },
            "safety_class": "observe_only",
            "requires_evidence": False,
            "priority": 200,
        },
    )