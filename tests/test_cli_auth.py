from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

import cli.client as client_module
from cli.main import cli
from cli.session import SessionStore, StoredSession


def _json_response(method: str, url: str, status_code: int, payload: object) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request(method, url),
    )


def _provider_payload(name: str = "local") -> list[dict[str, object]]:
    return [
        {
            "name": name,
            "label": "Local Superuser",
            "login_mode": "password",
            "cli_login_mode": "password",
            "browser_login": False,
            "device_login": False,
            "password_login": True,
        }
    ]


def _session_payload(
    *,
    session_id: str = "session-123",
    role: str = "operator",
    username: str = "operator",
) -> dict[str, object]:
    return {
        "session_id": session_id,
        "username": username,
        "expires_at": "2099-01-01T00:00:00+00:00",
        "provider": "local",
        "role": role,
        "display_name": username,
        "is_superuser": False,
        "permissions": ["read", "manage_recipes"],
        "token_type": "Bearer",
    }


def _me_payload(*, role: str = "operator", username: str = "operator") -> dict[str, object]:
    return {
        "username": username,
        "display_name": username,
        "provider": "local",
        "role": role,
        "principal_type": "user",
        "principal_id": 7,
        "is_superuser": False,
        "permissions": ["read", "manage_recipes"],
        "groups": ["ops"],
        "expires_at": "2099-01-01T00:00:00+00:00",
    }


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_cli_token_uses_session_cookie(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_request_with_retry_sync(**kwargs: object) -> httpx.Response:
        calls.append(dict(kwargs))
        return _json_response("GET", "http://example.test/api/v1/auth/me", 200, _me_payload())

    monkeypatch.setattr(client_module, "request_with_retry_sync", fake_request_with_retry_sync)

    result = runner.invoke(
        cli,
        ["--url", "http://example.test", "--token", "session-123", "--format", "json", "auth", "me"],
    )

    assert result.exit_code == 0
    assert len(calls) == 1
    assert calls[0]["cookies"] == {"session_token": "session-123"}
    assert calls[0]["headers"] == {}
    output = json.loads(result.output)
    assert output["role"] == "operator"


def test_cli_auto_login_with_username_and_password(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    calls: list[tuple[str, str, dict[str, object]]] = []

    def fake_request_with_retry_sync(**kwargs: object) -> httpx.Response:
        method = str(kwargs["method"])
        url = str(kwargs["url"])
        calls.append((method, url, dict(kwargs)))
        if url.endswith("/api/v1/auth/providers"):
            return _json_response(method, url, 200, _provider_payload())
        if url.endswith("/api/v1/auth/login"):
            assert kwargs["json"] == {
                "provider": "local",
                "username": "alice",
                "password": "secret",
            }
            return _json_response(method, url, 200, _session_payload(session_id="new-session", username="alice"))
        if url.endswith("/api/v1/auth/me"):
            assert kwargs["cookies"] == {"session_token": "new-session"}
            return _json_response(method, url, 200, _me_payload(username="alice"))
        raise AssertionError(f"Unexpected request {method} {url}")

    monkeypatch.setattr(client_module, "request_with_retry_sync", fake_request_with_retry_sync)

    result = runner.invoke(
        cli,
        [
            "--url",
            "http://example.test",
            "--username",
            "alice",
            "--password",
            "secret",
            "--format",
            "json",
            "auth",
            "me",
        ],
    )

    assert result.exit_code == 0
    output = json.loads(result.output)
    assert output["username"] == "alice"
    session = SessionStore().get("http://example.test")
    assert session is not None
    assert session.session_id == "new-session"
    assert [url for _, url, _ in calls] == [
        "http://example.test/api/v1/auth/providers",
        "http://example.test/api/v1/auth/login",
        "http://example.test/api/v1/auth/me",
    ]


def test_cli_retries_with_credentials_after_stale_stored_session(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    SessionStore().save(
        "http://example.test",
        StoredSession(
            session_id="stale-session",
            username="alice",
            expires_at="2099-01-01T00:00:00+00:00",
            provider="local",
            role="operator",
        ),
    )
    calls: list[tuple[str, str, dict[str, object]]] = []

    def fake_request_with_retry_sync(**kwargs: object) -> httpx.Response:
        method = str(kwargs["method"])
        url = str(kwargs["url"])
        calls.append((method, url, dict(kwargs)))
        if url.endswith("/api/v1/auth/me") and kwargs["cookies"] == {"session_token": "stale-session"}:
            return _json_response(method, url, 401, {"detail": "Valid session required"})
        if url.endswith("/api/v1/auth/providers"):
            return _json_response(method, url, 200, _provider_payload())
        if url.endswith("/api/v1/auth/login"):
            return _json_response(method, url, 200, _session_payload(session_id="fresh-session", username="alice"))
        if url.endswith("/api/v1/auth/me") and kwargs["cookies"] == {"session_token": "fresh-session"}:
            return _json_response(method, url, 200, _me_payload(username="alice"))
        raise AssertionError(f"Unexpected request {method} {url} {kwargs['cookies']}")

    monkeypatch.setattr(client_module, "request_with_retry_sync", fake_request_with_retry_sync)

    result = runner.invoke(
        cli,
        [
            "--url",
            "http://example.test",
            "--username",
            "alice",
            "--password",
            "secret",
            "--format",
            "json",
            "auth",
            "me",
        ],
    )

    assert result.exit_code == 0
    session = SessionStore().get("http://example.test")
    assert session is not None
    assert session.session_id == "fresh-session"
    assert [url for _, url, _ in calls] == [
        "http://example.test/api/v1/auth/me",
        "http://example.test/api/v1/auth/providers",
        "http://example.test/api/v1/auth/login",
        "http://example.test/api/v1/auth/me",
    ]


def test_auth_login_outputs_session_token_and_persists_session(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    def fake_request_with_retry_sync(**kwargs: object) -> httpx.Response:
        method = str(kwargs["method"])
        url = str(kwargs["url"])
        if url.endswith("/api/v1/auth/providers"):
            return _json_response(method, url, 200, _provider_payload())
        if url.endswith("/api/v1/auth/login"):
            return _json_response(method, url, 200, _session_payload(session_id="login-session"))
        raise AssertionError(f"Unexpected request {method} {url}")

    monkeypatch.setattr(client_module, "request_with_retry_sync", fake_request_with_retry_sync)

    result = runner.invoke(
        cli,
        [
            "--url",
            "http://example.test",
            "--format",
            "json",
            "auth",
            "login",
            "--provider",
            "local",
            "--username",
            "operator",
            "--password",
            "secret",
        ],
    )

    assert result.exit_code == 0
    output = json.loads(result.output)
    assert output["credential_type"] == "session_token"
    assert output["session_token"] == "login-session"
    session = SessionStore().get("http://example.test")
    assert session is not None
    assert session.session_id == "login-session"


def test_api_request_uses_session_auth_by_default(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_request_with_retry_sync(**kwargs: object) -> httpx.Response:
        calls.append(dict(kwargs))
        return _json_response(
            "GET",
            "http://example.test/api/v1/service-registry/ingredients",
            200,
            [{"id": 1, "service_type": "dummy"}],
        )

    monkeypatch.setattr(client_module, "request_with_retry_sync", fake_request_with_retry_sync)

    result = runner.invoke(
        cli,
        [
            "--url",
            "http://example.test",
            "--token",
            "session-123",
            "--format",
            "json",
            "api",
            "get",
            "/service-registry/ingredients",
        ],
    )

    assert result.exit_code == 0
    assert calls[0]["cookies"] == {"session_token": "session-123"}
    assert calls[0]["url"] == "http://example.test/api/v1/service-registry/ingredients"


def test_api_request_can_skip_session_and_pass_headers(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_request_with_retry_sync(**kwargs: object) -> httpx.Response:
        calls.append(dict(kwargs))
        return _json_response(
            "POST",
            "http://example.test/api/v1/webhook",
            202,
            {"status": "accepted"},
        )

    monkeypatch.setattr(client_module, "request_with_retry_sync", fake_request_with_retry_sync)

    result = runner.invoke(
        cli,
        [
            "--url",
            "http://example.test",
            "--format",
            "json",
            "api",
            "post",
            "/webhook",
            "--no-session",
            "--header",
            "Authorization: Bearer webhook-token",
            "--body-json",
            '{"status":"firing","alerts":[]}',
        ],
    )

    assert result.exit_code == 0
    assert calls[0]["cookies"] is None
    assert calls[0]["headers"]["Authorization"] == "Bearer webhook-token"
    assert calls[0]["url"] == "http://example.test/api/v1/webhook"
