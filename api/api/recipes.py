#  ____                        _  ____      _
# |  _ \ ___  _   _ _ __   __| |/ ___|__ _| | _____
# | |_) / _ \| | | | '_ \ / _` | |   / _` | |/ / _ \
# |  __/ (_) | |_| | | | | (_| | |__| (_| |   <  __/
# |_|   \___/ \__,_|_| |_|\__,_|\____\__,_|_|\_\___|
#
"""API endpoints for recipe management."""

from __future__ import annotations

from api.types import JSONObject

from datetime import datetime, timezone
from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from api.api.auth import require_operator, require_reader
from api.core.database import get_db
from api.core.config import get_settings
from api.core.logging import get_logger
from api.core.rate_limit import limiter
from api.models.models import Ingredient, Recipe, RecipeIngredient
from api.schemas.query_params import RecipeQueryParams, validate_query_params
from api.schemas.schemas import (
    DeleteResponse,
    RecipeCreate,
    RecipeDetailResponse,
    RecipeIngredientStatusResponse,
    RecipeStatusResponse,
    RecipeUpdate,
)
from api.services.communications_policy import (
    build_recipe_local_policy_step_specs,
    get_global_policy_routes,
    get_recipe_local_routes,
    get_visible_recipe_steps,
    global_policy_configured,
    is_communication_ingredient,
    is_communication_step,
    is_hidden_workflow_recipe,
    normalize_routes,
    policy_has_enabled_routes,
    route_payloads_for_response,
    replace_recipe_communication_steps,
)
from api.services.recipe_ingredient_cleanup import delete_recipe_ingredients_safely
from api.plugins.contract import (
    ServicePluginContractError,
    validate_service_operation,
    validate_service_payload_for_operation,
)

router = APIRouter()
logger = get_logger(__name__)


def _recipe_query():
    return select(Recipe).options(
        joinedload(Recipe.recipe_ingredients).joinedload(RecipeIngredient.ingredient)
    )


def _recipe_to_step_spec(step: RecipeIngredient) -> JSONObject:
    return {
        "ingredient_id": step.ingredient_id,
        "step_order": step.step_order,
        "on_success": step.on_success,
        "parallel_group": step.parallel_group,
        "depth": step.depth,
        "service_payload": step.service_payload,
        "service_exec_parameters_override": step.service_exec_parameters_override,
        "service_exec_expected_secs": step.service_exec_expected_secs,
        "service_exec_timeout": step.service_exec_timeout,
        "service_exec_expected_outcome": step.service_exec_expected_outcome,
        "run_phase": step.run_phase,
        "run_condition": step.run_condition,
    }


def _recipe_ingredient_row(
    *,
    recipe_id: int,
    spec: JSONObject,
    ingredient_id: int | None = None,
) -> RecipeIngredient:
    resolved_ingredient_id = ingredient_id or spec.get("ingredient_id")
    if resolved_ingredient_id is None:
        raise KeyError("ingredient_id")
    return RecipeIngredient(
        recipe_id=recipe_id,
        ingredient_id=resolved_ingredient_id,
        step_order=spec["step_order"],
        on_success=spec.get("on_success", "continue"),
        parallel_group=spec.get("parallel_group", 0),
        depth=spec.get("depth", 0),
        service_payload=spec.get("service_payload"),
        service_exec_parameters_override=spec.get("service_exec_parameters_override"),
        service_exec_expected_secs=spec.get("service_exec_expected_secs"),
        service_exec_timeout=spec.get("service_exec_timeout"),
        service_exec_expected_outcome=spec.get("service_exec_expected_outcome"),
        run_phase=spec.get("run_phase", "both"),
        run_condition=spec.get("run_condition", "always"),
    )


def _queue_recipe_steps(db: AsyncSession, *, recipe_id: int, step_specs: list[JSONObject]) -> None:
    for spec in step_specs:
        db.add(_recipe_ingredient_row(recipe_id=recipe_id, spec=spec))


