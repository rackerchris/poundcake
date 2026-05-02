"""Policy-aware database capability checks for protected control-plane data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

DatabaseCapability = Literal[
    "adapter-credential:read",
    "adapter-credential:write",
    "service-identity:read-own",
    "service-identity:write",
    "service-plugin:read",
    "service-plugin:update-status",
    "migration:apply",
    "app:data-read",
    "app:data-write",
    "genestack_monitoring:recipe-sync",
]


class DatabaseAccessError(PermissionError):
    """Raised when a principal is not allowed to perform a database operation."""


class AuthContextLike(Protocol):
    role: str
    is_service: bool
    service_plugin_id: int | None
    service_type: str | None
    plugin_type: str | None


@dataclass(frozen=True)
class DatabasePrincipal:
    """Normalized identity used by database helpers."""

    role: str | None = None
    service_type: str | None = None
    plugin_type: str | None = None
    service_plugin_id: int | None = None

    @property
    def normalized_role(self) -> str:
        return (self.role or "").strip().lower()

    @property
    def normalized_service_type(self) -> str:
        return (self.service_type or "").strip().lower()

    @property
    def normalized_plugin_type(self) -> str:
        return (self.plugin_type or "").strip().lower()

    @property
    def is_internal_service(self) -> bool:
        return self.normalized_plugin_type == "internal_plugin"


def principal_from_auth_context(context: AuthContextLike) -> DatabasePrincipal:
    """Build a database principal from the request auth context."""

    return DatabasePrincipal(
        role=getattr(context, "role", None),
        service_type=getattr(context, "service_type", None),
        plugin_type=getattr(context, "plugin_type", None),
        service_plugin_id=getattr(context, "service_plugin_id", None),
    )


def principal_for_internal_service(
    service_type: str,
    *,
    plugin_type: str = "internal_plugin",
    service_plugin_id: int | None = None,
) -> DatabasePrincipal:
    """Build a trusted internal helper principal.

    This constructor is only for fixed control-plane call sites. Do not pass
    request, user, plugin manifest, or other caller-controlled service names to it;
    use principal_from_auth_context() for request-scoped authorization.
    """

    return DatabasePrincipal(
        role="service",
        service_type=service_type,
        plugin_type=plugin_type,
        service_plugin_id=service_plugin_id,
    )


def database_capabilities_for_principal(
    principal: DatabasePrincipal,
) -> frozenset[DatabaseCapability]:
    """Return database capabilities granted to a normalized principal."""

    role = principal.normalized_role
    service_type = principal.normalized_service_type

    if role == "admin":
        return frozenset(
            {
                "adapter-credential:read",
                "adapter-credential:write",
                "service-identity:read-own",
                "service-identity:write",
                "service-plugin:read",
                "service-plugin:update-status",
                "app:data-read",
                "app:data-write",
            }
        )
    if role == "operator":
        return frozenset(
            {
                "service-plugin:read",
                "service-plugin:update-status",
                "app:data-read",
                "app:data-write",
            }
        )
    if role == "reader":
        return frozenset({"service-plugin:read", "app:data-read"})

    if service_type == "credential-manager" and principal.is_internal_service:
        return frozenset(
            {
                "adapter-credential:read",
                "adapter-credential:write",
                "service-plugin:read",
                "service-plugin:update-status",
            }
        )
    if service_type == "plugin-registry":
        return frozenset(
            {
                "service-plugin:read",
                "service-plugin:update-status",
            }
        )
    if service_type == "service-identity-manager":
        return frozenset(
            {
                "service-identity:write",
                "service-plugin:read",
            }
        )
    if service_type == "api":
        return frozenset(
            {
                "service-identity:read-own",
                "service-identity:write",
                "service-plugin:read",
                "service-plugin:update-status",
                "app:data-read",
                "app:data-write",
            }
        )
    if service_type == "genestack_monitoring" and principal.is_internal_service:
        return frozenset(
            {
                "service-identity:read-own",
                "service-plugin:read",
                "genestack_monitoring:recipe-sync",
            }
        )
    if principal.is_internal_service:
        return frozenset({"service-identity:read-own", "service-plugin:read"})

    return frozenset()


def require_database_capability(
    principal: DatabasePrincipal,
    capability: DatabaseCapability,
    *,
    target_service_type: str | None = None,
) -> None:
    """Raise when the principal is not allowed to use a protected DB capability."""

    capabilities = database_capabilities_for_principal(principal)
    if capability not in capabilities:
        raise DatabaseAccessError(
            f"database principal {principal.normalized_service_type or principal.normalized_role or '<unknown>'!r} "
            f"cannot use capability {capability!r}"
        )

    if capability in {"adapter-credential:read", "service-identity:read-own"}:
        target = (target_service_type or "").strip().lower()
        service_type = principal.normalized_service_type
        allowed_broad_readers = {"api"}
        if capability == "adapter-credential:read":
            allowed_broad_readers = {"credential-manager"}
        if target and service_type not in {target, *allowed_broad_readers}:
            raise DatabaseAccessError(
                f"database principal {service_type or '<unknown>'!r} cannot read credentials for {target!r}"
            )
