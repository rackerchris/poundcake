"""Bakery monitor liveness heartbeat.

PoundCake registers as a Bakery monitor and must check in on an interval so
Bakery can detect a downed monitor (missed checkins open a "poundcake is down"
ticket). The plugin rewrite dropped this loop; this module restores it.
"""

from __future__ import annotations

import asyncio
import os

import httpx

from api.core.config import get_settings
from api.core.logging import get_logger
from api.plugins.bakery.client import (
    BakeryClientConfig,
    bootstrap_monitor_credential,
    send_heartbeat,
    validate_transport_config,
)
from api.plugins.bakery.contract import MonitorHeartbeatResponse
from api.types import JSONObject

logger = get_logger(__name__)

MIN_HEARTBEAT_INTERVAL_SECONDS = 5
HEARTBEAT_REQ_ID = "BAKERY-MONITOR-HEARTBEAT"


def _bakery_plugin_enabled() -> bool:
    from api.plugins.catalog import get_enabled_plugins_for_bootstrap

    plugins, _failures = get_enabled_plugins_for_bootstrap()
    return any(plugin.service_type.strip().lower() == "bakery" for plugin in plugins)


def heartbeat_enabled() -> bool:
    settings = get_settings()
    if not settings.bakery_monitor_heartbeat_enabled:
        return False
    if not _bakery_plugin_enabled():
        return False
    return validate_transport_config() is None


def _heartbeat_payload() -> JSONObject:
    config = BakeryClientConfig.from_env()
    installation_id = os.getenv("POUNDCAKE_INSTANCE_ID", "").strip()
    app_version = os.getenv("POUNDCAKE_APP_VERSION", "").strip()
    payload: JSONObject = {}
    optional_fields: JSONObject = {
        "installation_id": installation_id,
        "app_version": app_version,
        "environment_label": config.environment_label.strip(),
        "region": config.region.strip(),
        "cluster_name": config.cluster_name.strip(),
        "namespace": config.namespace.strip(),
        "release_name": config.release_name.strip(),
    }
    for key, value in optional_fields.items():
        if value:
            payload[key] = value
    if config.tags:
        payload["tags"] = list(config.tags)
    details: JSONObject = {}
    if installation_id:
        details["instance_id"] = installation_id
    if details:
        payload["details"] = details
    return payload


async def heartbeat_once() -> MonitorHeartbeatResponse:
    payload = _heartbeat_payload()
    try:
        return await send_heartbeat(payload)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 401:
            raise
        logger.info(
            "Bakery monitor heartbeat unauthorized; re-registering monitor credential",
            extra={"req_id": HEARTBEAT_REQ_ID},
        )
        await bootstrap_monitor_credential(force=True)
        return await send_heartbeat(payload)


_heartbeat_task: asyncio.Task | None = None


def start_bakery_monitor_heartbeat() -> None:
    """Start the background Bakery monitor heartbeat task."""
    global _heartbeat_task
    if _heartbeat_task is not None and not _heartbeat_task.done():
        return
    if not heartbeat_enabled():
        return

    settings = get_settings()
    interval = max(
        int(settings.bakery_monitor_heartbeat_interval_seconds or 30),
        MIN_HEARTBEAT_INTERVAL_SECONDS,
    )

    async def _loop() -> None:
        nonlocal interval
        while True:
            try:
                response = await heartbeat_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Bakery monitor heartbeat failed",
                    extra={"req_id": HEARTBEAT_REQ_ID, "error": str(exc)},
                )
            else:
                response_interval = response.heartbeat_interval_sec
                if response_interval:
                    interval = max(int(response_interval), MIN_HEARTBEAT_INTERVAL_SECONDS)
            await asyncio.sleep(interval)

    async def _initial_then_loop() -> None:
        try:
            await heartbeat_once()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Initial Bakery monitor heartbeat failed",
                extra={"req_id": HEARTBEAT_REQ_ID, "error": str(exc)},
            )
        await _loop()

    _heartbeat_task = asyncio.create_task(_initial_then_loop(), name="bakery-monitor-heartbeat")
    logger.info(
        "Started Bakery monitor heartbeat",
        extra={"req_id": HEARTBEAT_REQ_ID, "interval_seconds": interval},
    )


def stop_bakery_monitor_heartbeat() -> None:
    """Stop the background Bakery monitor heartbeat task."""
    global _heartbeat_task
    if _heartbeat_task is None:
        return
    _heartbeat_task.cancel()
    _heartbeat_task = None
    logger.info("Stopped Bakery monitor heartbeat", extra={"req_id": HEARTBEAT_REQ_ID})
