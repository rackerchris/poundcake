"""Managed global and workflow-local communications policy helpers."""

from __future__ import annotations

from api.types import JSONObject

import re
import uuid
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from api.models.models import Ingredient, Recipe, RecipeIngredient, ServicePlugin
from api.plugins.catalog import build_enabled_plugin_capability_catalog
from api.plugins.contract import validate_service_payload_for_operation
from api.services.capability_resolution import (
    resolve_active_capability_ingredient,
    select_communication_capability,
)
from api.services.communications import (
    normalize_destination_target,
    normalize_destination_type,
    normalize_route_provider_config,
)
from api.core.config import get_settings
from api.core.logging import get_logger
from api.services.recipe_ingredient_cleanup import detach_recipe_ingredient_ids_safely

logger = get_logger(__name__)

MANAGED_TASK_PREFIX = "pcmcomms."
MANAGED_RECIPE_NAME_GLOBAL = "pcm-policy-global"
MANAGED_DESCRIPTION_GLOBAL = "[managed-comms:global]"
MANAGED_DESCRIPTION_FALLBACK = "[managed-comms:fallback]"
POLICY_METADATA_KEY = "poundcake_policy"

MATCHED_ROUTE_EVENTS = (
    ("remediation_failure_open", "open", "resolving", "resolved_after_failure", 1000),
    ("resolved_success_open", "open", "resolving", "resolved_after_success", 2000),
    ("resolved_success_close", "close", "resolving", "resolved_after_success", 2001),
    ("resolved_failure_notify", "notify", "resolving", "resolved_after_failure", 2100),
    ("resolved_timeout_notify", "notify", "resolving", "resolved_after_timeout", 2200),
)

FALLBACK_ROUTE_EVENTS = (
    ("fallback_open", "open", "firing", "always", 1000),
    ("fallback_close", "close", "resolving", "resolved_after_no_remediation", 2000),
)


@dataclass(slots=True)
class CommunicationRoute:
    id: str
    label: str
    service_type: str
    destination_target: str
    provider_config: JSONObject
    enabled: bool
    position: int


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return cleaned or "route"


def _coerce_route_id(
    value: Any, *, service_type: str, destination_target: str, position: int
) -> str:
    raw = str(value or "").strip()
    if raw:
        return raw
    base = "-".join(
        part
        for part in (
            _slug(service_type),
            _slug(destination_target or "default"),
            str(position),
        )
        if part
    )
    return f"{base}-{uuid.uuid4().hex[:8]}"


def _metadata_from_payload(service_payload: JSONObject | None) -> JSONObject:
    if not isinstance(service_payload, dict):
        return {}
    context = service_payload.get("context")
    if not isinstance(context, dict):
        return {}
    metadata = context.get(POLICY_METADATA_KEY)
    return metadata if isinstance(metadata, dict) else {}


def _step_service_payload(recipe_ingredient: RecipeIngredient | Any) -> JSONObject | None:
    payload = getattr(recipe_ingredient, "service_payload", None)
    if isinstance(payload, dict):
        return payload
    ingredient = getattr(recipe_ingredient, "ingredient", None)
    template = getattr(ingredient, "service_payload_template", None) if ingredient else None
    return template if isinstance(template, dict) else None


def is_communication_ingredient(ingredient: Ingredient | None) -> bool:
    if ingredient is None:
        return False
    return str(getattr(ingredient, "ingredient_purpose", "") or "").strip().lower() == "comms"


def is_communication_step(recipe_ingredient: RecipeIngredient | Any) -> bool:
    return is_communication_ingredient(getattr(recipe_ingredient, "ingredient", None))


def _operation_from_parameters(parameters: Any) -> str:
    if not isinstance(parameters, dict):
        return ""
    return str(parameters.get("operation") or "").strip().lower()


def _allowed_operations_from_parameters(parameters: Any) -> set[str]:
    if not isinstance(parameters, dict):
        return set()
    raw = parameters.get("allowed_operations")
    if not isinstance(raw, list):
        return set()
    return {str(item).strip().lower() for item in raw if str(item).strip()}


def _ingredient_supports_comms_operation(ingredient: Ingredient, operation: str) -> bool:
    normalized = operation.strip().lower()
    if str(ingredient.service_exec or "").strip().lower() == normalized:
        return True
    parameters = ingredient.service_exec_parameters or {}
    if _operation_from_parameters(parameters) == normalized:
        return True
    return normalized in _allowed_operations_from_parameters(parameters)


