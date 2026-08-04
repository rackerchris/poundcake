"""Shared route surface inventory for control-plane RBAC guardrails."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from api.types import AuthRole


class RouteSurface(StrEnum):
    """Payload-sensitivity classes for guarded API routes."""

    REPORTING_STATUS = "reporting_status"
    CONFIGURATION_EDITOR = "configuration_editor"
    ADMIN_OBSERVABILITY = "admin_observability"
    INTERNAL_RUNTIME = "internal_runtime"


@dataclass(frozen=True)
class RouteSurfaceEntry:
    """One explicitly classified guarded route."""

    method: str
    path: str
    surface: RouteSurface
    expected_role: AuthRole

    @property
    def key(self) -> tuple[str, str]:
        return (self.method, self.path)


GUARDED_ROUTE_PREFIXES: tuple[str, ...] = (
    "/api/v1/orders",
    "/api/v1/recipes",
    "/api/v1/dishes",
    "/api/v1/dish-ingredients",
    "/api/v1/service-registry",
    "/api/v1/scheduled-tasks",
    "/api/v1/observability",
    "/api/v1/communications",
    "/api/v1/plugins",
    "/api/v1/suppressions",
    "/api/v1/activity",
    "/api/v1/ui",
)


ROUTE_SURFACE_ENTRIES: tuple[RouteSurfaceEntry, ...] = (
    RouteSurfaceEntry("GET", "/api/v1/health/status", RouteSurface.REPORTING_STATUS, "reader"),
    RouteSurfaceEntry("GET", "/api/v1/orders/status", RouteSurface.REPORTING_STATUS, "reader"),
    RouteSurfaceEntry(
        "GET", "/api/v1/orders/{order_id}/status", RouteSurface.REPORTING_STATUS, "reader"
    ),
    RouteSurfaceEntry(
        "GET", "/api/v1/orders/{order_id}/timeline", RouteSurface.REPORTING_STATUS, "reader"
    ),
    RouteSurfaceEntry("GET", "/api/v1/recipes/status", RouteSurface.REPORTING_STATUS, "reader"),
    RouteSurfaceEntry(
        "GET", "/api/v1/recipes/{recipe_id}/status", RouteSurface.REPORTING_STATUS, "reader"
    ),
    RouteSurfaceEntry(
        "GET",
        "/api/v1/recipes/{recipe_id}/ingredient-status",
        RouteSurface.REPORTING_STATUS,
        "reader",
    ),
    RouteSurfaceEntry("GET", "/api/v1/dishes/status", RouteSurface.REPORTING_STATUS, "reader"),
    RouteSurfaceEntry(
        "GET",
        "/api/v1/dishes/{dish_id}/ingredient-status",
        RouteSurface.REPORTING_STATUS,
        "reader",
    ),
    RouteSurfaceEntry(
        "GET",
        "/api/v1/service-registry/ingredients/status",
        RouteSurface.REPORTING_STATUS,
        "reader",
    ),
    RouteSurfaceEntry(
        "GET", "/api/v1/scheduled-tasks/status", RouteSurface.REPORTING_STATUS, "reader"
    ),
    RouteSurfaceEntry(
        "GET", "/api/v1/suppressions/status", RouteSurface.REPORTING_STATUS, "reader"
    ),
    RouteSurfaceEntry(
        "GET",
        "/api/v1/suppressions/{suppression_id}/stats",
        RouteSurface.REPORTING_STATUS,
        "reader",
    ),
    RouteSurfaceEntry(
        "GET", "/api/v1/observability/overview", RouteSurface.REPORTING_STATUS, "reader"
    ),
    RouteSurfaceEntry(
        "GET", "/api/v1/observability/activity/status", RouteSurface.REPORTING_STATUS, "reader"
    ),
    RouteSurfaceEntry(
        "GET", "/api/v1/communications/activity/status", RouteSurface.REPORTING_STATUS, "reader"
    ),
    RouteSurfaceEntry(
        "GET", "/api/v1/ui/operator-actions", RouteSurface.CONFIGURATION_EDITOR, "reader"
    ),
    RouteSurfaceEntry(
        "GET", "/api/v1/activity/suppressed", RouteSurface.ADMIN_OBSERVABILITY, "reader"
    ),
    RouteSurfaceEntry("GET", "/api/v1/recipes/", RouteSurface.CONFIGURATION_EDITOR, "operator"),
    RouteSurfaceEntry("POST", "/api/v1/recipes/", RouteSurface.CONFIGURATION_EDITOR, "operator"),
    RouteSurfaceEntry(
        "GET", "/api/v1/recipes/{recipe_id}", RouteSurface.CONFIGURATION_EDITOR, "operator"
    ),
    RouteSurfaceEntry(
        "PUT", "/api/v1/recipes/{recipe_id}", RouteSurface.CONFIGURATION_EDITOR, "operator"
    ),
    RouteSurfaceEntry(
        "PATCH", "/api/v1/recipes/{recipe_id}", RouteSurface.CONFIGURATION_EDITOR, "operator"
    ),
    RouteSurfaceEntry(
        "DELETE", "/api/v1/recipes/{recipe_id}", RouteSurface.CONFIGURATION_EDITOR, "operator"
    ),
    RouteSurfaceEntry(
        "GET",
        "/api/v1/recipes/by-name/{recipe_name}",
        RouteSurface.CONFIGURATION_EDITOR,
        "operator",
    ),
    RouteSurfaceEntry(
        "GET",
        "/api/v1/service-registry/ingredients",
        RouteSurface.CONFIGURATION_EDITOR,
        "operator",
    ),
    RouteSurfaceEntry(
        "GET",
        "/api/v1/service-registry/ingredients/{ingredient_id}",
        RouteSurface.CONFIGURATION_EDITOR,
        "operator",
    ),
    RouteSurfaceEntry("GET", "/api/v1/scheduled-tasks", RouteSurface.CONFIGURATION_EDITOR, "admin"),
    RouteSurfaceEntry(
        "POST", "/api/v1/scheduled-tasks", RouteSurface.CONFIGURATION_EDITOR, "admin"
    ),
    RouteSurfaceEntry(
        "GET", "/api/v1/scheduled-tasks/{task_id}", RouteSurface.CONFIGURATION_EDITOR, "admin"
    ),
    RouteSurfaceEntry(
        "PATCH",
        "/api/v1/scheduled-tasks/{task_id}",
        RouteSurface.CONFIGURATION_EDITOR,
        "operator",
    ),
    RouteSurfaceEntry(
        "DELETE",
        "/api/v1/scheduled-tasks/{task_id}",
        RouteSurface.CONFIGURATION_EDITOR,
        "admin",
    ),
    RouteSurfaceEntry(
        "POST",
        "/api/v1/scheduled-tasks/{task_id}/run-now",
        RouteSurface.CONFIGURATION_EDITOR,
        "operator",
    ),
    RouteSurfaceEntry(
        "GET", "/api/v1/observability/activity", RouteSurface.CONFIGURATION_EDITOR, "reader"
    ),
    RouteSurfaceEntry(
        "GET", "/api/v1/communications/activity", RouteSurface.CONFIGURATION_EDITOR, "reader"
    ),
    RouteSurfaceEntry(
        "GET", "/api/v1/communications/policy", RouteSurface.CONFIGURATION_EDITOR, "reader"
    ),
    RouteSurfaceEntry(
        "PUT", "/api/v1/communications/policy", RouteSurface.CONFIGURATION_EDITOR, "admin"
    ),
    RouteSurfaceEntry("GET", "/api/v1/plugins", RouteSurface.CONFIGURATION_EDITOR, "reader"),
    RouteSurfaceEntry(
        "GET", "/api/v1/plugins/k8s/prometheus-rules", RouteSurface.CONFIGURATION_EDITOR, "reader"
    ),
    RouteSurfaceEntry(
        "GET",
        "/api/v1/plugins/k8s/prometheus-rules/{crd_name}",
        RouteSurface.CONFIGURATION_EDITOR,
        "reader",
    ),
    RouteSurfaceEntry(
        "GET",
        "/api/v1/plugins/k8s/prometheus-rules/{crd_name}/rules/{rule_name}",
        RouteSurface.CONFIGURATION_EDITOR,
        "reader",
    ),
    RouteSurfaceEntry(
        "PUT",
        "/api/v1/plugins/k8s/prometheus-rules/{crd_name}/rules/{rule_name}",
        RouteSurface.CONFIGURATION_EDITOR,
        "operator",
    ),
    RouteSurfaceEntry(
        "POST",
        "/api/v1/plugins/k8s/prometheus-rules/{crd_name}/rules",
        RouteSurface.CONFIGURATION_EDITOR,
        "operator",
    ),
    RouteSurfaceEntry(
        "POST",
        "/api/v1/plugins/genestack_monitoring/export-alert-updates",
        RouteSurface.CONFIGURATION_EDITOR,
        "operator",
    ),
    RouteSurfaceEntry(
        "POST",
        "/api/v1/plugins/prometheus/reload",
        RouteSurface.CONFIGURATION_EDITOR,
        "operator",
    ),
    RouteSurfaceEntry(
        "GET", "/api/v1/plugins/{service_type}", RouteSurface.CONFIGURATION_EDITOR, "reader"
    ),
    RouteSurfaceEntry(
        "GET",
        "/api/v1/plugins/{service_type}/configuration",
        RouteSurface.CONFIGURATION_EDITOR,
        "operator",
    ),
    RouteSurfaceEntry(
        "PUT",
        "/api/v1/plugins/{service_type}/configuration",
        RouteSurface.CONFIGURATION_EDITOR,
        "operator",
    ),
    RouteSurfaceEntry(
        "PATCH", "/api/v1/plugins/{service_type}", RouteSurface.CONFIGURATION_EDITOR, "operator"
    ),
    RouteSurfaceEntry(
        "POST",
        "/api/v1/plugins/{service_type}/test-connection",
        RouteSurface.CONFIGURATION_EDITOR,
        "operator",
    ),
    RouteSurfaceEntry(
        "PUT",
        "/api/v1/plugins/{service_type}/credentials",
        RouteSurface.CONFIGURATION_EDITOR,
        "admin",
    ),
    RouteSurfaceEntry(
        "GET", "/api/v1/plugins/{service_type}/health", RouteSurface.CONFIGURATION_EDITOR, "reader"
    ),
    RouteSurfaceEntry("GET", "/api/v1/suppressions", RouteSurface.CONFIGURATION_EDITOR, "reader"),
    RouteSurfaceEntry(
        "POST", "/api/v1/suppressions", RouteSurface.CONFIGURATION_EDITOR, "operator"
    ),
    RouteSurfaceEntry(
        "GET",
        "/api/v1/suppressions/{suppression_id}",
        RouteSurface.CONFIGURATION_EDITOR,
        "reader",
    ),
    RouteSurfaceEntry(
        "PATCH",
        "/api/v1/suppressions/{suppression_id}",
        RouteSurface.CONFIGURATION_EDITOR,
        "operator",
    ),
    RouteSurfaceEntry(
        "POST",
        "/api/v1/suppressions/{suppression_id}/cancel",
        RouteSurface.CONFIGURATION_EDITOR,
        "operator",
    ),
    RouteSurfaceEntry(
        "GET",
        "/api/v1/orders/{order_id}/execution-history",
        RouteSurface.ADMIN_OBSERVABILITY,
        "admin",
    ),
    RouteSurfaceEntry(
        "GET",
        "/api/v1/dishes/{dish_id}/ingredients",
        RouteSurface.ADMIN_OBSERVABILITY,
        "admin",
    ),
    RouteSurfaceEntry(
        "GET",
        "/api/v1/dishes/{dish_id}/ingredient-history",
        RouteSurface.ADMIN_OBSERVABILITY,
        "admin",
    ),
    RouteSurfaceEntry("GET", "/api/v1/orders", RouteSurface.INTERNAL_RUNTIME, "service"),
    RouteSurfaceEntry("POST", "/api/v1/orders", RouteSurface.INTERNAL_RUNTIME, "service"),
    RouteSurfaceEntry("GET", "/api/v1/orders/{order_id}", RouteSurface.INTERNAL_RUNTIME, "service"),
    RouteSurfaceEntry("PUT", "/api/v1/orders/{order_id}", RouteSurface.INTERNAL_RUNTIME, "service"),
    RouteSurfaceEntry("GET", "/api/v1/dishes", RouteSurface.INTERNAL_RUNTIME, "service"),
    RouteSurfaceEntry(
        "POST",
        "/api/v1/dish-ingredients/{dish_ingredient_id}/poll-claim",
        RouteSurface.INTERNAL_RUNTIME,
        "service",
    ),
    RouteSurfaceEntry(
        "POST",
        "/api/v1/dish-ingredients/{dish_ingredient_id}/poll-release",
        RouteSurface.INTERNAL_RUNTIME,
        "service",
    ),
    RouteSurfaceEntry(
        "POST",
        "/api/v1/dish-ingredients/{dish_ingredient_id}/reconcile",
        RouteSurface.INTERNAL_RUNTIME,
        "service",
    ),
    RouteSurfaceEntry(
        "POST",
        "/api/v1/dish-ingredients/{dish_ingredient_id}/execution-claim",
        RouteSurface.INTERNAL_RUNTIME,
        "service",
    ),
    RouteSurfaceEntry(
        "POST",
        "/api/v1/dish-ingredients/{dish_ingredient_id}/execution-release",
        RouteSurface.INTERNAL_RUNTIME,
        "service",
    ),
    RouteSurfaceEntry(
        "POST",
        "/api/v1/dish-ingredients/{dish_ingredient_id}/execution-reconcile",
        RouteSurface.INTERNAL_RUNTIME,
        "service",
    ),
    RouteSurfaceEntry(
        "GET", "/api/v1/scheduled-tasks/due", RouteSurface.INTERNAL_RUNTIME, "service"
    ),
    RouteSurfaceEntry(
        "GET", "/api/v1/dish-ingredients/in-flight", RouteSurface.INTERNAL_RUNTIME, "service"
    ),
    RouteSurfaceEntry(
        "GET",
        "/api/v1/dish-ingredients/cancel-requested",
        RouteSurface.INTERNAL_RUNTIME,
        "service",
    ),
    RouteSurfaceEntry(
        "GET",
        "/api/v1/dish-ingredients/advance-ready",
        RouteSurface.INTERNAL_RUNTIME,
        "service",
    ),
    RouteSurfaceEntry(
        "GET",
        "/api/v1/dish-ingredients/execution-pending",
        RouteSurface.INTERNAL_RUNTIME,
        "service",
    ),
    RouteSurfaceEntry(
        "GET",
        "/api/v1/expediter/status/{service_type}/{service_exec_id}",
        RouteSurface.INTERNAL_RUNTIME,
        "service",
    ),
    RouteSurfaceEntry(
        "POST", "/api/v1/ui/operator-actions", RouteSurface.CONFIGURATION_EDITOR, "operator"
    ),
)


def route_surface_entries(surface: RouteSurface | None = None) -> tuple[RouteSurfaceEntry, ...]:
    """Return classified route entries, optionally filtered by surface."""
    if surface is None:
        return ROUTE_SURFACE_ENTRIES
    return tuple(entry for entry in ROUTE_SURFACE_ENTRIES if entry.surface == surface)


def route_surface_keys(surface: RouteSurface | None = None) -> set[tuple[str, str]]:
    """Return classified route keys, optionally filtered by surface."""
    return {entry.key for entry in route_surface_entries(surface)}


def is_guarded_route(route: tuple[str, str]) -> bool:
    """Return True when a guarded route needs explicit surface classification."""
    method, path = route
    return method in {"GET", "POST", "PUT", "PATCH", "DELETE"} and path.startswith(
        GUARDED_ROUTE_PREFIXES
    )


def is_guarded_get_route(route: tuple[str, str]) -> bool:
    """Return True when a guarded route needs explicit surface classification."""
    return is_guarded_route(route)
