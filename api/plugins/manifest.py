"""Service plugin manifest contract."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from api.types import JSONObject
from api.plugins.contract import (
    HEALTH_CHECK_OPERATION,
    ServicePluginContractError,
    validate_service_operation,
)
from api.plugins.capabilities import (
    ServicePluginCapabilityError,
    validate_capability_template,
)
from api.plugins.internal_services import INTERNAL_SERVICE_TYPES

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from api.plugins.base import ExecutionAdapter

AdapterFactory = Callable[[], "ExecutionAdapter"]
HelperFactory = Callable[[], object]
PluginBootstrapFactory = Callable[
    ["AsyncSession", Mapping[str, object]],
    Awaitable[JSONObject],
]


class ServicePluginManifestError(ValueError):
    """Raised when a service plugin manifest is malformed."""


PLUGIN_SCHEDULED_TASK_TYPES = {
    "plugin_health_check",
    "service_execution",
}
PLUGIN_TIERS = {"community", "supported"}
SUPPORTED_PLUGIN_TYPES = {
    "bakery",
    "dummy",
}
HELPER_CAPABILITY_RE = re.compile(r"[a-z0-9][a-z0-9_]*(\.[a-z0-9][a-z0-9_]*)+")
INGREDIENT_TEMPLATE_CONTROL_PLANE_FIELDS = {
    "id",
    "is_active",
    "created_at",
    "updated_at",
    "deleted",
    "deleted_at",
}
RECIPE_STEP_CONTROL_PLANE_FIELDS = {
    "id",
    "recipe_id",
    "ingredient_id",
    "is_active",
    "created_at",
    "updated_at",
    "deleted",
    "deleted_at",
}


@dataclass(frozen=True, slots=True)
class ServicePlugin:
    """Static-at-runtime service plugin descriptor."""

    service_type: str
    adapter_factory: AdapterFactory
    ingredient_templates: Sequence[JSONObject] = ()
    recipe_templates: Sequence[JSONObject] = ()
    communication_routes: Sequence[JSONObject] = ()
    scheduled_tasks: Sequence[JSONObject] = ()
    capability_templates: Sequence[JSONObject] = ()
    helper_factory: HelperFactory | None = None
    helper_capabilities: Sequence[str] = ()
    required_helper_capabilities: Mapping[str, Sequence[str]] | None = None
    # Database capabilities required by the plugin for service-layer
    # operations (e.g., plugin_operations.upsert_recipes).  Each key is
    # the plugin's own service_type and each value is a sequence of
    # capability labels that the database-access policy must grant.
    required_db_capabilities: Mapping[str, Sequence[str]] | None = None
    # Bootstrap hooks are metadata-stage only: they may validate local helpers
    # or report metadata-safe readiness, but they must not write credentials,
    # mint internal HMAC identities, or perform plugin-authored registry writes.
    bootstrap_factory: PluginBootstrapFactory | None = None
    plugin_tier: str = "community"
    plugin_log_key: str | None = None
    allow_directory_mismatch: bool = False


def validate_service_plugin(plugin: ServicePlugin, *, directory_name: str) -> ServicePlugin:
    """Validate a discovered service plugin manifest."""
    service_type = (plugin.service_type or "").strip().lower()
    if not service_type:
        raise ServicePluginManifestError("service_type must not be empty")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", service_type):
        raise ServicePluginManifestError(
            f"service_type={plugin.service_type!r} must use lowercase letters, numbers, '-' or '_'"
        )
    if service_type != directory_name and not plugin.allow_directory_mismatch:
        raise ServicePluginManifestError(
            f"service_type={service_type!r} must match plugin directory {directory_name!r}"
        )
    if service_type in INTERNAL_SERVICE_TYPES:
        raise ServicePluginManifestError(
            f"service_type={service_type!r} is reserved for an internal PoundCake service"
        )
    if not callable(plugin.adapter_factory):
        raise ServicePluginManifestError(
            f"service_type={service_type!r} adapter_factory is invalid"
        )
    try:
        adapter = plugin.adapter_factory()
    except Exception as exc:  # noqa: BLE001
        raise ServicePluginManifestError(
            f"service_type={service_type!r} adapter_factory failed"
        ) from exc
    config_schema = adapter.operator_config_schema()
    if not isinstance(config_schema, dict):
        raise ServicePluginManifestError(
            f"service_type={service_type!r} operator_config_schema must be an object"
        )
    if config_schema.get("type") != "object":
        raise ServicePluginManifestError(
            f"service_type={service_type!r} operator_config_schema.type must be object"
        )
    if not isinstance(config_schema.get("properties", {}), Mapping):
        raise ServicePluginManifestError(
            f"service_type={service_type!r} operator_config_schema.properties must be an object"
        )
    if plugin.helper_factory is not None and not callable(plugin.helper_factory):
        raise ServicePluginManifestError(f"service_type={service_type!r} helper_factory is invalid")
    helper_capabilities = _normalize_capability_sequence(
        plugin.helper_capabilities,
        service_type=service_type,
        label="helper_capabilities",
    )
    if helper_capabilities and plugin.helper_factory is None:
        raise ServicePluginManifestError(
            f"service_type={service_type!r} helper_capabilities require helper_factory"
        )
    required_helper_capabilities = plugin.required_helper_capabilities or {}
    if not isinstance(required_helper_capabilities, Mapping):
        raise ServicePluginManifestError(
            f"service_type={service_type!r} required_helper_capabilities must be an object"
        )
    for provider, capabilities in required_helper_capabilities.items():
        provider_service_type = str(provider or "").strip().lower()
        if not provider_service_type:
            raise ServicePluginManifestError(
                f"service_type={service_type!r} required_helper_capabilities provider is empty"
            )
        if provider_service_type == service_type and plugin.helper_factory is None:
            raise ServicePluginManifestError(
                f"service_type={service_type!r} cannot require its own helper without helper_factory"
            )
        _normalize_capability_sequence(
            capabilities,
            service_type=service_type,
            label=f"required_helper_capabilities[{provider_service_type}]",
        )
    required_db_capabilities = plugin.required_db_capabilities or {}
    if not isinstance(required_db_capabilities, Mapping):
        raise ServicePluginManifestError(
            f"service_type={service_type!r} required_db_capabilities must be an object"
        )
    for provider_name, capabilities in required_db_capabilities.items():
        provider_name = str(provider_name or "").strip().lower()
        if not provider_name:
            raise ServicePluginManifestError(
                f"service_type={service_type!r} required_db_capabilities entry is empty"
            )
        if not isinstance(capabilities, (list, tuple)):
            raise ServicePluginManifestError(
                f"service_type={service_type!r} required_db_capabilities[{provider_name!r}] "
                f"must be a sequence"
            )
    if plugin.bootstrap_factory is not None and not callable(plugin.bootstrap_factory):
        raise ServicePluginManifestError(
            f"service_type={service_type!r} bootstrap_factory is invalid"
        )
    if plugin.bootstrap_factory is not None:
        external_helper_providers = sorted(
            str(provider or "").strip().lower()
            for provider in required_helper_capabilities
            if str(provider or "").strip().lower() != service_type
        )
        if external_helper_providers:
            raise ServicePluginManifestError(
                f"service_type={service_type!r} bootstrap_factory may only depend on its own "
                "helper capabilities; external helper dependencies must run through "
                "service_execution ingredients: " + ", ".join(external_helper_providers)
            )
    plugin_tier = (plugin.plugin_tier or "community").strip().lower()
    if plugin_tier not in PLUGIN_TIERS:
        raise ServicePluginManifestError(
            f"service_type={service_type!r} plugin_tier must be one of {sorted(PLUGIN_TIERS)}"
        )
    plugin_log_key = (plugin.plugin_log_key or "").strip().lower()
    if plugin_log_key and not re.fullmatch(r"[a-z0-9][a-z0-9-]*", plugin_log_key):
        raise ServicePluginManifestError(
            f"service_type={service_type!r} plugin_log_key must use lowercase letters, numbers or '-'"
        )
    if plugin_tier == "supported" and service_type not in SUPPORTED_PLUGIN_TYPES:
        raise ServicePluginManifestError(
            f"service_type={service_type!r} is not approved as a supported plugin"
        )
    if plugin_log_key and (
        plugin_tier != "supported" or service_type not in SUPPORTED_PLUGIN_TYPES
    ):
        raise ServicePluginManifestError(
            f"service_type={service_type!r} cannot register plugin_log_key unless it is an approved"
            " supported plugin"
        )
    for idx, template in enumerate(plugin.ingredient_templates):
        if not isinstance(template, dict):
            raise ServicePluginManifestError(
                f"service_type={service_type!r} ingredient_templates[{idx}] must be an object"
            )
        _reject_control_plane_fields(
            template,
            forbidden=INGREDIENT_TEMPLATE_CONTROL_PLANE_FIELDS,
            service_type=service_type,
            label=f"ingredient_templates[{idx}]",
        )
        try:
            validate_service_operation(template.get("service_exec_parameters"))
        except ServicePluginContractError as exc:
            raise ServicePluginManifestError(
                f"service_type={service_type!r} ingredient_templates[{idx}]."
                f"service_exec_parameters invalid: {exc}"
            ) from exc
        if str(template.get("service_exec") or "").strip().lower() == HEALTH_CHECK_OPERATION:
            params = template.get("service_exec_parameters")
            if not isinstance(params, dict):
                raise ServicePluginManifestError(
                    f"service_type={service_type!r} ingredient_templates[{idx}] "
                    "health_check requires service_exec_parameters"
                )
            if str(params.get("operation") or "").strip().lower() != HEALTH_CHECK_OPERATION:
                raise ServicePluginManifestError(
                    f"service_type={service_type!r} ingredient_templates[{idx}] "
                    "health_check operation must be health_check"
                )
            if params.get("allowed_operations") != [HEALTH_CHECK_OPERATION]:
                raise ServicePluginManifestError(
                    f"service_type={service_type!r} ingredient_templates[{idx}] "
                    "health_check allowed_operations must be ['health_check']"
                )
    for idx, template in enumerate(plugin.recipe_templates):
        if not isinstance(template, dict):
            raise ServicePluginManifestError(
                f"service_type={service_type!r} recipe_templates[{idx}] must be an object"
            )
        for step_idx, step in enumerate(template.get("recipe_ingredients") or ()):
            if not isinstance(step, dict):
                raise ServicePluginManifestError(
                    f"service_type={service_type!r} recipe_templates[{idx}]."
                    f"recipe_ingredients[{step_idx}] must be an object"
                )
            _reject_control_plane_fields(
                step,
                forbidden=RECIPE_STEP_CONTROL_PLANE_FIELDS,
                service_type=service_type,
                label=f"recipe_templates[{idx}].recipe_ingredients[{step_idx}]",
            )
    for idx, route in enumerate(plugin.communication_routes):
        if not isinstance(route, dict):
            raise ServicePluginManifestError(
                f"service_type={service_type!r} communication_routes[{idx}] must be an object"
            )
    normalized_capabilities: list[JSONObject] = []
    for idx, template in enumerate(plugin.capability_templates):
        try:
            normalized_capabilities.append(
                validate_capability_template(
                    template,
                    service_type=service_type,
                    ingredient_templates=plugin.ingredient_templates,
                    label=f"capability_templates[{idx}]",
                )
            )
        except ServicePluginCapabilityError as exc:
            raise ServicePluginManifestError(
                f"service_type={service_type!r} capability_templates[{idx}] invalid: {exc}"
            ) from exc
    seen_task_keys: set[str] = set()
    has_health_task = False
    for idx, task in enumerate(plugin.scheduled_tasks):
        if not isinstance(task, dict):
            raise ServicePluginManifestError(
                f"service_type={service_type!r} scheduled_tasks[{idx}] must be an object"
            )
        task_type = str(task.get("task_type") or "").strip()
        if task_type not in PLUGIN_SCHEDULED_TASK_TYPES:
            raise ServicePluginManifestError(
                f"service_type={service_type!r} scheduled_tasks[{idx}].task_type must be "
                f"one of {sorted(PLUGIN_SCHEDULED_TASK_TYPES)}"
            )
        task_key = str(task.get("task_key") or "").strip()
        if not task_key:
            raise ServicePluginManifestError(
                f"service_type={service_type!r} scheduled_tasks[{idx}].task_key is required"
            )
        if task.get("next_run_at") is not None:
            raise ServicePluginManifestError(
                f"service_type={service_type!r} scheduled_tasks[{idx}].next_run_at "
                "is owned by bootstrap scheduling"
            )
        if task_key in seen_task_keys:
            raise ServicePluginManifestError(
                f"service_type={service_type!r} duplicate scheduled task_key={task_key!r}"
            )
        seen_task_keys.add(task_key)
        task_service_type = str(task.get("service_type") or "").strip().lower()
        if task_type == "plugin_health_check":
            if task_service_type != service_type:
                raise ServicePluginManifestError(
                    f"service_type={service_type!r} scheduled_tasks[{idx}].service_type "
                    f"must match the plugin for plugin_health_check"
                )
            has_health_task = True
        if task_type == "service_execution" and task_service_type != service_type:
            raise ServicePluginManifestError(
                f"service_type={service_type!r} scheduled_tasks[{idx}].service_type "
                f"must match the plugin for service_execution"
            )
    if not has_health_task:
        raise ServicePluginManifestError(
            f"service_type={service_type!r} must declare a plugin_health_check scheduled task"
        )
    if normalized_capabilities:
        object.__setattr__(plugin, "capability_templates", tuple(normalized_capabilities))
    return plugin


def _normalize_capability_sequence(
    capabilities: Sequence[str],
    *,
    service_type: str,
    label: str,
) -> list[str]:
    if isinstance(capabilities, (str, bytes)) or not isinstance(capabilities, Sequence):
        raise ServicePluginManifestError(f"service_type={service_type!r} {label} must be a list")
    normalized: list[str] = []
    for idx, capability in enumerate(capabilities):
        value = str(capability or "").strip().lower()
        if not HELPER_CAPABILITY_RE.fullmatch(value):
            raise ServicePluginManifestError(
                f"service_type={service_type!r} {label}[{idx}] must be a lowercase dotted token"
            )
        normalized.append(value)
    if len(set(normalized)) != len(normalized):
        raise ServicePluginManifestError(
            f"service_type={service_type!r} {label} contains duplicates"
        )
    return normalized


def _reject_control_plane_fields(
    payload: Mapping[str, object],
    *,
    forbidden: set[str],
    service_type: str,
    label: str,
) -> None:
    for field in sorted(forbidden.intersection(payload)):
        raise ServicePluginManifestError(
            f"service_type={service_type!r} {label}.{field} is owned by PoundCake; "
            "plugins must declare immutable ingredient contract fields and mutable recipe step "
            "composition only"
        )
