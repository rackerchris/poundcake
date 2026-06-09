from __future__ import annotations

from api.core.config import Settings


def test_plugin_operation_database_url_reads_devstack_env_name(monkeypatch) -> None:
    monkeypatch.setenv(
        "POUNDCAKE_PLUGIN_OPERATION_DB_URL",
        "mysql+pymysql://pluginop:secret@db:3306/poundcake",
    )

    settings = Settings()

    assert (
        settings.plugin_operation_database_url
        == "mysql+pymysql://pluginop:secret@db:3306/poundcake"
    )


def test_auth0_ui_settings_ignore_generic_shared_env_names(monkeypatch) -> None:
    monkeypatch.delenv("POUNDCAKE_AUTH_AUTH0_UI_CLIENT_ID", raising=False)
    monkeypatch.delenv("POUNDCAKE_AUTH_AUTH0_UI_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("POUNDCAKE_AUTH_AUTH0_UI_CALLBACK_URL", raising=False)
    monkeypatch.setenv("POUNDCAKE_AUTH_AUTH0_CLIENT_ID", "generic-ui-client")
    monkeypatch.setenv("POUNDCAKE_AUTH_AUTH0_CLIENT_SECRET", "generic-ui-secret")
    monkeypatch.setenv("POUNDCAKE_AUTH_AUTH0_CALLBACK_URL", "https://generic.example/callback")

    settings = Settings()

    assert settings.auth_auth0_ui_enabled is False
    assert settings.auth_auth0_ui_client_id == ""
    assert settings.auth_auth0_ui_client_secret == ""
    assert settings.auth_auth0_ui_callback_url == ""


def test_azure_ad_cli_settings_ignore_generic_shared_env_names(monkeypatch) -> None:
    monkeypatch.delenv("POUNDCAKE_AUTH_AZURE_AD_CLI_CLIENT_ID", raising=False)
    monkeypatch.setenv("POUNDCAKE_AUTH_AZURE_AD_CLIENT_ID", "generic-cli-client")

    settings = Settings()

    assert settings.auth_azure_ad_cli_enabled is False
    assert settings.auth_azure_ad_cli_client_id == ""
