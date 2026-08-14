"""Dynamic service plugin discovery catalog."""

from __future__ import annotations

import copy
import os
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING

from api.plugins.capabilities import apply_operator_capability_overrides
from api.plugins.manifest import (
    ServicePlugin,
    ServicePluginManifestError,
    validate_service_plugin,
)
from api.services.capability_resolution import communication_routes_from_capability_catalog
from api.types import JSONObject

if TYPE_CHECKING:
    from api.plugins.registry import ExecutionAdapterRegistry

DEFAULT_ENABLED_PLUGINS = "dummy"
PLUGIN_MODULE_ROOT = "api.plugins"
PLUGIN_ROOT = Path(__file__).resolve().parent


def _enabled_plugin_names() -> list[str]:
    configured = os.getenv("POUNDCAKE_ENABLED_PLUGINS", DEFAULT_ENABLED_PLUGINS)
    names = [item.strip().lower() for item in configured.split(",") if item.strip()]
    enabled = names or [DEFAULT_ENABLED_PLUGINS]
    if "bakery" in enabled and "dummy" in enabled:
        enabled = [name for name in enabled if name != "dummy"]
    return enabled


def _discover_plugin_modules() -> dict[str, str]:
    modules: dict[str, str] = {}
    for path in sorted(PLUGIN_ROOT.iterdir()):
        if not path.is_dir() or path.name.startswith("_"):
            continue
        manifest_path = path / "plugin.py"
        if not manifest_path.is_file():
            continue
        modules[path.name] = f"{PLUGIN_MODULE_ROOT}.{path.name}.plugin"
    return modules


def _load_plugin(*, directory_name: str, module_name: str) -> ServicePlugin:
    module = import_module(module_name)
    plugin_getter = getattr(module, "get_plugin", None)
    if not callable(plugin_getter):
        raise ServicePluginManifestError(f"{module_name} must expose callable get_plugin()")
    get_plugin = plugin_getter
    plugin = get_plugin()
    if not isinstance(plugin, ServicePlugin):
        raise ServicePluginManifestError(f"{module_name}.get_plugin() must return ServicePlugin")
    return validate_service_plugin(plugin, directory_name=directory_name)


def get_enabled_plugins() -> list[ServicePlugin]:
    """Load enabled service plugin manifests."""
    plugins, failures = get_enabled_plugins_for_bootstrap()
    if failures:
        raise ServicePluginManifestError(
            "Enabled service plugin manifest not found or invalid: "
            + "; ".join(f"{failure['service_type']}: {failure['error']}" for failure in failures)
        )
    return plugins


def get_enabled_plugins_for_bootstrap() -> tuple[list[ServicePlugin], list[JSONObject]]:
    """Load enabled plugin manifests, returning per-plugin failures for bootstrap."""
    discovered = _discover_plugin_modules()
    enabled = _enabled_plugin_names()

    plugins: list[ServicePlugin] = []
    failures: list[JSONObject] = []
    seen_service_types: set[str] = set()
    for name in enabled:
        module_name = discovered.get(name)
        if module_name is None:
            failures.append(
                {
                    "service_type": name,
                    "error": "enabled service plugin manifest not found",
                }
            )
            continue
        try:
            plugin = _load_plugin(directory_name=name, module_name=module_name)
        except Exception as exc:  # noqa: BLE001
            failures.append(
                {
                    "service_type": name,
                    "error": f"enabled service plugin manifest invalid: {exc}",
                }
            )
            continue
        service_type = plugin.service_type.strip().lower()
        if service_type in seen_service_types:
            failures.append(
                {
                    "service_type": service_type,
                    "error": f"duplicate enabled service_type: {service_type}",
                }
            )
            continue
        seen_service_types.add(service_type)
        plugins.append(plugin)
    return plugins, failures


def _clone_templates(templates: list[JSONObject]) -> list[JSONObject]:
    return [copy.deepcopy(template) for template in templates]


def get_enabled_plugin_ingredient_templates() -> list[JSONObject]:
    """Return immutable templates advertised by enabled built-in plugins."""
    templates: list[JSONObject] = []
    for plugin in get_enabled_plugins():
        templates.extend(plugin.ingredient_templates)
    return _clone_templates(templates)


def get_enabled_plugin_recipe_templates() -> list[JSONObject]:
    """Return recipe templates advertised by enabled built-in plugins."""
    templates: list[JSONObject] = []
    for plugin in get_enabled_plugins():
        templates.extend(plugin.recipe_templates)
    return _clone_templates(templates)


def get_enabled_plugin_communication_routes() -> list[JSONObject]:
    """Return default communication routes derived from enabled capabilities."""
    return communication_routes_from_capability_catalog(build_enabled_plugin_capability_catalog())


def get_enabled_plugin_scheduled_task_templates() -> list[JSONObject]:
    """Return scheduled tasks advertised by enabled built-in plugins."""
    tasks: list[JSONObject] = []
    for plugin in get_enabled_plugins():
        tasks.extend(plugin.scheduled_tasks)
    return _clone_templates(tasks)


