#  ___                        _  ____      _
# |  _ \ ___  _   _ _ __   __| |/ ___|__ _| | _____
# | |_) / _ \| | | | '_ \ / _` | |   / _` | |/ / _ \
# |  __/ (_) | |_| | | | | (_| | |__| (_| |   <  __/
# |_|   \___/ \__,_|_| |_|\__,_|\____\__,_|_|\_\___|
#
"""Main FastAPI Entrypoint for PoundCake (Helm-Ready)."""

import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, Response, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from api.core.config import settings
from api.core.rate_limit import limiter
from api.core.middleware import PreHeatMiddleware
from api.core.logging import setup_logging, get_logger
from api.core.http_client import close_async_http_client, close_sync_http_client
from api.api.health import router as health_router
from api.api.cook import router as cook_router
from api.api.expediter import router as expediter_router
from api.api.recipes import router as recipes_router
from api.api.dishes import router as dishes_router
from api.api.orders import router as orders_router
from api.api.auth import router as auth_router
from api.api.auth import require_auth_if_enabled
from api.api.settings import router as settings_router
from api.api.service_registry import router as service_registry_router
from api.api.plugins import router as plugins_router
from api.api.scheduled_tasks import router as scheduled_tasks_router
from api.api.communications_policy import router as communications_policy_router
from api.api.webhook import router as webhook_router
from api.api.observability import router as observability_router
from api.api.suppressions import router as suppressions_router
from api.api.ui_operator_actions import router as ui_operator_actions_router
from api.schemas.schemas import HealthResponse, LivenessResponse
from api.plugins.bakery.heartbeat import (
    start_bakery_monitor_heartbeat,
    stop_bakery_monitor_heartbeat,
)
from datetime import datetime, timezone
import os
import socket

# Configure logging with custom formatter that includes req_id
setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("PoundCake API is starting up", extra={"req_id": "SYSTEM-STARTUP"})
    start_bakery_monitor_heartbeat()
    yield
    stop_bakery_monitor_heartbeat()
    await close_async_http_client()
    close_sync_http_client()
    logger.info("Powering down PoundCake", extra={"req_id": "SYSTEM-SHUTDOWN"})


app = FastAPI(
    title="PoundCake API",
    version=settings.app_version,
    lifespan=lifespan,
    dependencies=[Depends(require_auth_if_enabled)],
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
    redirect_slashes=False,  # Prevent 307 redirects for trailing slashes
)

# --- Middleware Registration ---
if not settings.debug:
    # Removed HTTPSRedirectMiddleware — it interferes with HTTP readiness/liveness probes
    pass
app.add_middleware(PreHeatMiddleware)

# Inject limiter state so SlowAPI can decorate route handlers
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Kubernetes / Prometheus Internal Metrics ---
@app.get("/metrics")
@limiter.limit(settings.rate_limit_internal)
async def metrics(request: Request):
    """Scrape endpoint for Prometheus Operator / ServiceMonitor."""
    _ = request.state.req_id
    if not settings.metrics_enabled:
        raise HTTPException(status_code=404, detail="Metrics disabled")
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/livez", response_model=LivenessResponse)
async def probe_liveness_check() -> LivenessResponse:
    """Unauthenticated process liveness probe outside the PoundCake API namespace."""
    return LivenessResponse(status="alive", version=settings.app_version)


@app.get("/readyz", response_model=HealthResponse)
async def probe_readiness_check() -> HealthResponse:
    """Unauthenticated kubelet readiness probe outside the PoundCake API namespace."""
    return HealthResponse(
        status="healthy",
        version=settings.app_version,
        instance_id=os.getenv("HOSTNAME", socket.gethostname()),
        timestamp=datetime.now(timezone.utc),
        components={},
    )


# Route Registration

# System & Monitoring
app.include_router(health_router, prefix="/api/v1", tags=["system"])
app.include_router(settings_router, prefix="/api/v1", tags=["system"])
app.include_router(communications_policy_router, prefix="/api/v1", tags=["communications"])
app.include_router(observability_router, prefix="/api/v1", tags=["observability"])
app.include_router(ui_operator_actions_router, prefix="/api/v1", tags=["observability"])

# Security / Authentication
app.include_router(auth_router, prefix="/api/v1", tags=["security"])

# Infrastructure & Automation
app.include_router(cook_router, prefix="/api/v1", tags=["infrastructure"])
app.include_router(expediter_router, prefix="/api/v1", tags=["infrastructure"])
app.include_router(service_registry_router, prefix="/api/v1", tags=["infrastructure"])
app.include_router(plugins_router, prefix="/api/v1", tags=["infrastructure"])
app.include_router(scheduled_tasks_router, prefix="/api/v1", tags=["infrastructure"])

# Business Logic
app.include_router(recipes_router, prefix="/api/v1", tags=["logic"])
app.include_router(dishes_router, prefix="/api/v1", tags=["executor"])

# Alert Ingestion (webhook)
app.include_router(webhook_router, prefix="/api/v1", tags=["ingestion"])
app.include_router(orders_router, prefix="/api/v1", tags=["ingestion"])
app.include_router(suppressions_router, prefix="/api/v1", tags=["ingestion"])


# --- Exception Handlers ---


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Sanitized 422 response that hides Pydantic internals."""
    req_id = getattr(request.state, "req_id", "UNKNOWN")
    logger.warning(
        "Request validation failed", extra={"req_id": req_id, "errors": exc.errors()}, exc_info=True
    )
    return JSONResponse(
        status_code=422,
        content={
            "error": "validation_error",
            "detail": jsonable_encoder(exc.errors()),
            "req_id": req_id,
        },
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Sanitized 4xx/500 HTTP error response."""
    req_id = getattr(request.state, "req_id", "UNKNOWN")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": "request_failed",
            "detail": exc.detail,
            "req_id": req_id,
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Generic 500 handler — never leaks internal details."""
    req_id = getattr(request.state, "req_id", "UNKNOWN")
    logger.error("Unhandled exception", extra={"req_id": req_id, "error": str(exc)}, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": "an_unexpected_error_occurred",
            "req_id": req_id,
        },
    )


# Local development entrypoint
if __name__ == "__main__":
    uvicorn.run(
        "api.main:app", host=settings.server_host, port=settings.server_port, reload=settings.debug
    )
