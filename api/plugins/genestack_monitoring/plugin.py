"""Genestack Monitoring service plugin manifest."""

from __future__ import annotations

from typing import TYPE_CHECKING

from api.plugins.genestack_monitoring.templates import (
    GENESTACK_MONITORING_INGREDIENT_TEMPLATES,
    GENESTACK_MONITORING_RECIPE_TEMPLATES,
    GENESTACK_MONITORING_SCHEDULED_TASKS,
)
from api.plugins.manifest import ServicePlugin

if TYPE_CHECKING:
    from api.plugins.base import ExecutionAdapter


def _adapter_factory() -> "ExecutionAdapter":
    from api.plugins.genestack_monitoring.adapter import GenestackMonitoringExecutionAdapter

    return GenestackMonitoringExecutionAdapter()


def get_plugin() -> ServicePlugin:
    """Return the Genestack Monitoring bootstrap plugin descriptor."""
    return ServicePlugin(
        service_type="genestack_monitoring",
        adapter_factory=_adapter_factory,
        ingredient_templates=GENESTACK_MONITORING_INGREDIENT_TEMPLATES,
        recipe_templates=GENESTACK_MONITORING_RECIPE_TEMPLATES,
        scheduled_tasks=GENESTACK_MONITORING_SCHEDULED_TASKS,
        required_helper_capabilities={
            "github": ("repo.read", "repo.list", "repo.write", "pull_request.create"),
            "k8s": ("k8s.prometheusrules.manage",),
            "prometheus": ("alert_rules.parse", "alert_rules.render"),
        },
        required_db_capabilities={
            "genestack_monitoring": ("recipe-sync",),
        },
        plugin_tier="community",
    )
