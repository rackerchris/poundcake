"""Cook dispatch database interaction contracts."""

from __future__ import annotations

import ast
import inspect
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import OperationalError

import api.api.cook as cook_api
import api.api.expediter as expediter_api
import api.api.orders as orders_api
from api.api.orders import dispatch_order
from api.schemas.schemas import OrderDispatchResponse


class _DbOrig:
    def __init__(self, code: int) -> None:
        self.args = (code, "simulated database error")


class _Session:
    def __init__(self) -> None:
        self.rollback_count = 0

    async def rollback(self) -> None:
        self.rollback_count += 1


class _ScalarResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def first(self) -> object | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[object]:
        return self._rows


class _Result:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def scalars(self) -> _ScalarResult:
        return _ScalarResult(self._rows)

    def unique(self) -> "_Result":
        return self

    def first(self) -> object | None:
        return self._rows[0] if self._rows else None


class _DispatchDb:
    def __init__(self, results: list[list[object]]) -> None:
        self._results = list(results)
        self.added: list[object] = []
        self.flushed = 0

    async def execute(self, _statement: object) -> _Result:
        if not self._results:
            raise AssertionError("unexpected execute call")
        return _Result(self._results.pop(0))

    def add(self, row: object) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        self.flushed += 1
        for idx, row in enumerate(self.added, start=1):
            if getattr(row, "id", None) is None:
                setattr(row, "id", 9000 + idx)


def _operational_error(code: int) -> OperationalError:
    return OperationalError("statement", {}, _DbOrig(code))


def _compile(statement: Any) -> str:
    return str(statement.compile(dialect=mysql.dialect()))


def test_recipe_lookup_is_not_locked() -> None:
    sql = _compile(orders_api._active_recipe_query("dummy-positive-result"))

    assert "FOR UPDATE" not in sql
    assert "recipes.name" in sql


def test_runtime_dispatch_queries_still_lock_rows() -> None:
    statements = [
        orders_api._order_for_dispatch_query(123),
        orders_api._active_firing_dish_query(123),
        orders_api._active_phase_dish_query(123, "firing"),
        orders_api._dish_ingredients_for_seed_query(456),
    ]

    assert all("FOR UPDATE" in _compile(statement) for statement in statements)


def test_cook_hands_provider_work_to_runner_without_local_dispatch() -> None:
    source = inspect.getsource(cook_api._advance_dish)
    tree = ast.parse(source)

    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"expediter_dispatch_from_cook", "execute_service_execution"}
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "dispatch"
        for node in ast.walk(tree)
    )
    assert "EXPEDITER_RUNNER_RECEIPT_PREFIX" in source
    assert '"receipt_owner": EXPEDITER_RUNNER_SERVICE_TYPE' in source
    assert 'service_exec_status="running"' in source


def test_cook_advance_does_not_recursively_drive_recipe_steps() -> None:
    source = inspect.getsource(cook_api._advance_dish)
    tree = ast.parse(source)

    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_advance_dish"
        for node in ast.walk(tree)
    )


def test_cook_does_not_own_downstream_cascade_reconciliation() -> None:
    source = inspect.getsource(cook_api)

    assert "_cancel_blocked_future_rows" not in source
    assert "blocked_by_prior_group_failure" not in source


def test_cook_lifecycle_logs_cover_order_to_runtime_handoffs() -> None:
    source = inspect.getsource(cook_api)

    assert "Cook order planned" in source
    assert "Cook dish advance evaluating" in source
    assert "Cook marking execution segment ready" in source
    assert "Cook runtime row marked ready" in source
    assert "Cook dish terminal" in source
    assert "Cook resolving dish planned" in source


def test_cook_runtime_rows_include_actual_outcome_for_guard_terminal_state() -> None:
    row = SimpleNamespace(
        id=1,
        req_id="unit-test",
        dish_id=10,
        recipe_ingredient_id=20,
        task_key="step_10_alertmanager-firing-guard",
        step_order=10,
        parallel_group=0,
        depth=10,
        service_type="alertmanager",
        service_exec="inspect",
        destination_target=None,
        service_payload={},
        service_exec_parameters={},
        service_exec_expected_secs=None,
        service_exec_timeout=30,
        service_exec_expected_outcome={"is_firing": True},
        retry_count=0,
        retry_delay=0,
        on_failure="stop",
        service_exec_id=None,
        service_exec_status="failed",
        service_exec_run_time=0,
        service_exec_actual_outcome={"is_firing": False},
        service_exec_error=None,
        created_at=None,
    )

    runtime_row = cook_api._dish_ingredient_runtime_dict(row)

    assert runtime_row["service_exec_actual_outcome"] == {"is_firing": False}