def _merged_step_parameters(recipe_ingredient: RecipeIngredient | Any) -> JSONObject:
    ingredient = getattr(recipe_ingredient, "ingredient", None)
    base = dict(
        (getattr(ingredient, "service_exec_parameters", None) if ingredient else None) or {}
    )
    overrides = getattr(recipe_ingredient, "service_exec_parameters_override", None)
    if isinstance(overrides, dict):
        base.update(overrides)
    return base


def is_hidden_workflow_recipe(recipe: Recipe | None) -> bool:
    if recipe is None:
        return False
    if (recipe.name or "") == MANAGED_RECIPE_NAME_GLOBAL:
        return True
    settings = get_settings()
    catch_all_name = str(settings.catch_all_recipe_name or "").strip()
    if catch_all_name and (recipe.name or "") == catch_all_name:
        return True
    return (recipe.description or "").startswith(MANAGED_DESCRIPTION_FALLBACK)


def is_route_available_for_update(
    *,
    order: Any,
    service_type: str,
    destination_target: str,
) -> bool:
    return True


def _route_from_metadata(metadata: JSONObject) -> CommunicationRoute | None:
    route_id = str(metadata.get("route_id") or "").strip()
    service_type = normalize_destination_type(metadata.get("service_type"))
    if not route_id or not service_type:
        return None
    return CommunicationRoute(
        id=route_id,
        label=str(metadata.get("label") or "").strip()
        or titleize_route(service_type, metadata.get("destination_target")),
        service_type=service_type,
        destination_target=normalize_destination_target(metadata.get("destination_target")),
        provider_config=normalize_route_provider_config(
            service_type,
            metadata.get("provider_config"),
            require_required=False,
        ),
        enabled=bool(metadata.get("enabled", True)),
        position=int(metadata.get("position") or 0),
    )


def titleize_route(service_type: str, destination_target: Any) -> str:
    target = normalize_destination_type(service_type).replace("_", " ").title()
    destination = normalize_destination_target(destination_target)
    return f"{target} - {destination}" if destination else target


def normalize_routes(
    routes: list[JSONObject] | list[CommunicationRoute],
) -> list[CommunicationRoute]:
    normalized: list[CommunicationRoute] = []
    for index, item in enumerate(routes, start=1):
        if isinstance(item, dict):
            raw = item
        elif is_dataclass(item):
            raw = asdict(item)
        else:
            raw = getattr(item, "__dict__", {})
        service_type = normalize_destination_type(raw.get("service_type"))
        if not service_type:
            continue
        destination_target = normalize_destination_target(raw.get("destination_target"))
        position = int(raw.get("position") or index)
        route = CommunicationRoute(
            id=_coerce_route_id(
                raw.get("id"),
                service_type=service_type,
                destination_target=destination_target,
                position=position,
            ),
            label=str(raw.get("label") or "").strip()
            or titleize_route(service_type, destination_target),
            service_type=service_type,
            destination_target=destination_target,
            provider_config=normalize_route_provider_config(
                service_type,
                raw.get("provider_config"),
            ),
            enabled=bool(raw.get("enabled", True)),
            position=position,
        )
        normalized.append(route)
    normalized.sort(key=lambda item: (item.position, item.label.lower(), item.service_type))
    for index, route in enumerate(normalized, start=1):
        route.position = index
    return normalized


def _managed_task_key(
    *,
    scope: str,
    owner_key: str,
    route_id: str,
    event_name: str,
) -> str:
    return f"{MANAGED_TASK_PREFIX}{scope}.{owner_key}.{route_id}.{event_name}"


def _managed_step_key_from_metadata(metadata: JSONObject) -> str | None:
    scope = str(metadata.get("scope") or "").strip()
    owner_key = str(metadata.get("owner_key") or "").strip()
    route_id = str(metadata.get("route_id") or "").strip()
    event_name = str(metadata.get("event") or "").strip()
    if not scope or not owner_key or not route_id or not event_name:
        return None
    return _managed_task_key(
        scope=scope,
        owner_key=owner_key,
        route_id=route_id,
        event_name=event_name,
    )


def _managed_step_key_from_recipe_ingredient(
    recipe_ingredient: RecipeIngredient | Any,
) -> str | None:
    return _managed_step_key_from_metadata(
        _metadata_from_payload(_step_service_payload(recipe_ingredient))
    )


