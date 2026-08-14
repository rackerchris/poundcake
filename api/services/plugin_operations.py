"""Service-layer database operations for plugin adapters.

Adapters call these functions to write or read database state.
All operations are RBAC-checked and use the plugin-operation
database identity with Helm-managed MariaDB grants.

Plugins MUST NOT open direct database sessions. All protected
database work is exposed through these operations, which enforce
RBAC capability checks and write through a dedicated MariaDB user
that is traceable in audit logs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from api.core.database import plugin_operation_db_session
from api.core.time import utc_now_db
from api.models.models import (
    Ingredient,
    Recipe,
    RecipeIngredient,
    ServicePlugin,
)
from api.models.models import ScheduledTask
from api.services.database_access import (
    DatabaseCapability,
    principal_for_internal_service,
    require_database_capability,
)
from api.services.capability_resolution import resolve_active_capability_ingredient
from api.types import JSONObject
from sqlalchemy import select

_UNSET = object()
_MANAGED_STEP_MARKERS = frozenset({"managed-by:poundcake-genestack-monitoring"})

# ---------------------------------------------------------------------------
# Data Models — lightweight, no SQLAlchemy dependencies
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecipePayload:
    """Generic recipe payload for plugin operations.

    Adapters build these from plugin-specific data then pass them
    to ``upsert_recipes()``.  This keeps plugin logic decoupled from
    database models.
    """

    name: str
    description: str | None
    enabled: bool
    clear_timeout_sec: int | None
    managed_by: str | None  # e.g. "managed-by:poundcake-genestack-monitoring"
    steps: list["RecipeStepPayload"]


@dataclass(frozen=True)
class RecipeStepPayload:
    """A single execution step within a recipe payload."""

    service_type: str
    service_exec: str
    task_key_template: str
    step_order: int
    service_payload: JSONObject
    service_exec_parameters_override: JSONObject | None
    expected_secs: int
    timeout: int
    expected_outcome: dict[str, Any]
    run_phase: str
    run_condition: str
    on_success: str = "continue"
    parallel_group: int = 0
    depth: int = 0


@dataclass(frozen=True)
class UpsertStats:
    """Summary returned by ``upsert_recipes()``."""

    created: int
    updated: int
    deleted: int
    skipped: int


@dataclass(frozen=True)
class RecipeManagementState:
    """Recipe ownership and lifecycle metadata exposed to adapters."""

    name: str
    exists: bool
    managed: bool
    enabled: bool
    deleted: bool


# ---------------------------------------------------------------------------
# RBAC enforcement
# ---------------------------------------------------------------------------


def _require_capability(
    requester_service_type: str,
    capability: DatabaseCapability,
    *,
    target_service_type: str | None = None,
) -> None:
    """Raise ``DatabaseAccessError`` when the caller lacks capability."""

    principal = principal_for_internal_service(requester_service_type)
    require_database_capability(principal, capability, target_service_type=target_service_type)


# ---------------------------------------------------------------------------
# Recipe / Ingredient / RecipeIngredient operations
# ---------------------------------------------------------------------------


async def upsert_recipes(
    *,
    requester_service_type: str,
    recipes: list[RecipePayload],
) -> UpsertStats:
    """Upsert recipes, ingredients, and recipe-ingredients through the protected boundary.

    This function enforces RBAC, builds or resolves ingredients by
    identity, then creates or updates recipe rows and their step
    assemblies.  All writes happen in a single database transaction
    backed by the plugin-operation MariaDB user.

    Adapters call this instead of managing database sessions or
    instantiating SQLAlchemy models directly.
    """

    _require_capability(requester_service_type, "genestack_monitoring:recipe-sync")

    if not recipes:
        return UpsertStats(created=0, updated=0, deleted=0, skipped=0)

    now = utc_now_db()
    created = 0
    updated = 0
    deleted = 0
    skipped = 0

    # Collect all unique ingredient identities from the payload.
    ingredient_identities: set[tuple[str, str, str]] = set()
    for recipe in recipes:
        for step in recipe.steps:
            ingredient_identities.add(
                (step.service_type, step.service_exec, step.task_key_template)
            )

    async with plugin_operation_db_session() as db:
        async with db.begin():
            # --- Ingredient lookup-or-create ---
            ingredient_map: dict[tuple[str, str, str], Ingredient] = {}
            for identity in sorted(ingredient_identities):
                svc_type, svc_exec, task_key = identity
                result = await db.execute(
                    select(Ingredient).where(
                        Ingredient.service_type == svc_type,
                        Ingredient.service_exec == svc_exec,
                        Ingredient.task_key_template == task_key,
                        Ingredient.is_active.is_(True),
                    )
                )
                existing = result.scalars().first()
                if existing is not None:
                    ingredient_map[identity] = existing
                else:
                    ingredient = Ingredient(
                        service_type=svc_type,
                        service_exec=svc_exec,
                        task_key_template=task_key,
                        ingredient_purpose="utility",
                        is_active=True,
                        default_expected_secs=30,
                        default_timeout=300,
                        retry_count=0,
                        retry_delay=5,
                        on_failure="stop",
                    )
                    db.add(ingredient)
                    await db.flush()
                    ingredient_map[identity] = ingredient

            # --- Recipe upsert ---
            recipe_by_name: dict[str, int] = {}
            for recipe_payload in recipes:
                name = recipe_payload.name
                result = await db.execute(select(Recipe).where(Recipe.name == name))
                recipe = result.scalars().first()

                if recipe is None:
                    recipe = Recipe(
                        name=name,
                        description=(
                            f"[managed-by:{recipe_payload.managed_by}] {recipe_payload.description or ''}"
                            if recipe_payload.managed_by
                            else recipe_payload.description
                        ),
                        enabled=recipe_payload.enabled,
                        clear_timeout_sec=recipe_payload.clear_timeout_sec,
                        deleted=False,
                    )
                    db.add(recipe)
                    await db.flush()
                    created += 1
                elif not _is_managed(recipe):
                    skipped += 1
                    continue
                else:
                    recipe.description = (
                        f"[managed-by:{recipe_payload.managed_by}] {recipe_payload.description or ''}"
                        if recipe_payload.managed_by
                        else recipe_payload.description
                    )
                    recipe.enabled = recipe_payload.enabled
                    recipe.clear_timeout_sec = recipe_payload.clear_timeout_sec
                    recipe.updated_at = now
                    recipe.deleted = False
                    updated += 1

                recipe_by_name[name] = int(recipe.id)

            # --- RecipeIngredient upsert ---
            for recipe_payload in recipes:
                recipe_id = recipe_by_name.get(recipe_payload.name)
                if recipe_id is None:
                    continue

                # Fetch existing managed RecipeIngredients.
                result = await db.execute(
                    select(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe_id)
                )
                existing_steps: dict[tuple[str, int], list[RecipeIngredient]] = {}
                stale_duplicate_step_ids: list[int] = []
                existing_rows = sorted(
                    result.scalars().all(),
                    key=lambda row: (int(getattr(row, "step_order", 0) or 0), int(row.id)),
                )
                for step_row in existing_rows:
                    step_key = _managed_recipe_step_key(step_row)
                    if step_key is None:
                        continue
                    bucket = existing_steps.setdefault(step_key, [])
                    if bucket:
                        stale_duplicate_step_ids.append(int(step_row.id))
                        continue
                    bucket.append(step_row)

                matched_step_ids: set[int] = set()
                for idx, step in enumerate(recipe_payload.steps, start=1):
                    ingredient = ingredient_map.get(
                        (
                            step.service_type,
                            step.service_exec,
                            step.task_key_template,
                        )
                    )
                    if ingredient is None:
                        continue

                    ingredient_id = int(ingredient.id)
                    step_key = ("index", idx)
                    existing_bucket = existing_steps.get(step_key, [])

                    if not existing_bucket:
                        ri = RecipeIngredient(
                            recipe_id=recipe_id,
                            ingredient_id=ingredient_id,
                            step_order=idx * 10,
                            on_success=step.on_success,
                            parallel_group=step.parallel_group,
                            depth=idx * 10,
                            service_payload=step.service_payload,
                            service_exec_parameters_override=(
                                step.service_exec_parameters_override
                            ),
                            service_exec_expected_secs=step.expected_secs,
                            service_exec_timeout=step.timeout,
                            service_exec_expected_outcome=(step.expected_outcome),
                            run_phase=step.run_phase,
                            run_condition=step.run_condition,
                        )
                        db.add(ri)
                    else:
                        ri = existing_bucket.pop(0)
                        matched_step_ids.add(int(ri.id))
                        ri.ingredient_id = ingredient_id
                        ri.step_order = idx * 10
                        ri.on_success = step.on_success
                        ri.parallel_group = step.parallel_group
                        ri.depth = idx * 10
                        ri.service_payload = step.service_payload
                        ri.service_exec_parameters_override = step.service_exec_parameters_override
                        ri.service_exec_expected_secs = step.expected_secs
                        ri.service_exec_timeout = step.timeout
                        ri.service_exec_expected_outcome = step.expected_outcome
                        ri.run_phase = step.run_phase
                        ri.run_condition = step.run_condition

                # Delete managed RecipeIngredients no longer in payload.
                stale_step_ids = set(stale_duplicate_step_ids)
                for rows in existing_steps.values():
                    for ri in rows:
                        if int(ri.id) not in matched_step_ids:
                            stale_step_ids.add(int(ri.id))
                if stale_step_ids:
                    for stale_step_id in sorted(stale_step_ids):
                        stale_row = await db.get(RecipeIngredient, stale_step_id)
                        if stale_row is None:
                            continue
                        await db.delete(stale_row)
                        deleted += 1

    return UpsertStats(created=created, updated=updated, deleted=deleted, skipped=skipped)


MANAGED_REMEDIATION_MARKER = "managed-by:poundcake-genestack-monitoring"


def _is_managed(recipe: Recipe) -> bool:
    """Return ``True`` when the recipe description carries a managed marker."""
    description = str(recipe.description or "")
    return "managed-by:" in description


def _managed_step_marker_matches(value: object) -> bool:
    normalized = str(value or "").strip()
    return normalized in _MANAGED_STEP_MARKERS


def _managed_recipe_step_key(step_row: RecipeIngredient) -> tuple[str, int] | None:
    params = getattr(step_row, "service_exec_parameters_override", None)
    if not isinstance(params, dict):
        return None
    if not _managed_step_marker_matches(params.get("managed_by")):
        return None
    managed_index = params.get("managed_index")
    try:
        if managed_index is not None:
            return ("index", int(managed_index))
    except (TypeError, ValueError):
        pass
    try:
        return ("step_order", int(getattr(step_row, "step_order", 0) or 0))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Ingredient lookup
# ---------------------------------------------------------------------------


async def get_ingredient(
    *,
    requester_service_type: str,
    service_type: str,
    service_exec: str,
    task_key_template: str,
) -> JSONObject | None:
    """Look up one active ingredient by its identity triplet.

    Returns a plain dict (not a SQLAlchemy model) containing only
    public metadata.  Adapters use this to validate that required
    ingredients exist before proceeding with dispatch logic.
    """

    _require_capability(requester_service_type, "service-plugin:read")

    async with plugin_operation_db_session() as db:
        result = await db.execute(
            select(Ingredient).where(
                Ingredient.service_type == service_type,
                Ingredient.service_exec == service_exec,
                Ingredient.task_key_template == task_key_template,
                Ingredient.is_active.is_(True),
            )
        )
        row = result.scalars().first()
        if row is None:
            return None
        return {
            "id": int(row.id),
            "service_type": row.service_type,
            "service_exec": row.service_exec,
            "task_key_template": row.task_key_template,
            "ingredient_purpose": row.ingredient_purpose,
            "is_active": bool(row.is_active),
            "default_expected_secs": int(row.default_expected_secs),
            "default_timeout": int(row.default_timeout) if row.default_timeout else None,
            "retry_count": int(row.retry_count),
            "on_failure": row.on_failure,
        }


async def resolve_capability_ingredient(
    *,
    requester_service_type: str,
    capability: JSONObject,
) -> JSONObject | None:
    """Resolve one normalized capability to an active immutable ingredient."""

    _require_capability(requester_service_type, "service-plugin:read")

    async with plugin_operation_db_session() as db:
        resolved = await resolve_active_capability_ingredient(db, capability=capability)
    if resolved is None:
        return None
    ingredient = resolved.ingredient
    return {
        "capability_id": resolved.capability_id,
        "service_type": resolved.service_type,
        "mode": resolved.mode,
        "operation": resolved.operation,
        "priority": resolved.priority,
        "defaults": resolved.defaults,
        "ingredient": {
            "id": int(ingredient.id),
            "service_type": ingredient.service_type,
            "service_exec": ingredient.service_exec,
            "destination_target": ingredient.destination_target or "",
            "task_key_template": ingredient.task_key_template,
            "ingredient_purpose": ingredient.ingredient_purpose,
            "is_active": bool(ingredient.is_active),
            "default_expected_secs": int(ingredient.default_expected_secs),
            "default_timeout": (
                int(ingredient.default_timeout) if ingredient.default_timeout else None
            ),
            "retry_count": int(ingredient.retry_count),
            "on_failure": ingredient.on_failure,
        },
    }


async def list_service_plugin_configs(
    *,
    requester_service_type: str,
) -> dict[str, JSONObject]:
    """Return enabled plugin operator configs keyed by service_type."""

    _require_capability(requester_service_type, "service-plugin:read")

    async with plugin_operation_db_session() as db:
        result = await db.execute(select(ServicePlugin).where(ServicePlugin.enabled.is_(True)))
        rows = result.scalars().all()
    configs: dict[str, JSONObject] = {}
    for row in rows:
        config = getattr(row, "plugin_config", None)
        if not isinstance(config, dict):
            continue
        configs[str(row.service_type or "").strip().lower()] = dict(config)
    return configs


async def list_recipe_management_states(
    *,
    requester_service_type: str,
    recipe_names: list[str],
) -> dict[str, RecipeManagementState]:
    """Return recipe ownership state keyed by recipe name."""

    _require_capability(requester_service_type, "service-plugin:read")

    normalized_names = sorted(
        {
            str(recipe_name or "").strip()
            for recipe_name in recipe_names
            if str(recipe_name or "").strip()
        }
    )
    if not normalized_names:
        return {}

    async with plugin_operation_db_session() as db:
        result = await db.execute(select(Recipe).where(Recipe.name.in_(normalized_names)))
        rows = result.scalars().all()

    states: dict[str, RecipeManagementState] = {}
    for row in rows:
        name = str(getattr(row, "name", "") or "").strip()
        if not name:
            continue
        states[name] = RecipeManagementState(
            name=name,
            exists=True,
            managed=_is_managed(row),
            enabled=bool(getattr(row, "enabled", False)),
            deleted=bool(getattr(row, "deleted", False)),
        )
    return states


# ---------------------------------------------------------------------------
# Service plugin state update
# ---------------------------------------------------------------------------


async def update_service_plugin_state(
    *,
    requester_service_type: str,
    service_type: str,
    plugin_config: JSONObject | None | object = _UNSET,
    health_status: str | object = _UNSET,
    health_message: str | None | object = _UNSET,
    health_error_code: str | None | object = _UNSET,
    health_latency_ms: int | None | object = _UNSET,
    last_health_check_at: datetime | None | object = _UNSET,
    last_success_at: datetime | None | object = _UNSET,
    consecutive_failures: int | object = _UNSET,
    enabled: bool | object = _UNSET,
    status_message: str | None | object = _UNSET,
) -> bool:
    """Update protected ``service_plugins`` state through the DB helper boundary."""

    _require_capability(requester_service_type, "service-plugin:update-status")

    normalized = service_type.strip().lower()
    async with plugin_operation_db_session() as db:
        result = await db.execute(
            select(ServicePlugin).where(ServicePlugin.service_type == normalized)
        )
        row = result.scalars().first()
        if row is None:
            return False

        if plugin_config is not _UNSET:
            row.plugin_config = plugin_config
        if enabled is not _UNSET:
            row.enabled = bool(enabled)
        if health_status is not _UNSET:
            row.health_status = str(health_status)
        if status_message is not _UNSET:
            row.status_message = status_message
        if health_message is not _UNSET:
            row.health_message = health_message
        if health_error_code is not _UNSET:
            row.health_error_code = health_error_code
        if health_latency_ms is not _UNSET:
            row.health_latency_ms = health_latency_ms
        if last_health_check_at is not _UNSET:
            row.last_health_check_at = last_health_check_at
        if last_success_at is not _UNSET:
            row.last_success_at = last_success_at
        elif health_status is not _UNSET and str(health_status).strip().lower() == "healthy":
            row.last_success_at = (
                last_health_check_at if last_health_check_at is not _UNSET else utc_now_db()
            )
        if consecutive_failures is not _UNSET:
            row.consecutive_failures = int(consecutive_failures)
        elif health_status is not _UNSET:
            if str(health_status).strip().lower() == "healthy":
                row.consecutive_failures = 0
            else:
                row.consecutive_failures = int(row.consecutive_failures or 0) + 1

        row.updated_at = utc_now_db()
        await db.commit()

    return True


async def disable_service_plugin_and_tasks(
    *,
    requester_service_type: str,
    service_type: str,
    health_status: str = "disabled",
    status_message: str | None = None,
    task_status: str = "disabled",
) -> bool:
    """Disable a service plugin and all of its scheduled tasks."""

    _require_capability(requester_service_type, "service-plugin:update-status")
    _require_capability(requester_service_type, "app:data-write")

    normalized = service_type.strip().lower()
    now = utc_now_db()
    async with plugin_operation_db_session() as db:
        result = await db.execute(
            select(ServicePlugin).where(ServicePlugin.service_type == normalized)
        )
        row = result.scalars().first()
        if row is None:
            return False

        row.enabled = False
        row.health_status = str(health_status)
        row.status_message = status_message
        row.updated_at = now

        task_result = await db.execute(
            select(ScheduledTask).where(ScheduledTask.service_type == normalized)
        )
        for task in task_result.scalars().all():
            task.is_enabled = False
            task.status = task_status
            task.next_run_at = None
            task.updated_at = now

        await db.commit()

    return True


# ---------------------------------------------------------------------------
# Scheduled Task update
# ---------------------------------------------------------------------------


async def update_scheduled_task(
    *,
    requester_service_type: str,
    task_key: str,
    next_run_at: datetime | None = None,
    status: str | None = None,
    last_message: str | None = None,
    last_started_at: datetime | None = None,
) -> bool:
    """Update a scheduled task's status, next-run time, or message."""

    _require_capability(requester_service_type, "app:data-write")

    async with plugin_operation_db_session() as db:
        result = await db.execute(select(ScheduledTask).where(ScheduledTask.task_key == task_key))
        row = result.scalars().first()
        if row is None:
            return False

        if next_run_at is not None:
            row.next_run_at = next_run_at
        if status is not None:
            row.status = status
        if last_message is not None:
            row.last_message = last_message
        if last_started_at is not None:
            row.last_started_at = last_started_at
        row.updated_at = utc_now_db()

    return True


# ---------------------------------------------------------------------------
# Dish metadata update
# ---------------------------------------------------------------------------


async def update_dish_metadata(
    *,
    requester_service_type: str,
    dish_id: int,
    metadata: JSONObject,
) -> bool:
    """Attach plugin execution metadata to a dish row.

    Metadata is stored in the dish's ``service_data`` JSON column,
    keyed by the calling service_type so multiple adapters can write
    their own data without collision.
    """

    _require_capability(requester_service_type, "app:data-write")

    async with plugin_operation_db_session() as db:
        from api.models.models import Dish

        result = await db.execute(select(Dish).where(Dish.id == dish_id))
        row = result.scalars().first()
        if row is None:
            return False

        existing: dict = row.service_data if isinstance(row.service_data, dict) else {}
        existing.setdefault("_plugin", {})
        existing["_plugin"][requester_service_type] = {
            **metadata,
            "_updated_at": utc_now_db().isoformat(),
        }
        row.service_data = existing

    return True
