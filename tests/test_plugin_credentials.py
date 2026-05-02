"""Tests for adapter credential-manager policy."""

from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from api.models.models import AdapterCredential, ServicePlugin
from api.services import credential_manager, credentials, service_identity
from api.services.credentials import (
    ServicePluginCredentialError,
    decrypt_payload,
    decrypt_service_identity_payload,
    encrypt_service_identity_payload,
)
from api.services.database_access import (
    DatabaseAccessError,
    principal_for_internal_service,
    require_database_capability,
)


class _Result:
    def __init__(self, row: ServicePlugin | None) -> None:
        self.row = row

    def scalar_one_or_none(self) -> ServicePlugin | None:
        return self.row


class _Db:
    def __init__(self, row: ServicePlugin | None) -> None:
        self.row = row

    async def execute(self, _statement: object) -> _Result:
        return _Result(self.row)


class _SequenceDb:
    def __init__(self, rows: list[object | None]) -> None:
        self.rows = rows
        self.added: list[object] = []

    async def execute(self, _statement: object) -> _Result:
        return _Result(self.rows.pop(0) if self.rows else None)  # type: ignore[arg-type]

    def add(self, row: object) -> None:
        self.added.append(row)

    def begin(self) -> "_SequenceDb":
        return self

    async def __aenter__(self) -> "_SequenceDb":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


class _StatementCapturingDb:
    def __init__(self, row: object | None) -> None:
        self.row = row
        self.statements: list[object] = []

    async def execute(self, statement: object) -> _Result:
        self.statements.append(statement)
        return _Result(self.row)  # type: ignore[arg-type]


def _plugin(
    *,
    service_type: str = "credential-manager",
    plugin_type: str = "internal_plugin",
    enabled: bool = True,
) -> ServicePlugin:
    return ServicePlugin(
        id=1,
        service_type=service_type,
        plugin_short_id="credm001",
        plugin_type=plugin_type,
        plugin_tier="supported",
        plugin_log_key=service_type,
        enabled=enabled,
        health_status="unknown",
    )


@asynccontextmanager
async def _credential_manager_session(db: _SequenceDb):
    yield db


def test_save_plugin_credential_is_not_public_write_api() -> None:
    assert not hasattr(credentials, "save_plugin_credential")
    assert not hasattr(credentials, "load_plugin_credential")


