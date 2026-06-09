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
    def __init__(self, dish, completed_rows):
        self._results = [dish, completed_rows]

    async def execute(self, _statement):
        return _ExecuteResult(self._results.pop(0))


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