def _step_matches_spec(recipe_ingredient: RecipeIngredient, spec: JSONObject) -> bool:
    return (
        recipe_ingredient.ingredient_id == spec["ingredient_id"]
        and recipe_ingredient.step_order == spec["step_order"]
        and recipe_ingredient.on_success == spec["on_success"]
        and recipe_ingredient.parallel_group == spec["parallel_group"]
        and recipe_ingredient.depth == spec["depth"]
        and recipe_ingredient.service_payload == spec["service_payload"]
        and recipe_ingredient.service_exec_parameters_override
        == spec["service_exec_parameters_override"]
        and recipe_ingredient.service_exec_expected_secs == spec["service_exec_expected_secs"]
        and recipe_ingredient.service_exec_timeout == spec["service_exec_timeout"]
        and recipe_ingredient.service_exec_expected_outcome == spec["service_exec_expected_outcome"]
        and recipe_ingredient.run_phase == spec["run_phase"]
        and recipe_ingredient.run_condition == spec["run_condition"]
    )


def _apply_step_spec(recipe_ingredient: RecipeIngredient, spec: JSONObject) -> None:
    recipe_ingredient.ingredient_id = spec["ingredient_id"]
    recipe_ingredient.step_order = spec["step_order"]
    recipe_ingredient.on_success = spec["on_success"]
    recipe_ingredient.parallel_group = spec["parallel_group"]
    recipe_ingredient.depth = spec["depth"]
    recipe_ingredient.service_payload = spec["service_payload"]
    recipe_ingredient.service_exec_parameters_override = spec["service_exec_parameters_override"]
    recipe_ingredient.service_exec_expected_secs = spec["service_exec_expected_secs"]
    recipe_ingredient.service_exec_timeout = spec["service_exec_timeout"]
    recipe_ingredient.service_exec_expected_outcome = spec["service_exec_expected_outcome"]
    recipe_ingredient.run_phase = spec["run_phase"]
    recipe_ingredient.run_condition = spec["run_condition"]


def _managed_payload(
    *,
    route: CommunicationRoute,
    scope: str,
    owner_key: str,
    event_name: str,
) -> JSONObject:
    semantic_text = {
        "remediation_failure_open": {
            "headline": "Alert requires attention",
            "summary": "PoundCake opened this communication because automated remediation failed.",
            "detail": "Automated remediation did not complete successfully.",
            "resolution": "",
        },
        "resolved_success_open": {
            "headline": "Alert cleared after successful auto-remediation",
            "summary": "PoundCake remediated this alert successfully and is closing the communication now that the alert has cleared.",
            "detail": "Alert cleared after successful auto-remediation.",
            "resolution": "Closing communication after successful auto-remediation.",
        },
        "resolved_success_close": {
            "headline": "Alert resolved",
            "summary": "PoundCake is closing this communication because the alert cleared after successful auto-remediation.",
            "detail": "Alert resolved after successful auto-remediation.",
            "resolution": "Closing communication.",
        },
        "resolved_failure_notify": {
            "headline": "Alert cleared after remediation failure",
            "summary": "The alert cleared after a remediation failure communication was already opened.",
            "detail": "Leaving the communication open for the responder.",
            "resolution": "",
        },
        "resolved_timeout_notify": {
            "headline": "Alert cleared after timeout",
            "summary": "The alert cleared after automation timed out and a communication was already opened.",
            "detail": "Leaving the communication open for the responder.",
            "resolution": "",
        },
        "fallback_open": {
            "headline": "Alert requires attention",
            "summary": "PoundCake did not find a matching workflow for this alert and opened a communication for human response.",
            "detail": "No matching workflow is configured for this alert.",
            "resolution": "",
        },
        "fallback_close": {
            "headline": "Alert cleared",
            "summary": "The unmatched alert has cleared and PoundCake is closing the fallback communication.",
            "detail": "Closing the existing communication because the alert has cleared.",
            "resolution": "Closing communication.",
        },
    }[event_name]
    metadata = {
        "managed": True,
        "scope": scope,
        "owner_key": owner_key,
        "route_id": route.id,
        "label": route.label,
        "service_type": route.service_type,
        "destination_target": route.destination_target,
        "provider_config": route.provider_config,
        "enabled": route.enabled,
        "position": route.position,
        "event": event_name,
    }
    return {
        "title": semantic_text["headline"],
        "description": semantic_text["summary"],
        "message": semantic_text["detail"],
        "source": "poundcake",
        "context": {
            "source": "poundcake",
            "route_label": route.label,
            "destination_target": route.destination_target,
            "provider_config": route.provider_config,
            "semantic_text": semantic_text,
            POLICY_METADATA_KEY: metadata,
        },
    }


