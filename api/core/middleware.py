#  ___                        _  ____      _
# |  _ \ ___  _   _ _ __   __| |/ ___|__ _| | _____
# | |_) / _ \| | | | '_ \ / _` | |   / _` | |/ / _ \
# |  __/ (_) | |_| | | | | (_| | |__| (_| |   <  __/
# |_|   \___/ \__,_|_| |_|\__,_|\____\__,_|_|\_\___|
#
"""Middleware for request ID tracking."""

import logging
import time
import uuid
import os
from typing import Any, Callable
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from api.core.logging import get_logger

logger = get_logger(__name__)
INSTANCE_ID = os.getenv("POD_NAME") or os.getenv("HOSTNAME") or "local"
INTERNAL_QUIET_SUCCESS_LATENCY_MS = 1000
PROBE_PATHS = frozenset(
    {
        "/livez",
        "/readyz",
    }
)
INTERNAL_QUIET_PATH_PREFIXES = (
    "/api/v1/cook/",
    "/api/v1/dish-ingredients/",
    "/api/v1/dishes/",
    "/api/v1/expediter/",
    "/api/v1/plugins/",
    "/api/v1/scheduled-tasks",
    "/api/v1/service-registry",
    "/api/v1/recipes/by-name/",
    "/api/v1/communications/policy",
)


def _is_internal_req_id(req_id: str) -> bool:
    return req_id.startswith("SYSTEM-")


def _is_internal_control_plane_path(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in INTERNAL_QUIET_PATH_PREFIXES)


def _is_probe_path(path: str) -> bool:
    return path in PROBE_PATHS


def request_completion_log_level(
    *,
    req_id: str,
    path: str,
    status_code: int,
    latency_ms: int,
) -> int:
    """Return the log level for request completion access logs."""
    if _is_probe_path(path) and status_code < 400:
        return logging.DEBUG
    if (
        (_is_internal_req_id(req_id) or _is_internal_control_plane_path(path))
        and status_code < 400
        and latency_ms < INTERNAL_QUIET_SUCCESS_LATENCY_MS
    ):
        return logging.DEBUG
    return logging.INFO


def request_auth_log_context(auth_context: Any) -> dict[str, object]:
    """Return safe authenticated-principal fields for request completion logs."""
    if auth_context is None:
        return {}

    return {
        "auth_principal_type": getattr(auth_context, "principal_type", None),
        "auth_username": getattr(auth_context, "username", None),
        "auth_role": getattr(auth_context, "role", None),
        "auth_service_type": getattr(auth_context, "service_type", None),
        "auth_principal_id": getattr(auth_context, "principal_id", None),
    }


class PreHeatMiddleware(BaseHTTPMiddleware):
    """Pre-heat middleware to inject req_id for ALL HTTP verbs.

    This middleware performs the "pre_heat" function by:
    - Generating a unique req_id (UUID) for every incoming request
    - Injecting it into the request state for use in route handlers
    - Adding it to response headers for client tracking
    - Logging request/response timing
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request and inject req_id."""

        # Pre-heat: Generate or extract req_id
        req_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        instance_id = request.headers.get("X-Instance-ID", INSTANCE_ID)

        # Inject req_id into request state for access in route handlers
        request.state.req_id = req_id
        request.state.instance_id = instance_id

        # Track processing time
        start_time = time.time()

        # Process request
        response = await call_next(request)

        # Calculate processing time
        latency_ms = int((time.time() - start_time) * 1000)

        # Add req_id and timing to response headers
        response.headers["X-Request-ID"] = req_id
        response.headers["X-Latency-Ms"] = str(latency_ms)
        response.headers["X-Instance-ID"] = instance_id

        # Log request
        log_level = request_completion_log_level(
            req_id=req_id,
            path=str(request.url.path),
            status_code=response.status_code,
            latency_ms=latency_ms,
        )
        log_context = {
            "req_id": req_id,
            "instance_id": instance_id,
            "method": request.method,
            "path": str(request.url.path),
            "status_code": response.status_code,
            "latency_ms": latency_ms,
        }
        log_context.update(request_auth_log_context(getattr(request.state, "auth_context", None)))
        logger.log(
            log_level,
            "Request completed",
            extra=log_context,
        )

        return response


def get_req_id(request: Request) -> str:
    """Get req_id from request state (injected by pre_heat middleware)."""
    return getattr(request.state, "req_id", str(uuid.uuid4()))
