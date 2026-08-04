"""Bootstrap enabled service plugin ingredients and recipes into the database."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.models import (
    Ingredient,
    Recipe,
    RecipeIngredient,
    ScheduledTask,
    ServicePlugin,
)
from api.core.logging import get_logger
from api.core.time import utc_now_db
from api.plugins.catalog import get_enabled_plugins_for_bootstrap, missing_helper_capabilities_for
from api.plugins.base import ExecutionAdapter
from api.plugins.internal_services import INTERNAL_SERVICE_TYPES
from api.plugins.manifest import ServicePlugin as ServicePluginManifest
from api.plugins.manifest import SUPPORTED_PLUGIN_TYPES
from api.services.credentials import (
    ServicePluginCredentialError,
    decrypt_service_identity_payload,
)
from api.services.credential_manager import mark_adapter_credential_error
from api.services.service_identity import upsert_internal_hmac_credential
from api.plugins.contract import (
    ServicePluginContractError,
    validate_service_operation,
    validate_payload_schema,
    validate_service_payload_for_operation,
)
from api.plugins.state import (
    PLUGIN_RUN_STATE_DISABLED,
    PLUGIN_RUN_STATE_FAILED,
    PLUGIN_RUN_STATE_HEALTHY,
    PLUGIN_RUN_STATE_INITIALIZING,
    PLUGIN_RUN_STATE_UNKNOWN,
)
from api.schemas.schemas import IngredientTemplateRegistration, ScheduledTaskCreate
from api.services.recipe_ingredient_cleanup import delete_recipe_ingredients_safely
from api.services.ingredient_registry import (
    IngredientRegistrationConflictError,
    ingredient_identity_map,
    register_ingredient_templates,
)
from api.types import JSONObject

PLUGIN_BOOTSTRAP_MARKER_FILE = "/app/config/poundcake_bootstrap_ready"
PLUGIN_SHORT_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz23456789"
PLUGIN_BOOTSTRAP_REQ_ID = "SYSTEM-PLUGIN-BOOTSTRAP"
# PoundCake-owned control-plane services are registered into the shared
# service_plugins registry as plugin_type="internal_plugin". External provider
# plugins remain filesystem-discovered from api/plugins/<service_type>/plugin.py.
INTERNAL_PLUGIN_DEFAULTS: tuple[tuple[str, str, int, str | None, int | None], ...] = (
    ("credential-manager", "CREDENTIAL_MANAGER_INTERVAL", 300, None, None),
    ("prep-chef", "PREP_INTERVAL", 5, "PREP_CHEF_LIMIT", 50),
    ("expediter-runner", "EXPEDITER_RUNNER_INTERVAL", 2, "EXPEDITER_RUNNER_LIMIT", 50),
    ("timer", "TIMER_INTERVAL", 10, "TIMER_LIMIT", 50),
    ("dishwasher", "DISHWASHER_INTERVAL", 300, None, None),
)
assert {service_type for service_type, *_rest in INTERNAL_PLUGIN_DEFAULTS} == INTERNAL_SERVICE_TYPES
INTERNAL_HMAC_CREDENTIAL_TYPE = "internal_control_plane_hmac"
STACKSTORM_CREDENTIAL_TYPE = "stackstorm_api_key"
logger = get_logger(__name__)


@asynccontextmanager
async def _maybe_transaction(db: AsyncSession | None) -> AsyncIterator[None]:
    if db is None:
        yield
        return
    async with db.begin():
        yield


class PluginBootstrapError(RuntimeError):
    """Raised when enabled service plugin bootstrap cannot complete."""


def _ingredient_identity(payload: JSONObject) -> tuple[str, str, str, str]:
    return (
        str(payload.get("service_type") or "").strip().lower(),
        str(payload.get("service_exec") or "").strip(),
        str(payload.get("destination_target") or "").strip(),
        str(payload.get("task_key_template") or "").strip(),
    )


def _generate_plugin_short_id(length: int = 8) -> str:
    """Generate a durable, URL-safe short id for plugin-scoped system work."""
    return "".join(secrets.choice(PLUGIN_SHORT_ID_ALPHABET) for _ in range(length))


async def _new_unique_plugin_short_id(db: AsyncSession) -> str:
    for _ in range(20):
        candidate = _generate_plugin_short_id()
        result = await db.execute(
            select(ServicePlugin).where(ServicePlugin.plugin_short_id == candidate)
        )
        if result.scalar_one_or_none() is None:
            return candidate
    raise PluginBootstrapError("could not generate a unique service plugin short id")


def _plugin_registration_metadata(plugin: ServicePluginManifest) -> tuple[str, str | None]:
    service_type = plugin.service_type.strip().lower()
    requested_tier = (plugin.plugin_tier or "community").strip().lower()
    requested_log_key = (plugin.plugin_log_key or "").strip().lower()
    if requested_tier == "supported" and service_type in SUPPORTED_PLUGIN_TYPES:
        return "supported", requested_log_key or service_type
    return "community", None


def _capabilities_hash(plugin: ServicePluginManifest) -> str:
    payload: JSONObject = {
        "service_type": plugin.service_type,
        "ingredient_templates": list(plugin.ingredient_templates),
        "capability_templates": list(plugin.capability_templates),
        "recipe_templates": list(plugin.recipe_templates),
        "scheduled_tasks": list(plugin.scheduled_tasks),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _internal_plugin_defaults() -> list[tuple[str, int, int | None]]:
    return [
        (
            service_type,
            _env_positive_int(interval_env_name, interval_default),
            (
                _env_positive_int(limit_env_name, limit_default)
                if limit_env_name is not None and limit_default is not None
                else None
            ),
        )
        for (
            service_type,
            interval_env_name,
            interval_default,
            limit_env_name,
            limit_default,
        ) in INTERNAL_PLUGIN_DEFAULTS
    ]


def _env_positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _new_internal_hmac_secret() -> str:
    return secrets.token_urlsafe(48)


async def _import_stackstorm_api_key_credential(
    db: AsyncSession,
    plugins: list[ServicePluginManifest],
) -> JSONObject:
    if not any(plugin.service_type.strip().lower() == "stackstorm" for plugin in plugins):
        return {"processed": 0, "imported": 0, "errors": 0, "reason": "stackstorm plugin disabled"}
    return {
        "processed": 1,
        "imported": 0,
        "errors": 0,
        "reason": "manual credential provisioning required",
    }


def _internal_hmac_key_id(service_type: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", service_type.strip().lower()).strip("-")
    return f"poundcake-control-plane:{normalized or 'plugin'}"


async def _upsert_internal_hmac_credential(
    db: AsyncSession,
    row: ServicePlugin,
    *,
    now: datetime,
) -> bool:
    credential_key_id = _internal_hmac_key_id(row.service_type)
    from api.models.models import ServiceIdentityCredential

    result = await db.execute(
        select(ServiceIdentityCredential).where(
            ServiceIdentityCredential.service_plugin_id == row.id,
            ServiceIdentityCredential.credential_type == INTERNAL_HMAC_CREDENTIAL_TYPE,
            ServiceIdentityCredential.credential_key_id == credential_key_id,
        )
    )
    credential = result.scalar_one_or_none()
    secret = ""
    if credential is not None:
        try:
            existing_payload = decrypt_service_identity_payload(credential.encrypted_payload)
        except ServicePluginCredentialError as exc:
            row.credential_status = "error"
            row.credential_error = str(exc)[:2000]
            return False
        secret = str(existing_payload.get("hmac_secret") or "").strip()
    if not secret:
        secret = _new_internal_hmac_secret()

    payload: JSONObject = {
        "hmac_key_id": credential_key_id,
        "hmac_secret": secret,
        "auth_scope": "poundcake_control_plane",
        "service_type": row.service_type,
    }
    try:
        await upsert_internal_hmac_credential(
            db,
            row,
            credential_key_id=credential_key_id,
            payload=payload,
        )
    except ServicePluginCredentialError as exc:
        row.credential_status = "error"
        row.credential_error = str(exc)[:2000]
        return False
    row.credential_status = "ready"
    row.credential_error = None
    row.last_credential_bootstrap_at = row.last_credential_bootstrap_at or now
    return True


def _plugin_log_extra(plugin: ServicePluginManifest, **values: object) -> JSONObject:
    extra: JSONObject = {
        "req_id": PLUGIN_BOOTSTRAP_REQ_ID,
        "service_type": plugin.service_type.strip().lower(),
        "ingredient_template_count": len(plugin.ingredient_templates),
        "recipe_template_count": len(plugin.recipe_templates),
        "scheduled_task_template_count": len(plugin.scheduled_tasks),
    }
    extra.update(values)
    return extra


def _safe_hook_result_summary(result: JSONObject) -> JSONObject:
    summary: JSONObject = {"result_keys": sorted(str(key) for key in result.keys())}
    for key, value in result.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            summary[str(key)] = value
        elif isinstance(value, list):
            summary[f"{key}_count"] = len(value)
        elif isinstance(value, dict):
            summary[f"{key}_keys"] = sorted(str(item) for item in value.keys())
    return summary


def _is_bootstrap_failure_message(value: str | None) -> bool:
    normalized = (value or "").strip().lower()
    return normalized.startswith(
        (
            "invalid plugin ",
            "missing plugin ",
            "service plugin bootstrap hook failed",
            "service plugin helper dependency unavailable",
        )
    )


async def _mark_service_plugin_failed(
    db: AsyncSession,
    *,
    service_type: str,
    message: str,
    credential_failed: bool = False,
) -> None:
    normalized = service_type.strip().lower()
    now = utc_now_db()
    result = await db.execute(select(ServicePlugin).where(ServicePlugin.service_type == normalized))
    row = result.scalar_one_or_none()
    if row is None:
        row = ServicePlugin(
            service_type=normalized,
            plugin_short_id=await _new_unique_plugin_short_id(db),
            plugin_type="external_plugin",
            plugin_tier="community",
            enabled=True,
            status_message=message[:2000],
            health_status=PLUGIN_RUN_STATE_FAILED,
            health_message=message[:2000],
            credential_status="error" if credential_failed else "unknown",
            credential_error=message[:2000] if credential_failed else None,
            registered_ingredient_count=0,
            registered_recipe_count=0,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        await db.flush()
        return
    row.enabled = True
    row.plugin_type = "external_plugin"
    row.status_message = message[:2000]
    row.health_status = PLUGIN_RUN_STATE_FAILED
    row.health_message = message[:2000]
    if credential_failed and row.credential_status != "ready":
        row.credential_status = "error"
        row.credential_error = message[:2000]
    row.updated_at = now


async def _register_plugin_ingredients(
    db: AsyncSession,
    plugins: list[ServicePluginManifest],
) -> tuple[JSONObject, dict[tuple[str, str, str, str], Ingredient], set[str]]:
    ingredient_payloads: list[IngredientTemplateRegistration] = []
    failed_plugins: set[str] = set()
    for plugin in plugins:
        service_type = plugin.service_type.strip().lower()
        plugin_payloads: list[IngredientTemplateRegistration] = []
        for raw_template in plugin.ingredient_templates:
            try:
                ingredient = IngredientTemplateRegistration.model_validate(raw_template)
                validate_payload_schema(ingredient.payload_schema)
            except (ServicePluginContractError, ValueError) as exc:
                message = f"Invalid plugin ingredient template for {service_type}: {exc}"
                await _mark_service_plugin_failed(db, service_type=service_type, message=message)
                logger.error(
                    "Service plugin ingredient template validation failed",
                    extra={
                        "req_id": PLUGIN_BOOTSTRAP_REQ_ID,
                        "bootstrap_stage": "plugin_ingredient_registration",
                        "service_type": service_type,
                        "error": str(exc),
                    },
                )
                failed_plugins.add(service_type)
                plugin_payloads = []
                break
            plugin_payloads.append(ingredient)
        ingredient_payloads.extend(plugin_payloads)

    try:
        registration = await register_ingredient_templates(db, ingredient_payloads, flush=True)
    except IngredientRegistrationConflictError as exc:
        raise PluginBootstrapError("Duplicate plugin ingredient template identity") from exc

    return (
        {
            **registration.stats(),
            "errors": len(failed_plugins),
            "failed_plugins": sorted(failed_plugins),
        },
        ingredient_identity_map(registration.rows),
        failed_plugins,
    )


def _recipe_step_payload(
    step: JSONObject,
    ingredient_map: dict[tuple[str, str, str, str], Ingredient],
) -> JSONObject:
    identity = _ingredient_identity(step)
    ingredient = ingredient_map.get(identity)
    if ingredient is None:
        raise PluginBootstrapError(f"Plugin recipe references unknown ingredient: {identity}")

    service_payload = step.get("service_payload")
    if service_payload is not None and not isinstance(service_payload, dict):
        raise PluginBootstrapError(
            f"Plugin recipe step service_payload invalid for {identity}: "
            "service_payload must be an object when provided"
        )
    resolved_payload = dict(ingredient.service_payload_template or {})
    if service_payload:
        resolved_payload.update(service_payload)
    resolved_parameters = dict(ingredient.service_exec_parameters or {})
    parameter_overrides = step.get("service_exec_parameters_override")
    if parameter_overrides is not None and not isinstance(parameter_overrides, dict):
        raise PluginBootstrapError(
            f"Plugin recipe step service_exec_parameters_override invalid for {identity}: "
            "service_exec_parameters_override must be an object when provided"
        )
    if parameter_overrides:
        resolved_parameters.update(parameter_overrides)
    service_payload_from_order = bool(step.get("service_payload_from_order"))
    try:
        validate_service_operation(resolved_parameters or None)
        if not service_payload_from_order:
            validate_service_payload_for_operation(
                resolved_payload,
                ingredient.payload_schema,
                resolved_parameters or None,
            )
    except ServicePluginContractError as exc:
        raise PluginBootstrapError(
            f"Plugin recipe step service_payload invalid for {identity}: {exc}"
        ) from exc

    payload = {
        "ingredient_id": ingredient.id,
        "step_order": int(step.get("step_order") or 1),
        "on_success": step.get("on_success", "continue"),
        "parallel_group": int(step.get("parallel_group") or 0),
        "depth": int(step.get("depth") or 0),
        "service_payload": service_payload,
        "service_exec_parameters_override": step.get("service_exec_parameters_override"),
        "service_exec_expected_secs": step.get("service_exec_expected_secs"),
        "service_exec_timeout": step.get("service_exec_timeout"),
        "service_exec_expected_outcome": step.get("service_exec_expected_outcome"),
        "run_phase": step.get("run_phase", "both"),
        "run_condition": step.get("run_condition", "always"),
    }
    if service_payload_from_order:
        payload["service_payload_from_order"] = True
    return payload


async def _register_plugin_recipes(
    db: AsyncSession,
    ingredient_map: dict[tuple[str, str, str, str], Ingredient],
    plugins: list[ServicePluginManifest],
) -> JSONObject:
    created = 0
    updated = 0
    processed = 0
    now = utc_now_db()

    recipe_templates: list[JSONObject] = []
    for plugin in plugins:
        recipe_templates.extend(plugin.recipe_templates)
    for recipe_template in recipe_templates:
        processed += 1
        name = str(recipe_template.get("name") or "").strip()
        if not name:
            raise PluginBootstrapError("Plugin recipe template name must not be empty")
        raw_steps = recipe_template.get("recipe_ingredients")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise PluginBootstrapError(f"Plugin recipe {name} must define recipe_ingredients")
        steps = [
            _recipe_step_payload(step, ingredient_map)
            for step in raw_steps
            if isinstance(step, dict)
        ]
        if len(steps) != len(raw_steps):
            raise PluginBootstrapError(f"Plugin recipe {name} contains non-object steps")

        result = await db.execute(select(Recipe).where(Recipe.name == name))
        recipe = result.scalars().first()
        if recipe is None:
            recipe = Recipe(
                name=name,
                description=recipe_template.get("description"),
                enabled=bool(recipe_template.get("enabled", True)),
                clear_timeout_sec=recipe_template.get("clear_timeout_sec"),
                deleted=False,
                deleted_at=None,
                updated_at=now,
            )
            db.add(recipe)
            await db.flush()
            created += 1
        else:
            recipe.description = recipe_template.get("description")
            recipe.enabled = bool(recipe_template.get("enabled", True))
            recipe.clear_timeout_sec = recipe_template.get("clear_timeout_sec")
            recipe.deleted = False
            recipe.deleted_at = None
            recipe.updated_at = now
            updated += 1

        await delete_recipe_ingredients_safely(db, recipe_id=recipe.id)
        for step in sorted(steps, key=lambda item: int(item["step_order"])):
            db.add(
                RecipeIngredient(
                    recipe_id=recipe.id,
                    ingredient_id=int(step["ingredient_id"]),
                    step_order=int(step["step_order"]),
                    on_success=step["on_success"],
                    parallel_group=int(step["parallel_group"]),
                    depth=int(step["depth"]),
                    service_payload=step["service_payload"],
                    service_exec_parameters_override=step["service_exec_parameters_override"],
                    service_exec_expected_secs=step["service_exec_expected_secs"],
                    service_exec_timeout=step["service_exec_timeout"],
                    service_exec_expected_outcome=step["service_exec_expected_outcome"],
                    run_phase=step["run_phase"],
                    run_condition=step["run_condition"],
                )
            )

    return {
        "created": created,
        "updated": updated,
        "processed": processed,
        "errors": 0,
    }


async def _register_service_plugins(
    db: AsyncSession,
    plugins: list[ServicePluginManifest],
) -> JSONObject:
    now = utc_now_db()
    created = 0
    updated = 0
    processed = 0
    for plugin in plugins:
        start = time.perf_counter()
        processed += 1
        service_type = plugin.service_type.strip().lower()
        health_result = None
        try:
            health_result = plugin.adapter_factory().health_check()
        except Exception:  # noqa: BLE001
            health_result = None
        logger.info(
            "Service plugin metadata registration start",
            extra=_plugin_log_extra(plugin, bootstrap_stage="service_plugin_registration"),
        )
        result = await db.execute(
            select(ServicePlugin).where(ServicePlugin.service_type == service_type)
        )
        row = result.scalar_one_or_none()
        plugin_tier, plugin_log_key = _plugin_registration_metadata(plugin)
        if row is None:
            row = ServicePlugin(
                service_type=service_type,
                plugin_short_id=await _new_unique_plugin_short_id(db),
                plugin_type="external_plugin",
                plugin_tier=plugin_tier,
                plugin_log_key=plugin_log_key,
                enabled=True,
                health_status=PLUGIN_RUN_STATE_INITIALIZING,
                capabilities_hash=_capabilities_hash(plugin),
                registered_ingredient_count=len(plugin.ingredient_templates),
                registered_recipe_count=len(plugin.recipe_templates),
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            await db.flush()
            created += 1
            action = "created"
        else:
            if not str(getattr(row, "plugin_short_id", "") or "").strip():
                row.plugin_short_id = await _new_unique_plugin_short_id(db)
            row.plugin_type = "external_plugin"
            row.plugin_tier = plugin_tier
            row.plugin_log_key = plugin_log_key
            row.enabled = True
            if str(row.health_status or "").strip().lower() in {
                "",
                PLUGIN_RUN_STATE_UNKNOWN,
                PLUGIN_RUN_STATE_DISABLED,
            }:
                row.health_status = PLUGIN_RUN_STATE_INITIALIZING
            if _is_bootstrap_failure_message(row.status_message):
                row.status_message = None
            if _is_bootstrap_failure_message(row.health_message):
                row.health_message = None
                if str(row.health_status or "").strip().lower() == PLUGIN_RUN_STATE_FAILED:
                    row.health_status = PLUGIN_RUN_STATE_INITIALIZING
            row.capabilities_hash = _capabilities_hash(plugin)
            row.registered_ingredient_count = len(plugin.ingredient_templates)
            row.registered_recipe_count = len(plugin.recipe_templates)
            row.updated_at = now
            updated += 1
            action = "updated"
        if health_result is not None:
            status = str(health_result.status or "").strip().lower()
            if status:
                row.health_status = status
            row.health_message = health_result.message
            row.health_error_code = health_result.error_code
            row.health_latency_ms = health_result.latency_ms
            row.health_details = health_result.details
            row.last_health_check_at = now
            row.health_check_state = "idle"
            if row.health_status == PLUGIN_RUN_STATE_HEALTHY:
                row.last_success_at = now
                row.consecutive_failures = 0
        logger.info(
            "Service plugin metadata registration complete",
            extra=_plugin_log_extra(
                plugin,
                bootstrap_stage="service_plugin_registration",
                plugin_short_id=row.plugin_short_id,
                action=action,
                elapsed_ms=int((time.perf_counter() - start) * 1000),
            ),
        )
    return {
        "created": created,
        "updated": updated,
        "processed": processed,
        "errors": 0,
    }


async def _register_internal_service_plugins(db: AsyncSession) -> JSONObject:
    now = utc_now_db()
    created = 0
    updated = 0
    processed = 0
    for service_type, run_interval_seconds, query_limit in _internal_plugin_defaults():
        start = time.perf_counter()
        processed += 1
        logger.info(
            "Internal service plugin metadata registration start",
            extra={
                "req_id": PLUGIN_BOOTSTRAP_REQ_ID,
                "bootstrap_stage": "internal_service_plugin_registration",
                "service_type": service_type,
                "run_interval_seconds": run_interval_seconds,
                "query_limit": query_limit,
            },
        )
        result = await db.execute(
            select(ServicePlugin).where(ServicePlugin.service_type == service_type)
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = ServicePlugin(
                service_type=service_type,
                plugin_short_id=await _new_unique_plugin_short_id(db),
                plugin_type="internal_plugin",
                plugin_tier="supported",
                plugin_log_key=service_type,
                enabled=True,
                run_interval_seconds=run_interval_seconds,
                query_limit=query_limit,
                status_message=None,
                health_status=PLUGIN_RUN_STATE_UNKNOWN,
                health_message="Internal service plugin registered",
                credential_status="unknown",
                capabilities_hash=None,
                registered_ingredient_count=0,
                registered_recipe_count=0,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            await db.flush()
            created += 1
            action = "created"
        else:
            row.plugin_type = "internal_plugin"
            row.plugin_tier = "supported"
            row.plugin_log_key = service_type
            if row.run_interval_seconds is None:
                row.run_interval_seconds = run_interval_seconds
            if row.query_limit is None:
                row.query_limit = query_limit
            if str(row.health_status or "").strip().lower() in {"", PLUGIN_RUN_STATE_INITIALIZING}:
                row.health_status = PLUGIN_RUN_STATE_UNKNOWN
            updated += 1
            action = "updated"
        if not str(row.credential_status or "").strip():
            row.credential_status = "unknown"
        row.status_message = "Internal service plugin registered"
        row.registered_ingredient_count = 0
        row.registered_recipe_count = 0
        row.updated_at = now
        logger.info(
            "Internal service plugin metadata registration complete",
            extra={
                "req_id": PLUGIN_BOOTSTRAP_REQ_ID,
                "bootstrap_stage": "internal_service_plugin_registration",
                "service_type": service_type,
                "plugin_short_id": row.plugin_short_id,
                "action": action,
                "elapsed_ms": int((time.perf_counter() - start) * 1000),
            },
        )
    return {
        "created": created,
        "updated": updated,
        "processed": processed,
        "errors": 0,
    }


def _core_scheduled_task_templates(now: datetime) -> list[JSONObject]:
    return []


def _scheduled_task_contract(row: ScheduledTask) -> JSONObject:
    return {
        "task_key": row.task_key,
        "task_type": row.task_type,
        "service_type": row.service_type,
        "service_exec": row.service_exec,
        "source": row.source,
        "task_payload": row.task_payload,
        "task_parameters": row.task_parameters,
        "expected_outcome": row.expected_outcome,
    }


def _scheduled_task_template_contract(payload: ScheduledTaskCreate) -> JSONObject:
    return {
        "task_key": payload.task_key,
        "task_type": payload.task_type,
        "service_type": payload.service_type,
        "service_exec": payload.service_exec,
        "source": payload.source,
        "task_payload": payload.task_payload,
        "task_parameters": payload.task_parameters,
        "expected_outcome": payload.expected_outcome,
    }


def _validate_scheduled_service_execution(
    payload: ScheduledTaskCreate,
    ingredient_map: dict[tuple[str, str, str, str], Ingredient],
) -> None:
    if payload.task_type != "service_execution":
        return
    service_type = (payload.service_type or "").strip().lower()
    service_exec = (payload.service_exec or "").strip()
    task_payload = payload.task_payload or {}
    candidates = [
        ingredient
        for identity, ingredient in ingredient_map.items()
        if identity[0] == service_type and identity[1] == service_exec
    ]
    if not candidates:
        raise PluginBootstrapError(
            f"Scheduled task {payload.task_key} references unknown service execution "
            f"{service_type}/{service_exec}"
        )
    errors: list[str] = []
    for ingredient in candidates:
        try:
            task_parameters = payload.task_parameters or ingredient.service_exec_parameters
            validate_service_operation(task_parameters)
            validate_service_payload_for_operation(
                task_payload,
                ingredient.payload_schema,
                task_parameters,
            )
            return
        except ServicePluginContractError as exc:
            errors.append(str(exc))
    raise PluginBootstrapError(
        f"Scheduled task {payload.task_key} task_payload invalid: {'; '.join(errors)}"
    )


def _initial_scheduled_task_next_run_at(
    payload: ScheduledTaskCreate,
    now: datetime,
) -> datetime | None:
    if payload.task_type == "plugin_health_check":
        return now
    return now + timedelta(seconds=max(1, payload.run_interval_seconds))


async def _register_scheduled_tasks(
    db: AsyncSession,
    ingredient_map: dict[tuple[str, str, str, str], Ingredient],
    plugins: list[ServicePluginManifest],
) -> JSONObject:
    now = utc_now_db()
    plugin_task_templates: list[JSONObject] = []
    for plugin in plugins:
        plugin_task_templates.extend(plugin.scheduled_tasks)
    raw_templates = [
        *_core_scheduled_task_templates(now),
        *plugin_task_templates,
    ]
    payloads: list[ScheduledTaskCreate] = []
    for raw_template in raw_templates:
        try:
            payload = ScheduledTaskCreate.model_validate(raw_template)
            _validate_scheduled_service_execution(payload, ingredient_map)
        except (ServicePluginContractError, ValueError) as exc:
            raise PluginBootstrapError(f"Invalid scheduled task template: {exc}") from exc
        payloads.append(payload)

    task_keys = [payload.task_key for payload in payloads]
    if len(set(task_keys)) != len(task_keys):
        raise PluginBootstrapError("Duplicate scheduled task template task_key")

    created = 0
    unchanged = 0
    updated = 0
    for payload in payloads:
        result = await db.execute(
            select(ScheduledTask).where(ScheduledTask.task_key == payload.task_key)
        )
        row = result.scalar_one_or_none()
        if row is None:
            db.add(
                ScheduledTask(
                    task_key=payload.task_key,
                    task_type=payload.task_type,
                    service_type=payload.service_type,
                    service_exec=payload.service_exec,
                    source=payload.source,
                    is_enabled=payload.is_enabled,
                    run_interval_seconds=payload.run_interval_seconds,
                    next_run_at=_initial_scheduled_task_next_run_at(payload, now),
                    priority=payload.priority,
                    timeout_seconds=payload.timeout_seconds,
                    task_payload=payload.task_payload,
                    task_parameters=payload.task_parameters,
                    expected_outcome=payload.expected_outcome,
                    status="idle" if payload.is_enabled else "disabled",
                    created_at=now,
                    updated_at=now,
                )
            )
            created += 1
            continue
        if _scheduled_task_contract(row) != _scheduled_task_template_contract(payload):
            row.task_type = payload.task_type
            row.service_type = payload.service_type
            row.service_exec = payload.service_exec
            row.source = payload.source
            row.task_payload = payload.task_payload
            row.task_parameters = payload.task_parameters
            row.expected_outcome = payload.expected_outcome
            row.updated_at = now
            updated += 1
        else:
            unchanged += 1

    return {
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "processed": len(payloads),
        "errors": 0,
    }


async def _run_plugin_bootstrap_hooks(
    db: AsyncSession,
    plugins: list[ServicePluginManifest],
) -> JSONObject:
    available_helpers = {
        plugin.service_type.strip().lower(): plugin.helper_factory()
        for plugin in plugins
        if plugin.helper_factory is not None
    }
    processed = 0
    errors = 0
    hooks: dict[str, JSONObject] = {}
    for plugin in plugins:
        bootstrap_factory = plugin.bootstrap_factory
        if bootstrap_factory is None:
            continue
        service_type = plugin.service_type.strip().lower()
        start = time.perf_counter()
        processed += 1
        logger.info(
            "Service plugin bootstrap hook start",
            extra={
                "req_id": PLUGIN_BOOTSTRAP_REQ_ID,
                "service_type": service_type,
                "bootstrap_stage": "plugin_bootstrap_hook",
            },
        )
        try:
            helpers = (
                {service_type: available_helpers[service_type]}
                if service_type in available_helpers
                else {}
            )
            async with _maybe_transaction(db):
                result = await bootstrap_factory(db, helpers)  # type: ignore[operator]
        except PluginBootstrapError as exc:
            errors += 1
            async with _maybe_transaction(db):
                await _mark_service_plugin_failed(db, service_type=service_type, message=str(exc))
            logger.error(
                "Service plugin bootstrap hook failed",
                extra={
                    "req_id": PLUGIN_BOOTSTRAP_REQ_ID,
                    "service_type": service_type,
                    "bootstrap_stage": "plugin_bootstrap_hook",
                    "elapsed_ms": int((time.perf_counter() - start) * 1000),
                    "error": str(exc),
                },
            )
            hooks[service_type] = {"status": "failed", "error": str(exc)}
            continue
        except Exception as exc:  # noqa: BLE001
            errors += 1
            message = f"Plugin bootstrap hook failed for service_type={service_type!r}: {exc}"
            async with _maybe_transaction(db):
                await _mark_service_plugin_failed(db, service_type=service_type, message=message)
            logger.error(
                "Service plugin bootstrap hook failed",
                extra={
                    "req_id": PLUGIN_BOOTSTRAP_REQ_ID,
                    "service_type": service_type,
                    "bootstrap_stage": "plugin_bootstrap_hook",
                    "elapsed_ms": int((time.perf_counter() - start) * 1000),
                    "error": str(exc),
                },
            )
            hooks[service_type] = {"status": "failed", "error": str(exc)}
            continue
        hooks[service_type] = result
        logger.info(
            "Service plugin bootstrap hook complete",
            extra={
                "req_id": PLUGIN_BOOTSTRAP_REQ_ID,
                "service_type": service_type,
                "bootstrap_stage": "plugin_bootstrap_hook",
                "elapsed_ms": int((time.perf_counter() - start) * 1000),
                "result_summary": _safe_hook_result_summary(result),
            },
        )
    return {
        "processed": processed,
        "errors": errors,
        "hooks": hooks,
    }


def _deferred_manifest_sync_stats(*, processed: int, authority: str) -> JSONObject:
    return {
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "retired": 0,
        "processed": processed,
        "errors": 0,
        "deferred": processed,
        "authority": authority,
    }


def _deferred_route_sync_stats(*, processed: int, authority: str) -> JSONObject:
    return {
        "processed": processed,
        "errors": 0,
        "deferred": processed,
        "authority": authority,
    }


async def _discover_healthy_plugins(
    db: AsyncSession,
    *,
    credential_failed: bool = False,
) -> tuple[list[ServicePluginManifest], list[JSONObject], list[JSONObject]]:
    enabled_plugins, plugin_load_failures = get_enabled_plugins_for_bootstrap()
    for failure in plugin_load_failures:
        await _mark_service_plugin_failed(
            db,
            service_type=str(failure["service_type"]),
            message=str(failure["error"]),
            credential_failed=credential_failed,
        )
    helper_dependency_failures = []
    available_capabilities = {
        plugin.service_type.strip().lower(): sorted(
            {str(item).strip().lower() for item in plugin.helper_capabilities}
        )
        for plugin in enabled_plugins
    }
    for plugin in enabled_plugins:
        service_type = plugin.service_type.strip().lower()
        missing = missing_helper_capabilities_for(plugin, available_capabilities)
        if not missing:
            continue
        message = f"{service_type} helper dependency failure: " + "; ".join(
            f"{provider} requires {', '.join(capabilities)}"
            for provider, capabilities in sorted(missing.items())
        )
        helper_dependency_failures.append(
            {
                "service_type": service_type,
                "error": message,
            }
        )
        await _mark_service_plugin_failed(
            db,
            service_type=service_type,
            message=message,
            credential_failed=credential_failed,
        )
    healthy_plugins = [
        plugin
        for plugin in enabled_plugins
        if plugin.service_type.strip().lower()
        not in {str(item["service_type"]) for item in helper_dependency_failures}
    ]
    return healthy_plugins, plugin_load_failures, helper_dependency_failures


async def bootstrap_plugin_registry(db: AsyncSession) -> JSONObject:
    """Initialize startup-only plugin registry metadata and bootstrap hooks."""
    async with db.begin():
        internal_plugin_stats = await _register_internal_service_plugins(db)
        enabled_plugins, plugin_load_failures, helper_dependency_failures = (
            await _discover_healthy_plugins(db)
        )
        logger.info(
            "Service plugin bootstrap discovered enabled plugins",
            extra={
                "req_id": PLUGIN_BOOTSTRAP_REQ_ID,
                "bootstrap_stage": "discovery",
                "plugin_count": len(enabled_plugins),
                "plugins": [plugin.service_type for plugin in enabled_plugins],
                "plugin_load_failures": plugin_load_failures,
            },
        )
        service_plugin_stats = await _register_service_plugins(db, enabled_plugins)
        ingredient_stats = _deferred_manifest_sync_stats(
            processed=sum(len(plugin.ingredient_templates) for plugin in enabled_plugins),
            authority="dishwasher",
        )
        recipe_stats = _deferred_manifest_sync_stats(
            processed=sum(len(plugin.recipe_templates) for plugin in enabled_plugins),
            authority="dishwasher",
        )
        scheduled_task_stats = _deferred_manifest_sync_stats(
            processed=sum(len(plugin.scheduled_tasks) for plugin in enabled_plugins),
            authority="dishwasher",
        )
        communication_route_stats = _deferred_route_sync_stats(
            processed=sum(
                1
                for plugin in enabled_plugins
                for template in plugin.capability_templates
                if str(template.get("mode") or "").strip().lower() == "communication"
            ),
            authority="dishwasher",
        )
    plugin_hook_stats = await _run_plugin_bootstrap_hooks(db, enabled_plugins)
    return {
        "internal_plugins": internal_plugin_stats,
        "service_plugins": service_plugin_stats,
        "plugin_load_failures": {
            "processed": len(plugin_load_failures),
            "errors": len(plugin_load_failures),
            "failures": plugin_load_failures,
        },
        "helper_dependency_failures": {
            "processed": len(helper_dependency_failures),
            "errors": len(helper_dependency_failures),
            "failures": helper_dependency_failures,
        },
        "ingredients": ingredient_stats,
        "recipes": recipe_stats,
        "scheduled_tasks": scheduled_task_stats,
        "plugin_bootstrap_hooks": plugin_hook_stats,
        "communication_routes": communication_route_stats,
    }


async def bootstrap_service_identities(db: AsyncSession) -> JSONObject:
    """Create or refresh internal control-plane HMAC identities."""
    now = utc_now_db()
    created = 0
    updated = 0
    processed = 0
    errors = 0
    async with db.begin():
        for service_type, _interval, _query_limit in _internal_plugin_defaults():
            processed += 1
            result = await db.execute(
                select(ServicePlugin).where(ServicePlugin.service_type == service_type)
            )
            row = result.scalar_one_or_none()
            if row is None:
                errors += 1
                raise PluginBootstrapError(
                    f"internal service plugin must exist before identity bootstrap: {service_type}"
                )
            before_status = str(row.credential_status or "").strip().lower()
            success = await _upsert_internal_hmac_credential(db, row, now=now)
            row.updated_at = now
            if success:
                row.status_message = "Internal service plugin registered"
                if before_status == "ready":
                    updated += 1
                else:
                    created += 1
            else:
                errors += 1
    return {
        "created": created,
        "updated": updated,
        "processed": processed,
        "errors": errors,
    }


async def bootstrap_adapter_credentials(db: AsyncSession) -> JSONObject:
    """Bootstrap adapter-owned startup credentials through credential-manager."""
    async with db.begin():
        enabled_plugins, plugin_load_failures, helper_dependency_failures = (
            await _discover_healthy_plugins(db, credential_failed=True)
        )
    processed = 0
    errors = 0
    credential_bootstrapped = 0
    skipped = 0
    plugins: dict[str, JSONObject] = {}
    for plugin in enabled_plugins:
        service_type = plugin.service_type.strip().lower()
        processed += 1
        try:
            adapter = plugin.adapter_factory()
            if not isinstance(adapter, ExecutionAdapter):
                raise PluginBootstrapError(
                    f"service_type={service_type!r} adapter_factory must return ExecutionAdapter"
                )
            await adapter.bootstrap_credentials()
        except ServicePluginCredentialError as exc:
            errors += 1
            await mark_adapter_credential_error(service_type=service_type, error=str(exc))
            plugins[service_type] = {"status": "failed", "error": str(exc)}
            continue
        except Exception as exc:  # noqa: BLE001
            errors += 1
            await mark_adapter_credential_error(service_type=service_type, error=str(exc))
            plugins[service_type] = {"status": "failed", "error": str(exc)}
            continue
        credential_bootstrapped += 1
        plugins[service_type] = {"status": "ready"}
    return {
        "processed": processed,
        "credential_bootstrapped": credential_bootstrapped,
        "skipped": skipped,
        "errors": errors,
        "plugin_load_failures": plugin_load_failures,
        "helper_dependency_failures": helper_dependency_failures,
        "plugins": plugins,
    }


async def bootstrap_enabled_plugins(db: AsyncSession) -> JSONObject:
    """Convenience wrapper that runs all startup stages in one process."""
    plugin_registry_stats = await bootstrap_plugin_registry(db)
    service_identity_stats = await bootstrap_service_identities(db)
    adapter_credential_stats = await bootstrap_adapter_credentials(db)
    return {
        "plugin_registry": plugin_registry_stats,
        "service_identities": service_identity_stats,
        "adapter_credentials": adapter_credential_stats,
    }


def mark_plugin_bootstrap_ready(path: str = PLUGIN_BOOTSTRAP_MARKER_FILE) -> None:
    """Write the bootstrap marker consumed by API health checks."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as marker:
        marker.write("true\n")