async def _validate_ingredient_ids(
    db: AsyncSession, *, step_specs: list[JSONObject]
) -> dict[int, Ingredient]:
    ingredient_ids = [int(item["ingredient_id"]) for item in step_specs]
    if not ingredient_ids:
        return {}
    result = await db.execute(select(Ingredient).where(Ingredient.id.in_(ingredient_ids)))
    ingredients = result.scalars().all()
    found_ids = {ingredient.id for ingredient in ingredients}
    missing = [ingredient_id for ingredient_id in ingredient_ids if ingredient_id not in found_ids]
    if missing:
        raise HTTPException(status_code=404, detail=f"Missing ingredients: {missing}")
    return {ingredient.id: ingredient for ingredient in ingredients}


def _inactive_ingredient_ids(recipe: Recipe) -> list[int]:
    return sorted(
        {
            int(step.ingredient_id)
            for step in recipe.recipe_ingredients
            if step.ingredient is not None and not bool(getattr(step.ingredient, "is_active", True))
        }
    )


def _validate_active_ingredients(
    ingredients_by_id: dict[int, Ingredient], *, allow_inactive: bool
) -> None:
    if allow_inactive:
        return
    inactive = sorted(
        ingredient_id
        for ingredient_id, ingredient in ingredients_by_id.items()
        if not bool(getattr(ingredient, "is_active", True))
    )
    if inactive:
        raise HTTPException(
            status_code=409,
            detail=f"Inactive ingredients cannot be used to build recipes: {inactive}",
        )


def _validate_non_communication_ingredients(ingredients_by_id: dict[int, Ingredient]) -> None:
    disallowed = sorted(
        ingredient_id
        for ingredient_id, ingredient in ingredients_by_id.items()
        if is_communication_ingredient(ingredient)
    )
    if disallowed:
        raise HTTPException(
            status_code=409,
            detail=(
                "Communication ingredients cannot be persisted in recipe_ingredients; "
                "use communications.mode='local' routes or inherited global communications: "
                f"{disallowed}"
            ),
        )


def _resolved_service_payload(ingredient: Ingredient, spec: JSONObject) -> Any:
    base = dict(getattr(ingredient, "service_payload_template", None) or {})
    form_payload = spec.get("service_payload")
    if form_payload is not None and not isinstance(form_payload, dict):
        return form_payload
    if isinstance(form_payload, dict):
        base.update(form_payload)
    return base


def _resolved_service_exec_parameters(
    ingredient: Ingredient, spec: JSONObject
) -> JSONObject | None:
    base = dict(getattr(ingredient, "service_exec_parameters", None) or {})
    overrides = spec.get("service_exec_parameters_override")
    if isinstance(overrides, dict):
        base.update(overrides)
    return base or None


def _validate_service_payloads(
    ingredients_by_id: dict[int, Ingredient], *, step_specs: list[JSONObject]
) -> None:
    for spec in step_specs:
        if bool(spec.get("service_payload_from_order")):
            continue
        ingredient = ingredients_by_id.get(int(spec["ingredient_id"]))
        if ingredient is None:
            continue
        schema = getattr(ingredient, "payload_schema", None)
        if schema is None:
            continue
        try:
            validate_service_payload_for_operation(
                _resolved_service_payload(ingredient, spec),
                schema,
                _resolved_service_exec_parameters(ingredient, spec),
            )
        except ServicePluginContractError as exc:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"recipe_ingredient ingredient_id={spec['ingredient_id']} "
                    f"service_payload invalid: {exc}"
                ),
            ) from exc


def _validate_service_operations(
    ingredients_by_id: dict[int, Ingredient], *, step_specs: list[JSONObject]
) -> None:
    for spec in step_specs:
        ingredient = ingredients_by_id.get(int(spec["ingredient_id"]))
        if ingredient is None:
            continue
        try:
            validate_service_operation(_resolved_service_exec_parameters(ingredient, spec))
        except ServicePluginContractError as exc:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"recipe_ingredient ingredient_id={spec['ingredient_id']} "
                    f"service_exec_parameters_override invalid: {exc}"
                ),
            ) from exc


