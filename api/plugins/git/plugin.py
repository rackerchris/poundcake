"""Git service plugin manifest."""

from __future__ import annotations

from typing import TYPE_CHECKING

from api.plugins.git.client import get_git_helper
from api.plugins.git.templates import (
    GIT_INGREDIENT_TEMPLATES,
    GIT_RECIPE_TEMPLATES,
    GIT_SCHEDULED_TASKS,
)
from api.plugins.manifest import ServicePlugin

if TYPE_CHECKING:
    from api.plugins.base import ExecutionAdapter


def _adapter_factory() -> "ExecutionAdapter":
    from api.plugins.git.adapter import GitExecutionAdapter

    return GitExecutionAdapter()


def get_plugin() -> ServicePlugin:
    """Return the Git service plugin descriptor."""
    return ServicePlugin(
        service_type="git",
        adapter_factory=_adapter_factory,
        ingredient_templates=GIT_INGREDIENT_TEMPLATES,
        recipe_templates=GIT_RECIPE_TEMPLATES,
        scheduled_tasks=GIT_SCHEDULED_TASKS,
        helper_factory=get_git_helper,
        helper_capabilities=(
            "repo.read",
            "repo.list",
            "repo.write",
            "pull_request.create",
        ),
        plugin_tier="community",
    )
