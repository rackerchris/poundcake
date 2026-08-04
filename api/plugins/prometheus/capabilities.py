"""Composable Prometheus capability templates."""

from __future__ import annotations

from api.types import JSONObject


def load_prometheus_capability_templates() -> tuple[JSONObject, ...]:
    """Advertise reusable Prometheus evidence and reload capabilities."""
    return (
        {
            "capability_id": "prometheus.inspect.alert-evidence.generic",
            "ingredient_ref": {
                "service_exec": "inspect",
                "destination_target": "prometheus",
                "task_key_template": "prometheus-inspect",
            },
            "operation": "alert_evidence",
            "mode": "inspection",
            "resource_kinds": ["alert_rule", "timeseries"],
            "trigger_match": {
                "phase": "evidence",
            },
            "required_inputs": ["alert_name", "labels"],
            "optional_inputs": ["lookback_seconds", "step_seconds"],
            "defaults": {
                "service_payload": {
                    "alert_name": "{{ order.raw_data.alertname }}",
                    "labels": "{{ order.labels }}",
                    "lookback_seconds": 3600,
                    "step_seconds": 60,
                },
                "service_exec_parameters": {
                    "operation": "alert_evidence",
                    "managed_role": "gather_prometheus_evidence",
                    "evidence_family": "prometheus",
                    "alert_name": "{{ order.raw_data.alertname }}",
                },
                "expected_outcome": {"success": True},
                "expected_secs": 15,
                "timeout": 90,
                "role": "gather_prometheus_evidence",
            },
            "safety_class": "observe_only",
            "requires_evidence": False,
            "priority": 400,
        },
        {
            "capability_id": "prometheus.reload.rule-state",
            "ingredient_ref": {
                "service_exec": "reload_config",
                "destination_target": "prometheus",
                "task_key_template": "prometheus-reload-config",
            },
            "operation": "reload_config",
            "mode": "action",
            "resource_kinds": ["alert_rule", "rule_state"],
            "required_inputs": [],
            "optional_inputs": [],
            "defaults": {
                "service_payload": {},
                "service_exec_parameters": {},
                "expected_outcome": {"success": True},
                "expected_secs": 10,
                "timeout": 60,
                "role": "reload_prometheus_rule_state",
            },
            "safety_class": "operator_guidance",
            "requires_evidence": False,
            "priority": 300,
        },
    )
