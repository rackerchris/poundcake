"""Tests for PoundCake internal HMAC API authentication."""

from __future__ import annotations

import pytest
from contextlib import asynccontextmanager
from starlette.requests import Request

from api.api.auth import _internal_hmac_context
from api.core.config import get_settings
from api.models.models import ServiceIdentityCredential, ServicePlugin
from api.services.credentials import encrypt_service_identity_payload
from api.services.service_identity import InternalHmacCredential
from api.services.auth_service import AccessDeniedError, _MEMORY_STATE, ensure_request_authorized
from shared.internal_hmac import INTERNAL_HMAC_NONCE_HEADER, build_internal_hmac_headers


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()
    _MEMORY_STATE.clear()
    yield
    _MEMORY_STATE.clear()
    get_settings.cache_clear()


def _request(
    *,
    method: str,
    path: str,
    body: bytes,
    headers: dict[str, str],
) -> Request:
    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": body, "more_body": False}

    raw_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1")) for key, value in headers.items()
    ]
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path.split("?", 1)[0],
            "query_string": path.split("?", 1)[1].encode("latin-1") if "?" in path else b"",
            "headers": raw_headers,
            "scheme": "http",
            "server": ("api", 8000),
        },
        receive,
    )


class _ExecuteResult:
    def __init__(
        self,
        rows: list[tuple[ServiceIdentityCredential, ServicePlugin]] | None,
    ) -> None:
        self.rows = rows or []

    def first(self) -> tuple[ServiceIdentityCredential, ServicePlugin] | None:
        return self.rows[0] if self.rows else None

    def all(self) -> list[tuple[ServiceIdentityCredential, ServicePlugin]]:
        return self.rows


class _Db:
    def __init__(
        self,
        rows: (
            tuple[ServiceIdentityCredential, ServicePlugin]
            | list[tuple[ServiceIdentityCredential, ServicePlugin]]
            | None
        ),
    ) -> None:
        if rows is None:
            self.rows: list[tuple[ServiceIdentityCredential, ServicePlugin]] = []
        elif isinstance(rows, list):
            self.rows = rows
        else:
            self.rows = [rows]

    async def execute(self, _statement: object) -> _ExecuteResult:
        return _ExecuteResult(self.rows)


def _plugin(
    *,
    enabled: bool = True,
    service_type: str = "timer",
    plugin_type: str = "internal_plugin",
) -> ServicePlugin:
    return ServicePlugin(
        id=1,
        service_type=service_type,
        plugin_short_id="timer001",
        plugin_type=plugin_type,
        plugin_tier="supported",
        plugin_log_key=service_type,
        enabled=enabled,
        health_status="unknown",
    )


async def test_internal_hmac_context_accepts_signed_control_plane_request(monkeypatch) -> None:
    get_settings.cache_clear()
    body = b'{"hello":"world"}'
    headers = build_internal_hmac_headers(
        key_id="unit-key",
        secret="unit-secret",
        method="POST",
        url_or_path="/api/v1/orders?source=unit",
        body=body,
        timestamp="1800000000",
    )
    monkeypatch.setattr("api.api.auth.time.time", lambda: 1800000000)

    async def credential_for_key(_db: object, key_id: str) -> InternalHmacCredential | None:
        if key_id != "unit-key":
            return None
        return InternalHmacCredential(
            secret="unit-secret",
            service_plugin_id=7,
            service_type="prep-chef",
            plugin_type="internal_plugin",
            enabled=True,
            auth_scope="poundcake_control_plane",
        )

    monkeypatch.setattr(
        "api.api.auth.internal_hmac_credential_for_key",
        credential_for_key,
    )

    context = await _internal_hmac_context(
        _request(method="POST", path="/api/v1/orders?source=unit", body=body, headers=headers),
        object(),  # type: ignore[arg-type]
    )

    assert context is not None
    assert context.provider == "service"
    assert context.role == "service"
    assert context.service_plugin_id == 7
    assert context.service_type == "prep-chef"
    assert context.plugin_type == "internal_plugin"
    assert context.credential_scope == "poundcake_control_plane"


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", [None, "unexpected-scope"])
async def test_internal_hmac_context_rejects_invalid_credential_scope(
    monkeypatch,
    scope: str | None,
) -> None:
    get_settings.cache_clear()
    body = b""
    headers = build_internal_hmac_headers(
        key_id="unit-key",
        secret="unit-secret",
        method="GET",
        url_or_path="/api/v1/plugins/timer",
        body=body,
        timestamp="1800000000",
    )
    monkeypatch.setattr("api.api.auth.time.time", lambda: 1800000000)

    async def credential_for_key(_db: object, key_id: str) -> InternalHmacCredential | None:
        if key_id != "unit-key":
            return None
        return InternalHmacCredential(
            secret="unit-secret",
            service_plugin_id=7,
            service_type="timer",
            plugin_type="internal_plugin",
            enabled=True,
            auth_scope=scope,
        )

    monkeypatch.setattr(
        "api.api.auth.internal_hmac_credential_for_key",
        credential_for_key,
    )

    context = await _internal_hmac_context(
        _request(method="GET", path="/api/v1/plugins/timer", body=body, headers=headers),
        object(),  # type: ignore[arg-type]
    )

    assert context is None


