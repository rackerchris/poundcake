"""Unit tests for shared manifest-driven ingredient registration."""

from __future__ import annotations

import pytest

from api.models.models import Ingredient
from api.schemas.schemas import IngredientTemplateRegistration
from api.services.ingredient_registry import (
    IngredientRegistrationConflictError,
    ingredient_contract_from_row,
    ingredient_identity_map,
    register_ingredient_templates,
)


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


def _ingredient_create(row: Ingredient) -> IngredientTemplateRegistration:
    return IngredientTemplateRegistration.model_validate(ingredient_contract_from_row(row))


class _ScalarRowsResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def scalars(self) -> "_ScalarRowsResult":
        return self

    def all(self) -> list[object]:
        return self._rows


class _FakeDb:
    def __init__(self, rows: list[Ingredient]) -> None:
        self.rows = rows
        self.added: list[object] = []

    async def execute(self, _statement: object) -> _ScalarRowsResult:
        return _ScalarRowsResult(self.rows)

    async def flush(self) -> None:
        for index, row in enumerate(self.added, start=100):
            if isinstance(row, Ingredient) and row.id is None:
                row.id = index

    def add(self, row: object) -> None:
        self.added.append(row)


@pytest.mark.asyncio
async def test_registration_creates_active_revision_when_only_disabled_matches() -> None:
    retired = _ingredient()
    retired.is_active = False
    payload = _ingredient_create(retired)
    db = _FakeDb([retired])

    result = await register_ingredient_templates(db, [payload])

    assert result.created == 1
    assert result.unchanged == 0
    assert result.retired == 0
    assert result.rows == [db.added[0]]
    assert result.rows[0].is_active is True
    assert retired.is_active is False


@pytest.mark.asyncio
async def test_registration_reuses_active_matching_ingredient() -> None:
    existing = _ingredient()
    payload = _ingredient_create(existing)
    db = _FakeDb([existing])

    result = await register_ingredient_templates(db, [payload])

    assert result.created == 0
    assert result.unchanged == 1
    assert result.retired == 0
    assert result.rows == [existing]
    assert db.added == []


@pytest.mark.asyncio
async def test_registration_retires_active_drift_and_creates_revision() -> None:
    existing = _ingredient()
    payload_data = ingredient_contract_from_row(existing)
    payload_data["default_timeout"] = 60
    db = _FakeDb([existing])

    result = await register_ingredient_templates(
        db, [IngredientTemplateRegistration.model_validate(payload_data)]
    )

    assert result.created == 1
    assert result.unchanged == 0
    assert result.retired == 1
    assert existing.is_active is False
    assert result.rows == [db.added[0]]
    assert result.rows[0].is_active is True
    assert result.rows[0].default_timeout == 60


@pytest.mark.asyncio
async def test_registration_detects_duplicate_identities_in_one_batch() -> None:
    payload = _ingredient_create(_ingredient())
    db = _FakeDb([])

    with pytest.raises(IngredientRegistrationConflictError, match="Duplicate service ingredient"):
        await register_ingredient_templates(db, [payload, payload])


@pytest.mark.asyncio
async def test_registration_returns_identity_map() -> None:
    existing = _ingredient()
    payload = _ingredient_create(existing)
    db = _FakeDb([existing])

    result = await register_ingredient_templates(db, [payload])

    assert ingredient_identity_map(result.rows) == {
        ("dummy", "positive_result", "dummy", "dummy-positive-result"): existing
    }
