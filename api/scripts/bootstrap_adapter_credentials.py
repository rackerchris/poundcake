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
    bakery_key_id = os.getenv("POUNDCAKE_BAKERY_BOOTSTRAP_HMAC_KEY_ID", "").strip()
    bakery_secret = os.getenv("POUNDCAKE_BAKERY_BOOTSTRAP_HMAC_SECRET", "").strip()
    if bool(bakery_key_id) != bool(bakery_secret):
        raise RuntimeError(
            "Bakery bootstrap credential requires both "
            "POUNDCAKE_BAKERY_BOOTSTRAP_HMAC_KEY_ID and "
            "POUNDCAKE_BAKERY_BOOTSTRAP_HMAC_SECRET"
        )
    if bakery_key_id:
        await write_adapter_credential(
            service_type="bakery",
            credential_type="bakery_bootstrap_hmac",
            credential_key_id="default",
            payload={"hmac_key_id": bakery_key_id, "hmac_secret": bakery_secret},
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
