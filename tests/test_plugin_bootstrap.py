"""Unit tests for service plugin bootstrap contract helpers."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pytest

from api.models.models import Ingredient, ServiceIdentityCredential, ServicePlugin
from api.plugins.manifest import ServicePlugin as ServicePluginManifest
from api.plugins.types import PluginHealthResult
from api.services import plugin_bootstrap


def _ingredient() -> Ingredient:
    return Ingredient(
        id=11,
        service_type="dummy",
        service_exec="positive_result",
        destination_target="dummy",
        task_key_template="dummy-positive-result",
        service_payload_template={"message": "template"},
        payload_schema={
            "type": "object",
            "properties": {"message": {"type": "string", "minLength": 1}},
            "required": ["message"],
            "additionalProperties": False,
        },
        service_exec_parameters=None,
        default_expected_secs=1,
        default_timeout=30,
        service_exec_expected_outcome_default={"success": True},
        ingredient_purpose="utility",
        is_active=True,
        is_blocking=True,
        retry_count=0,
        retry_delay=0,
        on_failure="stop",
        deleted=False,
    )


def test_recipe_step_payload_validates_filled_service_payload() -> None:
    ingredient = _ingredient()
    payload = plugin_bootstrap._recipe_step_payload(
        {
            "service_type": "dummy",
            "service_exec": "positive_result",
            "destination_target": "dummy",
            "task_key_template": "dummy-positive-result",
            "service_payload": {"message": "filled"},
        },
        {("dummy", "positive_result", "dummy", "dummy-positive-result"): ingredient},
    )
    assert payload["ingredient_id"] == 11
    assert payload["service_payload"] == {"message": "filled"}


def test_recipe_step_payload_rejects_invalid_filled_service_payload() -> None:
    ingredient = _ingredient()
    with pytest.raises(plugin_bootstrap.PluginBootstrapError, match="service_payload invalid"):
        plugin_bootstrap._recipe_step_payload(
            {
                "service_type": "dummy",
                "service_exec": "positive_result",
                "destination_target": "dummy",
                "task_key_template": "dummy-positive-result",
                "service_payload": {"message": "", "extra": True},
            },
            {("dummy", "positive_result", "dummy", "dummy-positive-result"): ingredient},
        )


def test_active_template_drift_is_detectable() -> None:
    ingredient = _ingredient()
    payload = plugin_bootstrap._ingredient_contract_from_row(ingredient)
    changed = dict(payload)
    changed["default_timeout"] = 60
    assert changed != payload


@pytest.mark.asyncio
async def test_plugin_bootstrap_creates_active_revision_for_disabled_matching_ingredient() -> None:
    retired = _ingredient()
    retired.is_active = False
    template = plugin_bootstrap._ingredient_contract_from_row(retired)
    plugin = ServicePluginManifest(
        service_type="dummy",
        adapter_factory=lambda: object(),
        ingredient_templates=(template,),
        recipe_templates=(),
    )
    db = _IngredientRegistrationFakeDb([retired])

    stats, ingredient_map, failed = await plugin_bootstrap._register_plugin_ingredients(
        db, [plugin]  # type: ignore[arg-type]
    )

    identity = ("dummy", "positive_result", "dummy", "dummy-positive-result")
    assert failed == set()
    assert stats["created"] == 1
    assert stats["retired"] == 0
    assert ingredient_map[identity] is db.added[0]
    assert retired.is_active is False
    assert db.added[0].is_active is True


@pytest.mark.asyncio
async def test_plugin_bootstrap_retires_active_drift_and_creates_revision() -> None:
    existing = _ingredient()
    template = plugin_bootstrap._ingredient_contract_from_row(existing)
    template["default_timeout"] = 60
    plugin = ServicePluginManifest(
        service_type="dummy",
        adapter_factory=lambda: object(),
        ingredient_templates=(template,),
        recipe_templates=(),
    )
    db = _IngredientRegistrationFakeDb([existing])

    stats, ingredient_map, failed = await plugin_bootstrap._register_plugin_ingredients(
        db, [plugin]  # type: ignore[arg-type]
    )

    identity = ("dummy", "positive_result", "dummy", "dummy-positive-result")
    assert failed == set()
    assert stats["created"] == 1
    assert stats["retired"] == 1
    assert stats["unchanged"] == 0
    assert existing.is_active is False
    assert ingredient_map[identity] is db.added[0]
    assert db.added[0].default_timeout == 60


@pytest.mark.asyncio
async def test_plugin_bootstrap_reuses_active_matching_ingredient() -> None:
    existing = _ingredient()
    template = plugin_bootstrap._ingredient_contract_from_row(existing)
    plugin = ServicePluginManifest(
        service_type="dummy",
        adapter_factory=lambda: object(),
        ingredient_templates=(template,),
        recipe_templates=(),
    )
    db = _IngredientRegistrationFakeDb([existing])

    stats, ingredient_map, failed = await plugin_bootstrap._register_plugin_ingredients(
        db, [plugin]  # type: ignore[arg-type]
    )

    identity = ("dummy", "positive_result", "dummy", "dummy-positive-result")
    assert failed == set()
    assert stats["created"] == 0
    assert stats["retired"] == 0
    assert stats["unchanged"] == 1
    assert ingredient_map[identity] is existing
    assert existing.is_active is True
    assert db.added == []


def test_scheduled_service_execution_payload_validates_against_template() -> None:
    ingredient = _ingredient()
    payload = plugin_bootstrap.ScheduledTaskCreate.model_validate(
        {
            "task_key": "dummy-scheduled-positive-result",
            "task_type": "service_execution",
            "service_type": "dummy",
            "service_exec": "positive_result",
            "task_payload": {"message": "scheduled"},
        }
    )
    plugin_bootstrap._validate_scheduled_service_execution(
        payload,
        {("dummy", "positive_result", "dummy", "dummy-positive-result"): ingredient},
    )


def test_scheduled_service_execution_rejects_invalid_payload() -> None:
    ingredient = _ingredient()
    payload = plugin_bootstrap.ScheduledTaskCreate.model_validate(
        {
            "task_key": "dummy-scheduled-positive-result",
            "task_type": "service_execution",
            "service_type": "dummy",
            "service_exec": "positive_result",
            "task_payload": {"message": "", "extra": True},
        }
    )
    with pytest.raises(plugin_bootstrap.PluginBootstrapError, match="task_payload invalid"):
        plugin_bootstrap._validate_scheduled_service_execution(
            payload,
            {("dummy", "positive_result", "dummy", "dummy-positive-result"): ingredient},
        )


def test_plugin_health_scheduled_task_runs_immediately_on_registration() -> None:
    now = datetime(2026, 5, 3, 17, 45, 0)
    payload = plugin_bootstrap.ScheduledTaskCreate.model_validate(
        {
            "task_key": "plugin-health-check:dummy",
            "task_type": "plugin_health_check",
            "service_type": "dummy",
            "service_exec": "health_check",
            "run_interval_seconds": 30,
        }
    )

    assert plugin_bootstrap._initial_scheduled_task_next_run_at(payload, now) == now


def test_service_execution_scheduled_task_defers_first_run_on_registration() -> None:
    now = datetime(2026, 5, 3, 17, 45, 0)
    payload = plugin_bootstrap.ScheduledTaskCreate.model_validate(
        {
            "task_key": "dummy-scheduled-positive-result",
            "task_type": "service_execution",
            "service_type": "dummy",
            "service_exec": "positive_result",
            "run_interval_seconds": 300,
            "task_payload": {"message": "scheduled"},
        }
    )

    assert plugin_bootstrap._initial_scheduled_task_next_run_at(payload, now) == (
        now + timedelta(seconds=300)
    )


def test_scheduled_task_next_run_at_is_owned_by_bootstrap_on_registration() -> None:
    now = datetime(2026, 5, 3, 17, 45, 0)
    explicit = now + timedelta(seconds=12)
    payload = plugin_bootstrap.ScheduledTaskCreate.model_validate(
        {
            "task_key": "dummy-scheduled-positive-result",
            "task_type": "service_execution",
            "service_type": "dummy",
            "service_exec": "positive_result",
            "run_interval_seconds": 300,
            "next_run_at": explicit,
            "task_payload": {"message": "scheduled"},
        }
    )

    assert plugin_bootstrap._initial_scheduled_task_next_run_at(payload, now) == (
        now + timedelta(seconds=300)
    )


def test_core_scheduled_tasks_are_empty_after_orders_only_rewrite() -> None:
    templates = plugin_bootstrap._core_scheduled_task_templates(plugin_bootstrap.datetime.now())
    assert templates == []


class _ScalarResult:
    def __init__(self, row: object | None) -> None:
        self._row = row

    def scalar_one_or_none(self) -> object | None:
        return self._row


class _ExecuteResult:
    def __init__(self, row: object | None) -> None:
        self._row = row

    def scalar_one_or_none(self) -> object | None:
        return self._row


class _ScalarRowsResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def scalars(self) -> "_ScalarRowsResult":
        return self

    def all(self) -> list[object]:
        return self._rows

    def scalar_one_or_none(self) -> object | None:
        return self._rows[0] if self._rows else None


class _IngredientRegistrationFakeDb:
    def __init__(self, rows: list[Ingredient]) -> None:
        self.rows = rows
        self.added: list[object] = []

    async def execute(self, statement: object) -> _ScalarRowsResult:
        if "FROM ingredients" in str(statement):
            return _ScalarRowsResult(self.rows)
        return _ScalarRowsResult([])

    async def flush(self) -> None:
        for index, row in enumerate(self.added, start=100):
            if isinstance(row, Ingredient) and row.id is None:
                row.id = index

    def add(self, row: object) -> None:
        self.added.append(row)


class _FakeDb:
    def __init__(self, row: ServicePlugin | None = None) -> None:
        self.row = row
        self.added: list[object] = []

    async def execute(self, statement: object) -> _ExecuteResult:
        if "service_identity_credentials" in str(statement):
            return _ExecuteResult(None)
        return _ExecuteResult(self.row)

    async def flush(self) -> None:
        for index, row in enumerate(self.added, start=1):
            if isinstance(row, ServicePlugin) and row.id is None:
                row.id = index

    def add(self, row: object) -> None:
        self.added.append(row)


class _BeginFakeDb(_FakeDb):
    async def __aenter__(self) -> "_BeginFakeDb":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def begin(self) -> "_BeginFakeDb":
        return self


@pytest.mark.asyncio
async def test_service_plugin_registry_counts_are_recorded(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    plugin = ServicePluginManifest(
        service_type="dummy",
        adapter_factory=lambda: object(),  # not used by this bootstrap helper
        ingredient_templates=({"service_type": "dummy"}, {"service_type": "dummy"}),
        recipe_templates=({"name": "one"},),
    )

    async def short_id(_db: object) -> str:
        return "dum7x2p9"

    monkeypatch.setattr(plugin_bootstrap, "_new_unique_plugin_short_id", short_id)

    db = _FakeDb()
    caplog.set_level(logging.INFO, logger="api.services.plugin_bootstrap")
    stats = await plugin_bootstrap._register_service_plugins(db, [plugin])  # type: ignore[arg-type]

    assert stats == {"created": 1, "updated": 0, "processed": 1, "errors": 0}
    assert len(db.added) == 1
    assert db.added[0].plugin_short_id == "dum7x2p9"
    assert db.added[0].plugin_tier == "community"
    assert db.added[0].plugin_log_key is None
    assert db.added[0].health_status == "initializing"
    assert db.added[0].registered_ingredient_count == 2
    assert db.added[0].registered_recipe_count == 1
    records = [
        record for record in caplog.records if record.name == "api.services.plugin_bootstrap"
    ]
    assert any(
        record.message == "Service plugin metadata registration start"
        and record.service_type == "dummy"
        and record.ingredient_template_count == 2
        and record.recipe_template_count == 1
        for record in records
    )
    assert any(
        record.message == "Service plugin metadata registration complete"
        and record.service_type == "dummy"
        and record.plugin_short_id == "dum7x2p9"
        and record.action == "created"
        for record in records
    )


@pytest.mark.asyncio
async def test_service_plugin_registry_seeds_initial_health_from_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Adapter:
        def health_check(self) -> PluginHealthResult:
            return PluginHealthResult(
                service_type="dummy",
                status="healthy",
                message="Dummy plugin configured",
                details={"mode": "dummy"},
            )

    plugin = ServicePluginManifest(
        service_type="dummy",
        adapter_factory=lambda: _Adapter(),
        ingredient_templates=({"service_type": "dummy"},),
        recipe_templates=(),
    )

    async def short_id(_db: object) -> str:
        return "dum7x2p9"

    monkeypatch.setattr(plugin_bootstrap, "_new_unique_plugin_short_id", short_id)

    db = _FakeDb()
    stats = await plugin_bootstrap._register_service_plugins(db, [plugin])  # type: ignore[arg-type]

    assert stats == {"created": 1, "updated": 0, "processed": 1, "errors": 0}
    assert db.added[0].health_status == "healthy"
    assert db.added[0].health_message == "Dummy plugin configured"
    assert db.added[0].health_details == {"mode": "dummy"}
    assert db.added[0].last_health_check_at is not None
    assert db.added[0].last_success_at is not None


@pytest.mark.asyncio
async def test_external_service_plugins_do_not_register_hmac_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = ServicePluginManifest(
        service_type="dummy",
        adapter_factory=lambda: object(),
        ingredient_templates=(),
        recipe_templates=(),
    )

    async def short_id(_db: object) -> str:
        return "dum7x2p9"

    monkeypatch.setattr(plugin_bootstrap, "_new_unique_plugin_short_id", short_id)
    monkeypatch.setenv("POUNDCAKE_PLUGIN_CREDENTIAL_ENCRYPTION_KEY", "unit-encryption-key")
    monkeypatch.setenv(
        "POUNDCAKE_SERVICE_IDENTITY_CREDENTIAL_ENCRYPTION_KEY",
        "unit-service-identity-key",
    )
    db = _FakeDb()

    await plugin_bootstrap._register_service_plugins(db, [plugin])  # type: ignore[arg-type]

    credentials = [row for row in db.added if isinstance(row, ServiceIdentityCredential)]
    assert credentials == []


@pytest.mark.asyncio
async def test_stackstorm_api_key_import_uses_credential_manager_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = ServicePluginManifest(
        service_type="stackstorm",
        adapter_factory=lambda: object(),
        ingredient_templates=(),
        recipe_templates=(),
    )
    saved: dict[str, object] = {}

    async def save_credential(**kwargs: object) -> None:
        saved.update(kwargs)

    monkeypatch.setenv("POUNDCAKE_STACKSTORM_API_KEY", "st2-generated-key")
    monkeypatch.setattr(plugin_bootstrap, "write_adapter_credential", save_credential)

    stats = await plugin_bootstrap._import_stackstorm_api_key_credential(
        _FakeDb(), [plugin]  # type: ignore[arg-type]
    )

    assert stats == {"processed": 1, "imported": 1, "errors": 0}
    assert saved["service_type"] == "stackstorm"
    assert saved["credential_type"] == "stackstorm_api_key"
    assert saved["credential_key_id"] == "default"
    assert "db" not in saved
    assert saved["payload"] == {
        "api_key": "st2-generated-key",
        "st2_api_key": "st2-generated-key",
    }


@pytest.mark.asyncio
async def test_internal_service_plugins_register_supported_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = iter(["credmaa1", "prepaaa1", "runnera1", "timeraa1", "dishaaa1"])

    async def short_id(_db: object) -> str:
        return next(ids)

    monkeypatch.setattr(plugin_bootstrap, "_new_unique_plugin_short_id", short_id)
    db = _FakeDb()

    stats = await plugin_bootstrap._register_internal_service_plugins(db)  # type: ignore[arg-type]

    assert stats == {"created": 5, "updated": 0, "processed": 5, "errors": 0}
    assert [row.service_type for row in db.added] == [
        "credential-manager",
        "prep-chef",
        "expediter-runner",
        "timer",
        "dishwasher",
    ]
    assert {row.plugin_type for row in db.added} == {"internal_plugin"}
    assert {row.plugin_tier for row in db.added} == {"supported"}
    assert {row.registered_ingredient_count for row in db.added} == {0}
    assert {row.registered_recipe_count for row in db.added} == {0}
    query_limits = {
        row.service_type: row.query_limit for row in db.added if isinstance(row, ServicePlugin)
    }
    assert query_limits == {
        "prep-chef": 50,
        "expediter-runner": 50,
        "timer": 50,
        "dishwasher": None,
        "credential-manager": None,
    }


@pytest.mark.asyncio
async def test_internal_service_plugins_register_hmac_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = iter(["credmaa1", "prepaaa1", "runnera1", "timeraa1", "dishaaa1"])

    async def short_id(_db: object) -> str:
        return next(ids)

    monkeypatch.setattr(plugin_bootstrap, "_new_unique_plugin_short_id", short_id)
    monkeypatch.setenv("POUNDCAKE_PLUGIN_CREDENTIAL_ENCRYPTION_KEY", "unit-encryption-key")
    monkeypatch.setenv(
        "POUNDCAKE_SERVICE_IDENTITY_CREDENTIAL_ENCRYPTION_KEY",
        "unit-service-identity-key",
    )
    db = _FakeDb()

    await plugin_bootstrap._register_internal_service_plugins(db)  # type: ignore[arg-type]

    credentials = [row for row in db.added if isinstance(row, ServiceIdentityCredential)]
    assert credentials == []
    plugins = [row for row in db.added if isinstance(row, ServicePlugin)]
    assert {row.credential_status for row in plugins} == {"unknown"}


@pytest.mark.asyncio
async def test_service_identity_bootstrap_registers_hmac_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    row = ServicePlugin(
        id=1,
        service_type="prep-chef",
        plugin_short_id="prepaaa1",
        credential_status="unknown",
        enabled=True,
    )
    db = _BeginFakeDb(row)
    monkeypatch.setattr(
        plugin_bootstrap,
        "_internal_plugin_defaults",
        lambda: (("prep-chef", 30, 50),),
    )

    async def upsert(_db: object, plugin_row: ServicePlugin, *, now: object) -> bool:
        del _db, now
        calls.append(plugin_row.service_type)
        plugin_row.credential_status = "ready"
        return True

    monkeypatch.setattr(plugin_bootstrap, "_upsert_internal_hmac_credential", upsert)

    stats = await plugin_bootstrap.bootstrap_service_identities(db)  # type: ignore[arg-type]

    assert stats == {"created": 1, "updated": 0, "processed": 1, "errors": 0}
    assert calls == ["prep-chef"]
    assert row.credential_status == "ready"


def test_internal_service_plugins_do_not_include_suppressions() -> None:
    service_types = {
        service_type
        for service_type, _interval, _query_limit in plugin_bootstrap._internal_plugin_defaults()
    }

    assert {"prep-chef", "expediter-runner", "timer", "dishwasher", "credential-manager"}.issubset(
        service_types
    )
    assert "suppression_sync" not in service_types
    assert "suppression_lifecycle" not in service_types
    assert "suppressions" not in service_types


@pytest.mark.asyncio
async def test_bootstrap_registers_internal_plugins_before_external_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def register_internal(_db: object) -> dict[str, int]:
        calls.append("internal")
        return {"created": 3, "updated": 0, "processed": 3, "errors": 0}

    monkeypatch.setattr(plugin_bootstrap, "_register_internal_service_plugins", register_internal)
    monkeypatch.setattr(
        plugin_bootstrap,
        "get_enabled_plugins_for_bootstrap",
        lambda: calls.append("external_discovery") or ([], []),
    )

    async def empty_stats(*_args: object, **_kwargs: object) -> dict[str, int]:
        return {"created": 0, "updated": 0, "processed": 0, "errors": 0}

    monkeypatch.setattr(plugin_bootstrap, "_register_service_plugins", empty_stats)

    async def empty_hooks(_db: object, _plugins: object) -> dict[str, object]:
        return {"processed": 0, "errors": 0, "hooks": {}}

    monkeypatch.setattr(plugin_bootstrap, "_run_plugin_bootstrap_hooks", empty_hooks)

    await plugin_bootstrap.bootstrap_plugin_registry(_BeginFakeDb())  # type: ignore[arg-type]

    assert calls[:2] == ["internal", "external_discovery"]


@pytest.mark.asyncio
async def test_bootstrap_registers_bakery_route_as_fallback_comms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bakery_route = {
        "id": "bakery-global-comms",
        "label": "Bakery Rackspace Core",
        "service_type": "bakery",
        "destination_target": "rackspace_core",
        "provider_config": {},
        "enabled": True,
        "position": 1,
    }
    plugin = ServicePluginManifest(
        service_type="bakery",
        adapter_factory=lambda: object(),
        ingredient_templates=(),
        recipe_templates=(),
        communication_routes=(bakery_route,),
    )

    async def empty_stats(*_args: object, **_kwargs: object) -> dict[str, int]:
        return {"created": 0, "updated": 0, "processed": 0, "errors": 0}

    async def empty_hooks(_db: object, _plugins: object) -> dict[str, object]:
        return {"processed": 0, "errors": 0, "hooks": {}}

    monkeypatch.setattr(
        plugin_bootstrap,
        "_register_internal_service_plugins",
        lambda _db: empty_stats(),
    )
    monkeypatch.setattr(
        plugin_bootstrap,
        "get_enabled_plugins_for_bootstrap",
        lambda: ([plugin], []),
    )
    monkeypatch.setattr(plugin_bootstrap, "_register_service_plugins", empty_stats)
    monkeypatch.setattr(plugin_bootstrap, "_run_plugin_bootstrap_hooks", empty_hooks)

    stats = await plugin_bootstrap.bootstrap_plugin_registry(_BeginFakeDb())  # type: ignore[arg-type]

    assert stats["communication_routes"] == {
        "processed": 1,
        "errors": 0,
        "deferred": 1,
        "authority": "dishwasher",
    }


@pytest.mark.asyncio
async def test_bootstrap_defers_manifest_sync_to_dishwasher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = ServicePluginManifest(
        service_type="dummy",
        adapter_factory=lambda: object(),
        ingredient_templates=(
            {"service_type": "dummy"},
            {"service_type": "dummy", "service_exec": "x"},
        ),
        recipe_templates=({"name": "dummy recipe"},),
        communication_routes=({"id": "dummy-route"},),
        scheduled_tasks=({"task_key": "dummy-health"}, {"task_key": "dummy-sync"}),
    )

    async def empty_stats(*_args: object, **_kwargs: object) -> dict[str, int]:
        return {"created": 0, "updated": 0, "processed": 0, "errors": 0}

    async def forbid(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("startup bootstrap must not own manifest sync writes")

    async def empty_hooks(_db: object, _plugins: object) -> dict[str, object]:
        return {"processed": 0, "errors": 0, "hooks": {}}

    monkeypatch.setattr(
        plugin_bootstrap,
        "_register_internal_service_plugins",
        lambda _db: empty_stats(),
    )
    monkeypatch.setattr(
        plugin_bootstrap,
        "get_enabled_plugins_for_bootstrap",
        lambda: ([plugin], []),
    )
    monkeypatch.setattr(plugin_bootstrap, "_register_plugin_ingredients", forbid)
    monkeypatch.setattr(plugin_bootstrap, "_register_plugin_recipes", forbid)
    monkeypatch.setattr(plugin_bootstrap, "_register_scheduled_tasks", forbid)
    monkeypatch.setattr(plugin_bootstrap, "_register_service_plugins", empty_stats)
    monkeypatch.setattr(plugin_bootstrap, "_run_plugin_bootstrap_hooks", empty_hooks)

    stats = await plugin_bootstrap.bootstrap_plugin_registry(_BeginFakeDb())  # type: ignore[arg-type]

    assert stats["ingredients"] == {
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "retired": 0,
        "processed": 2,
        "errors": 0,
        "deferred": 2,
        "authority": "dishwasher",
    }
    assert stats["recipes"] == {
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "retired": 0,
        "processed": 1,
        "errors": 0,
        "deferred": 1,
        "authority": "dishwasher",
    }
    assert stats["scheduled_tasks"] == {
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "retired": 0,
        "processed": 2,
        "errors": 0,
        "deferred": 2,
        "authority": "dishwasher",
    }
    assert stats["communication_routes"] == {
        "processed": 1,
        "errors": 0,
        "deferred": 1,
        "authority": "dishwasher",
    }


class _AdapterForBootstrap(plugin_bootstrap.ExecutionAdapter):
    service_type = "dummy"

    def __init__(self, collector: list[str], *, should_fail: bool = False) -> None:
        self.collector = collector
        self.should_fail = should_fail

    def validate(self, ctx):  # type: ignore[no-untyped-def]
        del ctx
        return None

    async def dispatch(self, ctx):  # type: ignore[no-untyped-def]
        del ctx
        raise NotImplementedError

    async def poll(self, ctx, service_exec_id: str):  # type: ignore[no-untyped-def]
        del ctx, service_exec_id
        raise NotImplementedError

    def health_check(self):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    async def bootstrap_credentials(self, *, force: bool = False) -> None:
        del force
        if self.should_fail:
            raise RuntimeError("boom")
        self.collector.append(self.service_type)


@pytest.mark.asyncio
async def test_adapter_credential_bootstrap_uses_adapter_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    plugin = ServicePluginManifest(
        service_type="dummy",
        adapter_factory=lambda: _AdapterForBootstrap(calls),
        ingredient_templates=(),
        recipe_templates=(),
    )

    async def discover(_db: object, credential_failed: bool = False):
        del _db, credential_failed
        return [plugin], [], []

    monkeypatch.setattr(
        plugin_bootstrap,
        "_discover_healthy_plugins",
        discover,
    )

    stats = await plugin_bootstrap.bootstrap_adapter_credentials(_BeginFakeDb())  # type: ignore[arg-type]

    assert calls == ["dummy"]
    assert stats["credential_bootstrapped"] == 1
    assert stats["errors"] == 0
    assert stats["plugins"] == {"dummy": {"status": "ready"}}


@pytest.mark.asyncio
async def test_adapter_credential_bootstrap_marks_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = ServicePluginManifest(
        service_type="dummy",
        adapter_factory=lambda: _AdapterForBootstrap([], should_fail=True),
        ingredient_templates=(),
        recipe_templates=(),
    )
    errors: list[tuple[str, str]] = []

    async def discover(_db: object, credential_failed: bool = False):
        del _db, credential_failed
        return [plugin], [], []

    monkeypatch.setattr(
        plugin_bootstrap,
        "_discover_healthy_plugins",
        discover,
    )

    async def mark_failed(*, service_type: str, error: str) -> None:
        errors.append((service_type, error))

    monkeypatch.setattr(plugin_bootstrap, "mark_adapter_credential_error", mark_failed)

    stats = await plugin_bootstrap.bootstrap_adapter_credentials(_BeginFakeDb())  # type: ignore[arg-type]

    assert stats["credential_bootstrapped"] == 0
    assert stats["errors"] == 1
    assert errors == [("dummy", "boom")]
    assert stats["plugins"] == {"dummy": {"status": "failed", "error": "boom"}}


@pytest.mark.asyncio
async def test_service_plugin_registry_keeps_existing_short_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = ServicePluginManifest(
        service_type="dummy",
        adapter_factory=lambda: object(),
        ingredient_templates=({"service_type": "dummy"},),
        recipe_templates=(),
    )
    row = ServicePlugin(
        service_type="dummy",
        plugin_short_id="dumstable",
        enabled=True,
        health_status="healthy",
        registered_ingredient_count=0,
        registered_recipe_count=0,
    )
    db = _FakeDb(row)
    stats = await plugin_bootstrap._register_service_plugins(db, [plugin])  # type: ignore[arg-type]

    assert stats["updated"] == 1
    assert row.plugin_short_id == "dumstable"
    assert row.health_status == "healthy"


@pytest.mark.asyncio
async def test_service_plugin_registry_sets_supported_log_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = ServicePluginManifest(
        service_type="dummy",
        adapter_factory=lambda: object(),
        ingredient_templates=({"service_type": "dummy"},),
        recipe_templates=(),
        plugin_tier="supported",
        plugin_log_key="dummy",
    )
    row = ServicePlugin(
        service_type="dummy",
        plugin_short_id="dumstable",
        plugin_tier="community",
        plugin_log_key=None,
        enabled=True,
        health_status="healthy",
        registered_ingredient_count=0,
        registered_recipe_count=0,
    )
    db = _FakeDb(row)
    stats = await plugin_bootstrap._register_service_plugins(db, [plugin])  # type: ignore[arg-type]

    assert stats["updated"] == 1
    assert row.plugin_tier == "supported"
    assert row.plugin_log_key == "dummy"


@pytest.mark.asyncio
async def test_plugin_bootstrap_hook_logs_progress(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def bootstrap(_db: object, _helpers: dict[str, object]) -> dict[str, object]:
        return {"processed": 3, "alerts": ["one", "two"], "details": {"safe": True}}

    plugin = ServicePluginManifest(
        service_type="genestack_monitoring",
        adapter_factory=lambda: object(),
        ingredient_templates=(),
        recipe_templates=(),
        bootstrap_factory=bootstrap,
    )
    caplog.set_level(logging.INFO, logger="api.services.plugin_bootstrap")

    stats = await plugin_bootstrap._run_plugin_bootstrap_hooks(None, [plugin])  # type: ignore[arg-type]

    assert stats["processed"] == 1
    records = [
        record for record in caplog.records if record.name == "api.services.plugin_bootstrap"
    ]
    assert any(
        record.message == "Service plugin bootstrap hook start"
        and record.service_type == "genestack_monitoring"
        for record in records
    )
    complete = next(
        record for record in records if record.message == "Service plugin bootstrap hook complete"
    )
    assert complete.service_type == "genestack_monitoring"
    assert complete.result_summary == {
        "result_keys": ["alerts", "details", "processed"],
        "processed": 3,
        "alerts_count": 2,
        "details_keys": ["safe"],
    }


@pytest.mark.asyncio
async def test_plugin_bootstrap_hook_receives_only_its_own_helper() -> None:
    provider_helper = {"provider": "helper"}
    consumer_helper = {"consumer": "helper"}

    provider = ServicePluginManifest(
        service_type="provider",
        adapter_factory=lambda: object(),
        helper_factory=lambda: provider_helper,
    )

    async def bootstrap(_db: object, helpers: dict[str, object]) -> dict[str, object]:
        assert helpers == {"consumer": consumer_helper}
        return {"processed": 1}

    consumer = ServicePluginManifest(
        service_type="consumer",
        adapter_factory=lambda: object(),
        helper_factory=lambda: consumer_helper,
        bootstrap_factory=bootstrap,
    )

    stats = await plugin_bootstrap._run_plugin_bootstrap_hooks(
        None,
        [provider, consumer],
    )  # type: ignore[arg-type]

    assert stats["processed"] == 1
    assert stats["errors"] == 0


@pytest.mark.asyncio
async def test_plugin_bootstrap_hook_failure_logs_service_type(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def bootstrap(_db: object, _helpers: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("boom")

    plugin = ServicePluginManifest(
        service_type="genestack_monitoring",
        adapter_factory=lambda: object(),
        ingredient_templates=(),
        recipe_templates=(),
        bootstrap_factory=bootstrap,
    )

    async def mark_failed(
        _db: object,
        *,
        service_type: str,
        message: str,
        credential_failed: bool = False,
    ) -> None:
        assert service_type == "genestack_monitoring"
        assert "boom" in message
        assert credential_failed is False

    monkeypatch.setattr(plugin_bootstrap, "_mark_service_plugin_failed", mark_failed)
    caplog.set_level(logging.ERROR, logger="api.services.plugin_bootstrap")

    stats = await plugin_bootstrap._run_plugin_bootstrap_hooks(None, [plugin])  # type: ignore[arg-type]

    assert stats["errors"] == 1
    assert stats["hooks"] == {"genestack_monitoring": {"status": "failed", "error": "boom"}}

    failure = next(
        record
        for record in caplog.records
        if record.message == "Service plugin bootstrap hook failed"
    )
    assert failure.service_type == "genestack_monitoring"
    assert failure.error == "boom"
