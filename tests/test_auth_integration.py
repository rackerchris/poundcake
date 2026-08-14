"""Auth-enabled integration tests for the global auth middleware + per-route guards."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from starlette.requests import Request

from api.api.auth import (
    require_auth_if_enabled,
    require_reader,
    require_operator,
    require_admin,
    require_service,
)
from api.core.rate_limit import reset_internal_rate_limits
from api.services.auth_service import (
    AuthContext,
    _MEMORY_STATE,
    ensure_request_authorized,
    get_session_store,
)
from api.core.config import get_settings


@pytest.fixture(autouse=True)
def _enable_auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POUNDCAKE_AUTH_ENABLED", "true")
    monkeypatch.setenv("POUNDCAKE_AUTH_USERNAME", "testadmin")
    monkeypatch.setenv("POUNDCAKE_AUTH_PASSWORD", "testpass123")
    get_settings.cache_clear()
    _MEMORY_STATE.clear()
    reset_internal_rate_limits()
    yield
    _MEMORY_STATE.clear()
    reset_internal_rate_limits()
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()
    _MEMORY_STATE.clear()
    reset_internal_rate_limits()
    yield
    _MEMORY_STATE.clear()
    reset_internal_rate_limits()
    get_settings.cache_clear()


def _request(*, method: str = "GET", path: str = "/api/v1/recipes/") -> Request:
    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    raw_headers = [
        (b"host", b"testserver"),
        (b"accept", b"application/json"),
    ]
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path.split("?", 1)[0],
            "query_string": path.split("?", 1)[1].encode("latin-1") if "?" in path else b"",
            "headers": raw_headers,
            "scheme": "http",
            "server": ("testserver", 80),
        },
        receive,
    )


def _request_with_headers(
    *,
    method: str = "GET",
    path: str = "/api/v1/recipes/",
    headers: dict[str, str] | None = None,
) -> Request:
    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    base_headers = {
        "host": "testserver",
        "accept": "application/json",
    }
    if headers:
        base_headers.update(headers)
    raw_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in base_headers.items()
    ]
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path.split("?", 1)[0],
            "query_string": path.split("?", 1)[1].encode("latin-1") if "?" in path else b"",
            "headers": raw_headers,
            "scheme": "http",
            "server": ("testserver", 80),
        },
        receive,
    )


def _mock_db() -> AsyncMock:
    db = AsyncMock()
    db.execute.return_value = MagicMock()
    db.execute.return_value.scalars.return_value.first.return_value = None
    db.execute.return_value.scalar_one_or_none.return_value = None
    return db


def _human_context(role: str) -> AuthContext:
    return AuthContext(
        provider="local",
        subject_id=f"{role}-subject",
        username=f"{role}-user",
        display_name=role.title(),
        groups=[],
        role=role,  # type: ignore[arg-type]
        principal_type="user",
        is_superuser=False,
        permissions=[],
    )


@pytest.mark.asyncio
async def test_global_auth_rejects_unauthenticated_request() -> None:
    """Verify an unauthenticated request to a protected route returns 401."""
    request = _request(method="GET", path="/api/v1/recipes/")
    db = _mock_db()

    with pytest.raises(Exception) as exc_info:
        await require_auth_if_enabled(request, session_token=None, db=db)

    assert exc_info.value.status_code == 401  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_testing_env_no_longer_bypasses_global_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TESTING", "true")
    get_settings.cache_clear()
    _MEMORY_STATE.clear()
    request = _request(method="GET", path="/api/v1/recipes/")
    db = _mock_db()

    with pytest.raises(Exception) as exc_info:
        await require_auth_if_enabled(request, session_token=None, db=db)

    assert exc_info.value.status_code == 401  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_auth_disabled_still_returns_local_admin_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POUNDCAKE_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    _MEMORY_STATE.clear()
    request = _request(method="GET", path="/api/v1/recipes/")
    db = _mock_db()

    context = await require_auth_if_enabled(request, session_token=None, db=db)

    assert context is not None
    assert context.username == "poundcake"
    assert context.role == "admin"
    assert context.is_superuser is True


@pytest.mark.asyncio
async def test_global_auth_allows_public_paths_without_auth() -> None:
    """Verify public paths bypass auth even when auth is enabled."""
    for path in (
        "/api/v1/auth/login",
        "/api/v1/auth/providers",
        "/api/v1/webhook",
        "/metrics",
        "/livez",
        "/readyz",
    ):
        request = _request(method="POST" if path == "/api/v1/webhook" else "GET", path=path)
        db = _mock_db()
        context = await require_auth_if_enabled(request, session_token=None, db=db)
        assert context is None, f"Public path {path} should return None context"


@pytest.mark.asyncio
async def test_global_auth_allows_options_preflight_without_auth() -> None:
    request = _request(method="OPTIONS", path="/api/v1/plugins/stackstorm/credentials")
    db = _mock_db()

    context = await require_auth_if_enabled(request, session_token=None, db=db)

    assert context is None


@pytest.mark.asyncio
async def test_require_reader_rejects_anonymous(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify require_reader dependency rejects an unauthenticated context with 401."""
    request = _request(path="/api/v1/recipes/status")
    db = _mock_db()

    with pytest.raises(Exception) as exc_info:
        context = await require_auth_if_enabled(request, session_token=None, db=db)
        await require_reader(context=context)
    assert exc_info.value.status_code == 401  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_require_operator_rejects_anonymous(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify require_operator dependency rejects an unauthenticated context with 401."""
    request = _request(method="POST", path="/api/v1/suppressions")
    db = _mock_db()

    with pytest.raises(Exception) as exc_info:
        context = await require_auth_if_enabled(request, session_token=None, db=db)
        await require_operator(context=context)
    assert exc_info.value.status_code == 401  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_require_admin_rejects_anonymous(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify require_admin dependency rejects an unauthenticated context with 401."""
    request = _request(method="PUT", path="/api/v1/communications/policy")
    db = _mock_db()

    with pytest.raises(Exception) as exc_info:
        context = await require_auth_if_enabled(request, session_token=None, db=db)
        await require_admin(context=context)
    assert exc_info.value.status_code == 401  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_require_service_rejects_anonymous(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify require_service dependency rejects an unauthenticated context with 401."""
    request = _request(method="POST", path="/api/v1/internal/service-registry/ingredients/bulk")
    db = _mock_db()

    with pytest.raises(Exception) as exc_info:
        context = await require_auth_if_enabled(request, session_token=None, db=db)
        await require_service(request, context=context)
    assert exc_info.value.status_code == 401  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_require_service_enforces_internal_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POUNDCAKE_RATE_LIMIT_INTERNAL", "1/minute")
    get_settings.cache_clear()
    reset_internal_rate_limits()

    request = _request(method="POST", path="/api/v1/internal/service-registry/ingredients/bulk")
    request.scope["route"] = type(
        "Route", (), {"path": "/api/v1/internal/service-registry/ingredients/bulk"}
    )()
    context = AuthContext(
        provider="service",
        subject_id="timer-subject",
        username="timer",
        display_name="Timer",
        groups=[],
        role="service",
        principal_type="service",
        permissions=[],
        service_type="timer",
    )

    allowed = await require_service(request, context=context)
    assert allowed is context

    with pytest.raises(Exception) as exc_info:
        await require_service(request, context=context)
    assert exc_info.value.status_code == 429  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_global_auth_rejects_invalid_session_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify an invalid session cookie is rejected with 401."""
    request = _request(method="GET", path="/api/v1/recipes/status")
    db = _mock_db()
    db.execute.return_value.scalar_one_or_none.return_value = None

    with pytest.raises(Exception) as exc_info:
        await require_auth_if_enabled(request, session_token="invalid-session-token", db=db)
    assert exc_info.value.status_code == 401  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_global_auth_rejects_webhook_bearer_on_non_webhook_route() -> None:
    request = _request_with_headers(
        method="GET",
        path="/api/v1/recipes/status",
        headers={"authorization": "Bearer secret-token"},
    )
    db = _mock_db()

    with pytest.raises(Exception) as exc_info:
        await require_auth_if_enabled(request, session_token=None, db=db)

    assert exc_info.value.status_code == 401  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("role", "method", "path"),
    [
        ("reader", "PUT", "/api/v1/plugins/stackstorm/credentials"),
        ("reader", "PATCH", "/api/v1/scheduled-tasks/1"),
        ("operator", "GET", "/api/v1/auth/bindings"),
        ("operator", "PUT", "/api/v1/plugins/stackstorm/credentials"),
        ("operator", "GET", "/api/v1/orders/1/execution-history"),
        ("admin", "GET", "/api/v1/orders"),
        ("admin", "POST", "/api/v1/cook/orders/1"),
    ],
)
def test_human_role_boundaries_reject_cross_boundary_access(
    role: str,
    method: str,
    path: str,
) -> None:
    with pytest.raises(Exception) as exc_info:
        ensure_request_authorized(_human_context(role), path, method)

    assert "cannot access" in str(exc_info.value)


@pytest.mark.asyncio
async def test_local_superuser_login_resolves_reader_order_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a local superuser can login and access a reader-level route."""
    from api.services.auth_service import (
        authenticate_password_provider,
        build_login_context,
        rehydrate_session_context,
    )

    store = get_session_store()
    identity = await authenticate_password_provider("local", "testadmin", "testpass123")
    assert identity is not None
    assert identity.provider == "local"

    db = _mock_db()
    db.execute.return_value.scalars.return_value.first.return_value = None
    context = await build_login_context(db, identity)
    assert context.role == "admin"

    stored = await store.create_session(context, ttl_seconds=3600)
    assert stored.session_id is not None

    rehydrated, error = await rehydrate_session_context(db, stored.session_id)
    assert rehydrated is not None
    assert error is None
    assert rehydrated.role == "admin"
    assert rehydrated.username == "testadmin"


@pytest.mark.asyncio
async def test_local_superuser_login_resolves_admin_privilege(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a local superuser login produces an admin role context."""
    from api.services.auth_service import authenticate_password_provider, build_login_context

    identity = await authenticate_password_provider("local", "testadmin", "testpass123")
    assert identity is not None

    db = _mock_db()
    db.execute.return_value.scalars.return_value.first.return_value = None
    context = await build_login_context(db, identity)
    assert context.role == "admin"
    assert context.is_superuser is True
    assert "manage_access" in context.permissions


@pytest.mark.asyncio
async def test_local_superuser_login_rejects_wrong_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a bad password for local superuser raises InvalidCredentialsError."""
    from api.services.auth_service import authenticate_password_provider, InvalidCredentialsError

    with pytest.raises(InvalidCredentialsError):
        await authenticate_password_provider("local", "testadmin", "wrongpassword")


def test_force_secure_cookie_defaults_to_true() -> None:
    """Verify force_secure_cookie defaults to True in config."""
    from api.core.config import settings

    assert settings.force_secure_cookie is True


def test_allowed_origins_env_parses_json_array(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify allowed_origins accepts a JSON array from environment config."""
    from api.core.config import get_settings
    from api.services.auth_service import _MEMORY_STATE

    monkeypatch.setenv(
        "POUNDCAKE_ALLOWED_ORIGINS",
        '["https://ui.example.com","https://poundcake.example.com"]',
    )
    get_settings.cache_clear()
    _MEMORY_STATE.clear()

    settings = get_settings()

    assert settings.allowed_origins == [
        "https://ui.example.com",
        "https://poundcake.example.com",
    ]


@pytest.mark.asyncio
async def test_set_session_cookie_respects_force_secure_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the session cookie is marked Secure when force_secure_cookie=True."""
    from api.core.config import get_settings
    from api.services.auth_service import _MEMORY_STATE
    from api.api.auth import _request_is_secure

    monkeypatch.setenv("POUNDCAKE_FORCE_SECURE_COOKIE", "true")
    monkeypatch.setenv("POUNDCAKE_AUTH_ENABLED", "true")
    get_settings.cache_clear()
    _MEMORY_STATE.clear()

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    raw_headers = [(b"host", b"testserver"), (b"accept", b"application/json")]
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/login",
            "query_string": b"",
            "headers": raw_headers,
            "scheme": "http",
            "server": ("testserver", 80),
        },
        receive,
    )
    assert _request_is_secure(request) is True

    monkeypatch.setenv("POUNDCAKE_FORCE_SECURE_COOKIE", "false")
    get_settings.cache_clear()
    _MEMORY_STATE.clear()

    request2 = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/login",
            "query_string": b"",
            "headers": raw_headers,
            "scheme": "http",
            "server": ("testserver", 80),
        },
        receive,
    )

    assert _request_is_secure(request2) is False
