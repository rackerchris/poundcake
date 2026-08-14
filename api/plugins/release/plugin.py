"""Release update notifications service plugin manifest."""

from __future__ import annotations

from typing import TYPE_CHECKING

from api.plugins.manifest import ServicePlugin
from api.plugins.release.capabilities import load_release_capability_templates
from api.plugins.release.templates import (
    RELEASE_INGREDIENT_TEMPLATES,
    RELEASE_RECIPE_TEMPLATES,
    RELEASE_SCHEDULED_TASKS,
)

if TYPE_CHECKING:
    from api.plugins.base import ExecutionAdapter


def _adapter_factory() -> "ExecutionAdapter":
    from api.plugins.release.adapter import ReleaseExecutionAdapter

    return ReleaseExecutionAdapter()


def get_plugin() -> ServicePlugin:
    """Return the release update service plugin descriptor."""
    return ServicePlugin(
        service_type="release",
        adapter_factory=_adapter_factory,
        ingredient_templates=RELEASE_INGREDIENT_TEMPLATES,
        recipe_templates=RELEASE_RECIPE_TEMPLATES,
        scheduled_tasks=RELEASE_SCHEDULED_TASKS,
        capability_templates=load_release_capability_templates(),
        plugin_tier="community",
    )
