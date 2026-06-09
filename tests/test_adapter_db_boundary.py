from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from api.plugins.genestack_monitoring.adapter import GenestackMonitoringExecutionAdapter
from api.plugins.genestack_monitoring.content_sync import (
    AlertExportPrepareResult,
    ContentSyncPrepareResult,
    RecipePayload,
    sync_genestack_monitoring_content_prepare,
)
from api.plugins.github.client import GitHubClient
from api.plugins.k8s.helper import KubernetesHelper
from api.plugins.prometheus.helper import PrometheusAlertRuleHelper
from api.plugins.types import ExecutionContext
from api.services import adapter_runtime
from api.services.plugin_operations import RecipeStepPayload
from api.services.plugin_operations import UpsertStats


class _FakeGenestackGitHubHelper:
    def __init__(self, *, token: str = "", allow_public_read: bool = False) -> None:
        self.token = token
        self.allow_public_read = allow_public_read

    def with_credentials(self, payload: dict[str, object] | None) -> "_FakeGenestackGitHubHelper":
        return _FakeGenestackGitHubHelper(
            token=str((payload or {}).get("token") or ""),
            allow_public_read=self.allow_public_read,
        )

    async def list_files(self, **_kwargs: object) -> dict[str, object]:
        return {"files": []}

    async def read_file(self, **_kwargs: object) -> dict[str, object]:
        return {"content": ""}

    async def commit_and_pr(self, **_kwargs: object) -> dict[str, object]:
        return {"success": True, "pull_request": {"number": 1}}


class _FakeGenestackK8sHelper:
    async def create_or_update_rule(self, **_kwargs: object) -> dict[str, object]:
        return {"success": True, "status": "succeeded"}

    async def list_prometheus_rules(self) -> list[dict[str, object]]:
        return []


class _FakeGenestackPrometheusHelper:
    def parse_rules_from_content(self, content: str, *, path: str) -> list[dict[str, object]]:
        _ = path
        if "kube-pod-crash-looping-critical" not in content:
            return []
        return [
            {
                "alert": "kube-pod-crash-looping-critical",
                "group": "poundcake-e2e",
                "path": "alerts/kubernetes/pods.yaml",
                "rule": {
                    "alert": "kube-pod-crash-looping-critical",
                    "expr": "vector(1)",
                    "labels": {"severity": "critical"},
                    "annotations": {"summary": "Pod crash looping"},
                },
                "source_format": "yaml",
                "wrapper_key": "groups",
            }
        ]

    def render_document(
        self,
        records_for_file: list[tuple[str, dict[str, object], object]],
        *,
        relative_path: str,
    ) -> dict[str, object]:
        return {"relative_path": relative_path, "rules": records_for_file}

    def dump_document(self, document: dict[str, object], *, relative_path: str) -> str:
        return f"# {relative_path}\n{document!r}"


@pytest.mark.asyncio
async def test_dispose_adapter_runtime_resources_uses_service_layer_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_dispose() -> None:
        calls.append("disposed")

    monkeypatch.setattr(adapter_runtime, "dispose_async_engines", fake_dispose)

    await adapter_runtime.dispose_adapter_runtime_resources()

    assert calls == ["disposed"]


