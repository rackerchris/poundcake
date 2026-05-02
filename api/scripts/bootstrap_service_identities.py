"""Initialize startup internal service identities."""

from __future__ import annotations

import asyncio

from api.core.database import SessionLocal, engine
from api.core.logging import get_logger, setup_logging
from api.services.plugin_bootstrap import bootstrap_service_identities

setup_logging()
logger = get_logger(__name__)


async def main() -> None:
    """Run startup service-identity initialization."""
    async with SessionLocal() as db:
        stats = await bootstrap_service_identities(db)
    logger.info(
        "Service identity startup initialization complete",
        extra={"req_id": "SYSTEM-PLUGIN-BOOTSTRAP", "stats": stats},
    )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