def test_cook_defers_finalization_when_blocking_failure_has_future_pending_rows() -> None:
    assert cook_api._has_pending_after_blocking_failure(
        [
            {
                "service_exec_status": "failed",
                "on_failure": "stop",
                "depth": 10,
                "parallel_group": 0,
            },
            {
                "service_exec_status": "pending",
                "on_failure": "stop",
                "depth": 20,
                "parallel_group": 0,
            },
        ]
    )


def test_alertmanager_guard_false_terminal_state_is_no_remediation_cancel() -> None:
    terminal_status, row_status, message = cook_api._dish_terminal_state(
        [
            {
                "service_exec_status": "failed",
                "on_failure": "stop",
                "service_exec_parameters": {
                    "guard_role": "remediation_precondition",
                    "false_outcome": "cancel_downstream_no_remediation",
                },
                "service_exec_actual_outcome": {"is_firing": False},
            },
            {"service_exec_status": "canceled", "on_failure": "stop"},
        ]
    )

    assert terminal_status == "canceled"
    assert row_status == "failed"
    assert message is not None
    assert "no longer shows the alert firing" in message


def test_expediter_dispatch_logs_runtime_receipts() -> None:
    source = inspect.getsource(expediter_api)

    assert "Expediter executed service workload" in source
    assert "dish_ingredient_id" in source
    assert "service_exec_id" in source


def test_expediter_does_not_export_ad_hoc_poll_boundary() -> None:
    assert not hasattr(expediter_api, "expediter_dispatch_from_cook")
    assert not hasattr(expediter_api, "expediter_poll_from_cook")


@pytest.mark.asyncio
async def test_dispatch_order_retries_retryable_record_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    sleep_calls: list[float] = []
    db = _Session()

    async def _dispatch_once(**_kwargs: Any) -> OrderDispatchResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _operational_error(1020)
        return OrderDispatchResponse(status="dispatched", order_id=123, dish_id=456)

    async def _record_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(orders_api, "_dispatch_order_once", _dispatch_once)
    monkeypatch.setattr(orders_api.asyncio, "sleep", _record_sleep)
    monkeypatch.setattr(orders_api.random, "uniform", lambda _low, _high: 0)

    result = await dispatch_order(
        request=SimpleNamespace(state=SimpleNamespace(req_id="req-retry")),
        order_id=123,
        db=db,  # type: ignore[arg-type]
    )

    assert result.status == "dispatched"
    assert calls == 2
    assert db.rollback_count == 1
    assert sleep_calls == [0.05]


@pytest.mark.asyncio
@pytest.mark.parametrize("error_code", [1205, 1213])
async def test_dispatch_order_retries_retryable_lock_errors(
    monkeypatch: pytest.MonkeyPatch,
    error_code: int,
) -> None:
    calls = 0
    db = _Session()

    async def _dispatch_once(**_kwargs: Any) -> OrderDispatchResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _operational_error(error_code)
        return OrderDispatchResponse(status="dispatched", order_id=123, dish_id=456)

    async def _noop_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(orders_api, "_dispatch_order_once", _dispatch_once)
    monkeypatch.setattr(orders_api.asyncio, "sleep", _noop_sleep)
    monkeypatch.setattr(orders_api.random, "uniform", lambda _low, _high: 0)

    result = await dispatch_order(
        request=SimpleNamespace(state=SimpleNamespace(req_id="req-retry")),
        order_id=123,
        db=db,  # type: ignore[arg-type]
    )

    assert result.status == "dispatched"
    assert calls == 2
    assert db.rollback_count == 1


@pytest.mark.asyncio
async def test_dispatch_order_does_not_retry_non_retryable_operational_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    db = _Session()

    async def _dispatch_once(**_kwargs: Any) -> OrderDispatchResponse:
        nonlocal calls
        calls += 1
        raise _operational_error(1064)

    async def _fail_sleep(_seconds: float) -> None:
        raise AssertionError("sleep should not be called")

    monkeypatch.setattr(orders_api, "_dispatch_order_once", _dispatch_once)
    monkeypatch.setattr(orders_api.asyncio, "sleep", _fail_sleep)

    with pytest.raises(OperationalError):
        await dispatch_order(
            request=SimpleNamespace(state=SimpleNamespace(req_id="req-non-retry")),
            order_id=123,
            db=db,  # type: ignore[arg-type]
        )

    assert calls == 1
    assert db.rollback_count == 0


