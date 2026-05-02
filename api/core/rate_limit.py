from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address


def _slowapi_key(request: dict) -> str:
    """Resolve rate-limit key from request context.

    Tries X-Client-IP / X-Forwarded-For headers first so the limiter
    honours reverse-proxy client information in production.  Falls back to
    the raw peer address.
    """
    forwarded = request.get("client")
    if forwarded:
        return get_remote_address(request)
    # Fallback: try HTTP headers explicitly
    ip = request.get("headers", {}).get("x-forwarded-for")
    if ip:
        # X-Forwarded-For can be comma-separated: "client, proxy1, proxy2"
        return ip.split(",", 1)[0].strip()
    return get_remote_address(request)


# Global limiter — configured via env vars so Helm can inject
# POUNDCAKE_RATE_LIMIT_DEFAULT, POUNDCAKE_RATE_LIMIT_WEBHOOK, etc.
limiter = Limiter(
    key_func=_slowapi_key,
    default_limits=[],  # No global default; set per-endpoint
    strategy="fixed-window",
)
