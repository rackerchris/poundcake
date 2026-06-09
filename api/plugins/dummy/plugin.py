"""Dummy service plugin manifest."""

from __future__ import annotations

from typing import TYPE_CHECKING

from api.plugins.dummy.capabilities import load_dummy_capability_templates
from api.plugins.dummy.bootstrap import bootstrap_dummy_helper_validation
from api.plugins.dummy.helper import get_dummy_helper
from api.plugins.dummy.templates import (
    DUMMY_COMMUNICATION_ROUTES,
    DUMMY_INGREDIENT_TEMPLATES,
    DUMMY_RECIPE_TEMPLATES,
    DUMMY_SCHEDULED_TASKS,
)
from api.plugins.manifest import ServicePlugin

if TYPE_CHECKING:
    from api.plugins.base import ExecutionAdapter


def _adapter_factory() -> "ExecutionAdapter":
    from api.plugins.dummy.adapter import DummyExecutionAdapter

    return DummyExecutionAdapter()


def get_plugin() -> ServicePlugin:
    """Return the dummy service plugin descriptor."""
    return ServicePlugin(
        service_type="dummy",
        adapter_factory=_adapter_factory,
        ingredient_templates=DUMMY_INGREDIENT_TEMPLATES,
        recipe_templates=DUMMY_RECIPE_TEMPLATES,
        communication_routes=DUMMY_COMMUNICATION_ROUTES,
        scheduled_tasks=DUMMY_SCHEDULED_TASKS,
        capability_templates=load_dummy_capability_templates(),
        helper_factory=get_dummy_helper,
        helper_capabilities=("dummy.echo",),
        required_helper_capabilities={"dummy": ("dummy.echo",)},
        bootstrap_factory=bootstrap_dummy_helper_validation,
        plugin_tier="supported",
        plugin_log_key="dummy",
    )
