"""Tests for expediter evidence classification and dish context assembly."""

from __future__ import annotations

from datetime import datetime

import pytest

from api.api import expediter
from api.models.models import Dish, DishIngredient


class _ScalarListResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        if isinstance(self._rows, list):
            return list(self._rows)
        return [self._rows] if self._rows is not None else []

    def first(self):
        if isinstance(self._rows, list):
            return self._rows[0] if self._rows else None
        return self._rows


class _ExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _ScalarListResult(self._rows)


class _FakeDb:
    """Returns queued results in call order: dish, same-dish rows, prior-dish rows."""

    def __init__(self, dish, completed_rows, prior_rows=None):
        self._results = [dish, completed_rows]
        if prior_rows is not None:
            self._results.append(prior_rows)
        self.calls = 0

    async def execute(self, _statement):
        result = self._results[self.calls] if self.calls < len(self._results) else []
        self.calls += 1
        return _ExecuteResult(result)


class _PriorTicketDb:
    """Returns queued results in call order: fingerprint, prior ticket rows."""

    def __init__(self, fingerprint, ticket_rows):
        self._results = [fingerprint, ticket_rows]
        self.calls = 0

    async def execute(self, _statement):
        result = self._results[self.calls] if self.calls < len(self._results) else []
        self.calls += 1
        return _ExecuteResult(result)


def _open_row(destination_target: str | None = "rackspace_core") -> DishIngredient:
    return DishIngredient(
        id=70,
        req_id="unit-test",
        dish_id=40,
        recipe_ingredient_id=11,
        task_key="bakery-comms-open",
        step_order=10,
        parallel_group=0,
        depth=0,
        service_type="bakery",
        service_exec="communication",
        destination_target=destination_target,
        service_exec_status="running",
        service_exec_parameters={"operation": "open"},
        deleted=False,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )


def test_is_bakery_open_step_classifies_open_operations() -> None:
    assert expediter._is_bakery_open_step(_open_row()) is True
    close_row = _open_row()
    close_row.service_exec_parameters = {"operation": "close"}
    assert expediter._is_bakery_open_step(close_row) is False
    other_row = _open_row()
    other_row.service_type = "discord"
    assert expediter._is_bakery_open_step(other_row) is False


@pytest.mark.asyncio
async def test_inject_prior_bakery_ticket_reuse_reopens_closed_ticket() -> None:
    prior_row = DishIngredient(
        id=1356,
        req_id="unit-test",
        dish_id=3596,
        service_type="bakery",
        service_exec="communication",
        service_exec_status="succeeded",
        service_exec_actual_outcome={
            "success": True,
            "_context_updates": {"bakery_comms_id": "TICKET-PRIOR"},
        },
        deleted=False,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )
    context: dict = {"destination_target": "rackspace_core"}

    await expediter._inject_prior_bakery_ticket_reuse(
        db=_PriorTicketDb("fp-1", [prior_row]),
        row=_open_row(),
        order_id=348,
        context=context,
    )

    assert context["ticket_id"] == "TICKET-PRIOR"
    assert context["communication_reuse_mode"] == "reopen"


@pytest.mark.asyncio
async def test_inject_prior_bakery_ticket_reuse_skips_when_ticket_id_present() -> None:
    context: dict = {"destination_target": "rackspace_core", "ticket_id": "TICKET-EXISTING"}

    await expediter._inject_prior_bakery_ticket_reuse(
        db=_PriorTicketDb("fp-1", []),
        row=_open_row(),
        order_id=348,
        context=context,
    )

    assert context["ticket_id"] == "TICKET-EXISTING"
    assert "communication_reuse_mode" not in context


@pytest.mark.asyncio
async def test_inject_prior_bakery_ticket_reuse_skips_non_ticket_targets() -> None:
    context: dict = {"destination_target": "discord"}

    await expediter._inject_prior_bakery_ticket_reuse(
        db=_PriorTicketDb("fp-1", []),
        row=_open_row(destination_target="discord"),
        order_id=348,
        context=context,
    )

    assert "ticket_id" not in context
    assert "communication_reuse_mode" not in context


@pytest.mark.asyncio
async def test_inject_prior_bakery_ticket_reuse_skips_when_no_prior_ticket() -> None:
    context: dict = {"destination_target": "rackspace_core"}

    await expediter._inject_prior_bakery_ticket_reuse(
        db=_PriorTicketDb("fp-1", []),
        row=_open_row(),
        order_id=348,
        context=context,
    )

    assert "ticket_id" not in context
    assert "communication_reuse_mode" not in context