@pytest.mark.asyncio
async def test_dispatch_order_exhausts_retryable_operational_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    sleep_calls: list[float] = []
    db = _Session()

    async def _dispatch_once(**_kwargs: Any) -> OrderDispatchResponse:
        nonlocal calls
        calls += 1
        raise _operational_error(1020)

    async def _record_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(orders_api, "_dispatch_order_once", _dispatch_once)
    monkeypatch.setattr(orders_api.asyncio, "sleep", _record_sleep)
    monkeypatch.setattr(orders_api.random, "uniform", lambda _low, _high: 0)

    with pytest.raises(OperationalError):
        await dispatch_order(
            request=SimpleNamespace(state=SimpleNamespace(req_id="req-exhaust")),
            order_id=123,
            db=db,  # type: ignore[arg-type]
        )

    assert calls == orders_api.MAX_COOK_DISPATCH_ATTEMPTS
    assert db.rollback_count == orders_api.MAX_COOK_DISPATCH_ATTEMPTS
    assert sleep_calls == [0.05, 0.1]


@pytest.mark.asyncio
async def test_dispatch_order_uses_dish_plan_for_inherited_comms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order = SimpleNamespace(
        id=123,
        req_id="req-global-comms",
        processing_status="new",
        alert_group_name="host-down",
        remediation_outcome=None,
        raw_data={},
        clear_timeout_sec=None,
        clear_deadline_at=None,
        clear_timed_out_at=None,
        auto_close_eligible=False,
        is_active=True,
        updated_at=None,
    )
    recipe = SimpleNamespace(
        id=501,
        name="host-down",
        enabled=True,
        clear_timeout_sec=None,
        recipe_ingredients=[],
    )
    inherited_step = SimpleNamespace(id=701, ingredient=None)
    db = _DispatchDb([[order], [recipe], [], []])
    captured: dict[str, object] = {}

    @asynccontextmanager
    async def _noop_write_transaction(_db: object):
        yield

    def _seed(**kwargs: Any) -> list[object]:
        captured["extra_recipe_ingredients"] = kwargs.get("extra_recipe_ingredients")
        captured["recipe"] = kwargs["recipe"]
        return []

    async def _build_plan(_db: object, **kwargs: Any) -> object:
        captured["plan_recipe"] = kwargs["recipe"]
        captured["plan_order"] = kwargs["order"]
        return SimpleNamespace(
            recipe=kwargs["recipe"],
            inherited_recipe_ingredients=[inherited_step],
        )

    monkeypatch.setattr(orders_api, "_write_transaction", _noop_write_transaction)
    monkeypatch.setattr(orders_api, "build_dish_plan", _build_plan)
    monkeypatch.setattr(orders_api, "seed_dish_ingredients_for_phase", _seed)

    response = await orders_api._dispatch_order_once(
        request=SimpleNamespace(state=SimpleNamespace(req_id="req-global-comms")),
        order_id=123,
        db=db,  # type: ignore[arg-type]
    )

    assert response.status == "dispatched"
    assert captured["plan_recipe"] is recipe
    assert captured["plan_order"] is order
    assert captured["recipe"] is recipe
    assert captured["extra_recipe_ingredients"] == [inherited_step]