def _build_route_step_specs(
    *,
    routes: list[CommunicationRoute],
    scope: str,
    owner_key: str,
    fallback: bool,
) -> list[JSONObject]:
    specs: list[JSONObject] = []
    events = FALLBACK_ROUTE_EVENTS if fallback else MATCHED_ROUTE_EVENTS
    for route in routes:
        for event_name, operation, run_phase, run_condition, base_step in events:
            step_order = base_step + (route.position * 10)
            specs.append(
                {
                    "task_key_template": _managed_task_key(
                        scope=scope,
                        owner_key=owner_key,
                        route_id=route.id,
                        event_name=event_name,
                    ),
                    "service_type": route.service_type,
                    "service_exec": operation,
                    "destination_target": route.destination_target,
                    "ingredient_purpose": "comms",
                    "service_payload": _managed_payload(
                        route=route,
                        scope=scope,
                        owner_key=owner_key,
                        event_name=event_name,
                    ),
                    "step_order": step_order,
                    "run_phase": run_phase,
                    "run_condition": run_condition,
                    "on_success": "continue",
                    "parallel_group": 0,
                    "depth": step_order,
                    "service_exec_parameters_override": {"operation": operation},
                    "service_exec_expected_outcome": {"success": True},
                }
            )
    return specs


def build_recipe_local_policy_step_specs(
    *,
    recipe_id: int,
    routes: list[JSONObject] | list[CommunicationRoute],
) -> tuple[list[CommunicationRoute], list[JSONObject]]:
    normalized = normalize_routes(routes)
    return normalized, _build_route_step_specs(
        routes=normalized,
        scope="recipe",
        owner_key=str(recipe_id),
        fallback=False,
    )


async def _delete_recipe_ingredient_ids_safely(
    db: AsyncSession, *, recipe_ingredient_ids: list[int]
) -> None:
    if not recipe_ingredient_ids:
        return
    await detach_recipe_ingredient_ids_safely(db, recipe_ingredient_ids=recipe_ingredient_ids)
    await db.execute(delete(RecipeIngredient).where(RecipeIngredient.id.in_(recipe_ingredient_ids)))


async def _recipe_communication_steps(
    db: AsyncSession,
    *,
    recipe: Recipe | Any,
) -> list[RecipeIngredient]:
    recipe_id = getattr(recipe, "id", None)
    if recipe_id is not None:
        result = await db.execute(
            select(RecipeIngredient)
            .options(joinedload(RecipeIngredient.ingredient))
            .where(RecipeIngredient.recipe_id == recipe_id)
            .order_by(RecipeIngredient.step_order.asc(), RecipeIngredient.id.asc())
        )
        unique_result = result.unique() if hasattr(result, "unique") else result
        return [row for row in unique_result.scalars().all() if is_communication_step(row)]
    return [
        ri for ri in getattr(recipe, "recipe_ingredients", []) or [] if is_communication_step(ri)
    ]


