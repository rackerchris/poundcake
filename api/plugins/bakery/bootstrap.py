"""Bakery plugin credential bootstrap hook."""

from __future__ import annotations

from collections.abc import Mapping

from api.plugins.bakery.client import ensure_monitor_credential_configured
from api.types import JSONObject


async def bootstrap_bakery_credentials(
    _db: object,
    _helpers: Mapping[str, object],
) -> JSONObject:
    """Verify the operator-provisioned Bakery monitor credential."""
    credential = await ensure_monitor_credential_configured()
    return {
        "processed": 1,
        "errors": 0,
        "bootstrap_status": "not_required",
        "credential_status": "ready",
        "monitor_uuid_present": bool(credential.monitor_uuid),
        "hmac_key_id_present": bool(credential.hmac_key_id),
    }
