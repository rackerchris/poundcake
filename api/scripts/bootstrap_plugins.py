"""Initialize startup-only plugin metadata, credentials, and hooks."""

from __future__ import annotations

import asyncio

from api.core.database import SessionLocal, engine
from api.core.logging import get_logger, setup_logging
from api.services.plugin_bootstrap import (
    bootstrap_adapter_credentials,
    bootstrap_plugin_registry,
    bootstrap_service_identities,
    mark_plugin_bootstrap_ready,
)

setup_logging()
logger = get_logger(__name__)


async def main() -> None:
    """Run all startup bootstrap stages in one process for local convenience."""
    async with SessionLocal() as db:
        stats = {
            "plugin_registry": await bootstrap_plugin_registry(db),
            "service_identities": await bootstrap_service_identities(db),
            "adapter_credentials": await bootstrap_adapter_credentials(db),
        }
    mark_plugin_bootstrap_ready()
    logger.info(
        "Service plugin startup initialization complete",
        extra={"req_id": "SYSTEM-PLUGIN-BOOTSTRAP", "stats": stats},
    )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