def get_enabled_plugin_capability_templates() -> list[JSONObject]:
    """Return capability templates advertised by enabled built-in plugins."""
    templates: list[JSONObject] = []
    for plugin in get_enabled_plugins():
        templates.extend(plugin.capability_templates)
    return _clone_templates(templates)


def build_enabled_plugin_capability_catalog(
    plugin_configs: dict[str, JSONObject] | None = None,
) -> list[JSONObject]:
    """Return the normalized capability catalog for enabled plugins."""
    configs = {
        str(key).strip().lower(): value
        for key, value in (plugin_configs or {}).items()
        if isinstance(value, dict)
    }
    catalog: list[JSONObject] = []
    for plugin in get_enabled_plugins():
        operator_config = configs.get(plugin.service_type.strip().lower())
        for template in plugin.capability_templates:
            catalog.append(
                apply_operator_capability_overrides(
                    template,
                    operator_config=operator_config,
                )
            )
    return sorted(
        _clone_templates(catalog),
        key=lambda item: (
            str(item.get("service_type") or "").strip().lower(),
            str(item.get("capability_id") or "").strip().lower(),
        ),
    )


def get_enabled_plugin_helpers() -> dict[str, object]:
    """Build helper instances advertised by enabled plugins."""
    helpers: dict[str, object] = {}
    for plugin in get_enabled_plugins():
        if plugin.helper_factory is None:
            continue
        service_type = plugin.service_type.strip().lower()
        helpers[service_type] = plugin.helper_factory()
    return helpers


def get_enabled_plugin_helper(service_type: str) -> object | None:
    """Return one enabled plugin helper by service type."""
    normalized = service_type.strip().lower()
    return get_enabled_plugin_helpers().get(normalized)


def get_enabled_plugin_helper_capabilities() -> dict[str, list[str]]:
    """Return advertised helper capabilities keyed by provider service type."""
    capabilities: dict[str, list[str]] = {}
    for plugin in get_enabled_plugins():
        service_type = plugin.service_type.strip().lower()
        capabilities[service_type] = sorted(
            {str(item).strip().lower() for item in plugin.helper_capabilities}
        )
    return capabilities


def missing_helper_capabilities_for(
    plugin: ServicePlugin,
    available: dict[str, list[str]] | None = None,
) -> dict[str, list[str]]:
    """Return helper capabilities required by a plugin but not advertised."""
    advertised = available or get_enabled_plugin_helper_capabilities()
    missing: dict[str, list[str]] = {}
    for provider, required in (plugin.required_helper_capabilities or {}).items():
        provider_service_type = str(provider).strip().lower()
        provider_capabilities = set(advertised.get(provider_service_type, []))
        missing_items = sorted(
            {
                str(capability).strip().lower()
                for capability in required
                if str(capability).strip().lower() not in provider_capabilities
            }
        )
        if missing_items:
            missing[provider_service_type] = missing_items
    return missing


def validate_enabled_plugin_helper_dependencies() -> None:
    """Ensure enabled plugins have the helper capabilities they require."""
    available = get_enabled_plugin_helper_capabilities()
    failures: list[str] = []
    for plugin in get_enabled_plugins():
        service_type = plugin.service_type.strip().lower()
        missing = missing_helper_capabilities_for(plugin, available)
        for provider, capabilities in sorted(missing.items()):
            failures.append(
                f"{service_type} requires {provider} helper capabilities: "
                + ", ".join(capabilities)
            )
    if failures:
        raise ServicePluginManifestError("; ".join(failures))


def get_enabled_plugin_bootstrap_factories() -> list[tuple[str, object]]:
    """Return plugin-owned bootstrap hooks for enabled plugins."""
    factories: list[tuple[str, object]] = []
    for plugin in get_enabled_plugins():
        if plugin.bootstrap_factory is not None:
            factories.append((plugin.service_type.strip().lower(), plugin.bootstrap_factory))
    return factories


def build_enabled_plugin_registry(
    plugin_configs: dict[str, JSONObject] | None = None,
) -> "ExecutionAdapterRegistry":
    """Build the runtime adapter registry from enabled plugin manifests."""
    from api.plugins.base import ExecutionAdapter
    from api.plugins.registry import ExecutionAdapterRegistry

    registry = ExecutionAdapterRegistry()
    configs = {
        str(key).strip().lower(): value
        for key, value in (plugin_configs or {}).items()
        if isinstance(value, dict)
    }
    for plugin in get_enabled_plugins():
        adapter = plugin.adapter_factory()
        if not isinstance(adapter, ExecutionAdapter):
            raise ServicePluginManifestError(
                f"service_type={plugin.service_type!r} adapter_factory must return ExecutionAdapter"
            )
        config = configs.get(plugin.service_type.strip().lower())
        if config is not None:
            adapter = adapter.with_operator_config(config)
        registry.register(adapter)
    return registry
