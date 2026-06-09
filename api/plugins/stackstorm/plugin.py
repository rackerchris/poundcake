"""StackStorm service plugin manifest."""

from __future__ import annotations

from typing import TYPE_CHECKING

from api.plugins.manifest import ServicePlugin
from api.plugins.stackstorm.capabilities import load_stackstorm_capability_templates
from api.plugins.stackstorm.templates import (
    STACKSTORM_INGREDIENT_TEMPLATES,
    STACKSTORM_RECIPE_TEMPLATES,
    STACKSTORM_SCHEDULED_TASKS,
)

if TYPE_CHECKING:
    from api.plugins.base import ExecutionAdapter


def _adapter_factory() -> "ExecutionAdapter":
    from api.plugins.stackstorm.adapter import StackStormExecutionAdapter
    from api.plugins.stackstorm.service import get_action_manager

    return StackStormExecutionAdapter(get_action_manager())


def get_plugin() -> ServicePlugin:
    """Return the StackStorm service plugin descriptor."""
    return ServicePlugin(
        service_type="stackstorm",
        adapter_factory=_adapter_factory,
        ingredient_templates=STACKSTORM_INGREDIENT_TEMPLATES,
        recipe_templates=STACKSTORM_RECIPE_TEMPLATES,
        scheduled_tasks=STACKSTORM_SCHEDULED_TASKS,
        capability_templates=load_stackstorm_capability_templates(),
    )
