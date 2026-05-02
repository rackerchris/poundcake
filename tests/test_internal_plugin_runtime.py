"""Tests for internal plugin worker runtime configuration."""

from __future__ import annotations

from urllib.parse import urlsplit

from kitchen import service_helpers
from shared.internal_hmac import (
    INTERNAL_HMAC_KEY_ID_HEADER,
    INTERNAL_HMAC_NONCE_HEADER,
    INTERNAL_HMAC_TIMESTAMP_HEADER,
    canonical_request_path,
    parse_internal_hmac_authorization,
)


class _Logger:
    def __init__(self) -> None:
        self.infos: list[dict[str, object]] = []
        self.warnings: list[dict[str, object]] = []

    def info(self, _message: str, *, extra: dict[str, object]) -> None:
        self.infos.append(extra)

    def warning(self, _message: str, *, extra: dict[str, object]) -> None:
        self.warnings.append(extra)


class _Response:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        return self._payload


def test_worker_runtime_config_reads_internal_plugin_api(monkeypatch) -> None:
    service_helpers._RUNTIME_CONFIG_CACHE.clear()
    service_helpers._INTERNAL_HMAC_SECRET_CACHE.clear()
    captured: dict[str, object] = {}

    def request(method: str, url: str, **kwargs: object) -> _Response:
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        return _Response(
            200,
            {
                "enabled": False,
                "run_interval_seconds": 42,
                "query_limit": 99,
            },
        )

    monkeypatch.setattr(service_helpers, "request_with_retry_sync", request)
    monkeypatch.setenv("POUNDCAKE_INTERNAL_HMAC_SERVICE_TYPE", "timer")

    async def load_credential(**_kwargs: object) -> dict[str, str]:
        return {"hmac_secret": "unit-secret"}

    monkeypatch.setattr(service_helpers, "_read_internal_hmac_payload", load_credential)

    config = service_helpers.get_worker_runtime_config(
        api_base_url="http://api:8000/api/v1",
        service_type="timer",
        req_id="unit-test",
        default_interval=10,
        default_query_limit=50,
        logger=_Logger(),
    )

    assert config == {
        "enabled": False,
        "run_interval_seconds": 42,
        "query_limit": 99,
        "source": "api",
    }
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["X-Request-ID"] == "unit-test"
    assert headers[INTERNAL_HMAC_KEY_ID_HEADER] == "poundcake-control-plane:timer"
    assert headers[INTERNAL_HMAC_TIMESTAMP_HEADER]
    assert headers[INTERNAL_HMAC_NONCE_HEADER]
    parsed = parse_internal_hmac_authorization(str(headers["Authorization"]))
    assert parsed is not None
    assert parsed[0] == "poundcake-control-plane:timer"


def test_wait_for_api_uses_public_readiness_probe(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def request(method: str, url: str, **kwargs: object) -> _Response:
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        return _Response(200, {"status": "healthy"})

    logger = _Logger()
    monkeypatch.setattr(service_helpers, "request_with_retry_sync", request)

    assert service_helpers.wait_for_api(
        "http://api:8000/api/v1",
        "SYSTEM-PREP-CHEF",
        logger,
        max_attempts=1,
    )

    assert captured["method"] == "GET"
    assert captured["url"] == "http://api:8000/readyz"
    assert captured["headers"] == {"X-Request-ID": "SYSTEM-PREP-CHEF"}
    assert logger.infos[-1]["path"] == "/readyz"


def test_control_plane_request_signs_query_string_and_body(monkeypatch) -> None:
    service_helpers._INTERNAL_HMAC_SECRET_CACHE.clear()
    captured: dict[str, object] = {}

    def request(method: str, url: str, **kwargs: object) -> _Response:
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        captured["content"] = kwargs.get("content")
        return _Response(200, {})

    monkeypatch.setattr(service_helpers, "request_with_retry_sync", request)
    monkeypatch.setenv("POUNDCAKE_INTERNAL_HMAC_SERVICE_TYPE", "timer")

    async def load_credential(**_kwargs: object) -> dict[str, str]:
        return {"hmac_secret": "unit-secret"}

    monkeypatch.setattr(service_helpers, "_read_internal_hmac_payload", load_credential)

    service_helpers.request_control_plane_sync(
        "POST",
        "http://api:8000/api/v1/internal/service-registry/ingredients/bulk",
        req_id="unit-test",
        params={"mark_bootstrap": "true"},
        json=[{"service_type": "dummy"}],
        timeout=5,
    )

    assert captured["content"] == b'[{"service_type":"dummy"}]'
    signed_url = str(captured["url"])
    assert canonical_request_path(signed_url) == (
        "/api/v1/internal/service-registry/ingredients/bulk?mark_bootstrap=true"
    )
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers[INTERNAL_HMAC_KEY_ID_HEADER] == "poundcake-control-plane:timer"
    assert headers[INTERNAL_HMAC_NONCE_HEADER]
    assert urlsplit(signed_url).query == "mark_bootstrap=true"


def test_unknown_worker_service_does_not_query_or_sign(monkeypatch) -> None:
    service_helpers._INTERNAL_HMAC_SECRET_CACHE.clear()
    captured: dict[str, object] = {}

    def request(method: str, url: str, **kwargs: object) -> _Response:
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        return _Response(200, {})

    async def load_credential(**_kwargs: object) -> dict[str, str]:
        raise AssertionError("unknown service should fail closed before credential lookup")

    monkeypatch.setattr(service_helpers, "request_with_retry_sync", request)
    monkeypatch.setattr(service_helpers, "_read_internal_hmac_payload", load_credential)
    monkeypatch.setenv("POUNDCAKE_INTERNAL_HMAC_SERVICE_TYPE", "external-plugin")

    service_helpers.request_control_plane_sync(
        "GET",
        "http://api:8000/api/v1/plugins/external-plugin",
        req_id="unit-test",
        timeout=5,
    )

    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers == {"X-Request-ID": "unit-test"}


def test_worker_runtime_config_falls_back_to_last_good_config(monkeypatch) -> None:
    service_helpers._RUNTIME_CONFIG_CACHE.clear()
    service_helpers._RUNTIME_CONFIG_CACHE["timer"] = {
        "enabled": False,
        "run_interval_seconds": 21,
        "query_limit": 33,
        "source": "api",
    }

    def request(*_args: object, **_kwargs: object) -> _Response:
        return _Response(503, {})

    logger = _Logger()
    monkeypatch.setattr(service_helpers, "request_with_retry_sync", request)

    config = service_helpers.get_worker_runtime_config(
        api_base_url="http://api:8000/api/v1",
        service_type="timer",
        req_id="unit-test",
        default_interval=10,
        default_query_limit=50,
        logger=logger,
    )

    assert config == {
        "enabled": False,
        "run_interval_seconds": 21,
        "query_limit": 33,
        "source": "api",
    }
    assert logger.warnings
