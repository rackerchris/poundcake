#  ___                        _  ____      _
# |  _ \ ___  _   _ _ __   __| |/ ___|__ _| | _____
# | |_) / _ \| | | | '_ \ / _` | |   / _` | |/ / _ \
# |  __/ (_) | |_| | | | | (_| | |__| (_| |   <  __/
# |_|   \___/ \__,_|_| |_|\__,_|\____\__,_|_|\_\___|
#
"""Health, readiness, and liveness endpoints."""

import os
import socket
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.api.auth import require_reader
from api.core.database import get_db
from api.core.config import get_settings
from api.schemas.schemas import ComponentHealth, HealthResponse, LivenessResponse
from api.core.logging import get_logger

router = APIRouter()
settings = get_settings()
logger = get_logger(__name__)
SYSTEM_REQ_ID = "SYSTEM-HEALTH"


def _overall_status(components: dict[str, ComponentHealth]) -> str:
    """Compute overall status from component statuses."""
    unhealthy_count = sum(1 for c in components.values() if c.status == "unhealthy")
    degraded_count = sum(1 for c in components.values() if c.status == "degraded")
    if unhealthy_count > 0:
        return "unhealthy"
    if degraded_count > 0:
        return "degraded"
    return "healthy"


def _readiness_status(components: dict[str, ComponentHealth]) -> str:
    """Compute readiness from PoundCake-owned components only."""
    return _overall_status(components)


async def _build_health_response(db: AsyncSession) -> HealthResponse:
    """Build PoundCake-owned component health response."""
    components = {}

    # Check MariaDB/MySQL Database
    try:
        await db.execute(text("SELECT 1"))
        components["database"] = ComponentHealth(status="healthy", message="Connected")
    except Exception as e:
        components["database"] = ComponentHealth(status="unhealthy", message=str(e))

    overall_status = _overall_status(components)

    # Get instance ID (pod name in Kubernetes, hostname otherwise)
    instance_id = os.getenv("HOSTNAME", socket.gethostname())

    return HealthResponse(
        status=overall_status,
        version=settings.app_version,
        instance_id=instance_id,
        timestamp=datetime.now(timezone.utc),
        components=components,
    )


@router.get("/live", response_model=LivenessResponse)
async def liveness_check() -> LivenessResponse:
    """Liveness endpoint for kubelet process checks — no auth required."""
    return LivenessResponse(status="alive", version=settings.app_version)


@router.get("/ready", response_model=HealthResponse)
async def readiness_check(
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> HealthResponse:
    """Readiness endpoint for dependency availability checks — no auth required."""
    health = await _build_health_response(db)
    readiness_status = _readiness_status(health.components)
    if readiness_status == "unhealthy":
        response.status_code = 503
    health.status = readiness_status
    return health


@router.get("/health", response_model=HealthResponse)
async def health_check(
    response: Response,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_reader),
) -> HealthResponse:
    """Comprehensive diagnostic health check for all PoundCake components."""
    health = await _build_health_response(db)
    if health.status != "healthy":
        response.status_code = 503
    return health


@router.get("/health/status", response_model=HealthResponse)
async def health_status(
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_reader),
) -> HealthResponse:
    """Reader-safe health status for UI reporting surfaces."""
    return await _build_health_response(db)
