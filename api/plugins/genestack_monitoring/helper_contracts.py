"""Helper contracts for the Genestack Monitoring plugin.

This module keeps Genestack Monitoring bound to the helper capabilities
advertised by other plugins rather than concrete helper implementations.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from api.plugins.catalog import (
    get_enabled_plugin_helper_capabilities,
    get_enabled_plugin_helpers,
)
from api.services.plugin_bootstrap import PluginBootstrapError
from api.types import JSONObject

GENESTACK_REQUIRED_HELPER_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "github": ("repo.read", "repo.list", "repo.write", "pull_request.create"),
    "k8s": ("k8s.prometheusrules.manage",),
    "prometheus": ("alert_rules.parse", "alert_rules.render"),
}

_HELPER_METHODS: dict[str, tuple[str, ...]] = {
    "github": ("list_files", "read_file", "commit_and_pr", "with_credentials"),
    "k8s": ("create_or_update_rule", "list_prometheus_rules"),
    "prometheus": ("parse_rules_from_content", "render_document", "dump_document"),
}


def resolve_enabled_genestack_helpers() -> Mapping[str, object]:
    """Return the enabled helper instances Genestack Monitoring depends on."""
    helpers = get_enabled_plugin_helpers()
    advertised = get_enabled_plugin_helper_capabilities()
    resolved: dict[str, object] = {}
    for provider, capabilities in GENESTACK_REQUIRED_HELPER_CAPABILITIES.items():
        helper = helpers.get(provider)
        if helper is None:
            raise PluginBootstrapError(
                f"genestack_monitoring requires enabled {provider} plugin helper"
            )
        missing_capabilities = [
            capability
            for capability in capabilities
            if capability not in set(advertised.get(provider, []))
        ]
        if missing_capabilities:
            raise PluginBootstrapError(
                f"genestack_monitoring requires advertised {provider} helper capabilities: "
                + ", ".join(missing_capabilities)
            )
        _require_helper_methods(
            helper,
            provider=provider,
            operation=f"genestack_monitoring {provider} helper",
            methods=_HELPER_METHODS[provider],
        )
        resolved[provider] = helper
    return resolved


def require_github_reader_helper(helpers: Mapping[str, object], *, operation: str) -> object:
    return _require_provider_helper(
        helpers,
        provider="github",
        operation=operation,
        methods=("list_files", "read_file"),
    )


def require_github_writer_helper(helpers: Mapping[str, object], *, operation: str) -> object:
    return _require_provider_helper(
        helpers,
        provider="github",
        operation=operation,
        methods=("commit_and_pr",),
    )


def require_github_credential_helper(helpers: Mapping[str, object]) -> object | None:
    helper = helpers.get("github")
    if helper is None:
        return None
    return _require_provider_helper(
        helpers,
        provider="github",
        operation="genestack_monitoring github credential hydration",
        methods=("with_credentials",),
    )


def require_k8s_helper(helpers: Mapping[str, object], *, operation: str) -> object:
    return _require_provider_helper(
        helpers,
        provider="k8s",
        operation=operation,
        methods=("create_or_update_rule", "list_prometheus_rules"),
    )


def require_prometheus_helper(helpers: Mapping[str, object], *, operation: str) -> object:
    return _require_provider_helper(
        helpers,
        provider="prometheus",
        operation=operation,
        methods=("parse_rules_from_content", "render_document", "dump_document"),
    )


def apply_github_credentials(helper: object, payload: JSONObject | None) -> object:
    with_credentials = getattr(helper, "with_credentials", None)
    if not callable(with_credentials):
        raise PluginBootstrapError(
            "genestack_monitoring github credential hydration requires enabled github plugin helper"
        )
    configured = with_credentials(payload)
    _require_helper_methods(
        configured,
        provider="github",
        operation="genestack_monitoring hydrated github helper",
        methods=("list_files", "read_file", "commit_and_pr"),
    )
    return configured


def set_allow_public_read(helper: object, *, allow_public_read: bool) -> object:
    if hasattr(helper, "allow_public_read"):
        setattr(helper, "allow_public_read", allow_public_read)
    return helper


def _require_provider_helper(
    helpers: Mapping[str, object],
    *,
    provider: str,
    operation: str,
    methods: Sequence[str],
) -> object:
    helper = helpers.get(provider)
    if helper is None:
        raise PluginBootstrapError(
            f"{operation} requires enabled {provider} plugin helper"
        )
    _require_helper_methods(helper, provider=provider, operation=operation, methods=methods)
    return helper


def _require_helper_methods(
    helper: object,
    *,
    provider: str,
    operation: str,
    methods: Sequence[str],
) -> None:
    missing = [
        name
        for name in methods
        if not _is_callable_attr(helper, name)
    ]
    if missing:
        raise PluginBootstrapError(
            f"{operation} requires enabled {provider} plugin helper"
        )


def _is_callable_attr(helper: object, name: str) -> bool:
    value = getattr(helper, name, None)
    return callable(value)