async def _serialize_recipe(db: AsyncSession, recipe: Recipe) -> JSONObject:
    visible_steps = get_visible_recipe_steps(recipe)
    local_routes = get_recipe_local_routes(recipe)
    if local_routes:
        communications = route_payloads_for_response(
            mode="local",
            effective_source="local" if policy_has_enabled_routes(local_routes) else None,
            routes=local_routes,
        )
    else:
        global_routes = await get_global_policy_routes(db)
        communications = route_payloads_for_response(
            mode="inherit",
            effective_source="global" if policy_has_enabled_routes(global_routes) else None,
            routes=global_routes,
        )

    inactive_ingredient_ids = _inactive_ingredient_ids(recipe)
    return {
        "id": recipe.id,
        "name": recipe.name,
        "description": recipe.description,
        "enabled": recipe.enabled,
        "clear_timeout_sec": recipe.clear_timeout_sec,
        "created_at": recipe.created_at,
        "updated_at": recipe.updated_at,
        "deleted": recipe.deleted,
        "deleted_at": recipe.deleted_at,
        "recipe_ingredients": visible_steps,
        "communications": communications,
        "can_execute": len(inactive_ingredient_ids) == 0,
        "inactive_ingredient_ids": inactive_ingredient_ids,
    }


async def _serialize_recipe_status(db: AsyncSession, recipe: Recipe) -> RecipeStatusResponse:
    visible_steps = get_visible_recipe_steps(recipe)
    local_routes = get_recipe_local_routes(recipe)
    if local_routes:
        route_count = len(local_routes)
    else:
        route_count = len(await get_global_policy_routes(db))
    inactive_ingredient_ids = _inactive_ingredient_ids(recipe)
    return RecipeStatusResponse.model_validate(
        {
            "id": recipe.id,
            "name": recipe.name,
            "description": recipe.description,
            "enabled": recipe.enabled,
            "clear_timeout_sec": recipe.clear_timeout_sec,
            "can_execute": len(inactive_ingredient_ids) == 0,
            "inactive_ingredient_count": len(inactive_ingredient_ids),
            "step_count": len(visible_steps),
            "communication_route_count": route_count,
            "created_at": recipe.created_at,
            "updated_at": recipe.updated_at,
        }
    )


def _serialize_recipe_ingredient_status(step: RecipeIngredient) -> RecipeIngredientStatusResponse:
    ingredient = getattr(step, "ingredient", None)
    return RecipeIngredientStatusResponse.model_validate(
        {
            "id": step.id,
            "recipe_id": step.recipe_id,
            "ingredient_id": step.ingredient_id,
            "step_order": step.step_order,
            "on_success": step.on_success,
            "parallel_group": step.parallel_group,
            "depth": step.depth,
            "run_phase": step.run_phase,
            "run_condition": step.run_condition,
            "service_type": getattr(ingredient, "service_type", None),
            "service_exec": getattr(ingredient, "service_exec", None),
            "task_key_template": getattr(ingredient, "task_key_template", None),
            "ingredient_purpose": getattr(ingredient, "ingredient_purpose", None),
            "ingredient_is_active": bool(getattr(ingredient, "is_active", True)),
            "ingredient_is_blocking": bool(getattr(ingredient, "is_blocking", True)),
            "expected_secs": step.service_exec_expected_secs
            or getattr(ingredient, "default_expected_secs", None),
            "timeout_secs": step.service_exec_timeout
            or getattr(ingredient, "default_timeout", None),
        }
    )


async def _validate_effective_communications(
    db: AsyncSession,
    *,
    enabled: bool,
    communications_mode: str,
    local_routes: list[Any],
) -> None:
    if not enabled:
        return
    if communications_mode == "local":
        if not policy_has_enabled_routes(local_routes):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Enabled workflows must define at least one enabled workflow-specific "
                    "communication route when using local communications."
                ),
            )
        return

    if not await global_policy_configured(db):
        raise HTTPException(
            status_code=400,
            detail=(
                "Enabled workflows must inherit a configured global communications policy "
                "or define workflow-specific communications."
            ),
        )


def _communications_payload_mode(payload: RecipeCreate | RecipeUpdate | None) -> str | None:
    if payload is None:
        return None
    communications = getattr(payload, "communications", None)
    if communications is None:
        return None
    return communications.mode


