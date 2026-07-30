"""Tests for plugin capability advertisement and catalog composition."""

from __future__ import annotations

import pytest

from api.plugins.capability_matrix import (
    NATIVE_K8S_PREFERRED_ALERT_GROUPS,
    OPERATOR_GUIDANCE_ONLY_ALERT_GROUPS,
    PLUGIN_CAPABILITY_OWNERSHIP_MATRIX,
    alert_group_provider_policy,
)
from api.plugins.catalog import (
    build_enabled_plugin_capability_catalog,
    get_enabled_plugin_communication_routes,
)
from api.plugins.alertmanager.capabilities import load_alertmanager_capability_templates
from api.plugins.bakery.capabilities import load_bakery_capability_templates
from api.plugins.git.capabilities import load_git_capability_templates
from api.plugins.dummy.capabilities import load_dummy_capability_templates
from api.plugins.github.capabilities import load_github_capability_templates
from api.plugins.k8s.capabilities import load_k8s_capability_templates
from api.plugins.manifest import ServicePlugin, ServicePluginManifestError, validate_service_plugin
from api.plugins.prometheus.capabilities import load_prometheus_capability_templates
from api.plugins.stackstorm.capabilities import load_stackstorm_capability_templates
from api.plugins.stackstorm.plugin import get_plugin as get_stackstorm_plugin


def _dummy_ingredient() -> dict[str, object]:
    return {
        "service_type": "dummy",
        "service_exec": "run",
        "destination_target": "dummy",
        "task_key_template": "dummy-run",
        "payload_schema": {
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "additionalProperties": False,
        },
        "service_payload_template": {"target": ""},
        "service_exec_parameters": {
            "operation": "run",
            "allowed_operations": ["run"],
            "operation_metadata": {"run": {"label": "Run"}},
        },
        "default_expected_secs": 5,
        "default_timeout": 30,
        "service_exec_expected_outcome_default": {"success": True},
        "ingredient_purpose": "utility",
        "is_blocking": True,
        "retry_count": 0,
        "retry_delay": 0,
        "on_failure": "stop",
    }


def _dummy_health_task() -> dict[str, object]:
    return {
        "task_key": "plugin-health-check:dummy",
        "task_type": "plugin_health_check",
        "service_type": "dummy",
        "service_exec": "health_check",
        "source": "plugin_manifest",
        "is_enabled": True,
        "run_interval_seconds": 60,
        "priority": 20,
        "timeout_seconds": 30,
        "task_payload": {},
        "task_parameters": {
            "operation": "health_check",
            "allowed_operations": ["health_check"],
            "operation_metadata": {"health_check": {"label": "Health check"}},
        },
        "expected_outcome": {"status": "healthy"},
    }


def test_service_plugin_manifest_accepts_capability_templates() -> None:
    plugin = ServicePlugin(
        service_type="stackstorm",
        adapter_factory=get_stackstorm_plugin().adapter_factory,
        ingredient_templates=[
            {
                **_dummy_ingredient(),
                "service_type": "stackstorm",
                "service_exec": "workflow_execution",
                "destination_target": "stackstorm",
                "task_key_template": "stackstorm-workflow-execution",
                "service_exec_parameters": {
                    "operation": "execute_workflow",
                    "allowed_operations": ["execute_workflow"],
                    "operation_metadata": {"execute_workflow": {"label": "Workflow"}},
                },
            }
        ],
        scheduled_tasks=[
            {
                **_dummy_health_task(),
                "task_key": "plugin-health-check:stackstorm",
                "service_type": "stackstorm",
            }
        ],
        capability_templates=[
            {
                "capability_id": "dummy.workflow.example",
                "ingredient_ref": {
                    "service_exec": "workflow_execution",
                    "destination_target": "stackstorm",
                    "task_key_template": "stackstorm-workflow-execution",
                },
                "operation": "execute_workflow",
                "mode": "workflow",
                "resource_kinds": ["deployment"],
                "trigger_match": {"domains": ["kubernetes"], "alert_groups": ["example"]},
                "required_inputs": ["alert_name"],
                "optional_inputs": ["labels"],
                "defaults": {"service_payload": {"workflow_ref": "poundcake.example"}},
                "safety_class": "observe_only",
            }
        ],
    )

    validated = validate_service_plugin(plugin, directory_name="stackstorm")

    assert validated.capability_templates[0]["capability_id"] == "dummy.workflow.example"
    assert validated.capability_templates[0]["service_type"] == "stackstorm"