def test_adapter_modules_do_not_import_generic_credential_loader() -> None:
    plugin_sources = Path("api/plugins").glob("**/*.py")

    offenders = [
        str(path)
        for path in plugin_sources
        if "load_plugin_credential" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


async def test_credential_manager_can_write_adapter_credentials() -> None:
    await credential_manager._require_credential_manager_writer(
        _Db(_plugin()),  # type: ignore[arg-type]
        credential_type="bakery_monitor_hmac",
    )


async def test_write_adapter_credential_uses_credential_manager_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POUNDCAKE_PLUGIN_CREDENTIAL_ENCRYPTION_KEY", "unit-test-key")
    target = _plugin(service_type="bakery")
    writer = _plugin()
    db = _SequenceDb([writer, target, None])
    monkeypatch.setattr(
        credential_manager,
        "credential_manager_db_session",
        lambda: _credential_manager_session(db),
    )

    await credential_manager.write_adapter_credential(
        service_type="bakery",
        credential_type="bakery_monitor_hmac",
        credential_key_id="default",
        payload={"hmac_secret": "secret"},
        rotated=True,
    )

    assert len(db.added) == 1
    row = db.added[0]
    assert isinstance(row, AdapterCredential)
    assert row.service_plugin_id == target.id
    assert row.credential_type == "bakery_monitor_hmac"
    assert target.credential_status == "ready"
    assert target.last_credential_rotation_at is not None


async def test_read_adapter_credential_with_policy_uses_credential_manager_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POUNDCAKE_PLUGIN_CREDENTIAL_ENCRYPTION_KEY", "unit-test-key")
    row = AdapterCredential(
        service_plugin_id=1,
        credential_type="bakery_monitor_hmac",
        credential_key_id="default",
        encrypted_payload=credential_manager.encrypt_payload({"hmac_secret": "secret"}),
        allow_public_read=True,
    )
    db = _SequenceDb([row])
    monkeypatch.setattr(
        credential_manager,
        "credential_manager_db_session",
        lambda: _credential_manager_session(db),
    )

    result = await credential_manager.read_adapter_credential_with_policy(
        service_type="bakery",
        credential_type="bakery_monitor_hmac",
        credential_key_id="default",
    )

    from api.services.credential_manager import AdapterCredentialResult

    assert isinstance(result, AdapterCredentialResult)
    assert result.allow_public_read is True
    assert result.payload == {"hmac_secret": "secret"}


async def test_read_adapter_credential_payload_returns_decrypted_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POUNDCAKE_PLUGIN_CREDENTIAL_ENCRYPTION_KEY", "unit-test-key")
    row = AdapterCredential(
        service_plugin_id=1,
        credential_type="bakery_monitor_hmac",
        credential_key_id="default",
        encrypted_payload=credential_manager.encrypt_payload({"hmac_secret": "secret"}),
        allow_public_read=True,
    )
    db = _SequenceDb([row])
    monkeypatch.setattr(
        credential_manager,
        "credential_manager_db_session",
        lambda: _credential_manager_session(db),
    )

    result = await credential_manager.read_adapter_credential_payload(
        service_type="bakery",
        credential_type="bakery_monitor_hmac",
        credential_key_id="default",
    )

    assert result == {"hmac_secret": "secret"}


async def test_read_adapter_credential_payload_returns_none_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POUNDCAKE_PLUGIN_CREDENTIAL_ENCRYPTION_KEY", "unit-test-key")
    db = _SequenceDb([None])
    monkeypatch.setattr(
        credential_manager,
        "credential_manager_db_session",
        lambda: _credential_manager_session(db),
    )

    result = await credential_manager.read_adapter_credential_payload(
        service_type="bakery",
        credential_type="bakery_monitor_hmac",
        credential_key_id="default",
    )

    assert result is None


async def test_read_adapter_credential_with_policy_default_allow_public_read_is_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POUNDCAKE_PLUGIN_CREDENTIAL_ENCRYPTION_KEY", "unit-test-key")
    row = AdapterCredential(
        service_plugin_id=1,
        credential_type="bakery_monitor_hmac",
        credential_key_id="default",
        encrypted_payload=credential_manager.encrypt_payload({"hmac_secret": "secret"}),
        allow_public_read=False,
    )
    db = _SequenceDb([row])
    monkeypatch.setattr(
        credential_manager,
        "credential_manager_db_session",
        lambda: _credential_manager_session(db),
    )

    result = await credential_manager.read_adapter_credential_with_policy(
        service_type="bakery",
        credential_type="bakery_monitor_hmac",
        credential_key_id="default",
    )

    assert result.allow_public_read is False


def test_non_credential_manager_internal_service_cannot_read_other_adapter_credentials() -> None:
    with pytest.raises(
        DatabaseAccessError,
        match="cannot use capability 'adapter-credential:read'",
    ):
        require_database_capability(
            principal_for_internal_service("timer"),
            "adapter-credential:read",
            target_service_type="bakery",
        )


async def test_read_adapter_credential_query_scopes_service_type_and_key_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POUNDCAKE_PLUGIN_CREDENTIAL_ENCRYPTION_KEY", "unit-test-key")
    row = AdapterCredential(
        service_plugin_id=1,
        credential_type="bakery_monitor_hmac",
        credential_key_id="shared",
        encrypted_payload=credential_manager.encrypt_payload({"hmac_secret": "secret"}),
        allow_public_read=False,
    )
    db = _StatementCapturingDb(row)

    @asynccontextmanager
    async def credential_session():
        yield db

    monkeypatch.setattr(
        credential_manager,
        "credential_manager_db_session",
        credential_session,
    )

    result = await credential_manager.read_adapter_credential_with_policy(
        service_type="bakery",
        credential_type="bakery_monitor_hmac",
        credential_key_id="shared",
    )

    assert result is not None
    assert len(db.statements) == 1
    statement_sql = str(db.statements[0])
    assert "service_plugins.service_type" in statement_sql
    assert "adapter_credentials.credential_key_id" in statement_sql
    assert "adapter_credentials.credential_type" in statement_sql


async def test_write_adapter_credential_requires_registered_credential_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POUNDCAKE_PLUGIN_CREDENTIAL_ENCRYPTION_KEY", "unit-test-key")
    db = _SequenceDb([None])
    monkeypatch.setattr(
        credential_manager,
        "credential_manager_db_session",
        lambda: _credential_manager_session(db),
    )

    with pytest.raises(
        ServicePluginCredentialError, match="credential writer service is not registered"
    ):
        await credential_manager.write_adapter_credential(
            service_type="bakery",
            credential_type="bakery_monitor_hmac",
            credential_key_id="default",
            payload={"hmac_secret": "secret"},
        )


async def test_write_adapter_credential_rejects_disabled_credential_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POUNDCAKE_PLUGIN_CREDENTIAL_ENCRYPTION_KEY", "unit-test-key")
    writer = _plugin(enabled=False)
    db = _SequenceDb([writer])
    monkeypatch.setattr(
        credential_manager,
        "credential_manager_db_session",
        lambda: _credential_manager_session(db),
    )

    with pytest.raises(ServicePluginCredentialError, match="not enabled for token generation"):
        await credential_manager.write_adapter_credential(
            service_type="bakery",
            credential_type="bakery_monitor_hmac",
            credential_key_id="default",
            payload={"hmac_secret": "secret"},
        )


async def test_write_adapter_credential_rejects_external_credential_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POUNDCAKE_PLUGIN_CREDENTIAL_ENCRYPTION_KEY", "unit-test-key")
    writer = _plugin(plugin_type="external_plugin")
    db = _SequenceDb([writer])
    monkeypatch.setattr(
        credential_manager,
        "credential_manager_db_session",
        lambda: _credential_manager_session(db),
    )

    with pytest.raises(ServicePluginCredentialError, match="not enabled for token generation"):
        await credential_manager.write_adapter_credential(
            service_type="bakery",
            credential_type="bakery_monitor_hmac",
            credential_key_id="default",
            payload={"hmac_secret": "secret"},
        )


async def test_write_adapter_credential_cannot_mint_internal_hmac(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POUNDCAKE_PLUGIN_CREDENTIAL_ENCRYPTION_KEY", "unit-test-key")
    db = _SequenceDb([])
    monkeypatch.setattr(
        credential_manager,
        "credential_manager_db_session",
        lambda: _credential_manager_session(db),
    )

    with pytest.raises(ServicePluginCredentialError, match="service-identity-owned"):
        await credential_manager.write_adapter_credential(
            service_type="timer",
            credential_type="internal_control_plane_hmac",
            credential_key_id="default",
            payload={"secret": "nope"},
        )


async def test_mark_adapter_credential_error_updates_only_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = _plugin()
    target = _plugin(service_type="bakery")
    db = _SequenceDb([writer, target])
    monkeypatch.setattr(
        credential_manager,
        "credential_manager_db_session",
        lambda: _credential_manager_session(db),
    )

    await credential_manager.mark_adapter_credential_error(
        service_type="bakery",
        error="bad credential",
    )

    assert target.credential_status == "error"
    assert target.credential_error == "bad credential"
    assert db.added == []


def test_adapter_credential_writer_identity_cannot_be_spoofed() -> None:
    signature = inspect.signature(credential_manager.write_adapter_credential)

    assert "writer_service_type" not in signature.parameters


def test_internal_hmac_writer_identity_cannot_be_spoofed() -> None:
    signature = inspect.signature(service_identity.upsert_internal_hmac_credential)

    assert "writer_service_type" not in signature.parameters


def test_credential_reader_identity_cannot_be_spoofed() -> None:
    adapter_signature = inspect.signature(credential_manager.read_adapter_credential)

    assert "requester_service_type" not in adapter_signature.parameters
    assert "read_internal_hmac_payload" not in service_identity.__all__


def test_service_identity_credentials_use_separate_encryption_key(monkeypatch) -> None:
    monkeypatch.setenv("POUNDCAKE_PLUGIN_CREDENTIAL_ENCRYPTION_KEY", "adapter-key")
    monkeypatch.setenv(
        "POUNDCAKE_SERVICE_IDENTITY_CREDENTIAL_ENCRYPTION_KEY",
        "service-identity-key",
    )

    encrypted = encrypt_service_identity_payload({"hmac_secret": "secret"})

    assert decrypt_service_identity_payload(encrypted) == {"hmac_secret": "secret"}
    with pytest.raises(Exception):
        decrypt_payload(encrypted)


def test_service_identity_credentials_require_service_identity_key(monkeypatch) -> None:
    monkeypatch.setenv("POUNDCAKE_PLUGIN_CREDENTIAL_ENCRYPTION_KEY", "adapter-key")
    monkeypatch.delenv("POUNDCAKE_SERVICE_IDENTITY_CREDENTIAL_ENCRYPTION_KEY", raising=False)

    with pytest.raises(
        ServicePluginCredentialError,
        match="POUNDCAKE_SERVICE_IDENTITY_CREDENTIAL_ENCRYPTION_KEY",
    ):
        encrypt_service_identity_payload({"hmac_secret": "secret"})


def test_service_identity_credentials_do_not_need_adapter_key(monkeypatch) -> None:
    monkeypatch.delenv("POUNDCAKE_PLUGIN_CREDENTIAL_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv(
        "POUNDCAKE_SERVICE_IDENTITY_CREDENTIAL_ENCRYPTION_KEY",
        "service-identity-key",
    )

    encrypted = encrypt_service_identity_payload({"hmac_secret": "secret"})

    assert decrypt_service_identity_payload(encrypted) == {"hmac_secret": "secret"}


async def test_external_plugin_cannot_write_adapter_credentials() -> None:
    with pytest.raises(ServicePluginCredentialError, match="not enabled for token generation"):
        await credential_manager._require_credential_manager_writer(
            _Db(_plugin(plugin_type="external_plugin")),  # type: ignore[arg-type]
            credential_type="bakery_monitor_hmac",
        )


async def test_shared_writer_cannot_mint_internal_control_plane_hmac() -> None:
    with pytest.raises(ServicePluginCredentialError, match="service-identity-owned"):
        await credential_manager._require_credential_manager_writer(
            _Db(_plugin()),  # type: ignore[arg-type]
            credential_type="internal_control_plane_hmac",
        )
