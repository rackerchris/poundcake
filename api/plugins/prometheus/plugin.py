"""Prometheus service plugin manifest."""

from __future__ import annotations

from typing import TYPE_CHECKING

from api.plugins.manifest import ServicePlugin
from api.plugins.prometheus.helper import get_prometheus_helper
from api.plugins.prometheus.templates import (
    PROMETHEUS_INGREDIENT_TEMPLATES,
    PROMETHEUS_RECIPE_TEMPLATES,
    PROMETHEUS_SCHEDULED_TASKS,
)

if TYPE_CHECKING:
    from api.plugins.base import ExecutionAdapter


def _adapter_factory() -> "ExecutionAdapter":
    from api.plugins.prometheus.adapter import PrometheusExecutionAdapter

    return PrometheusExecutionAdapter()


def get_plugin() -> ServicePlugin:
    """Return the Prometheus service plugin descriptor."""
    return ServicePlugin(
        service_type="prometheus",
        adapter_factory=_adapter_factory,
        ingredient_templates=PROMETHEUS_INGREDIENT_TEMPLATES,
        recipe_templates=PROMETHEUS_RECIPE_TEMPLATES,
        scheduled_tasks=PROMETHEUS_SCHEDULED_TASKS,
        helper_factory=get_prometheus_helper,
        helper_capabilities=(
            "alert_rules.parse",
            "alert_rules.index",
            "alert_rules.render",
        ),
        plugin_tier="community",
    )