def test_service_plugin_manifest_rejects_capability_with_unknown_operation() -> None:
    plugin = ServicePlugin(
        service_type="stackstorm",
        adapter_factory=get_stackstorm_plugin().adapter_factory,
        ingredient_templates=[
            {
                **_dummy_ingredient(),
                "service_type": "stackstorm",
                "service_exec": "workflow_execution",
                "destination_target": "stackstorm",
                "task_key_template": "stackstorm-workflow-execution",
                "service_exec_parameters": {
                    "operation": "execute_workflow",
                    "allowed_operations": ["execute_workflow"],
                    "operation_metadata": {"execute_workflow": {"label": "Workflow"}},
                },
            }
        ],
        scheduled_tasks=[
            {
                **_dummy_health_task(),
                "task_key": "plugin-health-check:stackstorm",
                "service_type": "stackstorm",
            }
        ],
        capability_templates=[
            {
                "capability_id": "dummy.workflow.example",
                "ingredient_ref": {
                    "service_exec": "workflow_execution",
                    "destination_target": "stackstorm",
                    "task_key_template": "stackstorm-workflow-execution",
                },
                "operation": "delete_workflow",
                "mode": "workflow",
            }
        ],
    )

    with pytest.raises(ServicePluginManifestError, match="capability_templates\\[0\\] invalid"):
        validate_service_plugin(plugin, directory_name="stackstorm")


def test_stackstorm_capability_templates_surface_only_available_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api.plugins.stackstorm.capabilities.load_stackstorm_action_definitions",
        lambda: [
            {"pack": "poundcake", "name": "host_down_remediation"},
            {"pack": "poundcake", "name": "kubernetes_kube_daemonset_rollout_stuck_remediation"},
        ],
    )
    monkeypatch.setattr(
        "api.plugins.stackstorm.capabilities.load_stackstorm_profile_metadata",
        lambda: {
            "profiles": [
                {
                    "domain": "kubernetes",
                    "workflow_prefix": "poundcake.kubernetes_",
                    "workflow_suffix": "_remediation",
                    "alert_groups": [
                        "kube-daemonset-rollout-stuck",
                        "kube-deployment-rollout-stuck",
                    ],
                }
            ]
        },
    )

    capability_ids = {
        template["capability_id"] for template in load_stackstorm_capability_templates()
    }

    assert "stackstorm.workflow.kubernetes.host_down" in capability_ids
    assert "stackstorm.workflow.kubernetes.kube-daemonset-rollout-stuck" not in capability_ids
    assert "stackstorm.workflow.kubernetes.kube-deployment-rollout-stuck" not in capability_ids


def test_stackstorm_capability_templates_surface_explicit_capability_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api.plugins.stackstorm.capabilities.load_stackstorm_action_definitions",
        lambda: [
            {"pack": "poundcake", "name": "blackbox_service_down_remediation"},
        ],
    )
    monkeypatch.setattr(
        "api.plugins.stackstorm.capabilities.load_stackstorm_profile_metadata",
        lambda: {
            "capabilities": [
                {
                    "capability_id": "stackstorm.workflow.blackbox.blackbox-service-down.remediation.explicit",
                    "workflow_ref": "poundcake.blackbox_service_down_remediation",
                    "domain": "blackbox",
                    "alert_groups": ["blackbox-service-down"],
                    "phase": "remediation",
                    "resource_kinds": ["endpoint"],
                    "required_inputs": ["instance"],
                    "defaults": {"instance": "{{ order.labels.instance }}"},
                    "safety_class": "operator_guidance",
                    "priority": 25,
                }
            ]
        },
    )

    capabilities = load_stackstorm_capability_templates()
    capability = next(
        template
        for template in capabilities
        if template["capability_id"]
        == "stackstorm.workflow.blackbox.blackbox-service-down.remediation.explicit"
    )

    assert capability["defaults"]["service_payload"]["workflow_ref"] == (
        "poundcake.blackbox_service_down_remediation"
    )
    assert capability["priority"] == 25
    assert capability["trigger_match"]["alert_groups"] == ["blackbox-service-down"]


