"""Tests for policy-aware database access capabilities."""

from __future__ import annotations

from pathlib import Path

import pytest

from api.services.database_access import (
    DatabaseAccessError,
    principal_for_internal_service,
    require_database_capability,
)


def test_credential_manager_can_write_adapter_credentials() -> None:
    principal = principal_for_internal_service("credential-manager")

    require_database_capability(principal, "adapter-credential:write")


def test_credential_manager_can_read_adapter_credentials() -> None:
    principal = principal_for_internal_service("credential-manager")

    require_database_capability(
        principal,
        "adapter-credential:read",
        target_service_type="bakery",
    )


def test_control_plane_services_can_write_service_identity_credentials() -> None:
    for service_type in ("api", "service-identity-manager"):
        principal = principal_for_internal_service(service_type)

        require_database_capability(principal, "service-identity:write")


def test_plugin_registry_can_update_service_plugin_status_only() -> None:
    principal = principal_for_internal_service("plugin-registry")

    require_database_capability(principal, "service-plugin:update-status")
    require_database_capability(principal, "service-plugin:read")

    with pytest.raises(DatabaseAccessError, match="service-identity:write"):
        require_database_capability(principal, "service-identity:write")
    with pytest.raises(DatabaseAccessError, match="adapter-credential:write"):
        require_database_capability(principal, "adapter-credential:write")


def test_dishwasher_cannot_write_adapter_credentials() -> None:
    principal = principal_for_internal_service("dishwasher")

    with pytest.raises(DatabaseAccessError, match="adapter-credential:write"):
        require_database_capability(principal, "adapter-credential:write")


def test_worker_reader_is_limited_to_own_credentials() -> None:
    principal = principal_for_internal_service("timer")

    require_database_capability(
        principal,
        "service-identity:read-own",
        target_service_type="timer",
    )
    with pytest.raises(DatabaseAccessError, match="cannot read credentials"):
        require_database_capability(
            principal,
            "service-identity:read-own",
            target_service_type="dishwasher",
        )


def test_worker_reader_cannot_read_adapter_credentials() -> None:
    principal = principal_for_internal_service("timer")

    with pytest.raises(DatabaseAccessError, match="adapter-credential:read"):
        require_database_capability(
            principal,
            "adapter-credential:read",
            target_service_type="timer",
        )


def test_trusted_internal_principal_constructor_stays_in_boundary_modules() -> None:
    allowed_files = {
        Path("api/services/database_access.py"),
        Path("api/services/credential_manager.py"),
        Path("api/services/service_identity.py"),
        Path("api/services/plugin_bootstrap.py"),
        Path("api/services/plugin_operations.py"),
    }
    offenders = sorted(
        path
        for path in Path("api").rglob("*.py")
        if path not in allowed_files and "principal_for_internal_service" in path.read_text()
    )

    assert offenders == []