@pytest.mark.asyncio
async def test_genestack_dispatch_routes_db_writes_through_plugin_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = ContentSyncPrepareResult(
        recipes=[
            RecipePayload(
                name="DemoAlert",
                description="managed",
                enabled=True,
                clear_timeout_sec=None,
                managed_by="poundcake-genestack-monitoring",
                steps=[
                    RecipeStepPayload(
                        service_type="k8s",
                        service_exec="pod_action",
                        task_key_template="k8s-pod-action-v1",
                        step_order=10,
                        service_payload={"name": "demo-pod"},
                        service_exec_parameters_override={
                            "operation": "delete",
                            "managed_role": "action_alert",
                            "managed_index": 1,
                        },
                        expected_secs=5,
                        timeout=30,
                        expected_outcome={"success": True},
                        run_phase="firing",
                        run_condition="always",
                    )
                ],
            )
        ],
        crds_applied=1,
        warning_recipes_skipped=0,
        warning_recipes_disabled=0,
        warning_recipes_preserved_nonmanaged=0,
        recipes_published=1,
        recipes_degraded_to_review=0,
        recipes_skipped_missing_capability=0,
        recipes_skipped_missing_ingredient=0,
        recipe_outcomes={"DemoAlert": "published_managed_recipe"},
        remediation_profiles_applied=1,
        remediation_profiles_skipped_missing_ingredients=0,
        processed=1,
    )
    captured: dict[str, object] = {}

    async def fake_prepare(
        _helpers: object,
        *,
        capabilities: list[dict[str, object]] | None = None,
    ) -> ContentSyncPrepareResult:
        assert capabilities is not None
        assert any(
            item.get("capability_id") == "dummy.communication.open.default"
            for item in capabilities
        )
        return prepared

    async def fake_upsert_recipes(**kwargs: object) -> UpsertStats:
        captured.update(kwargs)
        return UpsertStats(created=1, updated=0, deleted=0, skipped=0)

    async def fake_validate(_helpers: object, _params: dict) -> None:
        return None

    async def fake_plugin_configs(*, requester_service_type: str) -> dict[str, dict[str, object]]:
        assert requester_service_type == "genestack_monitoring"
        return {}

    async def fake_recipe_states(**_kwargs: object) -> dict[str, object]:
        return {}

    async def fake_get_ingredient(**_kwargs: object) -> dict[str, object]:
        return {"id": 1}

    monkeypatch.setattr(
        "api.plugins.genestack_monitoring.adapter.sync_genestack_monitoring_content_prepare",
        fake_prepare,
    )
    monkeypatch.setattr(
        "api.plugins.genestack_monitoring.adapter.upsert_recipes",
        fake_upsert_recipes,
    )
    monkeypatch.setattr(
        "api.plugins.genestack_monitoring.adapter._validate_all_ingredients",
        fake_validate,
    )
    monkeypatch.setattr(
        "api.plugins.genestack_monitoring.adapter.list_service_plugin_configs",
        fake_plugin_configs,
    )
    monkeypatch.setattr(
        "api.plugins.genestack_monitoring.adapter.list_recipe_management_states",
        fake_recipe_states,
    )
    monkeypatch.setattr(
        "api.plugins.genestack_monitoring.adapter.get_ingredient",
        fake_get_ingredient,
    )
    reload_calls: list[dict[str, object]] = []

    async def fake_reload_prometheus_rules(**kwargs: object):
        reload_calls.append(dict(kwargs))

        class _Result:
            status = "succeeded"
            service_exec_error = None

        return _Result()

    monkeypatch.setattr(
        "api.plugins.genestack_monitoring.adapter.reload_prometheus_rules",
        fake_reload_prometheus_rules,
    )

    adapter = GenestackMonitoringExecutionAdapter(
        helper_factory=lambda: {
            "github": _FakeGenestackGitHubHelper(),
            "k8s": _FakeGenestackK8sHelper(),
            "prometheus": _FakeGenestackPrometheusHelper(),
        }
    )
    ctx = ExecutionContext(
        service_type="genestack_monitoring",
        service_exec="content_sync",
        req_id="unit-test",
        service_payload={},
        service_exec_parameters={"operation": "sync_content"},
    )

    result = await adapter.dispatch(ctx)

    assert result.status == "succeeded"
    assert captured["requester_service_type"] == "genestack_monitoring"
    assert captured["recipes"] == prepared.recipes
    assert result.result["created"] == 1
    assert result.result["crds_applied"] == 1
    assert result.result["recipes_published"] == 1
    assert len(reload_calls) == 1
    assert reload_calls[0]["req_id"] == "unit-test"
    assert reload_calls[0]["operator_config"] is None
    assert reload_calls[0]["orchestrator"] is not None


def test_stackstorm_devstack_helper_uses_service_layer_boundaries() -> None:
    content = (
        Path(__file__).resolve().parents[1] / "helm/devstack/configure-stackstorm-adapter.sh"
    ).read_text()

    assert "from api.core.database import" not in content
    assert "dispose_adapter_runtime_resources" in content
    assert "write_adapter_credential" in content
    assert "update_service_plugin_state" in content


