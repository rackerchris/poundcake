"""Bakery plugin credential bootstrap hook."""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from api.plugins.bakery.client import bootstrap_monitor_credential
from api.types import JSONObject


async def bootstrap_bakery_credentials(
    _db: AsyncSession,
    _helpers: Mapping[str, object],
) -> JSONObject:
    """Bootstrap Bakery adapter credentials through the token-generation service."""
    credential = await bootstrap_monitor_credential(
        db=_db,
    )
    return {
        "processed": 1,
        "errors": 0,
        "credential_status": "ready",
        "monitor_uuid_present": bool(credential.monitor_uuid),
        "hmac_key_id_present": bool(credential.hmac_key_id),
    }
