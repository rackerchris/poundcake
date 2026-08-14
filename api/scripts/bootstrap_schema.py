"""Create the PoundCake database schema for a fresh deployment."""

from __future__ import annotations

import asyncio

import api.models.models  # noqa: F401
from api.core.database import Base, engine
from api.core.logging import get_logger, setup_logging
from api.plugins.internal_services import INTERNAL_SERVICE_IDENTITY_VIEW_BY_SERVICE
from sqlalchemy import text

setup_logging()
logger = get_logger(__name__)


async def main() -> None:
    """Create all SQLAlchemy-managed tables using the configured DB identity."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for service_type, view_name in INTERNAL_SERVICE_IDENTITY_VIEW_BY_SERVICE.items():
            await conn.execute(
                text(f"""
                    CREATE OR REPLACE VIEW {view_name} AS
                    SELECT
                        sic.id,
                        sic.service_plugin_id,
                        sic.credential_type,
                        sic.credential_key_id,
                        sic.encrypted_payload,
                        sic.created_at,
                        sic.updated_at
                    FROM service_identity_credentials AS sic
                    INNER JOIN service_plugins AS sp
                        ON sp.id = sic.service_plugin_id
                    WHERE sp.service_type = :service_type
                    """),
                {"service_type": service_type},
            )
    logger.info(
        "Database schema bootstrap complete",
        extra={"req_id": "SYSTEM-SCHEMA-BOOTSTRAP"},
    )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