@pytest.mark.asyncio
async def test_dispatch_order_skips_global_comms_when_recipe_has_local_comms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order = SimpleNamespace(
        id=124,
        req_id="req-local-comms",
        processing_status="new",
        alert_group_name="host-down",
        remediation_outcome=None,
        raw_data={},
        clear_timeout_sec=None,
        clear_deadline_at=None,
        clear_timed_out_at=None,
        auto_close_eligible=False,
        is_active=True,
        updated_at=None,
    )
    local_comms = SimpleNamespace(
        id=702,
        ingredient=SimpleNamespace(ingredient_purpose="comms"),
        run_phase="firing",
        run_condition="always",
        service_payload={
            "context": {
                "poundcake_policy": {
                    "route_id": "local-route",
                    "service_type": "bakery",
                    "destination_target": "ops",
                    "provider_config": {},
                    "enabled": True,
                    "position": 1,
                }
            }
        },
    )
    recipe = SimpleNamespace(
        id=502,
        name="host-down",
        enabled=True,
        clear_timeout_sec=None,
        recipe_ingredients=[local_comms],
    )
    db = _DispatchDb([[order], [recipe], [], []])
    captured: dict[str, object] = {}

    @asynccontextmanager
    async def _noop_write_transaction(_db: object):
        yield

    def _seed(**kwargs: Any) -> list[object]:
        captured["extra_recipe_ingredients"] = kwargs.get("extra_recipe_ingredients")
        return []

    async def _build_plan(_db: object, **kwargs: Any) -> object:
        return SimpleNamespace(recipe=kwargs["recipe"], inherited_recipe_ingredients=[])

    monkeypatch.setattr(orders_api, "_write_transaction", _noop_write_transaction)
    monkeypatch.setattr(orders_api, "build_dish_plan", _build_plan)
    monkeypatch.setattr(orders_api, "seed_dish_ingredients_for_phase", _seed)

    response = await orders_api._dispatch_order_once(
        request=SimpleNamespace(state=SimpleNamespace(req_id="req-local-comms")),
        order_id=124,
        db=db,  # type: ignore[arg-type]
    )

    assert response.status == "dispatched"
    assert captured["extra_recipe_ingredients"] == []


@pytest.mark.asyncio
async def test_dispatch_order_skips_when_recipe_is_missing_even_if_global_comms_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order = SimpleNamespace(
        id=125,
        req_id="req-no-recipe",
        processing_status="new",
        alert_group_name="missing-recipe",
        remediation_outcome=None,
        raw_data={},
        clear_timeout_sec=30,
        clear_deadline_at="deadline",
        clear_timed_out_at="timed-out",
        auto_close_eligible=True,
        is_active=False,
        updated_at=None,
    )
    db = _DispatchDb([[order], []])

    @asynccontextmanager
    async def _noop_write_transaction(_db: object):
        yield

    def _unexpected_seed(**_kwargs: Any) -> list[object]:
        raise AssertionError("dish ingredients should not be seeded without a recipe")

    monkeypatch.setattr(orders_api, "_write_transaction", _noop_write_transaction)
    monkeypatch.setattr(orders_api, "seed_dish_ingredients_for_phase", _unexpected_seed)

    response = await orders_api._dispatch_order_once(
        request=SimpleNamespace(state=SimpleNamespace(req_id="req-no-recipe")),
        order_id=125,
        db=db,  # type: ignore[arg-type]
    )

    assert response.status == "skipped"
    assert response.reason == "No recipe for missing-recipe"


@pytest.mark.asyncio
async def test_dispatch_order_without_exact_recipe_does_not_use_fallback_recipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order = SimpleNamespace(
        id=225,
        req_id="req-no-exact-recipe",
        processing_status="new",
        alert_group_name="missing-recipe-with-catch-all",
        remediation_outcome=None,
        raw_data={},
        clear_timeout_sec=30,
        clear_deadline_at="deadline",
        clear_timed_out_at="timed-out",
        auto_close_eligible=True,
        is_active=False,
        updated_at=None,
    )
    db = _DispatchDb([[order], []])

    @asynccontextmanager
    async def _noop_write_transaction(_db: object):
        yield

    def _unexpected_seed(**_kwargs: Any) -> list[object]:
        raise AssertionError("dish ingredients should not be seeded without a recipe")

    monkeypatch.setattr(orders_api, "_write_transaction", _noop_write_transaction)
    monkeypatch.setattr(orders_api, "seed_dish_ingredients_for_phase", _unexpected_seed)

    response = await orders_api._dispatch_order_once(
        request=SimpleNamespace(state=SimpleNamespace(req_id="req-no-exact-recipe")),
        order_id=225,
        db=db,  # type: ignore[arg-type]
    )

    assert response.status == "skipped"
    assert response.reason == "No recipe for missing-recipe-with-catch-all"
    assert order.processing_status == "resolving"