def _communications_payload_routes(
    payload: RecipeCreate | RecipeUpdate | None,
) -> list[JSONObject] | None:
    if payload is None:
        return None
    communications = getattr(payload, "communications", None)
    if communications is None:
        return None
    return [item.model_dump() for item in communications.routes]


def _step_specs_from_payload(step_items: list[JSONObject]) -> list[JSONObject]:
    return [
        {
            "ingredient_id": item["ingredient_id"],
            "step_order": item["step_order"],
            "on_success": item.get("on_success", "continue"),
            "parallel_group": item.get("parallel_group", 0),
            "depth": item.get("depth", 0),
            "service_payload": item.get("service_payload"),
            "service_exec_parameters_override": item.get("service_exec_parameters_override"),
            "service_exec_expected_secs": item.get("service_exec_expected_secs"),
            "service_exec_timeout": item.get("service_exec_timeout"),
            "service_exec_expected_outcome": item.get("service_exec_expected_outcome"),
            "run_phase": item.get("run_phase", "both"),
            "run_condition": item.get("run_condition", "always"),
            "service_payload_from_order": item.get("service_payload_from_order", False),
        }
        for item in step_items
    ]


@router.post("/recipes/", response_model=RecipeDetailResponse, status_code=201)
async def create_recipe(
    request: Request,
    recipe: RecipeCreate,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_operator),
) -> RecipeDetailResponse:
    """Create a new recipe with remediation/utility steps and optional local comms."""
    req_id = request.state.req_id
    logger.info("Creating recipe", extra={"req_id": req_id, "recipe_name": recipe.name})

    visible_step_specs = _step_specs_from_payload(
        [item.model_dump() for item in recipe.recipe_ingredients]
    )
    communications_mode = recipe.communications.mode
    local_routes = normalize_routes(_communications_payload_routes(recipe) or [])

    async with db.begin():
        ingredients_by_id = await _validate_ingredient_ids(db, step_specs=visible_step_specs)
        _validate_active_ingredients(ingredients_by_id, allow_inactive=False)
        _validate_non_communication_ingredients(ingredients_by_id)
        _validate_service_payloads(ingredients_by_id, step_specs=visible_step_specs)
        _validate_service_operations(ingredients_by_id, step_specs=visible_step_specs)
        await _validate_effective_communications(
            db,
            enabled=recipe.enabled,
            communications_mode=communications_mode,
            local_routes=local_routes,
        )
        result = await db.execute(select(Recipe).where(Recipe.name == recipe.name))
        existing = result.scalars().first()
        if existing:
            raise HTTPException(status_code=400, detail=f"Recipe '{recipe.name}' already exists")

        db_recipe = Recipe(
            name=recipe.name,
            description=recipe.description,
            enabled=recipe.enabled,
            clear_timeout_sec=recipe.clear_timeout_sec,
        )
        db.add(db_recipe)
        await db.flush()

        _queue_recipe_steps(db, recipe_id=db_recipe.id, step_specs=visible_step_specs)
        if communications_mode == "local":
            _, managed_specs = build_recipe_local_policy_step_specs(
                recipe_id=db_recipe.id,
                routes=local_routes,
            )
            await replace_recipe_communication_steps(
                db,
                recipe=db_recipe,
                step_specs=managed_specs,
            )

    result = await db.execute(_recipe_query().where(Recipe.name == recipe.name))
    db_recipe = result.unique().scalars().first()
    if db_recipe is None:
        raise HTTPException(status_code=500, detail="Recipe retrieval failed after create")
    return await _serialize_recipe(db, db_recipe)


@limiter.limit(get_settings().rate_limit_default)
@router.get("/recipes/", response_model=List[RecipeDetailResponse])
async def list_recipes(
    request: Request,
    params: RecipeQueryParams = Depends(validate_query_params(RecipeQueryParams)),
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_reader),
):
    """List user-facing workflows with communications summary."""
    _ = request.state.req_id
    query = _recipe_query()
    if params.name is not None:
        query = query.where(Recipe.name == params.name)
    if params.enabled is not None:
        query = query.where(Recipe.enabled == params.enabled)
    query = query.limit(params.limit).offset(params.offset)
    result = await db.execute(query)
    recipes = [
        recipe
        for recipe in result.unique().scalars().all()
        if not is_hidden_workflow_recipe(recipe)
    ]
    return [await _serialize_recipe(db, recipe) for recipe in recipes]


