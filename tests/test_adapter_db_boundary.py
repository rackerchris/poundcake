from __future__ import annotations

from pathlib import Path

import pytest

from api.plugins.genestack_monitoring.adapter import GenestackMonitoringExecutionAdapter
from api.plugins.genestack_monitoring.content_sync import ContentSyncPrepareResult, RecipePayload
from api.plugins.types import ExecutionContext
from api.services import adapter_runtime


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
                steps=[],
            )
        ],
        crds_applied=1,
        warning_recipes_skipped=0,
        warning_recipes_disabled=0,
        warning_recipes_preserved_nonmanaged=0,
        remediation_profiles_applied=1,
        remediation_profiles_skipped_missing_ingredients=0,
        processed=1,
    )
    captured: dict[str, object] = {}

    async def fake_prepare(_helpers: object) -> ContentSyncPrepareResult:
        return prepared

    async def fake_upsert_recipes(**kwargs: object) -> dict[str, int]:
        captured.update(kwargs)
        return {"created": 1, "updated": 0, "deleted": 0, "skipped": 0}

    async def fake_validate(_helpers: object, _params: dict) -> None:
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

    adapter = GenestackMonitoringExecutionAdapter(
        helper_factory=lambda: {
            "github": object(),
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
    assert captured["requester_service_type"] == "genestack_monitoring"
    assert captured["recipes"] == prepared.recipes
    assert result.result["created"] == 1
    assert result.result["crds_applied"] == 1


def test_stackstorm_devstack_helper_uses_service_layer_boundaries() -> None:
    content = (
        Path(__file__).resolve().parents[1] / "helm/devstack/configure-stackstorm-adapter.sh"
    ).read_text()

    assert "from api.core.database import" not in content
    assert "dispose_adapter_runtime_resources" in content
    assert "write_adapter_credential" in content
    assert "update_service_plugin_state" in content
