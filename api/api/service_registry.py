"""Service registry router for immutable service plugin templates."""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.api.auth import require_operator, require_reader, require_service
from api.core.database import get_db
from api.models.models import Ingredient
from api.schemas.schemas import (
    IngredientResponse,
    IngredientStatusResponse,
    IngredientTemplateRegistration,
)
from api.plugins.contract import (
    ServicePluginContractError,
    validate_payload_schema,
)
from api.services.ingredient_registry import (
    IngredientRegistrationConflictError,
    register_ingredient_templates,
)

router = APIRouter()


def _serialize_ingredient_status(ingredient: Ingredient) -> IngredientStatusResponse:
    return IngredientStatusResponse.model_validate(
        {
            "id": ingredient.id,
            "service_type": ingredient.service_type,
            "service_exec": ingredient.service_exec,
            "destination_target": ingredient.destination_target or "",
            "task_key_template": ingredient.task_key_template,
            "ingredient_purpose": ingredient.ingredient_purpose,
            "is_active": ingredient.is_active,
            "is_blocking": ingredient.is_blocking,
            "default_expected_secs": ingredient.default_expected_secs,
            "default_timeout": ingredient.default_timeout,
            "retry_count": ingredient.retry_count,
            "retry_delay": ingredient.retry_delay,
            "on_failure": ingredient.on_failure,
            "created_at": ingredient.created_at,
            "updated_at": ingredient.updated_at,
        }
    )


@router.post(
    "/internal/service-registry/ingredients/bulk",
    response_model=list[IngredientResponse],
)
async def register_internal_service_ingredients_bulk(
    response: Response,
    ingredients: list[IngredientTemplateRegistration],
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_service),
) -> list[IngredientResponse]:
    """Register plugin ingredient templates through the internal service boundary."""
    for ingredient in ingredients:
        try:
            validate_payload_schema(ingredient.payload_schema)
        except ServicePluginContractError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        result = await register_ingredient_templates(db, ingredients)
    except IngredientRegistrationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    response.headers["X-PoundCake-Created-Count"] = str(result.created)
    response.headers["X-PoundCake-Changed"] = "true" if result.created else "false"
    for row in result.rows:
        await db.refresh(row)
    return result.rows


@router.get("/service-registry/ingredients", response_model=List[IngredientResponse])
async def list_service_ingredients(
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_operator),
) -> list[Ingredient]:
    """List immutable service plugin ingredient templates."""
    result = await db.execute(select(Ingredient).where(Ingredient.deleted.is_(False)))
    return list(result.scalars().all())


@router.get("/service-registry/ingredients/status", response_model=List[IngredientStatusResponse])
async def list_service_ingredient_statuses(
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_reader),
) -> list[IngredientStatusResponse]:
    """List redacted service plugin ingredient status rows."""
    result = await db.execute(select(Ingredient).where(Ingredient.deleted.is_(False)))
    return [_serialize_ingredient_status(row) for row in result.scalars().all()]


@router.get("/service-registry/ingredients/{ingredient_id}", response_model=IngredientResponse)
async def get_service_ingredient(
    ingredient_id: int,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_operator),
) -> IngredientResponse:
    """Fetch one immutable service plugin ingredient template."""
    result = await db.execute(select(Ingredient).where(Ingredient.id == ingredient_id))
    ingredient = result.scalars().first()
    if ingredient is None or ingredient.deleted:
        raise HTTPException(status_code=404, detail="Service ingredient not found")
    return ingredient