def test_is_evidence_runtime_row_accepts_specialized_gather_roles() -> None:
    row = DishIngredient(
        id=1,
        req_id="unit-test",
        dish_id=2,
        service_type="alertmanager",
        service_exec="inspect",
        service_exec_parameters={"managed_role": "gather_alertmanager_evidence"},
        deleted=False,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )

    assert expediter._is_evidence_runtime_row(row) is True


@pytest.mark.asyncio
async def test_dish_execution_context_collects_specialized_evidence_rows() -> None:
    dish = Dish(
        id=2,
        order_id=99,
        run_phase="firing",
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )
    active_row = DishIngredient(
        id=50,
        req_id="unit-test",
        dish_id=2,
        recipe_ingredient_id=500,
        task_key="bakery-comms",
        step_order=70,
        parallel_group=0,
        depth=0,
        service_type="bakery",
        service_exec="communication",
        service_exec_status="running",
        deleted=False,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )
    alertmanager_row = DishIngredient(
        id=10,
        req_id="unit-test",
        dish_id=2,
        recipe_ingredient_id=100,
        task_key="alertmanager-inspect",
        step_order=20,
        parallel_group=0,
        depth=0,
        service_type="alertmanager",
        service_exec="inspect",
        service_exec_status="succeeded",
        service_exec_parameters={"managed_role": "gather_alertmanager_evidence"},
        service_exec_actual_outcome={"success": True},
        deleted=False,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )
    endpoint_row = DishIngredient(
        id=20,
        req_id="unit-test",
        dish_id=2,
        recipe_ingredient_id=200,
        task_key="stackstorm-workflow-execution",
        step_order=30,
        parallel_group=0,
        depth=0,
        service_type="stackstorm",
        service_exec="workflow_execution",
        service_exec_status="succeeded",
        service_exec_parameters={
            "managed_role": "gather_endpoint_evidence",
            "evidence_family": "blackbox",
        },
        service_exec_actual_outcome={"status": "succeeded"},
        deleted=False,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )

    context = await expediter._dish_execution_context(
        db=_FakeDb(dish, [alertmanager_row, endpoint_row]),
        row=active_row,
    )

    assert context["order_id"] == 99
    assert [entry["managed_role"] for entry in context["evidence"]] == [
        "gather_alertmanager_evidence",
        "gather_endpoint_evidence",
    ]
    assert [entry["service_type"] for entry in context["evidence"]] == [
        "alertmanager",
        "stackstorm",
    ]


@pytest.mark.asyncio
async def test_dish_execution_context_propagates_prior_dish_context_updates() -> None:
    """A ticket id created in the firing dish must reach the resolving dish's close step."""
    resolving_dish = Dish(
        id=3611,
        order_id=1374,
        run_phase="resolving",
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )
    close_row = DishIngredient(
        id=1371,
        req_id="unit-test",
        dish_id=3611,
        recipe_ingredient_id=12,
        task_key="bakery-comms-close",
        step_order=20,
        parallel_group=0,
        depth=0,
        service_type="bakery",
        service_exec="communication",
        service_exec_status="running",
        deleted=False,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )
    firing_create_row = DishIngredient(
        id=1356,
        req_id="unit-test",
        dish_id=3596,
        recipe_ingredient_id=11,
        task_key="bakery-comms-open",
        step_order=10,
        parallel_group=0,
        depth=0,
        service_type="bakery",
        service_exec="communication",
        service_exec_status="succeeded",
        service_exec_actual_outcome={
            "success": True,
            "status": "succeeded",
            "ticket_id": "b610e349-02c6-4e85-b1be-ad14c5ef0b02",
            "_context_updates": {"bakery_comms_id": "b610e349-02c6-4e85-b1be-ad14c5ef0b02"},
        },
        deleted=False,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )

    context = await expediter._dish_execution_context(
        db=_FakeDb(resolving_dish, [], [firing_create_row]),
        row=close_row,
    )

    assert context["order_id"] == 1374
    assert context["context_updates"] == {"bakery_comms_id": "b610e349-02c6-4e85-b1be-ad14c5ef0b02"}
    assert context["ingredients"] == []