async def replace_recipe_communication_steps(
    db: AsyncSession,
    *,
    recipe: Recipe,
    step_specs: list[JSONObject],
) -> None:
    capability_catalog = build_enabled_plugin_capability_catalog(await _enabled_plugin_configs(db))
    comm_steps = await _recipe_communication_steps(db, recipe=recipe)
    existing_by_key: dict[str, RecipeIngredient] = {}
    remove_ids: list[int] = []
    for recipe_ingredient in comm_steps:
        managed_key = _managed_step_key_from_recipe_ingredient(recipe_ingredient)
        if managed_key and managed_key not in existing_by_key:
            existing_by_key[managed_key] = recipe_ingredient
        else:
            remove_ids.append(recipe_ingredient.id)

    desired_keys: set[str] = set()
    for spec in step_specs:
        capability = select_communication_capability(
            capability_catalog,
            service_type=str(spec["service_type"] or "").strip(),
            destination_target=str(spec["destination_target"] or "").strip(),
        )
        if capability is None:
            raise ValueError(
                "No enabled communication capability registered for "
                f"{spec['service_type']}.{spec['service_exec']}"
            )
        resolved = await resolve_active_capability_ingredient(
            db,
            capability={
                **capability,
                "operation": str(spec["service_exec"] or "").strip().lower(),
            },
        )
        if resolved is None:
            raise ValueError(
                "No active communication ingredient resolved for "
                f"{spec['service_type']}.{spec['service_exec']}"
            )
        ingredient = resolved.ingredient
        service_exec_parameters = dict(ingredient.service_exec_parameters or {})
        overrides = spec.get("service_exec_parameters_override")
        if isinstance(overrides, dict):
            service_exec_parameters.update(overrides)
        validate_service_payload_for_operation(
            spec["service_payload"],
            ingredient.payload_schema,
            service_exec_parameters or None,
        )
        spec["ingredient_id"] = ingredient.id
        spec["service_exec_expected_secs"] = ingredient.default_expected_secs
        spec["service_exec_timeout"] = ingredient.default_timeout

        managed_key = str(spec["task_key_template"])
        desired_keys.add(managed_key)
        existing = existing_by_key.get(managed_key)
        if existing is not None:
            if not _step_matches_spec(existing, spec):
                _apply_step_spec(existing, spec)
            continue

        db.add(
            RecipeIngredient(
                recipe_id=recipe.id,
                ingredient_id=spec["ingredient_id"],
                step_order=spec["step_order"],
                on_success=spec["on_success"],
                parallel_group=spec["parallel_group"],
                depth=spec["depth"],
                service_payload=spec["service_payload"],
                service_exec_parameters_override=spec["service_exec_parameters_override"],
                service_exec_expected_secs=spec["service_exec_expected_secs"],
                service_exec_timeout=spec["service_exec_timeout"],
                service_exec_expected_outcome=spec["service_exec_expected_outcome"],
                run_phase=spec["run_phase"],
                run_condition=spec["run_condition"],
            )
        )

    remove_ids.extend(
        recipe_ingredient.id
        for managed_key, recipe_ingredient in existing_by_key.items()
        if managed_key not in desired_keys
    )
    await _delete_recipe_ingredient_ids_safely(db, recipe_ingredient_ids=remove_ids)


async def _enabled_plugin_configs(db: AsyncSession) -> dict[str, JSONObject]:
    result = await db.execute(select(ServicePlugin).where(ServicePlugin.enabled.is_(True)))
    rows = result.scalars().all()
    configs: dict[str, JSONObject] = {}
    for row in rows:
        plugin_config = getattr(row, "plugin_config", None)
        if not isinstance(plugin_config, dict):
            continue
        configs[str(row.service_type or "").strip().lower()] = dict(plugin_config)
    return configs


async def ensure_global_policy_recipe(db: AsyncSession) -> Recipe:
    result = await db.execute(
        select(Recipe)
        .options(joinedload(Recipe.recipe_ingredients).joinedload(RecipeIngredient.ingredient))
        .where(Recipe.name == MANAGED_RECIPE_NAME_GLOBAL)
        .with_for_update()
    )
    recipe = result.unique().scalars().first()
    now = _now()
    if recipe is None:
        recipe = Recipe(
            name=MANAGED_RECIPE_NAME_GLOBAL,
            description=f"{MANAGED_DESCRIPTION_GLOBAL} Global communications policy",
            enabled=True,
            clear_timeout_sec=None,
            deleted=False,
            deleted_at=None,
            updated_at=now,
            recipe_ingredients=[],
        )
        db.add(recipe)
        await db.flush()
    else:
        recipe.description = f"{MANAGED_DESCRIPTION_GLOBAL} Global communications policy"
        recipe.enabled = True
        recipe.deleted = False
        recipe.deleted_at = None
        recipe.updated_at = now
    return recipe


async def _load_global_policy_recipe(
    db: AsyncSession, *, for_update: bool = False
) -> Recipe | None:
    query = (
        select(Recipe)
        .options(joinedload(Recipe.recipe_ingredients).joinedload(RecipeIngredient.ingredient))
        .where(Recipe.name == MANAGED_RECIPE_NAME_GLOBAL)
    )
    if for_update:
        query = query.with_for_update()
    result = await db.execute(query)
    return result.unique().scalars().first()


def get_recipe_local_routes(recipe: Recipe | Any) -> list[CommunicationRoute]:
    return _group_routes_from_steps(
        [ri for ri in getattr(recipe, "recipe_ingredients", []) or [] if is_communication_step(ri)]
    )