def test_stackstorm_explicit_capability_entries_override_legacy_generated_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api.plugins.stackstorm.capabilities.load_stackstorm_action_definitions",
        lambda: [
            {"pack": "poundcake", "name": "blackbox_service_down_remediation"},
        ],
    )
    monkeypatch.setattr(
        "api.plugins.stackstorm.capabilities.load_stackstorm_profile_metadata",
        lambda: {
            "profiles": [
                {
                    "domain": "blackbox",
                    "workflow_refs": ["poundcake.blackbox_service_down_remediation"],
                    "alert_groups": ["blackbox-service-down"],
                }
            ],
            "capabilities": [
                {
                    "capability_id": "stackstorm.workflow.blackbox.blackbox-service-down.remediation",
                    "workflow_ref": "poundcake.blackbox_service_down_remediation",
                    "domain": "blackbox",
                    "alert_groups": ["blackbox-service-down"],
                    "phase": "remediation",
                    "resource_kinds": ["endpoint"],
                    "required_inputs": ["instance"],
                    "optional_inputs": ["evidence"],
                    "defaults": {"instance": "{{ order.labels.instance }}"},
                    "role": "action_alert",
                    "safety_class": "operator_guidance",
                    "requires_evidence": True,
                    "priority": 350,
                    "expected_secs": 45,
                    "timeout": 360,
                }
            ],
        },
    )

    capabilities = [
        template
        for template in load_stackstorm_capability_templates()
        if template["capability_id"]
        == "stackstorm.workflow.blackbox.blackbox-service-down.remediation"
    ]

    assert len(capabilities) == 1
    capability = capabilities[0]
    assert capability["priority"] == 350
    assert capability["defaults"]["expected_secs"] == 45
    assert capability["defaults"]["timeout"] == 360
    assert capability["defaults"]["service_payload"]["inputs"] == {
        "instance": "{{ order.labels.instance }}"
    }


def test_k8s_capability_templates_surface_bounded_native_mutations() -> None:
    capabilities = {
        template["capability_id"]: template for template in load_k8s_capability_templates()
    }

    assert (
        capabilities["k8s.remediation.kubernetes.kube-pod-crash-looping"]["operation"] == "delete"
    )
    assert (
        capabilities["k8s.remediation.kubernetes.kube-deployment-rollout-stuck"]["operation"]
        == "rollout_restart"
    )
    assert (
        capabilities["k8s.remediation.kubernetes.kube-daemonset-rollout-stuck"]["ingredient_ref"][
            "service_exec"
        ]
        == "workload_action"
    )
    assert capabilities["k8s.remediation.kubernetes.failed-job-cleanup"]["operation"] == "delete"
    assert (
        capabilities["k8s.remediation.kubernetes.scale-deployment"]["operation"]
        == "scale_deployment"
    )
    assert (
        capabilities["k8s.remediation.kubernetes.patch-hpa-bounds"]["operation"]
        == "patch_hpa_bounds"
    )


def test_alertmanager_capability_templates_surface_guard_and_evidence() -> None:
    capabilities = {
        template["capability_id"]: template for template in load_alertmanager_capability_templates()
    }

    assert (
        capabilities["alertmanager.inspect.verify-firing.before-evidence"]["operation"]
        == "verify_firing"
    )
    assert (
        capabilities["alertmanager.inspect.verify-firing.before-action"]["defaults"]["role"]
        == "verify_before_action"
    )
    assert (
        capabilities["alertmanager.inspect.active-alerts.evidence"]["defaults"][
            "service_exec_parameters"
        ]["managed_role"]
        == "gather_alertmanager_evidence"
    )


