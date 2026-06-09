"""GitHub service plugin manifest."""

from __future__ import annotations

from typing import TYPE_CHECKING

from api.plugins.github.capabilities import load_github_capability_templates
from api.plugins.github.client import get_github_helper
from api.plugins.github.templates import (
    GITHUB_INGREDIENT_TEMPLATES,
    GITHUB_RECIPE_TEMPLATES,
    GITHUB_SCHEDULED_TASKS,
)
from api.plugins.manifest import ServicePlugin

if TYPE_CHECKING:
    from api.plugins.base import ExecutionAdapter


def _adapter_factory() -> "ExecutionAdapter":
    from api.plugins.github.adapter import GitHubExecutionAdapter

    return GitHubExecutionAdapter()


def get_plugin() -> ServicePlugin:
    """Return the GitHub service plugin descriptor."""
    return ServicePlugin(
        service_type="github",
        adapter_factory=_adapter_factory,
        ingredient_templates=GITHUB_INGREDIENT_TEMPLATES,
        recipe_templates=GITHUB_RECIPE_TEMPLATES,
        scheduled_tasks=GITHUB_SCHEDULED_TASKS,
        capability_templates=load_github_capability_templates(),
        helper_factory=get_github_helper,
        helper_capabilities=(
            "repo.read",
            "repo.list",
            "repo.write",
            "pull_request.create",
        ),
        plugin_tier="community",
    )
