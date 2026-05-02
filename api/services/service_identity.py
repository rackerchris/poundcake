"""Service Identity Manager for internal PoundCake HMAC credentials."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import worker_reader_db_session
from api.core.time import utc_now_db
from api.models.models import ServiceIdentityCredential, ServicePlugin
from api.plugins.internal_services import INTERNAL_SERVICE_IDENTITY_VIEW_BY_SERVICE
from api.services.credentials import (
    INTERNAL_CONTROL_PLANE_HMAC_CREDENTIAL_TYPE,
    ServicePluginCredentialError,
    decrypt_service_identity_payload,
    encrypt_service_identity_payload,
)
from api.services.database_access import (
    DatabaseAccessError,
    principal_for_internal_service,
    require_database_capability,
)
from api.types import JSONObject


@dataclass(frozen=True)
class InternalHmacCredential:
    secret: str
    service_plugin_id: int
    service_type: str
    plugin_type: str
    enabled: bool
    auth_scope: str | None = None


def _require_identity_reader(*, requester_service_type: str, target_service_type: str) -> None:
    try:
        require_database_capability(
            principal_for_internal_service(requester_service_type),
            "service-identity:read-own",
            target_service_type=target_service_type,
        )
    except DatabaseAccessError as exc:
        raise ServicePluginCredentialError(str(exc)) from exc


def _require_identity_writer(*, writer_service_type: str) -> None:
    try:
        require_database_capability(
            principal_for_internal_service(writer_service_type),
            "service-identity:write",
        )
    except DatabaseAccessError as exc:
        raise ServicePluginCredentialError(str(exc)) from exc


async def upsert_internal_hmac_credential(
    db: AsyncSession,
    row: ServicePlugin,
    *,
    credential_key_id: str,
    payload: JSONObject,
) -> None:
    """Create or update one bootstrap-owned internal HMAC credential."""
    _require_identity_writer(writer_service_type="service-identity-manager")
    result = await db.execute(
        select(ServiceIdentityCredential).where(
            ServiceIdentityCredential.service_plugin_id == row.id,
            ServiceIdentityCredential.credential_type
            == INTERNAL_CONTROL_PLANE_HMAC_CREDENTIAL_TYPE,
            ServiceIdentityCredential.credential_key_id == credential_key_id,
        )
    )
    credential = result.scalar_one_or_none()
    now = utc_now_db()
    encrypted = encrypt_service_identity_payload(payload)
    if credential is None:
        credential = ServiceIdentityCredential(
            service_plugin_id=row.id,
            credential_type=INTERNAL_CONTROL_PLANE_HMAC_CREDENTIAL_TYPE,
            credential_key_id=credential_key_id,
            encrypted_payload=encrypted,
            created_at=now,
            updated_at=now,
        )
        db.add(credential)
    else:
        credential.encrypted_payload = encrypted
        credential.updated_at = now


async def _read_internal_hmac_payload(
    *,
    service_type: str,
    credential_key_id: str,
) -> JSONObject | None:
    """Read one internal HMAC payload through the service identity contract."""
    normalized_service_type = service_type.strip().lower()
    view_name = INTERNAL_SERVICE_IDENTITY_VIEW_BY_SERVICE.get(normalized_service_type)
    if view_name is None:
        raise ServicePluginCredentialError(
            f"internal HMAC service identity is not registered for worker: {service_type}"
        )
    _require_identity_reader(
        requester_service_type=normalized_service_type,
        target_service_type=normalized_service_type,
    )
    async with worker_reader_db_session(normalized_service_type) as db:
        result = await db.execute(
            text(f"""
                SELECT encrypted_payload
                FROM {view_name}
                WHERE credential_key_id = :credential_key_id
                """),
            {"credential_key_id": credential_key_id},
        )
        encrypted_payload = result.scalar_one_or_none()
        if encrypted_payload is None:
            return None
        return decrypt_service_identity_payload(str(encrypted_payload))


async def internal_hmac_credential_for_key(
    db: AsyncSession,
    key_id: str,
) -> InternalHmacCredential | None:
    """Load one internal HMAC credential and registered service identity."""
    result = await db.execute(
        select(ServiceIdentityCredential, ServicePlugin)
        .join(ServicePlugin, ServicePlugin.id == ServiceIdentityCredential.service_plugin_id)
        .where(
            ServiceIdentityCredential.credential_type
            == INTERNAL_CONTROL_PLANE_HMAC_CREDENTIAL_TYPE,
            ServiceIdentityCredential.credential_key_id == key_id,
        )
    )
    rows = result.all()
    if len(rows) != 1:
        return None
    credential, plugin = rows[0]
    if plugin is None or not bool(plugin.enabled):
        return None
    if str(plugin.plugin_type or "").strip().lower() != "internal_plugin":
        return None
    try:
        payload = decrypt_service_identity_payload(credential.encrypted_payload)
    except ServicePluginCredentialError:
        return None
    secret = str(payload.get("hmac_secret") or "").strip()
    if not secret:
        return None
    return InternalHmacCredential(
        secret=secret,
        service_plugin_id=int(plugin.id),
        service_type=str(plugin.service_type or "").strip().lower(),
        plugin_type=str(plugin.plugin_type or "").strip().lower(),
        enabled=bool(plugin.enabled),
        auth_scope=str(payload.get("auth_scope") or "").strip() or None,
    )


__all__ = [
    "InternalHmacCredential",
    "internal_hmac_credential_for_key",
    "upsert_internal_hmac_credential",
]