def test_prometheus_capability_templates_surface_alert_evidence_and_reload() -> None:
    capabilities = {
        template["capability_id"]: template for template in load_prometheus_capability_templates()
    }

    capability = capabilities["prometheus.inspect.alert-evidence.generic"]
    assert capability["operation"] == "alert_evidence"
    assert capability["defaults"]["service_exec_parameters"]["managed_role"] == (
        "gather_prometheus_evidence"
    )
    assert capability["defaults"]["service_payload"]["lookback_seconds"] == 3600
    assert capabilities["prometheus.reload.rule-state"]["operation"] == "reload_config"


def test_github_capability_templates_surface_source_evidence_and_write() -> None:
    capabilities = {
        template["capability_id"]: template for template in load_github_capability_templates()
    }

    assert capabilities["github.repo.read.genestack-source-rule"]["operation"] == "read_file"
    assert (
        capabilities["github.repo.read.genestack-source-rule"]["defaults"][
            "service_exec_parameters"
        ]["managed_role"]
        == "gather_source_rule_evidence"
    )
    assert capabilities["github.repo.write.commit-and-pr"]["operation"] == "commit_and_pr"


def test_git_capability_templates_surface_read_and_write_operations() -> None:
    capabilities = {
        template["capability_id"]: template for template in load_git_capability_templates()
    }

    assert capabilities["git.repo.read.file"]["operation"] == "read_file"
    assert capabilities["git.repo.read.list-files"]["operation"] == "list_files"
    assert capabilities["git.repo.write.commit-files"]["operation"] == "commit_files"
    assert capabilities["git.repo.write.commit-and-pr"]["operation"] == "commit_and_pr"


def test_bakery_capability_templates_surface_default_communication() -> None:
    capabilities = {
        template["capability_id"]: template for template in load_bakery_capability_templates()
    }

    capability = capabilities["bakery.communication.open.default"]
    assert capability["mode"] == "communication"
    assert capability["operation"] == "open"
    assert capability["ingredient_ref"]["service_exec"] == "communication"
    assert capability["defaults"]["role"] == "communicate"


def test_dummy_capability_templates_surface_default_communication() -> None:
    capabilities = {
        template["capability_id"]: template for template in load_dummy_capability_templates()
    }

    capability = capabilities["dummy.communication.open.default"]
    assert capability["mode"] == "communication"
    assert capability["operation"] == "open"
    assert capability["ingredient_ref"]["service_exec"] == "communication"
    assert capability["defaults"]["role"] == "communicate"


def test_provider_ownership_matrix_matches_expected_policy() -> None:
    assert set(PLUGIN_CAPABILITY_OWNERSHIP_MATRIX) == {
        "alertmanager",
        "bakery",
        "dummy",
        "git",
        "github",
        "k8s",
        "prometheus",
        "stackstorm",
    }
    assert PLUGIN_CAPABILITY_OWNERSHIP_MATRIX["k8s"]["category"] == "bounded_native_mutation"
    assert PLUGIN_CAPABILITY_OWNERSHIP_MATRIX["stackstorm"]["category"] == "workflow_orchestration"
    assert PLUGIN_CAPABILITY_OWNERSHIP_MATRIX["bakery"]["category"] == "communication"
    assert PLUGIN_CAPABILITY_OWNERSHIP_MATRIX["dummy"]["category"] == "communication"
    assert (
        alert_group_provider_policy(domain="kubernetes", alert_group="kube-pod-crash-looping")
        == "k8s"
    )
    assert (
        alert_group_provider_policy(domain="blackbox", alert_group="blackbox-service-down")
        == "stackstorm"
    )
    assert (
        alert_group_provider_policy(domain="kubernetes", alert_group="kube-node-not-ready")
        == "operator_guidance_only"
    )
    assert NATIVE_K8S_PREFERRED_ALERT_GROUPS
    assert OPERATOR_GUIDANCE_ONLY_ALERT_GROUPS