@limiter.limit(get_settings().rate_limit_default)
@router.get("/recipes/status", response_model=List[RecipeStatusResponse])
async def list_recipe_statuses(
    request: Request,
    params: RecipeQueryParams = Depends(validate_query_params(RecipeQueryParams)),
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_reader),
) -> list[RecipeStatusResponse]:
    """List redacted recipe statuses for reporting and selection views."""
    _ = request.state.req_id
    query = _recipe_query()
    if params.name is not None:
        query = query.where(Recipe.name == params.name)
    if params.enabled is not None:
        query = query.where(Recipe.enabled == params.enabled)
    query = query.limit(params.limit).offset(params.offset)
    result = await db.execute(query)
    recipes = [
        recipe
        for recipe in result.unique().scalars().all()
        if not is_hidden_workflow_recipe(recipe)
    ]
    return [await _serialize_recipe_status(db, recipe) for recipe in recipes]


@limiter.limit(get_settings().rate_limit_default)
@router.get("/recipes/{recipe_id}/status", response_model=RecipeStatusResponse)
async def get_recipe_status(
    request: Request,
    recipe_id: int,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_reader),
):
    """Get redacted status for a workflow."""
    result = await db.execute(_recipe_query().where(Recipe.id == recipe_id))
    recipe = result.unique().scalars().first()
    if not recipe or is_hidden_workflow_recipe(recipe):
        raise HTTPException(status_code=404, detail="Recipe not found")
    return await _serialize_recipe_status(db, recipe)


@limiter.limit(get_settings().rate_limit_default)
@router.get(
    "/recipes/{recipe_id}/ingredient-status",
    response_model=List[RecipeIngredientStatusResponse],
)
async def list_recipe_ingredient_statuses(
    request: Request,
    recipe_id: int,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_reader),
) -> list[RecipeIngredientStatusResponse]:
    """List redacted recipe step topology/status for reporting views."""
    result = await db.execute(_recipe_query().where(Recipe.id == recipe_id))
    recipe = result.unique().scalars().first()
    if not recipe or is_hidden_workflow_recipe(recipe):
        raise HTTPException(status_code=404, detail="Recipe not found")
    visible_steps = get_visible_recipe_steps(recipe)
    return [_serialize_recipe_ingredient_status(step) for step in visible_steps]


@router.get("/recipes/{recipe_id}", response_model=RecipeDetailResponse)
async def get_recipe(
    recipe_id: int,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_operator),
):
    """Get a workflow with non-communications steps and effective communications settings."""
    result = await db.execute(_recipe_query().where(Recipe.id == recipe_id))
    recipe = result.unique().scalars().first()
    if not recipe or is_hidden_workflow_recipe(recipe):
        raise HTTPException(status_code=404, detail="Recipe not found")
    return await _serialize_recipe(db, recipe)


@router.get("/recipes/by-name/{recipe_name}", response_model=RecipeDetailResponse)
async def get_recipe_by_name(
    recipe_name: str,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_operator),
):
    """Get a workflow by name."""
    result = await db.execute(_recipe_query().where(Recipe.name == recipe_name))
    recipe = result.unique().scalars().first()
    if not recipe or is_hidden_workflow_recipe(recipe):
        raise HTTPException(status_code=404, detail=f"Recipe '{recipe_name}' not found")
    return await _serialize_recipe(db, recipe)


@router.delete("/recipes/{recipe_id}", response_model=DeleteResponse)
async def delete_recipe(
    request: Request,
    recipe_id: int,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_operator),
) -> DeleteResponse:
    """Disable a workflow while preserving recipe steps and historical dish links."""
    req_id = request.state.req_id
    logger.info("Disabling recipe", extra={"req_id": req_id, "recipe_id": recipe_id})

    async with db.begin():
        result = await db.execute(select(Recipe).where(Recipe.id == recipe_id).with_for_update())
        recipe = result.unique().scalars().first()
        if not recipe or is_hidden_workflow_recipe(recipe):
            raise HTTPException(status_code=404, detail="Recipe not found")
        recipe_name = recipe.name
        recipe.enabled = False
        recipe.deleted = False
        recipe.deleted_at = None
        recipe.updated_at = datetime.now(timezone.utc)
    return DeleteResponse(
        status="disabled", id=recipe_id, message=f"Recipe '{recipe_name}' disabled successfully"
    )