def test_bakery_devstack_helper_uses_service_layer_boundaries() -> None:
    content = (
        Path(__file__).resolve().parents[1] / "helm/devstack/configure-bakery-adapter.sh"
    ).read_text()

    assert "from api.core.database import" not in content
    assert "dispose_adapter_runtime_resources" in content
    assert "write_adapter_credential" in content
    assert "update_service_plugin_state" in content


@pytest.mark.asyncio
async def test_genestack_dispatch_hydrates_github_helper_with_credential_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = ContentSyncPrepareResult(
        recipes=[],
        crds_applied=0,
        warning_recipes_skipped=0,
        warning_recipes_disabled=0,
        warning_recipes_preserved_nonmanaged=0,
        recipes_published=0,
        recipes_degraded_to_review=0,
        recipes_skipped_missing_capability=0,
        recipes_skipped_missing_ingredient=0,
        recipe_outcomes={},
        remediation_profiles_applied=0,
        remediation_profiles_skipped_missing_ingredients=0,
        processed=0,
    )
    captured: dict[str, object] = {}

    async def fake_prepare(
        helpers: object,
        *,
        capabilities: list[dict[str, object]] | None = None,
    ) -> ContentSyncPrepareResult:
        captured["github_helper"] = helpers["github"]
        assert capabilities is not None
        assert any(
            item.get("capability_id") == "dummy.communication.open.default"
            for item in capabilities
        )
        return prepared

    async def fake_upsert_recipes(**_kwargs: object) -> UpsertStats:
        return UpsertStats(created=0, updated=0, deleted=0, skipped=0)

    async def fake_validate(_helpers: object, _params: dict) -> None:
        return None

    async def fake_plugin_configs(*, requester_service_type: str) -> dict[str, dict[str, object]]:
        assert requester_service_type == "genestack_monitoring"
        return {}

    async def fake_recipe_states(**_kwargs: object) -> dict[str, object]:
        return {}

    async def fake_get_ingredient(**_kwargs: object) -> dict[str, object]:
        return {"id": 1}

    async def fake_read_credential_with_policy(**_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(payload={"token": ""}, allow_public_read=True)

    monkeypatch.setattr(
        "api.plugins.genestack_monitoring.adapter.sync_genestack_monitoring_content_prepare",
        fake_prepare,
    )
    monkeypatch.setattr(
        "api.plugins.genestack_monitoring.adapter.upsert_recipes",
        fake_upsert_recipes,
    )
    monkeypatch.setattr(
        "api.plugins.genestack_monitoring.adapter._validate_all_ingredients",
        fake_validate,
    )
    monkeypatch.setattr(
        "api.plugins.genestack_monitoring.adapter.list_service_plugin_configs",
        fake_plugin_configs,
    )
    monkeypatch.setattr(
        "api.plugins.genestack_monitoring.adapter.list_recipe_management_states",
        fake_recipe_states,
    )
    monkeypatch.setattr(
        "api.plugins.genestack_monitoring.adapter.get_ingredient",
        fake_get_ingredient,
    )
    monkeypatch.setattr(
        "api.plugins.genestack_monitoring.adapter.read_adapter_credential_with_policy",
        fake_read_credential_with_policy,
    )
    async def fake_reload_prometheus_rules(**_kwargs: object):
        class _Result:
            status = "succeeded"
            service_exec_error = None

        return _Result()

    monkeypatch.setattr(
        "api.plugins.genestack_monitoring.adapter.reload_prometheus_rules",
        fake_reload_prometheus_rules,
    )

    adapter = GenestackMonitoringExecutionAdapter(
        helper_factory=lambda: {
            "github": GitHubClient(default_repo="rackerchris/genestack-monitoring"),
            "k8s": object(),
            "prometheus": object(),
        }
    )
    ctx = ExecutionContext(
        service_type="genestack_monitoring",
        service_exec="content_sync",
        req_id="unit-test",
        service_payload={},
        service_exec_parameters={"operation": "sync_content"},
    )

    result = await adapter.dispatch(ctx)

    assert result.status == "succeeded"
    helper = captured["github_helper"]
    assert isinstance(helper, GitHubClient)
    assert helper.allow_public_read is True


@pytest.mark.asyncio
async def test_genestack_dispatch_reports_missing_ingredient_honestly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = ContentSyncPrepareResult(
        recipes=[
            RecipePayload(
                name="DemoAlert",
                description="managed",
                enabled=True,
                clear_timeout_sec=None,
                managed_by="poundcake-genestack-monitoring",
                steps=[
                    RecipeStepPayload(
                        service_type="k8s",
                        service_exec="pod_action",
                        task_key_template="k8s-pod-action-v1",
                        step_order=10,
                        service_payload={"name": "demo-pod"},
                        service_exec_parameters_override={
                            "operation": "delete",
                            "managed_role": "action_alert",
                            "managed_index": 1,
                        },
                        expected_secs=5,
                        timeout=30,
                        expected_outcome={"success": True},
                        run_phase="firing",
                        run_condition="always",
                    )
                ],
            )
        ],
        crds_applied=1,
        warning_recipes_skipped=0,
        warning_recipes_disabled=0,
        warning_recipes_preserved_nonmanaged=0,
        recipes_published=1,
        recipes_degraded_to_review=0,
        recipes_skipped_missing_capability=0,
        recipes_skipped_missing_ingredient=0,
        recipe_outcomes={"DemoAlert": "published_managed_recipe"},
        remediation_profiles_applied=1,
        remediation_profiles_skipped_missing_ingredients=0,
        processed=1,
    )

    async def fake_prepare(
        _helpers: object,
        *,
        capabilities: list[dict[str, object]] | None = None,
    ) -> ContentSyncPrepareResult:
        assert capabilities is not None
        return prepared

    async def fake_upsert_recipes(**kwargs: object) -> UpsertStats:
        assert kwargs["recipes"] == []
        return UpsertStats(created=0, updated=0, deleted=0, skipped=0)

    async def fake_validate(_helpers: object, _params: dict) -> None:
        return None

    async def fake_plugin_configs(*, requester_service_type: str) -> dict[str, dict[str, object]]:
        assert requester_service_type == "genestack_monitoring"
        return {}

    async def fake_recipe_states(**_kwargs: object) -> dict[str, object]:
        return {}

    async def fake_get_ingredient(**_kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        "api.plugins.genestack_monitoring.adapter.sync_genestack_monitoring_content_prepare",
        fake_prepare,
    )
    monkeypatch.setattr(
        "api.plugins.genestack_monitoring.adapter.upsert_recipes",
        fake_upsert_recipes,
    )
    monkeypatch.setattr(
        "api.plugins.genestack_monitoring.adapter._validate_all_ingredients",
        fake_validate,
    )
    monkeypatch.setattr(
        "api.plugins.genestack_monitoring.adapter.list_service_plugin_configs",
        fake_plugin_configs,
    )
    monkeypatch.setattr(
        "api.plugins.genestack_monitoring.adapter.list_recipe_management_states",
        fake_recipe_states,
    )
    monkeypatch.setattr(
        "api.plugins.genestack_monitoring.adapter.get_ingredient",
        fake_get_ingredient,
    )
    async def fake_reload_prometheus_rules(**_kwargs: object):
        class _Result:
            status = "succeeded"
            service_exec_error = None

        return _Result()

    monkeypatch.setattr(
        "api.plugins.genestack_monitoring.adapter.reload_prometheus_rules",
        fake_reload_prometheus_rules,
    )

    adapter = GenestackMonitoringExecutionAdapter(
        helper_factory=lambda: {
            "github": _FakeGenestackGitHubHelper(),
            "k8s": _FakeGenestackK8sHelper(),
            "prometheus": _FakeGenestackPrometheusHelper(),
        }
    )
    ctx = ExecutionContext(
        service_type="genestack_monitoring",
        service_exec="content_sync",
        req_id="unit-test",
        service_payload={},
        service_exec_parameters={"operation": "sync_content"},
    )

    result = await adapter.dispatch(ctx)

    assert result.status == "succeeded"
    assert result.result["recipes_skipped_missing_ingredient"] == 1
    assert result.result["recipe_outcomes"]["DemoAlert"] == "skipped_missing_ingredient"


@pytest.mark.asyncio
async def test_genestack_repo_sync_delegates_export_to_github_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = AlertExportPrepareResult(
        repo="rackerchris/genestack-monitoring",
        base_branch="main",
        branch="poundcake/genestack-alert-update-demoalert",
        files={"alerts/demo.yaml": "spec:\n  groups: []\n"},
        message="Prepared Genestack alert update.",
        skipped={"missing_source_metadata": 0, "non_genestack_rules": 0},
        warnings=[],
        selected_rule="DemoAlert",
    )
    captured: dict[str, object] = {}

    async def fake_prepare(
        _helpers: object,
        *,
        crd_name: str,
        group_name: str,
        rule_name: str,
        namespace: str = "",
    ) -> AlertExportPrepareResult:
        captured["target"] = (crd_name, group_name, rule_name, namespace)
        return prepared

    async def fake_read_credential_with_policy(**_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(payload={"token": "secret"}, allow_public_read=False)

    class _WritableGitHubClient(GitHubClient):
        def with_credentials(self, payload: dict[str, object] | None) -> "_WritableGitHubClient":
            helper = _WritableGitHubClient(
                token=str((payload or {}).get("token") or ""),
                default_repo=self.default_repo,
                default_branch=self.default_branch,
            )
            helper.allow_public_read = self.allow_public_read
            return helper

        async def commit_and_pr(
            self,
            *,
            repo: str | None = None,
            base_branch: str | None = None,
            branch: str,
            files: dict[str, str],
            commit_message: str,
            title: str,
            body: str = "",
        ) -> dict[str, object]:
            captured["repo"] = repo
            captured["base_branch"] = base_branch
            captured["branch"] = branch
            captured["files"] = files
            captured["commit_message"] = commit_message
            captured["title"] = title
            captured["body"] = body
            return {
                "success": True,
                "pull_request": {"number": 44, "url": "https://example.test/pr/44"},
            }

    monkeypatch.setattr(
        "api.plugins.genestack_monitoring.adapter.export_genestack_alert_updates_prepare",
        fake_prepare,
    )
    monkeypatch.setattr(
        "api.plugins.genestack_monitoring.adapter.read_adapter_credential_with_policy",
        fake_read_credential_with_policy,
    )

    adapter = GenestackMonitoringExecutionAdapter(
        helper_factory=lambda: {
            "github": _WritableGitHubClient(default_repo="rackerchris/genestack-monitoring"),
            "k8s": object(),
            "prometheus": object(),
        }
    )
    ctx = ExecutionContext(
        service_type="genestack_monitoring",
        service_exec="repo_sync",
        req_id="unit-test",
        service_payload={
            "namespace": "monitoring",
            "crd_name": "demo-rules",
            "group_name": "demo",
            "rule_name": "DemoAlert",
        },
        service_exec_parameters={"operation": "export_alert_updates"},
    )

    result = await adapter.dispatch(ctx)

    assert result.status == "succeeded"
    assert captured["target"] == ("demo-rules", "demo", "DemoAlert", "monitoring")
    assert captured["repo"] == "rackerchris/genestack-monitoring"
    assert result.result["pull_request"]["number"] == 44


def _k8s_pod_delete_capability() -> dict[str, object]:
    return {
        "capability_id": "k8s.remediation.kubernetes.kube-pod-crash-looping",
        "service_type": "k8s",
        "ingredient_ref": {
            "service_exec": "pod_action",
            "task_key_template": "k8s-pod-action",
            "destination_target": "kubernetes",
        },
        "operation": "delete",
        "mode": "action",
        "trigger_match": {
            "domains": ["kubernetes"],
            "alert_groups": ["kube-pod-crash-looping"],
            "phase": "remediation",
        },
        "defaults": {
            "service_payload": {
                "namespace": "{{ order.labels.namespace }}",
                "pod_name": "{{ order.labels.pod }}",
            },
            "service_exec_parameters": {
                "operation": "delete",
                "mutation_family": "pod_delete",
                "require_controller_owned": True,
            },
            "expected_outcome": {"success": True},
            "expected_secs": 10,
            "timeout": 120,
            "role": "action_alert",
        },
        "safety_class": "safe_restart",
        "enabled": True,
        "priority": 200,
    }


def _bakery_communication_capability() -> dict[str, object]:
    return {
        "capability_id": "bakery.communication.open.default",
        "service_type": "bakery",
        "ingredient_ref": {
            "service_exec": "communication",
            "task_key_template": "bakery-comms",
            "destination_target": "rackspace_core",
        },
        "operation": "open",
        "mode": "communication",
        "trigger_match": {
            "phase": "communicate",
        },
        "defaults": {
            "service_payload": {},
            "service_exec_parameters": {
                "operation": "open",
            },
            "expected_outcome": {"success": True},
            "expected_secs": 5,
            "timeout": 120,
            "role": "communicate",
        },
        "safety_class": "operator_guidance",
        "enabled": True,
        "priority": 200,
    }


def _blackbox_remediation_capability() -> dict[str, object]:
    return {
        "capability_id": "stackstorm.workflow.blackbox.blackbox-service-down.remediation",
        "service_type": "stackstorm",
        "ingredient_ref": {
            "service_exec": "workflow_execution",
            "task_key_template": "stackstorm-workflow-execution",
            "destination_target": "stackstorm",
        },
        "operation": "execute_workflow",
        "mode": "workflow",
        "trigger_match": {
            "domains": ["blackbox"],
            "alert_groups": ["blackbox-service-down"],
            "phase": "remediation",
        },
        "defaults": {
            "service_payload": {
                "workflow_ref": "poundcake.blackbox_service_down_remediation",
                "inputs": {"instance": "{{ order.labels.instance }}"},
            },
            "service_exec_parameters": {"operation": "execute_workflow"},
            "expected_outcome": {"status": "succeeded"},
            "expected_secs": 30,
            "timeout": 300,
            "role": "action_alert",
        },
        "safety_class": "operator_guidance",
        "enabled": True,
        "priority": 100,
    }


@pytest.mark.asyncio
async def test_genestack_content_sync_builds_native_k8s_recipe_from_capability_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    github = GitHubClient(default_repo="rackerchris/genestack-monitoring")
    k8s = KubernetesHelper()
    prometheus = PrometheusAlertRuleHelper()

    async def fake_list_files(**_kwargs: object) -> dict[str, object]:
        return {"files": [{"path": "alerts/kubernetes/pods.yaml"}]}

    async def fake_read_file(**_kwargs: object) -> dict[str, object]:
        return {
            "content": """
groups:
  - name: poundcake-e2e
    rules:
      - alert: kube-pod-crash-looping-critical
        expr: vector(1)
        labels:
          severity: critical
        annotations:
          summary: Pod crash looping
""".strip()
        }

    async def fake_create_or_update_rule(**_kwargs: object) -> dict[str, object]:
        return {"success": True, "status": "succeeded"}

    monkeypatch.setattr(github, "list_files", fake_list_files)
    monkeypatch.setattr(github, "read_file", fake_read_file)
    monkeypatch.setattr(k8s, "create_or_update_rule", fake_create_or_update_rule)

    result = await sync_genestack_monitoring_content_prepare(
        {"github": github, "k8s": k8s, "prometheus": prometheus},
        capabilities=[_k8s_pod_delete_capability(), _bakery_communication_capability()],
    )

    assert result.remediation_profiles_applied == 1
    recipe = result.recipes[0]
    assert recipe.name == "kube-pod-crash-looping-critical"
    assert recipe.steps[-1].service_type == "bakery"
    action_step = next(step for step in recipe.steps if step.service_exec == "pod_action")
    assert action_step.service_type == "k8s"
    assert action_step.service_exec_parameters_override["operation"] == "delete"


@pytest.mark.asyncio
async def test_genestack_content_sync_builds_stackstorm_recipe_from_capability_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    github = GitHubClient(default_repo="rackerchris/genestack-monitoring")
    k8s = KubernetesHelper()
    prometheus = PrometheusAlertRuleHelper()

    async def fake_list_files(**_kwargs: object) -> dict[str, object]:
        return {"files": [{"path": "alerts/blackbox/http.yaml"}]}

    async def fake_read_file(**_kwargs: object) -> dict[str, object]:
        return {
            "content": """
groups:
  - name: poundcake-e2e
    rules:
      - alert: blackbox-service-down-critical
        expr: vector(1)
        labels:
          severity: critical
        annotations:
          summary: Service down
""".strip()
        }

    async def fake_create_or_update_rule(**_kwargs: object) -> dict[str, object]:
        return {"success": True, "status": "succeeded"}

    monkeypatch.setattr(github, "list_files", fake_list_files)
    monkeypatch.setattr(github, "read_file", fake_read_file)
    monkeypatch.setattr(k8s, "create_or_update_rule", fake_create_or_update_rule)

    result = await sync_genestack_monitoring_content_prepare(
        {"github": github, "k8s": k8s, "prometheus": prometheus},
        capabilities=[_blackbox_remediation_capability(), _bakery_communication_capability()],
    )

    assert result.remediation_profiles_applied == 1
    recipe = result.recipes[0]
    assert recipe.name == "blackbox-service-down-critical"
    assert recipe.steps[-1].service_type == "bakery"
    action_step = next(
        step for step in recipe.steps if step.service_exec == "workflow_execution"
    )
    assert action_step.service_type == "stackstorm"
    assert action_step.service_payload["workflow_ref"] == "poundcake.blackbox_service_down_remediation"


@pytest.mark.asyncio
async def test_genestack_content_sync_accepts_capability_compatible_helpers() -> None:
    github = _FakeGenestackGitHubHelper()
    k8s = _FakeGenestackK8sHelper()
    prometheus = _FakeGenestackPrometheusHelper()

    async def fake_list_files(**_kwargs: object) -> dict[str, object]:
        return {"files": [{"path": "alerts/kubernetes/pods.yaml"}]}

    async def fake_read_file(**_kwargs: object) -> dict[str, object]:
        return {
            "content": """
groups:
  - name: poundcake-e2e
    rules:
      - alert: kube-pod-crash-looping-critical
        expr: vector(1)
        labels:
          severity: critical
        annotations:
          summary: Pod crash looping
""".strip()
        }

    github.list_files = fake_list_files  # type: ignore[method-assign]
    github.read_file = fake_read_file  # type: ignore[method-assign]

    result = await sync_genestack_monitoring_content_prepare(
        {"github": github, "k8s": k8s, "prometheus": prometheus},
        capabilities=[_k8s_pod_delete_capability(), _bakery_communication_capability()],
    )

    assert result.remediation_profiles_applied == 1
    assert result.recipes[0].name == "kube-pod-crash-looping-critical"


def test_genestack_default_helpers_resolve_from_enabled_plugin_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    github = _FakeGenestackGitHubHelper()
    k8s = _FakeGenestackK8sHelper()
    prometheus = _FakeGenestackPrometheusHelper()

    monkeypatch.setattr(
        "api.plugins.genestack_monitoring.helper_contracts.get_enabled_plugin_helpers",
        lambda: {"github": github, "k8s": k8s, "prometheus": prometheus},
    )
    monkeypatch.setattr(
        "api.plugins.genestack_monitoring.helper_contracts.get_enabled_plugin_helper_capabilities",
        lambda: {
            "github": ["pull_request.create", "repo.list", "repo.read", "repo.write"],
            "k8s": ["k8s.prometheusrules.manage"],
            "prometheus": ["alert_rules.parse", "alert_rules.render"],
        },
    )

    adapter = GenestackMonitoringExecutionAdapter()

    resolved = adapter._helper_factory()

    assert resolved == {"github": github, "k8s": k8s, "prometheus": prometheus}
