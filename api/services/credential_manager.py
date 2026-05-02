"""Credential-manager boundary for adapter/provider secret material."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import credential_manager_db_session
from api.core.time import utc_now_db

from api.models.models import AdapterCredential, ServicePlugin
from api.services.credentials import (
    CREDENTIAL_MANAGER_SERVICE_TYPE,
    INTERNAL_CONTROL_PLANE_HMAC_CREDENTIAL_TYPE,
    ServicePluginCredentialError,
    decrypt_payload,
    encrypt_payload,
)
from api.services.database_access import (
    DatabaseAccessError,
    principal_for_internal_service,
    require_database_capability,
)
from api.types import JSONObject


@dataclass(frozen=True)
class AdapterCredentialResult:
    """Decrypted adapter credential with credential-manager policy metadata."""

    payload: JSONObject
    """Decrypted credential payload (token, url, etc.)."""

    allow_public_read: bool
    """Whether the credential manager permits unauthenticated public read
    endpoints.  Default is ``false``; operators/bootstrap must explicitly
    set this when writing the credential.  Adapters must honour this flag
    and must not use public read paths when it is ``false``."""


def _require_adapter_reader(*, requester_service_type: str, target_service_type: str) -> None:
    try:
        require_database_capability(
            principal_for_internal_service(requester_service_type),
            "adapter-credential:read",
            target_service_type=target_service_type,
        )
    except DatabaseAccessError as exc:
        raise ServicePluginCredentialError(str(exc)) from exc


async def _require_credential_manager_writer(db: AsyncSession, *, credential_type: str) -> None:
    normalized_credential_type = credential_type.strip().lower()
    if normalized_credential_type == INTERNAL_CONTROL_PLANE_HMAC_CREDENTIAL_TYPE:
        raise ServicePluginCredentialError(
            "internal control-plane HMAC credentials are service-identity-owned"
        )
    try:
        require_database_capability(
            principal_for_internal_service(CREDENTIAL_MANAGER_SERVICE_TYPE),
            "adapter-credential:write",
        )
    except DatabaseAccessError as exc:
        raise ServicePluginCredentialError(str(exc)) from exc
    result = await db.execute(
        select(ServicePlugin).where(ServicePlugin.service_type == CREDENTIAL_MANAGER_SERVICE_TYPE)
    )
    writer = result.scalar_one_or_none()
    if writer is None:
        raise ServicePluginCredentialError(
            f"credential writer service is not registered: {CREDENTIAL_MANAGER_SERVICE_TYPE}"
        )
    if (
        not bool(writer.enabled)
        or str(writer.plugin_type or "").strip().lower() != "internal_plugin"
    ):
        raise ServicePluginCredentialError(
            "credential writer service is not enabled for token generation: "
            f"{CREDENTIAL_MANAGER_SERVICE_TYPE}"
        )


async def _require_credential_manager_status_writer(db: AsyncSession) -> None:
    try:
        require_database_capability(
            principal_for_internal_service(CREDENTIAL_MANAGER_SERVICE_TYPE),
            "service-plugin:update-status",
        )
    except DatabaseAccessError as exc:
        raise ServicePluginCredentialError(str(exc)) from exc
    result = await db.execute(
        select(ServicePlugin).where(ServicePlugin.service_type == CREDENTIAL_MANAGER_SERVICE_TYPE)
    )
    writer = result.scalar_one_or_none()
    if writer is None:
        raise ServicePluginCredentialError(
            f"credential status writer service is not registered: {CREDENTIAL_MANAGER_SERVICE_TYPE}"
        )
    if (
        not bool(writer.enabled)
        or str(writer.plugin_type or "").strip().lower() != "internal_plugin"
    ):
        raise ServicePluginCredentialError(
            "credential status writer service is not enabled: " f"{CREDENTIAL_MANAGER_SERVICE_TYPE}"
        )


async def read_adapter_credential(
    *,
    service_type: str,
    credential_type: str,
    credential_key_id: str = "default",
) -> AdapterCredentialResult | None:
    """Compatibility wrapper for policy-aware adapter credential reads.

    Prefer ``read_adapter_credential_payload()`` for normal secret consumers
    and ``read_adapter_credential_with_policy()`` for adapters that implement
    unauthenticated public-read fallback behavior.
    """
    return await read_adapter_credential_with_policy(
        service_type=service_type,
        credential_type=credential_type,
        credential_key_id=credential_key_id,
    )


async def read_adapter_credential_with_policy(
    *,
    service_type: str,
    credential_type: str,
    credential_key_id: str = "default",
) -> AdapterCredentialResult | None:
    """Read adapter credentials plus credential-manager policy metadata.

    Returns an :class:`AdapterCredentialResult` containing the decrypted
    payload and the credential-manager's ``allow_public_read`` policy flag.
    The credential manager is the authoritative policy gate — adapters must
    honour the ``allow_public_read`` value and must not use unauthenticated
    public read paths when it is ``false``.
    """
    normalized_service_type = service_type.strip().lower()
    _require_adapter_reader(
        requester_service_type=CREDENTIAL_MANAGER_SERVICE_TYPE,
        target_service_type=normalized_service_type,
    )
    async with credential_manager_db_session() as db:
        result = await db.execute(
            select(AdapterCredential)
            .join(ServicePlugin, ServicePlugin.id == AdapterCredential.service_plugin_id)
            .where(
                ServicePlugin.service_type == normalized_service_type,
                AdapterCredential.credential_type == credential_type,
                AdapterCredential.credential_key_id == credential_key_id,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return AdapterCredentialResult(
            payload=decrypt_payload(row.encrypted_payload),
            allow_public_read=bool(row.allow_public_read),
        )


async def read_adapter_credential_payload(
    *,
    service_type: str,
    credential_type: str,
    credential_key_id: str = "default",
) -> JSONObject | None:
    """Read only decrypted adapter secret material.

    This is the default helper for adapters that only need secret payloads and
    do not implement a separate unauthenticated public-read execution path.
    """
    result = await read_adapter_credential_with_policy(
        service_type=service_type,
        credential_type=credential_type,
        credential_key_id=credential_key_id,
    )
    return result.payload if result else None


async def write_adapter_credential(
    *,
    service_type: str,
    credential_type: str,
    credential_key_id: str,
    payload: JSONObject,
    allow_public_read: bool = False,
    rotated: bool = False,
) -> None:
    """Write an adapter credential through the credential-manager service boundary.

    The ``allow_public_read`` flag is managed by the credential manager as the
    authoritative policy gate.  When ``true``, the adapter is permitted to use
    unauthenticated public read endpoints (e.g. ``raw.githubusercontent.com``).
    Default is ``false`` — operators/bootstrap must explicitly enable it when
    public reads are intentional.
    """
    try:
        async with credential_manager_db_session() as db:
            async with db.begin():
                await _require_credential_manager_writer(db, credential_type=credential_type)
                service_result = await db.execute(
                    select(ServicePlugin).where(
                        ServicePlugin.service_type == service_type.strip().lower()
                    )
                )
                plugin = service_result.scalar_one_or_none()
                if plugin is None:
                    raise ServicePluginCredentialError(
                        f"service plugin is not registered: {service_type}"
                    )
                result = await db.execute(
                    select(AdapterCredential).where(
                        AdapterCredential.service_plugin_id == plugin.id,
                        AdapterCredential.credential_type == credential_type,
                        AdapterCredential.credential_key_id == credential_key_id,
                    )
                )
                row = result.scalar_one_or_none()
                now = utc_now_db()
                encrypted = encrypt_payload(payload)
                if row is None:
                    row = AdapterCredential(
                        service_plugin_id=plugin.id,
                        credential_type=credential_type,
                        credential_key_id=credential_key_id,
                        encrypted_payload=encrypted,
                        allow_public_read=allow_public_read,
                        created_at=now,
                        updated_at=now,
                    )
                    db.add(row)
                else:
                    row.encrypted_payload = encrypted
                    row.allow_public_read = allow_public_read
                    row.updated_at = now

                plugin.credential_status = "ready"
                plugin.credential_error = None
                plugin.last_credential_bootstrap_at = plugin.last_credential_bootstrap_at or now
                if rotated:
                    plugin.last_credential_rotation_at = now
    except RuntimeError as exc:
        raise ServicePluginCredentialError(str(exc)) from exc


async def mark_adapter_credential_error(*, service_type: str, error: str) -> None:
    """Mark adapter credential status through the credential-manager boundary."""
    try:
        async with credential_manager_db_session() as db:
            async with db.begin():
                await _require_credential_manager_status_writer(db)
                result = await db.execute(
                    select(ServicePlugin).where(
                        ServicePlugin.service_type == service_type.strip().lower()
                    )
                )
                plugin = result.scalar_one_or_none()
                if plugin is None:
                    return
                plugin.credential_status = "error"
                plugin.credential_error = error[:2000]
    except RuntimeError as exc:
        raise ServicePluginCredentialError(str(exc)) from exc


__all__ = [
    "AdapterCredentialResult",
    "ServicePluginCredentialError",
    "mark_adapter_credential_error",
    "read_adapter_credential",
    "read_adapter_credential_payload",
    "read_adapter_credential_with_policy",
    "write_adapter_credential",
]
