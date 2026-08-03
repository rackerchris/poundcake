"""Release update version comparison and check logic."""

from __future__ import annotations

from datetime import datetime, timezone


from api.core.config import get_settings
from api.core.logging import get_logger
from api.plugins.release.oci_client import (
    OciChartRelease,
    _client_from_settings,
    compare_versions,
)

logger = get_logger(__name__)

NOTIFIED_STATE = "notified"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def is_release_newer(
    release: OciChartRelease,
    *,
    current_app_version: str,
    current_chart_version: str,
) -> bool:
    app_comparison = compare_versions(release.app_version, current_app_version)
    if app_comparison > 0:
        return True
    if app_comparison < 0:
        return False
    if not current_chart_version:
        return False
    return compare_versions(release.chart_version, current_chart_version) > 0


async def check_once() -> dict:
    settings = get_settings()

    if not settings.release_update_enabled:
        return {
            "status": "disabled",
            "message": "Release update notifications are disabled.",
        }

    client = _client_from_settings()
    latest = await client.fetch_latest_release(
        include_prereleases=settings.release_update_include_prereleases
    )

    if latest is None:
        return {
            "status": "no_releases",
            "message": "No valid releases found in OCI registry.",
        }

    if not is_release_newer(
        latest,
        current_app_version=settings.app_version,
        current_chart_version=settings.chart_version,
    ):
        return {
            "status": "current",
            "current_app_version": settings.app_version,
            "current_chart_version": settings.chart_version,
            "available_app_version": latest.app_version,
            "available_chart_version": latest.chart_version,
            "message": "Current version is up to date.",
        }

    from api.services.adapter_runtime import process_release_update_notification

    result = await process_release_update_notification(
        oci_repository=settings.release_update_oci_repository,
        current_app_version=settings.app_version,
        current_chart_version=settings.chart_version,
        available_app_version=latest.app_version,
        available_chart_version=latest.chart_version,
        available_created_at=latest.created_at,
        req_id="SYSTEM-RELEASE-UPDATE",
    )

    logger.info(
        "Release update check completed",
        extra={
            "status": result.get("status"),
            "available_app_version": latest.app_version,
            "available_chart_version": latest.chart_version,
        },
    )

    return {
        "status": "update_available",
        "delivery_status": result.get("status"),
        "current_app_version": settings.app_version,
        "current_chart_version": settings.chart_version,
        "available_app_version": latest.app_version,
        "available_chart_version": latest.chart_version,
        "available_created_at": latest.created_at.isoformat() if latest.created_at else None,
        **result,
    }
