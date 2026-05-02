"""Initialize startup adapter credentials."""

from __future__ import annotations

import asyncio

from api.core.database import SessionLocal, engine
from api.core.logging import get_logger, setup_logging
from api.services.plugin_bootstrap import (
    bootstrap_adapter_credentials,
    mark_plugin_bootstrap_ready,
)

setup_logging()
logger = get_logger(__name__)


async def main() -> None:
    """Run startup adapter credential initialization."""
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