@pytest.mark.asyncio
async def test_dispatch_order_resolving_phase_seeds_local_communication_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order = SimpleNamespace(
        id=126,
        req_id="req-resolving-local-comms",
        processing_status="resolving",
        alert_status="resolved",
        alert_group_name="host-down",
        remediation_outcome="succeeded",
        raw_data={},
        clear_timeout_sec=None,
        clear_deadline_at=None,
        clear_timed_out_at=None,
        auto_close_eligible=False,
        is_active=True,
        updated_at=None,
    )
    local_comms = SimpleNamespace(
        id=703,
        ingredient=SimpleNamespace(ingredient_purpose="comms"),
        run_phase="resolving",
        run_condition="resolved_after_success",
        service_payload={
            "context": {
                "poundcake_policy": {
                    "route_id": "local-route",
                    "service_type": "bakery",
                    "destination_target": "ops",
                    "provider_config": {},
                    "enabled": True,
                    "position": 1,
                }
            }
        },
    )
    recipe = SimpleNamespace(
        id=503,
        name="host-down",
        enabled=True,
        clear_timeout_sec=None,
        recipe_ingredients=[local_comms],
    )
    db = _DispatchDb([[order], [recipe], [], []])
    captured: dict[str, object] = {}

    @asynccontextmanager
    async def _noop_write_transaction(_db: object):
        yield

    def _seed(**kwargs: Any) -> list[object]:
        captured["phase"] = kwargs["phase"]
        captured["extra_recipe_ingredients"] = kwargs.get("extra_recipe_ingredients")
        captured["recipe"] = kwargs["recipe"]
        return []

    async def _build_plan(_db: object, **kwargs: Any) -> object:
        return SimpleNamespace(recipe=kwargs["recipe"], inherited_recipe_ingredients=[])

    monkeypatch.setattr(orders_api, "_write_transaction", _noop_write_transaction)
    monkeypatch.setattr(orders_api, "build_dish_plan", _build_plan)
    monkeypatch.setattr(orders_api, "seed_dish_ingredients_for_phase", _seed)

    response = await orders_api._dispatch_order_once(
        request=SimpleNamespace(state=SimpleNamespace(req_id="req-resolving-local-comms")),
        order_id=126,
        db=db,  # type: ignore[arg-type]
    )

    assert response.status == "dispatched"
    assert response.run_phase == "resolving"
    assert captured["phase"] == "resolving"
    assert captured["recipe"] is recipe
    assert captured["extra_recipe_ingredients"] == []


@pytest.mark.asyncio
async def test_dispatch_order_resolving_phase_uses_dish_plan_for_inherited_comms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order = SimpleNamespace(
        id=127,
        req_id="req-resolving-global-comms",
        processing_status="resolving",
        alert_status="resolved",
        alert_group_name="host-down",
        remediation_outcome="succeeded",
        raw_data={},
        clear_timeout_sec=None,
        clear_deadline_at=None,
        clear_timed_out_at=None,
        auto_close_eligible=False,
        is_active=True,
        updated_at=None,
    )
    recipe = SimpleNamespace(
        id=504,
        name="host-down",
        enabled=True,
        clear_timeout_sec=None,
        recipe_ingredients=[],
    )
    inherited_step = SimpleNamespace(id=704, ingredient=None)
    db = _DispatchDb([[order], [recipe], [], []])
    captured: dict[str, object] = {}

    @asynccontextmanager
    async def _noop_write_transaction(_db: object):
        yield

    def _seed(**kwargs: Any) -> list[object]:
        captured["phase"] = kwargs["phase"]
        captured["extra_recipe_ingredients"] = kwargs.get("extra_recipe_ingredients")
        return []

    async def _build_plan(_db: object, **kwargs: Any) -> object:
        return SimpleNamespace(
            recipe=kwargs["recipe"],
            inherited_recipe_ingredients=[inherited_step],
        )

    monkeypatch.setattr(orders_api, "_write_transaction", _noop_write_transaction)
    monkeypatch.setattr(orders_api, "build_dish_plan", _build_plan)
    monkeypatch.setattr(orders_api, "seed_dish_ingredients_for_phase", _seed)

    response = await orders_api._dispatch_order_once(
        request=SimpleNamespace(state=SimpleNamespace(req_id="req-resolving-global-comms")),
        order_id=127,
        db=db,  # type: ignore[arg-type]
    )

    assert response.status == "dispatched"
    assert response.run_phase == "resolving"
    assert captured["phase"] == "resolving"
    assert captured["extra_recipe_ingredients"] == [inherited_step]
