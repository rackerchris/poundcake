"""Alertmanager service plugin manifest."""

from __future__ import annotations

from typing import TYPE_CHECKING

from api.plugins.alertmanager.templates import (
    ALERTMANAGER_INGREDIENT_TEMPLATES,
    ALERTMANAGER_RECIPE_TEMPLATES,
    ALERTMANAGER_SCHEDULED_TASKS,
)
from api.plugins.manifest import ServicePlugin

if TYPE_CHECKING:
    from api.plugins.base import ExecutionAdapter


def _adapter_factory() -> "ExecutionAdapter":
    from api.plugins.alertmanager.adapter import AlertmanagerExecutionAdapter

    return AlertmanagerExecutionAdapter()


def get_plugin() -> ServicePlugin:
    """Return the Alertmanager service plugin descriptor."""
    return ServicePlugin(
        service_type="alertmanager",
        adapter_factory=_adapter_factory,
        ingredient_templates=ALERTMANAGER_INGREDIENT_TEMPLATES,
        recipe_templates=ALERTMANAGER_RECIPE_TEMPLATES,
        scheduled_tasks=ALERTMANAGER_SCHEDULED_TASKS,
        plugin_tier="community",
    )
