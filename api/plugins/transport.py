"""Shared transport contract for HTTP-backed service plugins."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from api.types import JSONObject


@dataclass(frozen=True, slots=True)
class PluginHttpTransportConfig:
    """Static runtime HTTP transport settings for a service plugin."""

    service_label: str
    base_url: str
    verify_ssl: bool = True
    username: str = ""
    password: str = ""
    bearer_token: str = ""
    timeout_seconds: float = 10.0

    @property
    def auth_mode(self) -> str:
        """Return the configured authentication mode without exposing secrets."""
        if self.bearer_token:
            return "bearer"
        if self.username or self.password:
            return "basic"
        return "none"

    @property
    def secure_transport(self) -> bool:
        """Return whether the configured URL is safe for credentialed transport."""
        return is_secure_plugin_transport(self.base_url)

    def validate_security(self) -> str | None:
        """Reject credentialed remote HTTP URLs before a plugin makes network calls."""
        if self.auth_mode != "none" and not self.secure_transport:
            return (
                f"{self.service_label} authentication requires HTTPS or an "
                "in-cluster service URL"
            )
        return None

    def request_kwargs(self) -> dict[str, Any]:
        """Return httpx kwargs for auth and TLS without leaking credentials."""
        kwargs: dict[str, Any] = {"verify": self.verify_ssl}
        if self.bearer_token:
            kwargs["headers"] = {"Authorization": f"Bearer {self.bearer_token}"}
        elif self.username or self.password:
            kwargs["auth"] = (self.username, self.password)
        return kwargs

    def safe_details(self) -> JSONObject:
        """Return inspectable metadata suitable for health/API payloads."""
        return {
            "url": self.base_url,
            "verify_ssl": self.verify_ssl,
            "auth_mode": self.auth_mode,
            "secure_transport": self.secure_transport,
        }

    def with_credentials(self, payload: JSONObject | None) -> "PluginHttpTransportConfig":
        """Return a copy with credential-manager supplied auth material."""
        if not payload:
            return PluginHttpTransportConfig(
                service_label=self.service_label,
                base_url=self.base_url,
                verify_ssl=self.verify_ssl,
                timeout_seconds=self.timeout_seconds,
            )
        bearer_token = str(
            payload.get("bearer_token")
            or payload.get("token")
            or payload.get("api_key")
            or payload.get("access_token")
            or ""
        ).strip()
        username = str(payload.get("username") or payload.get("user") or "").strip()
        password = str(payload.get("password") or "").strip()
        return PluginHttpTransportConfig(
            service_label=self.service_label,
            base_url=self.base_url,
            verify_ssl=self.verify_ssl,
            username=username if not bearer_token else "",
            password=password if not bearer_token else "",
            bearer_token=bearer_token,
            timeout_seconds=self.timeout_seconds,
        )

    def with_operator_config(self, config: JSONObject | None) -> "PluginHttpTransportConfig":
        """Return a copy with non-secret operator supplied transport settings."""
        if not config:
            return self
        normalized = normalize_http_operator_config(
            config,
            default_url=self.base_url,
            default_verify_ssl=self.verify_ssl,
            default_timeout_seconds=self.timeout_seconds,
            service_label=self.service_label,
        )
        return PluginHttpTransportConfig(
            service_label=self.service_label,
            base_url=str(normalized["url"]),
            verify_ssl=bool(normalized["verify_ssl"]),
            timeout_seconds=float(normalized["timeout_seconds"]),
        )


def is_secure_plugin_transport(url: str) -> bool:
    """Allow credentials over HTTPS, loopback, or Kubernetes in-cluster service DNS."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https":
        return True
    return host in {"localhost", "127.0.0.1", "::1"} or host.endswith(
        (".svc", ".svc.cluster.local")
    )


def http_operator_config_schema(*, service_label: str) -> JSONObject:
    return {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "title": f"{service_label} URL",
                "format": "uri",
            },
            "verify_ssl": {"type": "boolean", "title": "Verify SSL"},
            "timeout_seconds": {
                "type": "number",
                "title": "Timeout seconds",
                "minimum": 1,
                "maximum": 300,
            },
        },
        "required": ["url"],
        "additionalProperties": False,
    }


def normalize_http_operator_config(
    config: JSONObject | None,
    *,
    default_url: str,
    default_verify_ssl: bool,
    default_timeout_seconds: float,
    service_label: str,
) -> JSONObject:
    raw = dict(config or {})
    url = str(raw.get("url") or default_url or "").strip().rstrip("/")
    if not url:
        raise ValueError(f"{service_label} URL is required")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError(f"{service_label} URL must start with http:// or https://")
    try:
        timeout = float(raw.get("timeout_seconds", default_timeout_seconds))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{service_label} timeout_seconds must be a number") from exc
    if timeout < 1 or timeout > 300:
        raise ValueError(f"{service_label} timeout_seconds must be between 1 and 300")
    return {
        "url": url,
        "verify_ssl": bool(raw.get("verify_ssl", default_verify_ssl)),
        "timeout_seconds": timeout,
    }


def plugin_transport_from_env(
    *,
    service_label: str,
    env_prefix: str,
    default_url: str = "",
    default_verify_ssl: bool = True,
    default_timeout_seconds: float = 10.0,
) -> PluginHttpTransportConfig:
    """Load a standard HTTP transport config from POUNDCAKE_<PLUGIN>_* env vars."""
    prefix = env_prefix.strip().upper()
    return PluginHttpTransportConfig(
        service_label=service_label,
        base_url=os.getenv(f"{prefix}_URL", default_url).strip().rstrip("/"),
        verify_ssl=_env_bool(f"{prefix}_VERIFY_SSL", default_verify_ssl),
        username=os.getenv(f"{prefix}_USERNAME", "").strip(),
        password=os.getenv(f"{prefix}_PASSWORD", "").strip(),
        bearer_token=os.getenv(f"{prefix}_BEARER_TOKEN", "").strip(),
        timeout_seconds=_env_float(f"{prefix}_TIMEOUT_SECONDS", default_timeout_seconds),
    )


def merge_plugin_request_kwargs(
    transport: PluginHttpTransportConfig,
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Merge plugin transport auth/TLS kwargs with per-call httpx kwargs."""
    merged = dict(kwargs)
    request_kwargs = transport.request_kwargs()
    headers = dict(request_kwargs.pop("headers", {}) or {})
    headers.update(dict(merged.pop("headers", {}) or {}))
    merged.update(request_kwargs)
    if headers:
        merged["headers"] = headers
    return merged


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)