def test_capability_catalog_applies_operator_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POUNDCAKE_ENABLED_PLUGINS", "stackstorm")
    monkeypatch.setattr(
        "api.plugins.stackstorm.plugin.load_stackstorm_capability_templates",
        lambda: (
            {
                "capability_id": "stackstorm.workflow.kubernetes.kube-daemonset-rollout-stuck",
                "ingredient_ref": {
                    "service_exec": "workflow_execution",
                    "destination_target": "stackstorm",
                    "task_key_template": "stackstorm-workflow-execution",
                },
                "operation": "execute_workflow",
                "mode": "workflow",
                "resource_kinds": ["daemonset"],
                "trigger_match": {
                    "domains": ["kubernetes"],
                    "alert_groups": ["kube-daemonset-rollout-stuck"],
                    "phase": "remediation",
                },
                "defaults": {
                    "service_payload": {
                        "workflow_ref": "poundcake.kubernetes_kube_daemonset_rollout_stuck_remediation"
                    }
                },
                "safety_class": "safe_restart",
            },
        ),
    )

    catalog = build_enabled_plugin_capability_catalog(
        {
            "stackstorm": {
                "capabilities_enabled": {
                    "stackstorm.workflow.kubernetes.kube-daemonset-rollout-stuck": False,
                },
                "capability_overrides": {
                    "stackstorm.workflow.kubernetes.kube-daemonset-rollout-stuck": {
                        "workflow_ref": "poundcake.custom_daemonset_restart",
                        "priority": 50,
                    }
                },
            }
        }
    )

    capability = catalog[0]

    assert capability["enabled"] is False
    assert capability["disabled_reason"] == "operator_disabled"
    assert capability["priority"] == 50
    assert (
        capability["defaults"]["service_payload"]["workflow_ref"]
        == "poundcake.custom_daemonset_restart"
    )


def test_capability_catalog_can_disable_default_bakery_communication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POUNDCAKE_ENABLED_PLUGINS", "bakery")
    monkeypatch.setattr(
        "api.plugins.bakery.plugin.load_bakery_capability_templates",
        lambda: (
            {
                "capability_id": "bakery.communication.open.default",
                "ingredient_ref": {
                    "service_exec": "communication",
                    "destination_target": "rackspace_core",
                    "task_key_template": "bakery-comms",
                },
                "operation": "open",
                "mode": "communication",
                "defaults": {
                    "service_payload": {},
                    "service_exec_parameters": {"operation": "open"},
                },
                "safety_class": "operator_guidance",
                "trigger_match": {"phase": "communicate"},
                "priority": 200,
            },
        ),
    )

    catalog = build_enabled_plugin_capability_catalog(
        {
            "bakery": {
                "capabilities_enabled": {
                    "bakery.communication.open.default": False,
                }
            }
        }
    )

    assert [item["service_type"] for item in catalog] == ["bakery"]
    bakery = catalog[0]

    assert bakery["enabled"] is False
    assert bakery["disabled_reason"] == "operator_disabled"


def test_capability_catalog_ignores_forbidden_override_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POUNDCAKE_ENABLED_PLUGINS", "bakery")
    catalog = build_enabled_plugin_capability_catalog(
        {
            "bakery": {
                "capability_overrides": {
                    "bakery.communication.open.default": {
                        "ingredient_ref": {
                            "service_exec": "run-away",
                            "destination_target": "wrong",
                            "task_key_template": "wrong",
                        },
                        "mode": "workflow",
                        "operation": "delete",
                        "priority": 250,
                    }
                }
            }
        }
    )

    capability = next(
        c for c in catalog if c["capability_id"] == "bakery.communication.open.default"
    )

    assert capability["priority"] == 250
    assert capability["ingredient_ref"]["service_exec"] == "communication"
    assert capability["ingredient_ref"]["task_key_template"] == "bakery-comms"
    assert capability["mode"] == "communication"
    assert capability["operation"] == "open"


def test_enabled_plugin_communication_routes_are_derived_from_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POUNDCAKE_ENABLED_PLUGINS", "bakery")

    routes = get_enabled_plugin_communication_routes()

    assert [route["service_type"] for route in routes] == ["bakery"]
    assert routes[0]["id"] == "bakery.communication.open.default"
