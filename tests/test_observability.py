"""Tests for mission-control observability feeds."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.dialects import mysql

from api.api.observability import _load_communication_activity


class _EmptyResult:
    def all(self) -> list[Any]:
        return []


class _CapturingDb:
    def __init__(self) -> None:
        self.statement: Any = None

    async def execute(self, statement: Any) -> _EmptyResult:
        self.statement = statement
        return _EmptyResult()


@pytest.mark.asyncio
async def test_communication_activity_only_loads_comms_ingredients() -> None:
    db = _CapturingDb()

    await _load_communication_activity(db, limit=25)  # type: ignore[arg-type]

    compiled = str(
        db.statement.compile(
            dialect=mysql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "ingredients.ingredient_purpose = 'comms'" in compiled
    assert "JOIN recipe_ingredients" in compiled
    assert "JOIN ingredients" in compiled


@pytest.mark.asyncio
async def test_communication_activity_keeps_health_check_comms_rows() -> None:
    db = _CapturingDb()

    await _load_communication_activity(  # type: ignore[arg-type]
        db,
        exclude_plugin_health_checks=True,
        limit=25,
    )

    compiled = str(
        db.statement.compile(
            dialect=mysql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "plugin-health-check" not in compiled
