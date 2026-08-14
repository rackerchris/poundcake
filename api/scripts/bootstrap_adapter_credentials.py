"""Initialize startup adapter credentials."""

from __future__ import annotations

import asyncio
import os

from api.core.database import SessionLocal, engine
from api.core.logging import get_logger, setup_logging
from api.services.plugin_bootstrap import (
    bootstrap_adapter_credentials,
    mark_plugin_bootstrap_ready,
)
from api.services.credential_manager import write_adapter_credential

setup_logging()
logger = get_logger(__name__)


async def main() -> None:
    """Run startup adapter credential initialization."""
    bakery_monitor_id = os.getenv("POUNDCAKE_BAKERY_MONITOR_ID", "").strip()
    bakery_monitor_uuid = os.getenv("POUNDCAKE_BAKERY_MONITOR_UUID", "").strip()
    bakery_key_id = os.getenv("POUNDCAKE_BAKERY_MONITOR_HMAC_KEY_ID", "").strip()
    bakery_secret = os.getenv("POUNDCAKE_BAKERY_MONITOR_HMAC_SECRET", "").strip()
    bakery_values = (bakery_monitor_id, bakery_monitor_uuid, bakery_key_id, bakery_secret)
    bakery_bootstrap_key_id = os.getenv("POUNDCAKE_BAKERY_BOOTSTRAP_HMAC_KEY_ID", "").strip()
    bakery_bootstrap_key = os.getenv("POUNDCAKE_BAKERY_BOOTSTRAP_HMAC_KEY", "").strip()
    has_bootstrap = bool(bakery_bootstrap_key_id and bakery_bootstrap_key)
    if any(bakery_values) and not all(bakery_values) and not has_bootstrap:
        raise RuntimeError(
            "Bakery monitor credential requires POUNDCAKE_BAKERY_MONITOR_ID, "
            "POUNDCAKE_BAKERY_MONITOR_UUID, POUNDCAKE_BAKERY_MONITOR_HMAC_KEY_ID, "
            "and POUNDCAKE_BAKERY_MONITOR_HMAC_SECRET, or a complete bootstrap HMAC"
        )
    if all(bakery_values):
        await write_adapter_credential(
            service_type="bakery",
            credential_type="bakery_monitor_hmac",
            credential_key_id="default",
            payload={
                "monitor_id": bakery_monitor_id,
                "monitor_uuid": bakery_monitor_uuid,
                "hmac_key_id": bakery_key_id,
                "hmac_secret": bakery_secret,
            },
        )
    async with SessionLocal() as db:
        stats = await bootstrap_adapter_credentials(db)
    mark_plugin_bootstrap_ready()
    logger.info(
        "Adapter credential startup initialization complete",
        extra={"req_id": "SYSTEM-PLUGIN-BOOTSTRAP", "stats": stats},
    )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
