#!/usr/bin/env python3
#  ___                        _  ____      _
# |  _ \ ___  _   _ _ __   __| |/ ___|__ _| | _____
# | |_) / _ \| | | | '_ \ / _` | |   / _` | |/ / _ \
# |  __/ (_) | |_| | | | | (_| | |__| (_| |   <  __/
# |_|   \___/ \__,_|_| |_|\__,_|\____\__,_|_|\_\___|
#
"""Shared helpers for kitchen services."""

import os
import asyncio
import json as jsonlib
import re
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from api.core.http_client import request_with_retry_sync
from api.core.config import get_settings
from api.plugins.internal_services import INTERNAL_WORKER_SERVICE_TYPES
from api.services.service_identity import _read_internal_hmac_payload
from shared.internal_hmac import build_internal_hmac_headers

_INTERNAL_HMAC_SECRET_CACHE: dict[str, str] = {}


class InternalControlPlaneAuthError(RuntimeError):
    """Raised when an internal worker cannot sign control-plane requests."""


class WorkerRuntimeConfigError(RuntimeError):
    """Raised when worker runtime config cannot be loaded from the control plane."""


def _internal_hmac_service_type() -> str:
    service_type = (
        os.getenv("POUNDCAKE_INTERNAL_HMAC_SERVICE_TYPE", "").strip()
        or os.getenv("SERVICE_NAME", "").strip()
    )
    normalized = re.sub(r"[^a-z0-9_-]+", "-", service_type.lower()).strip("-")
    if normalized not in INTERNAL_WORKER_SERVICE_TYPES:
        return ""
    return normalized


def _internal_hmac_key_id() -> str:
    service_type = _internal_hmac_service_type()
    if not service_type:
        return ""
    normalized = re.sub(r"[^a-z0-9_-]+", "-", service_type.lower()).strip("-")
    return f"poundcake-control-plane:{normalized}"


def _internal_hmac_secret() -> str:
    key_id = _internal_hmac_key_id()
    if not key_id:
        return ""
    cached = _INTERNAL_HMAC_SECRET_CACHE.get(key_id)
    if cached:
        return cached
    service_type = _internal_hmac_service_type()
    if not service_type:
        return ""
    try:
        payload = asyncio.run(
            _read_internal_hmac_payload(
                service_type=service_type,
                credential_key_id=key_id,
            )
        )
    except Exception as exc:
        raise InternalControlPlaneAuthError(
            f"internal HMAC credential lookup failed for {service_type}"
        ) from exc
    if not payload:
        raise InternalControlPlaneAuthError(
            f"internal HMAC credential not found for {service_type}"
        )
    secret = str(payload.get("hmac_secret") or "").strip()
    if not secret:
        raise InternalControlPlaneAuthError(
            f"internal HMAC credential payload is missing hmac_secret for {service_type}"
        )
    _INTERNAL_HMAC_SECRET_CACHE[key_id] = secret
    return secret


def get_service_headers(
    req_id: str,
    *,
    method: str | None = None,
    url: str | None = None,
    body: bytes = b"",
) -> dict[str, str]:
    """Build shared headers for internal service-to-service API calls."""
    headers = {"X-Request-ID": req_id}
    secret = _internal_hmac_secret()
    if secret and method and url:
        headers.update(
            build_internal_hmac_headers(
                key_id=_internal_hmac_key_id(),
                secret=secret,
                method=method,
                url_or_path=url,
                body=body,
            )
        )
    return headers


def request_control_plane_sync(
    method: str,
    url: str,
    *,
    req_id: str,
    headers: dict[str, str] | None = None,
    json: dict[str, Any] | list[Any] | None = None,
    content: bytes | str | None = None,
    params: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Any:
    """Send one signed internal HTTP request to the PoundCake control plane."""
    signed_url = str(httpx.URL(url, params=params)) if params else url
    body = b""
    merged_headers = dict(headers or {})
    if json is not None:
        body = jsonlib.dumps(json, sort_keys=True, separators=(",", ":")).encode("utf-8")
        kwargs["content"] = body
        merged_headers.setdefault("Content-Type", "application/json")
    elif content is not None:
        if isinstance(content, bytes):
            body = content
        else:
            body = content.encode("utf-8")
        kwargs["content"] = body
    merged_headers.update(
        get_service_headers(
            req_id,
            method=method,
            url=signed_url,
            body=body,
        )
    )
    return request_with_retry_sync(method, signed_url, headers=merged_headers, **kwargs)


def wait_for_api(
    api_base_url: str,
    system_req_id: str,
    logger: Any,
    max_attempts: int = 60,
    delay_sec: float = 2.0,
    require_healthy: bool = True,
    retries: int | None = None,
) -> bool:
    """Wait for unauthenticated PoundCake process readiness before service loops."""
    logger.info(
        "Waiting for API to be ready",
        extra={"req_id": system_req_id, "api_url": api_base_url},
    )
    parsed_api_url = urlsplit(api_base_url)
    api_root_url = f"{parsed_api_url.scheme}://{parsed_api_url.netloc}"

    for attempt in range(1, max_attempts + 1):
        try:
            start_time = time.time()
            if retries is None:
                retries = get_settings().poller_http_retries
            resp = request_with_retry_sync(
                "GET",
                f"{api_root_url}/readyz",
                headers={"X-Request-ID": system_req_id},
                timeout=5,
                retries=retries,
            )
            latency_ms = int((time.time() - start_time) * 1000)
            if resp.status_code == 200 or (not require_healthy and resp.status_code == 503):
                logger.info(
                    "API is ready",
                    extra={
                        "req_id": system_req_id,
                        "method": "GET",
                        "status_code": resp.status_code,
                        "latency_ms": latency_ms,
                        "path": "/readyz",
                    },
                )
                return True
        except Exception:
            pass

        if attempt < max_attempts:
            time.sleep(delay_sec)

    logger.warning(
        "API did not become ready. Starting anyway...",
        extra={"req_id": system_req_id, "max_attempts": max_attempts},
    )
    return False


def get_worker_runtime_config(
    *,
    api_base_url: str,
    service_type: str,
    req_id: str,
    default_interval: int,
    default_query_limit: int | None = None,
    logger: Any,
) -> dict[str, Any]:
    """Return runtime config for an internal worker from the control plane."""
    normalized = service_type.strip().lower()
    try:
        resp = request_control_plane_sync(
            "GET",
            f"{api_base_url.rstrip('/')}/plugins/{normalized}",
            req_id=req_id,
            timeout=5,
            retries=get_settings().poller_http_retries,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"runtime config lookup failed with HTTP {resp.status_code}")
        payload = resp.json()
        if not isinstance(payload, dict):
            raise RuntimeError("runtime config lookup returned non-object payload")
        interval = payload.get("run_interval_seconds")
        if not isinstance(interval, int) or interval < 1:
            raise RuntimeError("runtime config missing positive run_interval_seconds")
        config = {
            "enabled": bool(payload.get("enabled", True)),
            "run_interval_seconds": interval,
            "source": "api",
        }
        if default_query_limit is not None:
            query_limit = payload.get("query_limit")
            if not isinstance(query_limit, int) or query_limit < 1:
                raise RuntimeError("runtime config missing positive query_limit")
            config["query_limit"] = query_limit
        return config
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Internal plugin runtime config unavailable",
            extra={"req_id": req_id, "service_type": normalized, "error": str(exc)},
        )
        raise WorkerRuntimeConfigError(
            f"runtime config lookup failed for {normalized}: {exc}"
        ) from exc