@router.put("/recipes/{recipe_id}", response_model=RecipeDetailResponse)
@router.patch("/recipes/{recipe_id}", response_model=RecipeDetailResponse)
async def update_recipe(
    request: Request,
    recipe_id: int,
    payload: RecipeUpdate,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_operator),
):
    """Update a workflow, preserving comms when omitted and normalizing local comms when supplied."""
    recipe: Recipe | None = None
    async with db.begin():
        result = await db.execute(_recipe_query().where(Recipe.id == recipe_id).with_for_update())
        recipe = result.unique().scalars().first()
        if not recipe or is_hidden_workflow_recipe(recipe):
            raise HTTPException(status_code=404, detail="Recipe not found")

        existing_visible_specs = [
            _recipe_to_step_spec(step) for step in get_visible_recipe_steps(recipe)
        ]
        existing_comm_specs = [
            _recipe_to_step_spec(step)
            for step in recipe.recipe_ingredients
            if is_communication_step(step)
        ]
        current_local_routes = get_recipe_local_routes(recipe)

        update_data = payload.model_dump(exclude_unset=True)
        recipe_ingredients = update_data.pop("recipe_ingredients", None)
        communications = update_data.pop("communications", None)
        for key, value in update_data.items():
            setattr(recipe, key, value)

        final_visible_specs = existing_visible_specs
        if recipe_ingredients is not None:
            final_visible_specs = _step_specs_from_payload(recipe_ingredients)
            ingredients_by_id = await _validate_ingredient_ids(db, step_specs=final_visible_specs)
            _validate_active_ingredients(ingredients_by_id, allow_inactive=False)
            _validate_non_communication_ingredients(ingredients_by_id)
            _validate_service_payloads(ingredients_by_id, step_specs=final_visible_specs)
            _validate_service_operations(ingredients_by_id, step_specs=final_visible_specs)

        final_communications_mode = "local" if current_local_routes else "inherit"
        final_local_routes = current_local_routes
        final_comm_specs = existing_comm_specs
        if communications is not None:
            final_communications_mode = communications["mode"]
            if final_communications_mode == "local":
                final_local_routes, managed_specs = build_recipe_local_policy_step_specs(
                    recipe_id=recipe.id,
                    routes=communications["routes"],
                )
                final_comm_specs = [
                    {
                        "managed_spec": spec,
                    }
                    for spec in managed_specs
                ]
            else:
                final_local_routes = []
                final_comm_specs = []

        await _validate_effective_communications(
            db,
            enabled=bool(recipe.enabled),
            communications_mode=final_communications_mode,
            local_routes=final_local_routes,
        )

        if recipe_ingredients is not None or communications is not None:
            await delete_recipe_ingredients_safely(db, recipe_id=recipe.id)
            recipe.recipe_ingredients = []
            _queue_recipe_steps(db, recipe_id=recipe.id, step_specs=final_visible_specs)
            managed_specs = [
                spec["managed_spec"]
                for spec in final_comm_specs
                if isinstance(spec, dict) and "managed_spec" in spec
            ]
            if managed_specs:
                await replace_recipe_communication_steps(
                    db,
                    recipe=recipe,
                    step_specs=managed_specs,
                )
            else:
                for spec in final_comm_specs:
                    db.add(_recipe_ingredient_row(recipe_id=recipe.id, spec=spec))
            await db.flush()

        recipe.updated_at = datetime.now(timezone.utc)

    if recipe is None:
        raise HTTPException(status_code=500, detail="Recipe update failed")

    result = await db.execute(
        _recipe_query().where(Recipe.id == recipe_id).execution_options(populate_existing=True)
    )
    updated_recipe = result.unique().scalars().first()
    if updated_recipe is None:
        raise HTTPException(status_code=500, detail="Recipe update failed")
    return await _serialize_recipe(db, updated_recipe)
