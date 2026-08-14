"""Bakery plugin credential bootstrap hook."""

from __future__ import annotations

from collections.abc import Mapping

from api.plugins.bakery.client import bootstrap_monitor_credential
from api.types import JSONObject


async def bootstrap_bakery_credentials(
    _db: object,
    _helpers: Mapping[str, object],
) -> JSONObject:
    """Register with Bakery when needed and persist the issued monitor HMAC."""
    credential = await bootstrap_monitor_credential()
    return {
        "processed": 1,
        "errors": 0,
        "bootstrap_status": "registered",
        "credential_status": "ready",
        "monitor_uuid_present": bool(credential.monitor_uuid),
        "hmac_key_id_present": bool(credential.hmac_key_id),
    }
