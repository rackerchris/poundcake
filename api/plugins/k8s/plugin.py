"""Kubernetes service plugin manifest."""

from __future__ import annotations

from typing import TYPE_CHECKING

from api.plugins.k8s.capabilities import load_k8s_capability_templates
from api.plugins.k8s.helper import get_kubernetes_helper
from api.plugins.k8s.templates import (
    K8S_INGREDIENT_TEMPLATES,
    K8S_RECIPE_TEMPLATES,
    K8S_SCHEDULED_TASKS,
)
from api.plugins.manifest import ServicePlugin

if TYPE_CHECKING:
    from api.plugins.base import ExecutionAdapter


def _adapter_factory() -> "ExecutionAdapter":
    from api.plugins.k8s.adapter import KubernetesExecutionAdapter

    return KubernetesExecutionAdapter()


def get_plugin() -> ServicePlugin:
    """Return the Kubernetes service plugin descriptor."""
    return ServicePlugin(
        service_type="k8s",
        adapter_factory=_adapter_factory,
        ingredient_templates=K8S_INGREDIENT_TEMPLATES,
        recipe_templates=K8S_RECIPE_TEMPLATES,
        scheduled_tasks=K8S_SCHEDULED_TASKS,
        capability_templates=load_k8s_capability_templates(),
        helper_factory=get_kubernetes_helper,
        helper_capabilities=(
            "k8s.cluster.connect",
            "k8s.deployments.manage",
            "k8s.deployments.read",
            "k8s.diagnostics.read",
            "k8s.nodes.read",
            "k8s.pods.manage",
            "k8s.pods.read",
            "k8s.prometheusrules.manage",
            "k8s.workloads.manage",
            "k8s.workloads.read",
        ),
        plugin_tier="community",
    )