async def test_internal_hmac_context_rejects_stale_timestamp(monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("POUNDCAKE_INTERNAL_HMAC_CLOCK_SKEW_SECONDS", "5")
    get_settings.cache_clear()
    body = b""
    headers = build_internal_hmac_headers(
        key_id="unit-key",
        secret="unit-secret",
        method="GET",
        url_or_path="/api/v1/plugins/timer",
        body=body,
        timestamp="1799999990",
    )
    monkeypatch.setattr("api.api.auth.time.time", lambda: 1800000000)

    async def credential_for_key(_db: object, key_id: str) -> InternalHmacCredential | None:
        if key_id != "unit-key":
            return None
        return InternalHmacCredential(
            secret="unit-secret",
            service_plugin_id=7,
            service_type="timer",
            plugin_type="internal_plugin",
            enabled=True,
            auth_scope="poundcake_control_plane",
        )

    monkeypatch.setattr("api.api.auth.internal_hmac_credential_for_key", credential_for_key)

    context = await _internal_hmac_context(
        _request(method="GET", path="/api/v1/plugins/timer", body=body, headers=headers),
        object(),  # type: ignore[arg-type]
    )

    assert context is None


async def test_internal_hmac_context_uses_auth_verifier_session(monkeypatch) -> None:
    get_settings.cache_clear()
    body = b""
    headers = build_internal_hmac_headers(
        key_id="unit-key",
        secret="unit-secret",
        method="GET",
        url_or_path="/api/v1/plugins/timer",
        body=body,
        timestamp="1800000000",
    )
    monkeypatch.setattr("api.api.auth.time.time", lambda: 1800000000)
    auth_db = object()
    captured: dict[str, object] = {}

    @asynccontextmanager
    async def verifier_session():
        captured["opened"] = True
        yield auth_db

    async def credential_for_key(db: object, key_id: str) -> InternalHmacCredential | None:
        captured["db"] = db
        if key_id != "unit-key":
            return None
        return InternalHmacCredential(
            secret="unit-secret",
            service_plugin_id=7,
            service_type="timer",
            plugin_type="internal_plugin",
            enabled=True,
            auth_scope="poundcake_control_plane",
        )

    monkeypatch.setattr("api.api.auth.auth_verifier_db_session", verifier_session)
    monkeypatch.setattr("api.api.auth.internal_hmac_credential_for_key", credential_for_key)

    context = await _internal_hmac_context(
        _request(method="GET", path="/api/v1/plugins/timer", body=body, headers=headers)
    )

    assert context is not None
    assert captured == {"opened": True, "db": auth_db}


async def test_internal_hmac_context_reads_secret_from_service_identity_credentials(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "POUNDCAKE_SERVICE_IDENTITY_CREDENTIAL_ENCRYPTION_KEY",
        "unit-service-identity-key",
    )
    get_settings.cache_clear()
    body = b""
    headers = build_internal_hmac_headers(
        key_id="poundcake-control-plane:timer",
        secret="unit-secret",
        method="GET",
        url_or_path="/api/v1/plugins/timer",
        body=body,
        timestamp="1800000000",
    )
    monkeypatch.setattr("api.api.auth.time.time", lambda: 1800000000)
    credential = ServiceIdentityCredential(
        service_plugin_id=1,
        credential_type="internal_control_plane_hmac",
        credential_key_id="poundcake-control-plane:timer",
        encrypted_payload=encrypt_service_identity_payload(
            {
                "hmac_key_id": "poundcake-control-plane:timer",
                "hmac_secret": "unit-secret",
                "auth_scope": "poundcake_control_plane",
            }
        ),
    )

    context = await _internal_hmac_context(
        _request(method="GET", path="/api/v1/plugins/timer", body=body, headers=headers),
        _Db((credential, _plugin(service_type="timer"))),  # type: ignore[arg-type]
    )

    assert context is not None
    assert context.role == "service"
    assert context.service_type == "timer"
    assert context.plugin_type == "internal_plugin"


async def test_internal_hmac_context_rejects_disabled_plugin(monkeypatch) -> None:
    monkeypatch.setenv(
        "POUNDCAKE_SERVICE_IDENTITY_CREDENTIAL_ENCRYPTION_KEY",
        "unit-service-identity-key",
    )
    get_settings.cache_clear()
    body = b""
    headers = build_internal_hmac_headers(
        key_id="poundcake-control-plane:timer",
        secret="unit-secret",
        method="GET",
        url_or_path="/api/v1/plugins/timer",
        body=body,
        timestamp="1800000000",
    )
    monkeypatch.setattr("api.api.auth.time.time", lambda: 1800000000)
    credential = ServiceIdentityCredential(
        service_plugin_id=1,
        credential_type="internal_control_plane_hmac",
        credential_key_id="poundcake-control-plane:timer",
        encrypted_payload=encrypt_service_identity_payload(
            {
                "hmac_key_id": "poundcake-control-plane:timer",
                "hmac_secret": "unit-secret",
            }
        ),
    )

    context = await _internal_hmac_context(
        _request(method="GET", path="/api/v1/plugins/timer", body=body, headers=headers),
        _Db((credential, _plugin(enabled=False, service_type="timer"))),  # type: ignore[arg-type]
    )

    assert context is None


async def test_internal_hmac_context_rejects_external_plugin_credential(monkeypatch) -> None:
    monkeypatch.setenv(
        "POUNDCAKE_SERVICE_IDENTITY_CREDENTIAL_ENCRYPTION_KEY",
        "unit-service-identity-key",
    )
    get_settings.cache_clear()
    body = b""
    headers = build_internal_hmac_headers(
        key_id="poundcake-control-plane:dummy",
        secret="unit-secret",
        method="GET",
        url_or_path="/api/v1/plugins/dummy",
        body=body,
        timestamp="1800000000",
    )
    monkeypatch.setattr("api.api.auth.time.time", lambda: 1800000000)
    credential = ServiceIdentityCredential(
        service_plugin_id=1,
        credential_type="internal_control_plane_hmac",
        credential_key_id="poundcake-control-plane:dummy",
        encrypted_payload=encrypt_service_identity_payload(
            {
                "hmac_key_id": "poundcake-control-plane:dummy",
                "hmac_secret": "unit-secret",
            }
        ),
    )

    context = await _internal_hmac_context(
        _request(method="GET", path="/api/v1/plugins/dummy", body=body, headers=headers),
        _Db((credential, _plugin(service_type="dummy", plugin_type="external_plugin"))),  # type: ignore[arg-type]
    )

    assert context is None


async def test_internal_hmac_context_rejects_duplicate_key_id(monkeypatch) -> None:
    monkeypatch.setenv(
        "POUNDCAKE_SERVICE_IDENTITY_CREDENTIAL_ENCRYPTION_KEY",
        "unit-service-identity-key",
    )
    get_settings.cache_clear()
    body = b""
    headers = build_internal_hmac_headers(
        key_id="poundcake-control-plane:shared",
        secret="unit-secret",
        method="GET",
        url_or_path="/api/v1/plugins/timer",
        body=body,
        timestamp="1800000000",
    )
    monkeypatch.setattr("api.api.auth.time.time", lambda: 1800000000)
    credential = ServiceIdentityCredential(
        service_plugin_id=1,
        credential_type="internal_control_plane_hmac",
        credential_key_id="poundcake-control-plane:shared",
        encrypted_payload=encrypt_service_identity_payload(
            {
                "hmac_key_id": "poundcake-control-plane:shared",
                "hmac_secret": "unit-secret",
            }
        ),
    )

    context = await _internal_hmac_context(
        _request(method="GET", path="/api/v1/plugins/timer", body=body, headers=headers),
        _Db(
            [
                (credential, _plugin(service_type="timer")),
                (credential, _plugin(service_type="prep-chef")),
            ]
        ),  # type: ignore[arg-type]
    )

    assert context is None


async def test_internal_hmac_context_rejects_tampered_body(monkeypatch) -> None:
    get_settings.cache_clear()
    headers = build_internal_hmac_headers(
        key_id="unit-key",
        secret="unit-secret",
        method="POST",
        url_or_path="/api/v1/orders",
        body=b'{"hello":"world"}',
        timestamp="1800000000",
    )
    monkeypatch.setattr("api.api.auth.time.time", lambda: 1800000000)

    async def credential_for_key(_db: object, key_id: str) -> InternalHmacCredential | None:
        if key_id != "unit-key":
            return None
        return InternalHmacCredential(
            secret="unit-secret",
            service_plugin_id=7,
            service_type="prep-chef",
            plugin_type="internal_plugin",
            enabled=True,
            auth_scope="poundcake_control_plane",
        )

    monkeypatch.setattr(
        "api.api.auth.internal_hmac_credential_for_key",
        credential_for_key,
    )

    context = await _internal_hmac_context(
        _request(
            method="POST",
            path="/api/v1/orders",
            body=b'{"hello":"tampered"}',
            headers=headers,
        ),
        object(),  # type: ignore[arg-type]
    )

    assert context is None


async def test_internal_hmac_context_rejects_tampered_query_string(monkeypatch) -> None:
    get_settings.cache_clear()
    body = b""
    headers = build_internal_hmac_headers(
        key_id="unit-key",
        secret="unit-secret",
        method="GET",
        url_or_path="/api/v1/plugins/timer?limit=1",
        body=body,
        timestamp="1800000000",
    )
    monkeypatch.setattr("api.api.auth.time.time", lambda: 1800000000)

    async def credential_for_key(_db: object, key_id: str) -> InternalHmacCredential | None:
        if key_id != "unit-key":
            return None
        return InternalHmacCredential(
            secret="unit-secret",
            service_plugin_id=7,
            service_type="timer",
            plugin_type="internal_plugin",
            enabled=True,
            auth_scope="poundcake_control_plane",
        )

    monkeypatch.setattr("api.api.auth.internal_hmac_credential_for_key", credential_for_key)

    context = await _internal_hmac_context(
        _request(
            method="GET",
            path="/api/v1/plugins/timer?limit=999",
            body=body,
            headers=headers,
        ),
        object(),  # type: ignore[arg-type]
    )

    assert context is None


async def test_internal_hmac_context_rejects_tampered_method(monkeypatch) -> None:
    get_settings.cache_clear()
    body = b"{}"
    headers = build_internal_hmac_headers(
        key_id="unit-key",
        secret="unit-secret",
        method="POST",
        url_or_path="/api/v1/cook/orders/1",
        body=body,
        timestamp="1800000000",
        nonce="unit-nonce",
    )
    monkeypatch.setattr("api.api.auth.time.time", lambda: 1800000000)

    async def credential_for_key(_db: object, key_id: str) -> InternalHmacCredential | None:
        if key_id != "unit-key":
            return None
        return InternalHmacCredential(
            secret="unit-secret",
            service_plugin_id=7,
            service_type="prep-chef",
            plugin_type="internal_plugin",
            enabled=True,
            auth_scope="poundcake_control_plane",
        )

    monkeypatch.setattr("api.api.auth.internal_hmac_credential_for_key", credential_for_key)

    context = await _internal_hmac_context(
        _request(method="PUT", path="/api/v1/cook/orders/1", body=body, headers=headers),
        object(),  # type: ignore[arg-type]
    )

    assert context is None


async def test_internal_hmac_context_rejects_mismatched_key_id_header(monkeypatch) -> None:
    get_settings.cache_clear()
    body = b""
    headers = build_internal_hmac_headers(
        key_id="unit-key",
        secret="unit-secret",
        method="GET",
        url_or_path="/api/v1/plugins/timer",
        body=body,
        timestamp="1800000000",
    )
    headers["X-PoundCake-Internal-Key-ID"] = "different-key"
    monkeypatch.setattr("api.api.auth.time.time", lambda: 1800000000)

    async def credential_for_key(_db: object, key_id: str) -> InternalHmacCredential | None:
        if key_id != "unit-key":
            return None
        return InternalHmacCredential(
            secret="unit-secret",
            service_plugin_id=7,
            service_type="timer",
            plugin_type="internal_plugin",
            enabled=True,
            auth_scope="poundcake_control_plane",
        )

    monkeypatch.setattr("api.api.auth.internal_hmac_credential_for_key", credential_for_key)

    context = await _internal_hmac_context(
        _request(method="GET", path="/api/v1/plugins/timer", body=body, headers=headers),
        object(),  # type: ignore[arg-type]
    )

    assert context is None


async def test_internal_hmac_context_rejects_replayed_mutating_nonce(monkeypatch) -> None:
    get_settings.cache_clear()
    body = b'{"hello":"world"}'
    headers = build_internal_hmac_headers(
        key_id="unit-key",
        secret="unit-secret",
        method="POST",
        url_or_path="/api/v1/orders",
        body=body,
        timestamp="1800000000",
        nonce="unit-nonce",
    )
    monkeypatch.setattr("api.api.auth.time.time", lambda: 1800000000)

    async def credential_for_key(_db: object, key_id: str) -> InternalHmacCredential | None:
        if key_id != "unit-key":
            return None
        return InternalHmacCredential(
            secret="unit-secret",
            service_plugin_id=7,
            service_type="prep-chef",
            plugin_type="internal_plugin",
            enabled=True,
            auth_scope="poundcake_control_plane",
        )

    monkeypatch.setattr(
        "api.api.auth.internal_hmac_credential_for_key",
        credential_for_key,
    )

    first_context = await _internal_hmac_context(
        _request(method="POST", path="/api/v1/orders", body=body, headers=headers),
        object(),  # type: ignore[arg-type]
    )
    replay_context = await _internal_hmac_context(
        _request(method="POST", path="/api/v1/orders", body=body, headers=headers),
        object(),  # type: ignore[arg-type]
    )

    assert first_context is not None
    assert replay_context is None


async def test_internal_hmac_context_rejects_mutation_without_nonce(monkeypatch) -> None:
    get_settings.cache_clear()
    body = b'{"hello":"world"}'
    headers = build_internal_hmac_headers(
        key_id="unit-key",
        secret="unit-secret",
        method="POST",
        url_or_path="/api/v1/orders",
        body=body,
        timestamp="1800000000",
        nonce="unit-nonce",
    )
    headers.pop(INTERNAL_HMAC_NONCE_HEADER)
    monkeypatch.setattr("api.api.auth.time.time", lambda: 1800000000)

    async def credential_for_key(_db: object, key_id: str) -> InternalHmacCredential | None:
        if key_id != "unit-key":
            return None
        return InternalHmacCredential(
            secret="unit-secret",
            service_plugin_id=7,
            service_type="prep-chef",
            plugin_type="internal_plugin",
            enabled=True,
            auth_scope="poundcake_control_plane",
        )

    monkeypatch.setattr(
        "api.api.auth.internal_hmac_credential_for_key",
        credential_for_key,
    )

    context = await _internal_hmac_context(
        _request(method="POST", path="/api/v1/orders", body=body, headers=headers),
        object(),  # type: ignore[arg-type]
    )

    assert context is None


async def test_internal_hmac_context_database_nonce_store_rejects_replay(monkeypatch) -> None:
    """Verify database-backed nonce store detects replay across the same session."""
    monkeypatch.setenv("POUNDCAKE_INTERNAL_HMAC_NONCE_STORE", "database")
    get_settings.cache_clear()
    body = b'{"hello":"world"}'
    headers = build_internal_hmac_headers(
        key_id="unit-key",
        secret="unit-secret",
        method="POST",
        url_or_path="/api/v1/orders",
        body=body,
        timestamp="1800000000",
        nonce="unit-nonce-db",
    )
    monkeypatch.setattr("api.api.auth.time.time", lambda: 1800000000)

    async def credential_for_key(_db: object, key_id: str) -> InternalHmacCredential | None:
        if key_id != "unit-key":
            return None
        return InternalHmacCredential(
            secret="unit-secret",
            service_plugin_id=7,
            service_type="prep-chef",
            plugin_type="internal_plugin",
            enabled=True,
            auth_scope="poundcake_control_plane",
        )

    monkeypatch.setattr(
        "api.api.auth.internal_hmac_credential_for_key",
        credential_for_key,
    )

    insert_results: list[bool] = []

    class _FakeResult:
        def __init__(self, rowcount: int) -> None:
            self.rowcount = rowcount

    async def fake_execute(_statement: object, _params: object | None = None) -> _FakeResult:
        if not insert_results:
            insert_results.append(True)
            return _FakeResult(1)
        insert_results.append(True)
        return _FakeResult(0)

    class _FakeSession:
        async def execute(self, *args: object, **kwargs: object) -> _FakeResult:
            return await fake_execute(args, kwargs)

        async def commit(self) -> None:
            pass

    @asynccontextmanager
    async def fake_verifier_session():
        yield _FakeSession()

    monkeypatch.setattr("api.api.auth.auth_verifier_db_session", fake_verifier_session)

    first_context = await _internal_hmac_context(
        _request(method="POST", path="/api/v1/orders", body=body, headers=headers),
    )
    replay_context = await _internal_hmac_context(
        _request(method="POST", path="/api/v1/orders", body=body, headers=headers),
    )

    assert first_context is not None
    assert replay_context is None
    assert len(insert_results) == 2


def test_scoped_internal_services_allow_expected_workflow_routes() -> None:
    prep = InternalHmacCredential(
        secret="secret",
        service_plugin_id=1,
        service_type="prep-chef",
        plugin_type="internal_plugin",
        enabled=True,
    )
    timer = InternalHmacCredential(
        secret="secret",
        service_plugin_id=2,
        service_type="timer",
        plugin_type="internal_plugin",
        enabled=True,
    )
    expediter_runner = InternalHmacCredential(
        secret="secret",
        service_plugin_id=5,
        service_type="expediter-runner",
        plugin_type="internal_plugin",
        enabled=True,
    )
    dishwasher = InternalHmacCredential(
        secret="secret",
        service_plugin_id=3,
        service_type="dishwasher",
        plugin_type="internal_plugin",
        enabled=True,
    )
    credential_manager = InternalHmacCredential(
        secret="secret",
        service_plugin_id=4,
        service_type="credential-manager",
        plugin_type="internal_plugin",
        enabled=True,
    )
    cases = [
        (prep, "GET", "/api/v1/orders"),
        (prep, "POST", "/api/v1/cook/orders/1"),
        (timer, "GET", "/api/v1/dish-ingredients/in-flight"),
        (timer, "GET", "/api/v1/dish-ingredients/cancel-requested"),
        (timer, "POST", "/api/v1/dish-ingredients/1/poll-claim"),
        (timer, "POST", "/api/v1/dish-ingredients/1/poll-release"),
        (timer, "POST", "/api/v1/dish-ingredients/1/reconcile"),
        (timer, "POST", "/api/v1/cook/dishes/1/advance"),
        (timer, "GET", "/api/v1/expediter/status/dummy/abc"),
        (timer, "POST", "/api/v1/expediter/cancel/dummy/abc"),
        (expediter_runner, "GET", "/api/v1/dish-ingredients/execution-pending"),
        (expediter_runner, "POST", "/api/v1/dish-ingredients/1/execution-claim"),
        (expediter_runner, "POST", "/api/v1/dish-ingredients/1/execution-release"),
        (expediter_runner, "POST", "/api/v1/dish-ingredients/1/reconcile"),
        (expediter_runner, "POST", "/api/v1/expediter/execute/1"),
        (expediter_runner, "POST", "/api/v1/cook/dishes/1/advance"),
        (dishwasher, "GET", "/api/v1/plugins"),
        (dishwasher, "POST", "/api/v1/internal/service-registry/ingredients/bulk"),
        (dishwasher, "POST", "/api/v1/recipes/"),
        (dishwasher, "GET", "/api/v1/scheduled-tasks/due"),
        (dishwasher, "POST", "/api/v1/scheduled-tasks"),
        (dishwasher, "POST", "/api/v1/orders"),
        (credential_manager, "GET", "/api/v1/plugins"),
        (credential_manager, "GET", "/api/v1/plugins/credential-manager"),
    ]
    for credential, method, path in cases:
        context = _context_from_credential(credential)
        ensure_request_authorized(context, path, method)


def test_scoped_internal_services_deny_cross_service_or_admin_routes() -> None:
    timer = _context_from_credential(
        InternalHmacCredential(
            secret="secret",
            service_plugin_id=2,
            service_type="timer",
            plugin_type="internal_plugin",
            enabled=True,
        )
    )
    with pytest.raises(AccessDeniedError):
        ensure_request_authorized(timer, "/api/v1/recipes/", "POST")
    with pytest.raises(AccessDeniedError):
        ensure_request_authorized(timer, "/api/v1/auth/bindings", "GET")
    with pytest.raises(AccessDeniedError):
        ensure_request_authorized(timer, "/api/v1/expediter/execute/1", "POST")

    expediter_runner = _context_from_credential(
        InternalHmacCredential(
            secret="secret",
            service_plugin_id=5,
            service_type="expediter-runner",
            plugin_type="internal_plugin",
            enabled=True,
        )
    )
    with pytest.raises(AccessDeniedError):
        ensure_request_authorized(expediter_runner, "/api/v1/expediter/status/dummy/abc", "GET")
    with pytest.raises(AccessDeniedError):
        ensure_request_authorized(expediter_runner, "/api/v1/dish-ingredients/1/poll-claim", "POST")
    with pytest.raises(AccessDeniedError):
        ensure_request_authorized(expediter_runner, "/api/v1/scheduled-tasks/due", "GET")

    external = _context_from_credential(
        InternalHmacCredential(
            secret="secret",
            service_plugin_id=9,
            service_type="dummy",
            plugin_type="external_plugin",
            enabled=True,
        )
    )
    with pytest.raises(AccessDeniedError):
        ensure_request_authorized(external, "/api/v1/dish-ingredients/1/reconcile", "POST")
    with pytest.raises(AccessDeniedError):
        ensure_request_authorized(
            external, "/api/v1/internal/service-registry/ingredients/bulk", "POST"
        )

    reserved_external = _context_from_credential(
        InternalHmacCredential(
            secret="secret",
            service_plugin_id=10,
            service_type="timer",
            plugin_type="external_plugin",
            enabled=True,
        )
    )
    with pytest.raises(AccessDeniedError):
        ensure_request_authorized(reserved_external, "/api/v1/dish-ingredients/1/reconcile", "POST")


def test_unregistered_generic_service_context_cannot_call_internal_workflow_routes() -> None:
    from api.services.auth_service import service_token_context

    context = service_token_context()
    with pytest.raises(AccessDeniedError):
        ensure_request_authorized(context, "/api/v1/dish-ingredients/1/reconcile", "POST")
    with pytest.raises(AccessDeniedError):
        ensure_request_authorized(context, "/api/v1/cook/orders/1", "POST")


def test_service_route_matching_rejects_sibling_prefixes() -> None:
    dishwasher = _context_from_credential(
        InternalHmacCredential(
            secret="secret",
            service_plugin_id=3,
            service_type="dishwasher",
            plugin_type="internal_plugin",
            enabled=True,
        )
    )

    for path in (
        "/api/v1/recipes-admin",
        "/api/v1/service-registry-extra",
        "/api/v1/internal/service-registry-extra",
        "/api/v1/scheduled-tasks-extra",
    ):
        with pytest.raises(AccessDeniedError):
            ensure_request_authorized(dishwasher, path, "POST")


def _context_from_credential(credential: InternalHmacCredential):
    from api.services.auth_service import service_token_context

    return service_token_context(
        service_plugin_id=credential.service_plugin_id,
        service_type=credential.service_type,
        plugin_type=credential.plugin_type,
        credential_scope=credential.auth_scope or "poundcake_control_plane",
    )
