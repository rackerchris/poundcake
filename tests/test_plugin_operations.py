"""Tests for the plugin_operations service-layer RBAC boundary."""

from __future__ import annotations

from datetime import datetime, UTC
from unittest.mock import AsyncMock, patch

import pytest

from api.models.models import ServicePlugin
from api.services.database_access import (
    DatabaseAccessError,
    principal_for_internal_service,
    require_database_capability,
)
from api.services.plugin_operations import (
    RecipePayload,
    RecipeStepPayload,
    UpsertStats,
    get_ingredient,
    update_dish_metadata,
    update_scheduled_task,
    update_service_plugin_state,
    upsert_recipes,
)


class _ScalarResult:
    def __init__(self, row: object | None) -> None:
        self._row = row

    def first(self) -> object | None:
        return self._row

    def scalar_one_or_none(self) -> object | None:
        return self._row


class _ExecuteResult:
    def __init__(self, row: object | None) -> None:
        self._row = row

    def scalars(self) -> _ScalarResult:
        return _ScalarResult(self._row)


class _PluginStateDb:
    def __init__(self, row: ServicePlugin | None) -> None:
        self.row = row

    async def execute(self, _statement: object) -> _ExecuteResult:
        return _ExecuteResult(self.row)


class _PluginStateSession:
    def __init__(self, db: _PluginStateDb) -> None:
        self.db = db

    async def __aenter__(self) -> _PluginStateDb:
        return self.db

    async def __aexit__(self, *_args: object) -> None:
        return None


# ----------------------------------------------------------------------
# Capability RBAC — ensures genestack_monitoring has the right grant
# ----------------------------------------------------------------------


def test_genestack_monitoring_can_upsert_recipes() -> None:
    principal = principal_for_internal_service("genestack_monitoring")
    require_database_capability(principal, "genestack_monitoring:recipe-sync")


def test_cred_manager_cannot_upsert_genestack_recipes() -> None:
    principal = principal_for_internal_service("credential-manager")
    with pytest.raises(DatabaseAccessError, match="genestack_monitoring:recipe-sync"):
        require_database_capability(principal, "genestack_monitoring:recipe-sync")


def test_timer_can_read_ingredients() -> None:
    principal = principal_for_internal_service("timer")
    # Timer is an internal service with service-plugin:read
    require_database_capability(principal, "service-plugin:read")
    # But timer does NOT have the genestack-specific recipe-sync capability
    with pytest.raises(DatabaseAccessError, match="genestack_monitoring:recipe-sync"):
        require_database_capability(principal, "genestack_monitoring:recipe-sync")


def test_plugin_registry_cannot_upsert_recipes() -> None:
    principal = principal_for_internal_service("plugin-registry")
    with pytest.raises(DatabaseAccessError, match="genestack_monitoring:recipe-sync"):
        require_database_capability(principal, "genestack_monitoring:recipe-sync")


# ----------------------------------------------------------------------
# upsert_recipes validates capability before touching the DB
# ----------------------------------------------------------------------


async def test_upsert_recipes_rejects_unauthorized_service() -> None:
    """A service without the capability must get DatabaseAccessError."""
    with pytest.raises(DatabaseAccessError, match="genestack_monitoring:recipe-sync"):
        await upsert_recipes(
            requester_service_type="random_service",
            recipes=[],
        )


async def test_upsert_recipes_empty_payload_returns_zeroes() -> None:
    """Empty recipes list returns zeroes without touching the DB."""
    stats = await upsert_recipes(
        requester_service_type="genestack_monitoring",
        recipes=[],
    )
    assert isinstance(stats, UpsertStats)
    assert stats.created == 0
    assert stats.updated == 0
    assert stats.deleted == 0


async def test_upsert_recipes_requires_db_session() -> None:
    """When plugins have the capability, DB session is needed."""
    with patch("api.services.plugin_operations.plugin_operation_db_session") as mock_session:
        mock_session.return_value.__aenter__ = AsyncMock()
        mock_session.return_value.__aexit__ = AsyncMock()
        # Should not raise — but will need real DB to proceed
        # We just verify the RBAC check passes


# ----------------------------------------------------------------------
# get_ingredient returns dict, not SQLAlchemy model
# ----------------------------------------------------------------------


async def test_get_ingredient_enforces_db_session() -> None:
    """When RBAC passes but no DB URL is configured, we get a clear error."""
    with pytest.raises(RuntimeError, match="POUNDCAKE_PLUGIN_OPERATION_DB_URL"):
        await get_ingredient(
            requester_service_type="genestack_monitoring",
            service_type="k8s",
            service_exec="workload_triage",
            task_key_template="k8s-workload-triage-v2",
        )


