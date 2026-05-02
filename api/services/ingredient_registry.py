"""Shared manifest-driven ingredient registration helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.models import Ingredient
from api.schemas.schemas import IngredientTemplateRegistration
from api.types import JSONObject


class IngredientRegistrationConflictError(RuntimeError):
    """Raised when one registration batch contains duplicate identities."""


@dataclass(slots=True)
class IngredientRegistrationResult:
    """Resolved ingredient registration state for one registration batch."""

    rows: list[Ingredient]
    created: int
    unchanged: int
    retired: int

    @property
    def processed(self) -> int:
        return len(self.rows)

    def stats(self) -> JSONObject:
        return {
            "created": self.created,
            "unchanged": self.unchanged,
            "retired": self.retired,
            "processed": self.processed,
        }


def ingredient_identity(
    ingredient: IngredientTemplateRegistration | JSONObject,
) -> tuple[str, str, str, str]:
    if isinstance(ingredient, dict):
        return (
            str(ingredient.get("service_type") or "").strip().lower(),
            str(ingredient.get("service_exec") or "").strip(),
            str(ingredient.get("destination_target") or "").strip(),
            str(ingredient.get("task_key_template") or "").strip(),
        )
    return (
        ingredient.service_type,
        ingredient.service_exec,
        ingredient.destination_target or "",
        ingredient.task_key_template,
    )


def ingredient_contract_from_create(ingredient: IngredientTemplateRegistration) -> JSONObject:
    return {
        "service_type": ingredient.service_type,
        "service_exec": ingredient.service_exec,
        "destination_target": ingredient.destination_target or "",
        "task_key_template": ingredient.task_key_template,
        "service_payload_template": ingredient.service_payload_template,
        "payload_schema": ingredient.payload_schema,
        "service_exec_parameters": ingredient.service_exec_parameters,
        "default_expected_secs": ingredient.default_expected_secs,
        "default_timeout": ingredient.default_timeout,
        "service_exec_expected_outcome_default": ingredient.service_exec_expected_outcome_default,
        "ingredient_purpose": ingredient.ingredient_purpose,
        "is_blocking": ingredient.is_blocking,
        "retry_count": ingredient.retry_count,
        "retry_delay": ingredient.retry_delay,
        "on_failure": ingredient.on_failure,
    }


def ingredient_contract_from_row(ingredient: Ingredient) -> JSONObject:
    return {
        "service_type": ingredient.service_type,
        "service_exec": ingredient.service_exec,
        "destination_target": ingredient.destination_target or "",
        "task_key_template": ingredient.task_key_template,
        "service_payload_template": ingredient.service_payload_template,
        "payload_schema": ingredient.payload_schema,
        "service_exec_parameters": ingredient.service_exec_parameters,
        "default_expected_secs": ingredient.default_expected_secs,
        "default_timeout": ingredient.default_timeout,
        "service_exec_expected_outcome_default": ingredient.service_exec_expected_outcome_default,
        "ingredient_purpose": ingredient.ingredient_purpose,
        "is_blocking": ingredient.is_blocking,
        "retry_count": ingredient.retry_count,
        "retry_delay": ingredient.retry_delay,
        "on_failure": ingredient.on_failure,
    }


def ingredient_contracts_match(
    row: Ingredient,
    ingredient: IngredientTemplateRegistration,
) -> bool:
    return ingredient_contract_from_row(row) == ingredient_contract_from_create(ingredient)


def ingredient_row(ingredient: IngredientTemplateRegistration) -> Ingredient:
    return Ingredient(
        service_type=ingredient.service_type,
        service_exec=ingredient.service_exec,
        destination_target=ingredient.destination_target or "",
        task_key_template=ingredient.task_key_template,
        service_payload_template=ingredient.service_payload_template,
        payload_schema=ingredient.payload_schema,
        service_exec_parameters=ingredient.service_exec_parameters,
        default_expected_secs=ingredient.default_expected_secs,
        default_timeout=ingredient.default_timeout,
        service_exec_expected_outcome_default=ingredient.service_exec_expected_outcome_default,
        ingredient_purpose=ingredient.ingredient_purpose,
        is_active=True,
        is_blocking=ingredient.is_blocking,
        retry_count=ingredient.retry_count,
        retry_delay=ingredient.retry_delay,
        on_failure=ingredient.on_failure,
        deleted=False,
        deleted_at=None,
    )


def ingredient_identity_map(
    rows: list[Ingredient],
) -> dict[tuple[str, str, str, str], Ingredient]:
    return {
        (
            row.service_type,
            row.service_exec,
            row.destination_target or "",
            row.task_key_template,
        ): row
        for row in rows
    }


async def register_ingredient_templates(
    db: AsyncSession,
    ingredients: list[IngredientTemplateRegistration],
    *,
    flush: bool = False,
) -> IngredientRegistrationResult:
    identities = [ingredient_identity(ingredient) for ingredient in ingredients]
    if len(set(identities)) != len(identities):
        raise IngredientRegistrationConflictError("Duplicate service ingredient in request")
    if not identities:
        return IngredientRegistrationResult(rows=[], created=0, unchanged=0, retired=0)

    existing_result = await db.execute(
        select(Ingredient).where(
            tuple_(
                Ingredient.service_type,
                Ingredient.service_exec,
                Ingredient.destination_target,
                Ingredient.task_key_template,
            ).in_(identities),
            Ingredient.deleted.is_(False),
        )
    )
    active_by_identity: dict[tuple[str, str, str, str], Ingredient] = {}
    for row in existing_result.scalars().all():
        identity = (
            row.service_type,
            row.service_exec,
            row.destination_target or "",
            row.task_key_template,
        )
        if bool(row.is_active):
            active_by_identity.setdefault(identity, row)

    rows: list[Ingredient] = []
    created = 0
    unchanged = 0
    retired = 0
    now = datetime.now(timezone.utc)
    for ingredient in ingredients:
        identity = ingredient_identity(ingredient)
        existing = active_by_identity.get(identity)
        if existing is None:
            row = ingredient_row(ingredient)
            db.add(row)
            if flush:
                await db.flush()
            rows.append(row)
            created += 1
            continue
        if not ingredient_contracts_match(existing, ingredient):
            existing.is_active = False
            existing.updated_at = now
            row = ingredient_row(ingredient)
            db.add(row)
            if flush:
                await db.flush()
            rows.append(row)
            created += 1
            retired += 1
            continue
        rows.append(existing)
        unchanged += 1

    return IngredientRegistrationResult(
        rows=rows,
        created=created,
        unchanged=unchanged,
        retired=retired,
    )
