"""Bakery service plugin manifest."""

from __future__ import annotations

from typing import TYPE_CHECKING

from api.plugins.bakery.capabilities import load_bakery_capability_templates
from api.plugins.bakery.templates import (
    BAKERY_SCHEDULED_TASKS,
    communication_routes,
    ingredient_templates,
    recipe_templates,
)
from api.plugins.manifest import ServicePlugin

if TYPE_CHECKING:
    from api.plugins.base import ExecutionAdapter


def _adapter_factory() -> "ExecutionAdapter":
    from api.plugins.bakery.adapter import BakeryExecutionAdapter

    return BakeryExecutionAdapter()


def get_plugin() -> ServicePlugin:
    """Return the Bakery service plugin descriptor."""
    return ServicePlugin(
        service_type="bakery",
        adapter_factory=_adapter_factory,
        ingredient_templates=ingredient_templates(),
        recipe_templates=recipe_templates(),
        communication_routes=communication_routes(),
        scheduled_tasks=BAKERY_SCHEDULED_TASKS,
        capability_templates=load_bakery_capability_templates(),
        plugin_tier="supported",
        plugin_log_key="bakery",
    )
