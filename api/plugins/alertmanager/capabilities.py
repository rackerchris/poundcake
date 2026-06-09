"""Composable Alertmanager capability templates."""

from __future__ import annotations

from api.types import JSONObject


def load_alertmanager_capability_templates() -> tuple[JSONObject, ...]:
    """Advertise reusable Alertmanager evidence and guard capabilities."""
    return (
        {
            "capability_id": "alertmanager.inspect.verify-firing.before-evidence",
            "ingredient_ref": {
                "service_exec": "inspect",
                "destination_target": "alertmanager",
                "task_key_template": "alertmanager-firing-guard",
            },
            "operation": "verify_firing",
            "mode": "inspection",
            "resource_kinds": ["alert"],
            "trigger_match": {
                "phase": "verify_before_evidence",
            },
            "required_inputs": ["fingerprint", "labels"],
            "optional_inputs": ["active", "limit"],
            "defaults": {
                "service_payload": {
                    "fingerprint": "{{ order.raw_data.fingerprint }}",
                    "labels": "{{ order.labels }}",
                    "active": True,
                    "limit": 1,
                },
                "service_exec_parameters": {
                    "operation": "verify_firing",
                    "guard_role": "remediation_precondition",
                    "false_outcome": "cancel_downstream_no_remediation",
                },
                "expected_outcome": {"is_firing": True},
                "expected_secs": 5,
                "timeout": 30,
                "role": "verify_before_evidence",
            },
            "safety_class": "observe_only",
            "requires_evidence": False,
            "priority": 500,
        },
        {
            "capability_id": "alertmanager.inspect.verify-firing.before-action",
            "ingredient_ref": {
                "service_exec": "inspect",
                "destination_target": "alertmanager",
                "task_key_template": "alertmanager-firing-guard",
            },
            "operation": "verify_firing",
            "mode": "inspection",
            "resource_kinds": ["alert"],
            "trigger_match": {
                "phase": "verify_before_action",
            },
            "required_inputs": ["fingerprint", "labels"],
            "optional_inputs": ["active", "limit"],
            "defaults": {
                "service_payload": {
                    "fingerprint": "{{ order.raw_data.fingerprint }}",
                    "labels": "{{ order.labels }}",
                    "active": True,
                    "limit": 1,
                },
                "service_exec_parameters": {
                    "operation": "verify_firing",
                    "guard_role": "remediation_precondition",
                    "false_outcome": "cancel_downstream_no_remediation",
                },
                "expected_outcome": {"is_firing": True},
                "expected_secs": 5,
                "timeout": 30,
                "role": "verify_before_action",
            },
            "safety_class": "observe_only",
            "requires_evidence": False,
            "priority": 500,
        },
        {
            "capability_id": "alertmanager.inspect.active-alerts.evidence",
            "ingredient_ref": {
                "service_exec": "inspect",
                "destination_target": "alertmanager",
                "task_key_template": "alertmanager-inspect",
            },
            "operation": "list_alerts",
            "mode": "inspection",
            "resource_kinds": ["alert"],
            "trigger_match": {
                "phase": "evidence",
            },
            "required_inputs": ["labels", "matchers"],
            "optional_inputs": ["active", "silenced", "inhibited", "limit"],
            "defaults": {
                "service_payload": {
                    "labels": "{{ order.labels }}",
                    "matchers": [
                        'alertname="{{ order.raw_data.alertname }}"',
                        'group_name="{{ order.alert_group_name }}"',
                        'instance="{{ order.labels.instance }}"',
                    ],
                    "active": True,
                    "silenced": False,
                    "inhibited": False,
                    "limit": 20,
                },
                "service_exec_parameters": {
                    "operation": "list_alerts",
                    "managed_role": "gather_alertmanager_evidence",
                    "evidence_family": "alertmanager",
                },
                "expected_outcome": {"success": True},
                "expected_secs": 10,
                "timeout": 60,
                "role": "gather_alertmanager_evidence",
            },
            "safety_class": "observe_only",
            "requires_evidence": False,
            "priority": 400,
        },
        {
            "capability_id": "alertmanager.suppression.create",
            "ingredient_ref": {
                "service_exec": "suppression",
                "destination_target": "alertmanager",
                "task_key_template": "alertmanager-create-suppression",
            },
            "operation": "create",
            "mode": "action",
            "resource_kinds": ["suppression", "alert_silence"],
            "required_inputs": ["name", "starts_at", "ends_at", "matchers"],
            "optional_inputs": ["reason", "created_by", "summary_ticket_enabled"],
            "defaults": {
                "service_payload": {},
                "service_exec_parameters": {"operation": "create"},
                "expected_outcome": {"success": True},
                "expected_secs": 10,
                "timeout": 60,
            },
            "safety_class": "operator_guidance",
            "requires_evidence": False,
            "priority": 250,
        },
        {
            "capability_id": "alertmanager.suppression.expire",
            "ingredient_ref": {
                "service_exec": "suppression",
                "destination_target": "alertmanager",
                "task_key_template": "alertmanager-expire-suppression",
            },
            "operation": "expire",
            "mode": "action",
            "resource_kinds": ["suppression", "alert_silence"],
            "required_inputs": ["source_ref"],
            "optional_inputs": [],
            "defaults": {
                "service_payload": {},
                "service_exec_parameters": {"operation": "expire"},
                "expected_outcome": {"success": True},
                "expected_secs": 10,
                "timeout": 60,
            },
            "safety_class": "operator_guidance",
            "requires_evidence": False,
            "priority": 250,
        },
    )
