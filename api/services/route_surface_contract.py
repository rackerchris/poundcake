"""Shared route surface inventory for control-plane RBAC guardrails."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from api.types import AuthRole


class RouteSurface(StrEnum):
    """Payload-sensitivity classes for guarded GET routes."""

    REPORTING_STATUS = "reporting_status"
    CONFIGURATION_EDITOR = "configuration_editor"
    ADMIN_OBSERVABILITY = "admin_observability"
    INTERNAL_RUNTIME = "internal_runtime"


@dataclass(frozen=True)
class RouteSurfaceEntry:
    """One explicitly classified GET route."""

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
    RouteSurfaceEntry("GET", "/api/v1/recipes/", RouteSurface.CONFIGURATION_EDITOR, "operator"),
    RouteSurfaceEntry(
        "GET", "/api/v1/recipes/{recipe_id}", RouteSurface.CONFIGURATION_EDITOR, "operator"
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
        "GET", "/api/v1/scheduled-tasks/{task_id}", RouteSurface.CONFIGURATION_EDITOR, "admin"
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
    RouteSurfaceEntry("GET", "/api/v1/plugins", RouteSurface.CONFIGURATION_EDITOR, "reader"),
    RouteSurfaceEntry(
        "GET", "/api/v1/plugins/k8s/prometheus-rules", RouteSurface.CONFIGURATION_EDITOR, "reader"
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
        "GET", "/api/v1/plugins/{service_type}/health", RouteSurface.CONFIGURATION_EDITOR, "reader"
    ),
    RouteSurfaceEntry("GET", "/api/v1/suppressions", RouteSurface.CONFIGURATION_EDITOR, "reader"),
    RouteSurfaceEntry(
        "GET",
        "/api/v1/suppressions/{suppression_id}",
        RouteSurface.CONFIGURATION_EDITOR,
        "reader",
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
    RouteSurfaceEntry("GET", "/api/v1/orders/{order_id}", RouteSurface.INTERNAL_RUNTIME, "service"),
    RouteSurfaceEntry("GET", "/api/v1/dishes", RouteSurface.INTERNAL_RUNTIME, "service"),
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
)


def route_surface_entries(surface: RouteSurface | None = None) -> tuple[RouteSurfaceEntry, ...]:
    """Return classified route entries, optionally filtered by surface."""
    if surface is None:
        return ROUTE_SURFACE_ENTRIES
    return tuple(entry for entry in ROUTE_SURFACE_ENTRIES if entry.surface == surface)


def route_surface_keys(surface: RouteSurface | None = None) -> set[tuple[str, str]]:
    """Return classified route keys, optionally filtered by surface."""
    return {entry.key for entry in route_surface_entries(surface)}


def is_guarded_get_route(route: tuple[str, str]) -> bool:
    """Return True when a GET route needs explicit surface classification."""
    method, path = route
    return method == "GET" and path.startswith(GUARDED_ROUTE_PREFIXES)
