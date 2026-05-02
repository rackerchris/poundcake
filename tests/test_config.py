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
