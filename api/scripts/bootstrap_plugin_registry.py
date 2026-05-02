"""Initialize startup plugin registry metadata and bootstrap hooks."""

from __future__ import annotations

import asyncio

from api.core.database import SessionLocal, engine
from api.core.logging import get_logger, setup_logging
from api.services.plugin_bootstrap import bootstrap_plugin_registry

setup_logging()
logger = get_logger(__name__)


async def main() -> None:
    """Run startup plugin-registry initialization."""
    async with SessionLocal() as db:
        stats = await bootstrap_plugin_registry(db)
    logger.info(
        "Service plugin registry startup initialization complete",
        extra={"req_id": "SYSTEM-PLUGIN-BOOTSTRAP", "stats": stats},
    )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