def recipe_uses_local_communications(recipe: Recipe | Any) -> bool:
    return bool(get_recipe_local_routes(recipe))


def get_visible_recipe_steps(recipe: Recipe | Any) -> list[RecipeIngredient]:
    return [
        ri
        for ri in getattr(recipe, "recipe_ingredients", []) or []
        if not is_communication_step(ri)
    ]


async def get_global_policy_routes(db: AsyncSession) -> list[CommunicationRoute]:
    recipe = await _load_global_policy_recipe(db)
    if recipe is None:
        return []
    return _group_routes_from_steps(recipe.recipe_ingredients)


async def get_global_policy_recipe_for_planning(db: AsyncSession) -> Recipe | None:
    recipe = await _load_global_policy_recipe(db)
    if recipe is None:
        return None
    routes = _group_routes_from_steps(recipe.recipe_ingredients)
    if not any(route.enabled for route in routes):
        return None
    return recipe


async def global_policy_configured(db: AsyncSession) -> bool:
    routes = await get_global_policy_routes(db)
    return any(route.enabled for route in routes)


async def get_effective_recipe_routes(
    db: AsyncSession, recipe: Recipe | Any
) -> tuple[str | None, list[CommunicationRoute]]:
    local_routes = get_recipe_local_routes(recipe)
    if any(route.enabled for route in local_routes):
        return "local", local_routes
    global_routes = await get_global_policy_routes(db)
    enabled_global = [route for route in global_routes if route.enabled]
    if enabled_global:
        return "global", global_routes
    return None, []


async def sync_global_policy_routes(
    db: AsyncSession,
    *,
    routes: list[JSONObject] | list[CommunicationRoute],
) -> list[CommunicationRoute]:
    normalized = normalize_routes(routes)
    recipe = await ensure_global_policy_recipe(db)
    await replace_recipe_communication_steps(
        db,
        recipe=recipe,
        step_specs=_build_route_step_specs(
            routes=normalized,
            scope="global",
            owner_key="global",
            fallback=False,
        ),
    )
    recipe.updated_at = _now()
    return normalized


async def sync_recipe_local_policy(
    db: AsyncSession,
    *,
    recipe: Recipe,
    routes: list[JSONObject] | list[CommunicationRoute],
) -> list[CommunicationRoute]:
    normalized = normalize_routes(routes)
    await replace_recipe_communication_steps(
        db,
        recipe=recipe,
        step_specs=_build_route_step_specs(
            routes=normalized,
            scope="recipe",
            owner_key=str(recipe.id),
            fallback=False,
        ),
    )
    recipe.updated_at = _now()
    return normalized


async def clear_recipe_local_policy(db: AsyncSession, *, recipe: Recipe) -> None:
    await replace_recipe_communication_steps(db, recipe=recipe, step_specs=[])
    recipe.updated_at = _now()


def _route_lists_match(
    existing: list[CommunicationRoute], desired: list[CommunicationRoute]
) -> bool:
    if len(existing) != len(desired):
        return False
    existing_targets = {r.destination_target for r in existing if r.enabled}
    desired_targets = {r.destination_target for r in desired if r.enabled}
    return existing_targets == desired_targets


async def sync_fallback_policy_recipe(
    db: AsyncSession,
    *,
    routes: list[CommunicationRoute],
) -> Recipe | None:
    """Sync the fallback recipe so it stays in step with the current global policy routes.

    When no enabled routes are configured the fallback recipe is disabled.
    When routes exist the fallback recipe is enabled with ``fallback_open``
    and ``fallback_close`` steps for each enabled route.
    """
    settings = get_settings()
    recipe_name = str(settings.catch_all_recipe_name or "").strip()
    if not recipe_name:
        return None

    result = await db.execute(
        select(Recipe)
        .options(joinedload(Recipe.recipe_ingredients).joinedload(RecipeIngredient.ingredient))
        .where(Recipe.name == recipe_name)
        .with_for_update()
    )
    recipe = result.unique().scalars().first()
    now = _now()

    if recipe is None:
        recipe = Recipe(
            name=recipe_name,
            description=f"{MANAGED_DESCRIPTION_FALLBACK} Fallback communications policy",
            enabled=True,
            clear_timeout_sec=None,
            deleted=False,
            deleted_at=None,
            updated_at=now,
            recipe_ingredients=[],
        )
        db.add(recipe)
        await db.flush()
    else:
        recipe.description = f"{MANAGED_DESCRIPTION_FALLBACK} Fallback communications policy"
        recipe.deleted = False
        recipe.deleted_at = None
        recipe.updated_at = now

    enabled_routes = [route for route in routes if route.enabled]
    if not enabled_routes:
        if recipe.enabled is False and not get_recipe_local_routes(recipe):
            return recipe
        recipe.enabled = False
        await replace_recipe_communication_steps(db, recipe=recipe, step_specs=[])
        return recipe

    existing_routes = get_recipe_local_routes(recipe)
    if recipe.enabled and _route_lists_match(existing_routes, enabled_routes):
        return recipe

    recipe.enabled = True
    await replace_recipe_communication_steps(
        db,
        recipe=recipe,
        step_specs=_build_route_step_specs(
            routes=enabled_routes,
            scope="fallback",
            owner_key="fallback",
            fallback=True,
        ),
    )
    return recipe