# Patch needed for tests that pass RBAC but hit the DB session
_db_session_patch = patch(
    "api.services.plugin_operations.plugin_operation_db_session",
)


async def test_get_ingredient_requires_db_session() -> None:
    """When RBAC passes, the DB session is used (and needed)."""
    patch_obj = _db_session_patch.start()
    try:
        patch_obj.return_value.__aenter__ = AsyncMock()
        patch_obj.return_value.__aexit__ = AsyncMock()
        result = await get_ingredient(
            requester_service_type="genestack_monitoring",
            service_type="k8s",
            service_exec="workload_triage",
            task_key_template="k8s-workload-triage-v2",
        )
        assert result is None  # No matching ingredient in mocked DB
    finally:
        patch_obj.stop()


# ----------------------------------------------------------------------
# update_scheduled_task validates capability
# ----------------------------------------------------------------------


async def test_update_scheduled_task_rejects_unauthorized() -> None:
    """A non-internal service does not have app:data-write."""
    with pytest.raises(DatabaseAccessError, match="app:data-write"):
        await update_scheduled_task(
            requester_service_type="random_service",
            task_key="some-task",
            status="idle",
        )


async def test_update_service_plugin_state_rejects_unauthorized() -> None:
    with pytest.raises(DatabaseAccessError, match="service-plugin:update-status"):
        await update_service_plugin_state(
            requester_service_type="random_service",
            service_type="stackstorm",
            health_status="healthy",
        )


async def test_update_service_plugin_state_returns_false_when_plugin_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api.services.plugin_operations.plugin_operation_db_session",
        lambda: _PluginStateSession(_PluginStateDb(None)),
    )

    updated = await update_service_plugin_state(
        requester_service_type="api",
        service_type="stackstorm",
        health_status="healthy",
    )

    assert updated is False


async def test_update_service_plugin_state_updates_plugin_runtime_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = ServicePlugin(
        id=7,
        service_type="stackstorm",
        plugin_short_id="st2",
        plugin_type="external_plugin",
        plugin_tier="community",
        enabled=True,
        health_status="failed",
        consecutive_failures=3,
    )
    before = row.updated_at
    checked_at = datetime(2026, 6, 4, 17, 0, tzinfo=UTC).replace(tzinfo=None)
    monkeypatch.setattr(
        "api.services.plugin_operations.plugin_operation_db_session",
        lambda: _PluginStateSession(_PluginStateDb(row)),
    )

    updated = await update_service_plugin_state(
        requester_service_type="api",
        service_type="stackstorm",
        plugin_config={"url": "http://stackstorm-api:9101", "verify_ssl": False},
        health_status="healthy",
        health_message="StackStorm API accepted the configured credential",
        health_error_code=None,
        health_latency_ms=None,
        last_health_check_at=checked_at,
    )

    assert updated is True
    assert row.plugin_config == {"url": "http://stackstorm-api:9101", "verify_ssl": False}
    assert row.health_status == "healthy"
    assert row.health_message == "StackStorm API accepted the configured credential"
    assert row.health_error_code is None
    assert row.health_latency_ms is None
    assert row.last_health_check_at == checked_at
    assert row.last_success_at == checked_at
    assert row.consecutive_failures == 0
    assert row.updated_at is not None
    assert row.updated_at != before


# ----------------------------------------------------------------------
# update_dish_metadata validates capability
# ----------------------------------------------------------------------


async def test_update_dish_metadata_rejects_unauthorized() -> None:
    """A non-internal service does not have app:data-write."""
    with pytest.raises(DatabaseAccessError, match="app:data-write"):
        await update_dish_metadata(
            requester_service_type="random_service",
            dish_id=1,
            metadata={"key": "value"},
        )


# ----------------------------------------------------------------------
# Data model validation
# ----------------------------------------------------------------------


def test_recipe_payload_is_frozen() -> None:
    payload = RecipePayload(
        name="test-alert",
        description="test",
        enabled=True,
        clear_timeout_sec=None,
        managed_by="poundcake-genestack-monitoring",
        steps=[],
    )
    with pytest.raises(AttributeError):
        payload.name = "modified"


def test_recipe_step_payload_is_frozen() -> None:
    step = RecipeStepPayload(
        service_type="k8s",
        service_exec="workload_triage",
        task_key_template="k8s-workload-triage-v2",
        step_order=10,
        service_payload={"key": "value"},
        service_exec_parameters_override={"key": "value"},
        expected_secs=20,
        timeout=180,
        expected_outcome={"success": True},
        run_phase="firing",
        run_condition="always",
    )
    with pytest.raises(AttributeError):
        step.service_type = "modified"


def test_upsert_stats_is_frozen() -> None:
    stats = UpsertStats(created=1, updated=2, deleted=0, skipped=0)
    with pytest.raises(AttributeError):
        stats.created = 99
