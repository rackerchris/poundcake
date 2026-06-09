from __future__ import annotations

from fastapi import HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from limits import parse
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter


def _slowapi_key(request: object) -> str:
    """Resolve rate-limit key from request context.

    Tries X-Client-IP / X-Forwarded-For headers first so the limiter
    honours reverse-proxy client information in production.  Falls back to
    the raw peer address.
    """
    client = getattr(request, "client", None)
    if client:
        return get_remote_address(request)

    if isinstance(request, dict):
        forwarded = request.get("client")
        if forwarded:
            return get_remote_address(request)
        headers = request.get("headers", {})
        if isinstance(headers, dict):
            ip = headers.get("x-forwarded-for")
            if ip:
                return ip.split(",", 1)[0].strip()
        return get_remote_address(request)

    headers = getattr(request, "headers", None)
    if headers is not None:
        ip = headers.get("x-forwarded-for")
        if ip:
            return ip.split(",", 1)[0].strip()

    forwarded = getattr(request, "scope", {}).get("client") if hasattr(request, "scope") else None
    if forwarded:
        return get_remote_address(request)
    return get_remote_address(request)


# Global limiter — configured via env vars so Helm can inject
# POUNDCAKE_RATE_LIMIT_DEFAULT, POUNDCAKE_RATE_LIMIT_WEBHOOK, etc.
limiter = Limiter(
    key_func=_slowapi_key,
    default_limits=[],  # No global default; set per-endpoint
    strategy="fixed-window",
)

_internal_storage = MemoryStorage()
_internal_limiter = FixedWindowRateLimiter(_internal_storage)


def reset_internal_rate_limits() -> None:
    """Reset the in-memory service-route limiter used by internal auth paths."""
    _internal_storage.reset()


def enforce_internal_service_rate_limit(request: Request, service_type: str) -> None:
    """Apply the configured fixed-window limit to internal service-authenticated routes."""
    from api.core.config import get_settings

    route = request.scope.get("route")
    route_path = getattr(route, "path", request.url.path)
    key = f"{service_type}:{request.method.upper()}:{route_path}"
    limit = parse(get_settings().rate_limit_internal)
    if not _internal_limiter.hit(limit, key):
        raise HTTPException(
            status_code=429,
            detail="Internal service rate limit exceeded",
        )
