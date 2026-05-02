"""Unit tests for authentication service bootstrap boundaries."""

from __future__ import annotations

from types import SimpleNamespace

from api.services import auth_service


def _settings(
    *,
    auth_enabled: bool = True,
    auth_local_enabled: bool = True,
    auth_username: str = "",
    auth_password: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        auth_enabled=auth_enabled,
        auth_local_enabled=auth_local_enabled,
        auth_username=auth_username,
        auth_password=auth_password,
    )


def test_local_superuser_credentials_use_secret_backed_env(monkeypatch) -> None:
    monkeypatch.setattr(
        auth_service,
        "get_settings",
        lambda: _settings(auth_username="admin", auth_password="secret"),
    )

    assert auth_service.get_local_superuser_credentials() == ("admin", "secret")


def test_local_superuser_credentials_do_not_read_kubernetes_secret(monkeypatch) -> None:
    def fail_import(_name: str) -> object:
        raise AssertionError("auth must not import Kubernetes clients for bootstrap secrets")

    monkeypatch.setattr(
        auth_service,
        "get_settings",
        lambda: _settings(auth_username="dev-admin", auth_password="dev-secret"),
    )
    monkeypatch.setattr("importlib.import_module", fail_import)

    assert auth_service.get_local_superuser_credentials() == ("dev-admin", "dev-secret")


def test_local_superuser_credentials_absent_without_env(monkeypatch) -> None:
    monkeypatch.setattr(auth_service, "get_settings", lambda: _settings())

    assert auth_service.get_local_superuser_credentials() is None