async def ensure_fallback_recipe(
    db: AsyncSession,
    *,
    req_id: str,
) -> Recipe | None:
    """Ensure the catch-all fallback recipe matches the effective global policy.

    Called at order dispatch when no recipe matches the alert group so unmatched
    alerts still open the configured fallback communication routes.
    """
    routes = await get_global_policy_routes(db)
    recipe = await sync_fallback_policy_recipe(db, routes=routes)
    logger.info(
        "Ensured fallback recipe from communications policy",
        extra={
            "req_id": req_id,
            "recipe_name": recipe.name if recipe is not None else None,
            "recipe_id": recipe.id if recipe is not None else None,
            "route_count": len(routes),
        },
    )
    return recipe


def _group_routes_from_steps(steps: list[RecipeIngredient]) -> list[CommunicationRoute]:
    grouped: dict[str, CommunicationRoute] = {}
    for step in steps:
        ingredient = step.ingredient
        if ingredient is None or not is_communication_ingredient(ingredient):
            continue
        metadata = _metadata_from_payload(_step_service_payload(step))
        route = _route_from_metadata(metadata)
        if route is None:
            continue
        route.provider_config = normalize_route_provider_config(
            route.service_type,
            route.provider_config,
            require_required=False,
        )
        grouped[route.id] = route
    return sorted(grouped.values(), key=lambda item: (item.position, item.label.lower()))


def serialize_route(route: CommunicationRoute) -> JSONObject:
    return {
        "id": route.id,
        "label": route.label,
        "service_type": route.service_type,
        "destination_target": route.destination_target,
        "provider_config": route.provider_config,
        "enabled": route.enabled,
        "position": route.position,
    }


def lifecycle_summary() -> dict[str, str]:
    return {
        "success": "When an alert clears after successful auto-remediation, PoundCake opens and then closes each configured route.",
        "failure": "When remediation fails, PoundCake opens each configured route and leaves it open.",
        "unmatched_alert": "When no matching workflow exists, PoundCake opens each configured fallback route immediately.",
        "clear_after_failure": "When an alert clears after remediation failed, PoundCake leaves failure routes open for the responder.",
    }


def route_payloads_for_response(
    *,
    mode: str,
    effective_source: str | None,
    routes: list[CommunicationRoute],
) -> JSONObject:
    return {
        "mode": mode,
        "effective_source": effective_source,
        "routes": [serialize_route(route) for route in routes],
    }


def policy_has_enabled_routes(routes: list[CommunicationRoute]) -> bool:
    return any(route.enabled for route in routes)


def should_seed_route_step(
    *,
    recipe_ingredient: RecipeIngredient | Any,
    order: Any,
) -> bool:
    ingredient = getattr(recipe_ingredient, "ingredient", None)
    if ingredient is None:
        return True
    operation = _operation_from_parameters(_merged_step_parameters(recipe_ingredient))
    run_condition = str(getattr(recipe_ingredient, "run_condition", "") or "").strip().lower()
    if operation not in {"notify", "close"}:
        return True
    if run_condition not in {
        "resolved_after_failure",
        "resolved_after_timeout",
        "resolved_after_no_remediation",
    }:
        return True
    metadata = _metadata_from_payload(_step_service_payload(recipe_ingredient))
    service_type = metadata.get("service_type") or getattr(ingredient, "service_type", "")
    destination_target = metadata.get("destination_target") or getattr(
        ingredient, "destination_target", ""
    )
    return is_route_available_for_update(
        order=order,
        service_type=str(service_type),
        destination_target=str(destination_target),
    )
