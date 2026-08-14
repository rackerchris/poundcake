"""Bakery client for PoundCake communication operations."""

from __future__ import annotations

from api.types import JSONObject

import asyncio
import hashlib
import os
import time
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from api.core.http_client import request_with_retry
from api.core.logging import get_logger
from api.plugins.bakery.contract import (
    CommunicationAcceptedResponse,
    CommunicationCloseRequest,
    CommunicationNotifyRequest,
    CommunicationOpenRequest,
    CommunicationOperationResponse,
    CommunicationResponse,
    CommunicationUpdateRequest,
)
from api.plugins.transport import is_secure_plugin_transport
from api.services.credential_manager import (
    ServicePluginCredentialError,
    mark_adapter_credential_error,
    read_adapter_credential_payload,
)
from shared.hmac import build_hmac_signing_payload, canonical_json_body, hmac_sha256_hex

logger = get_logger(__name__)

TERMINAL_OPERATION_STATUSES = {
    "succeeded",
    "success",
    "completed",
    "failed",
    "errored",
    "dead_letter",
    "canceled",
    "cancelled",
    "timeout",
    "timed_out",
}
BAKERY_CREDENTIAL_TYPE = "bakery_monitor_hmac"
BAKERY_CREDENTIAL_KEY_ID = "default"
MISSING_BAKERY_CREDENTIAL_MESSAGE = (
    "Bakery monitor HMAC credential is not configured; configure "
    "bakery_monitor_hmac/default through Credential Manager"
)


@dataclass(frozen=True, slots=True)
class BakeryClientConfig:
    """Non-secret Bakery adapter connection settings."""

    base_url: str = ""
    verify_ssl: bool = True
    timeout_seconds: int = 15
    max_retries: int = 2
    poll_interval_seconds: float = 2.0
    poll_timeout_seconds: int = 60
    plugin_id: str = ""
    environment_label: str = ""
    region: str = ""
    cluster_name: str = ""
    namespace: str = ""
    release_name: str = ""
    tags: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "BakeryClientConfig":
        return cls(
            base_url=os.getenv("POUNDCAKE_BAKERY_BASE_URL", "").strip(),
            verify_ssl=os.getenv("POUNDCAKE_BAKERY_TLS_VERIFY", "true").strip().lower()
            not in {"0", "false", "no", "off"},
            timeout_seconds=int(os.getenv("POUNDCAKE_BAKERY_REQUEST_TIMEOUT_SECONDS", "15")),
            max_retries=int(os.getenv("POUNDCAKE_BAKERY_MAX_RETRIES", "2")),
            poll_interval_seconds=float(os.getenv("POUNDCAKE_BAKERY_POLL_INTERVAL_SECONDS", "2.0")),
            poll_timeout_seconds=int(os.getenv("POUNDCAKE_BAKERY_POLL_TIMEOUT_SECONDS", "60")),
            plugin_id=os.getenv("POUNDCAKE_BAKERY_PLUGIN_ID", "poundcake/bakery-plugin").strip(),
            environment_label=os.getenv("POUNDCAKE_BAKERY_PLUGIN_ENVIRONMENT_LABEL", ""),
            region=os.getenv("POUNDCAKE_BAKERY_PLUGIN_REGION", ""),
            cluster_name=os.getenv("POUNDCAKE_BAKERY_PLUGIN_CLUSTER_NAME", ""),
            namespace=os.getenv("POUNDCAKE_BAKERY_PLUGIN_NAMESPACE", ""),
            release_name=os.getenv("POUNDCAKE_BAKERY_PLUGIN_RELEASE_NAME", ""),
            tags=tuple(
                item.strip()
                for item in os.getenv("POUNDCAKE_BAKERY_PLUGIN_TAGS", "").split(",")
                if item.strip()
            ),
        )


_bakery_config_context: ContextVar[BakeryClientConfig | None] = ContextVar(
    "bakery_client_config",
    default=None,
)


def current_bakery_config() -> BakeryClientConfig:
    return _bakery_config_context.get() or BakeryClientConfig.from_env()


def set_bakery_client_config(config: BakeryClientConfig) -> Token[BakeryClientConfig | None]:
    return _bakery_config_context.set(config)


def reset_bakery_client_config(token: Token[BakeryClientConfig | None]) -> None:
    _bakery_config_context.reset(token)


def record_bakery_request_failure(action: str, reason: str) -> None:
    logger.warning(
        "Bakery plugin request failed",
        extra={"action": action, "reason": reason},
    )


def _bakery_base_url() -> str:
    return current_bakery_config().base_url.strip()


def _bakery_plugin_id() -> str:
    return current_bakery_config().plugin_id.strip() or "poundcake/bakery-plugin"


def _bakery_request_timeout_seconds() -> int:
    return current_bakery_config().timeout_seconds


def _bakery_max_retries() -> int:
    return current_bakery_config().max_retries


def _bakery_poll_interval_seconds() -> float:
    return current_bakery_config().poll_interval_seconds


def _bakery_poll_timeout_seconds() -> int:
    return current_bakery_config().poll_timeout_seconds


def _bakery_tls_verify() -> bool | str:
    ca_bundle = os.getenv("POUNDCAKE_BAKERY_TLS_CA_BUNDLE", "").strip()
    if ca_bundle:
        return ca_bundle
    return current_bakery_config().verify_ssl


def validate_transport_config() -> str | None:
    base_url = _bakery_base_url().rstrip("/")
    if not base_url:
        return "POUNDCAKE_BAKERY_BASE_URL is required for bakery plugin"
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"}:
        return "POUNDCAKE_BAKERY_BASE_URL must use http or https"
    if not is_secure_plugin_transport(base_url):
        return (
            "POUNDCAKE_BAKERY_BASE_URL must use https, loopback HTTP, " "or in-cluster service DNS"
        )
    return None


def validate_bootstrap_config() -> str | None:
    config_error = validate_transport_config()
    return config_error


class _BakeryTicketModel(BaseModel):
    """Typed PoundCake-local alias for Bakery communication responses."""

    model_config = ConfigDict(extra="forbid")


class BakeryTicketAccepted(_BakeryTicketModel):
    ticket_id: str
    operation_id: str
    action: str
    status: str
    created_at: datetime


class BakeryTicketResource(_BakeryTicketModel):
    ticket_id: str
    provider_type: str
    provider_ticket_id: str | None = None
    state: str
    latest_error: str | None = None
    created_at: datetime
    updated_at: datetime
    data_source: str = "local_cache"
    ticket_data: JSONObject | None = None
    last_sync_operation_id: str | None = None
    last_sync_at: datetime | None = None


class BakeryTicketOperation(_BakeryTicketModel):
    operation_id: str
    ticket_id: str
    action: str
    status: str
    attempt_count: int
    max_attempts: int
    next_attempt_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    last_error: str | None = None
    provider_response: JSONObject | None = None
    created_at: datetime
    updated_at: datetime


class BakeryHealth(_BakeryTicketModel):
    status: str
    version: str | None = None
    instance_id: str | None = None
    timestamp: datetime | None = None
    components: JSONObject = Field(default_factory=dict)


class BakeryMonitorCredential(_BakeryTicketModel):
    model_config = ConfigDict(extra="ignore")

    monitor_uuid: str
    monitor_id: str
    hmac_key_id: str
    hmac_secret: str
    heartbeat_interval_sec: int | None = None
    miss_threshold: int | None = None
    route_sync_required: bool | None = None
    created_at: datetime | None = None


def _canonical_body(payload: JSONObject | None) -> str:
    return canonical_json_body(payload)


def _model_payload(model: BaseModel) -> JSONObject:
    return model.model_dump(mode="json", exclude_none=True)


async def _build_headers(method: str, path: str, payload: JSONObject | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    credential = await _ensure_monitor_credential()
    headers.update(
        _signed_headers(
            method=method,
            path=path,
            payload=payload,
            key_id=credential.hmac_key_id,
            secret=credential.hmac_secret,
            monitor_uuid=credential.monitor_uuid,
        )
    )
    return headers


def _signed_headers(
    *,
    method: str,
    path: str,
    payload: JSONObject | None,
    key_id: str,
    secret: str,
    monitor_uuid: str | None = None,
) -> dict[str, str]:
    body = _canonical_body(payload).encode("utf-8")
    timestamp = str(int(time.time()))
    signing_payload = build_hmac_signing_payload(timestamp, method, path, body)
    signature = hmac_sha256_hex(secret, signing_payload)
    headers = {
        "Authorization": f"HMAC {key_id}:{signature}",
        "X-Timestamp": timestamp,
    }
    if monitor_uuid:
        headers["X-Bakery-Monitor-UUID"] = monitor_uuid
    return headers


def build_idempotency_key(req_id: str, action: str) -> str:
    seed = f"{req_id}:{action}:{uuid.uuid4()}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _redact_key(key: str) -> str:
    if len(key) < 8:
        return "redacted"
    return f"{key[:4]}...{key[-4:]}"


async def _request(
    action: str,
    method: str,
    path: str,
    *,
    payload: JSONObject | None = None,
    idempotency_key: str | None = None,
) -> JSONObject:
    config_error = validate_transport_config()
    if config_error:
        raise RuntimeError(config_error)
    url = f"{_bakery_base_url().rstrip('/')}{path}"
    body = _canonical_body(payload)
    headers = await _build_headers(method, path, payload)
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key

    try:
        response = await request_with_retry(
            method,
            url,
            headers=headers,
            content=body.encode("utf-8") if body else None,
            timeout=_bakery_request_timeout_seconds(),
            retries=_bakery_max_retries(),
            verify=_bakery_tls_verify(),
        )
    except Exception as exc:  # noqa: BLE001
        record_bakery_request_failure(action, "transport_exception")
        logger.error(
            "Bakery request transport failure",
            extra={"action": action, "path": path, "error": str(exc)},
        )
        raise

    if response.status_code >= 400:
        reason = f"http_{response.status_code}"
        record_bakery_request_failure(action, reason)
        logger.error(
            "Bakery request failed",
            extra={
                "action": action,
                "path": path,
                "status_code": response.status_code,
                "idempotency_key": _redact_key(idempotency_key or ""),
                "response": response.text,
            },
        )
        response.raise_for_status()

    return response.json()


async def _prepare_managed_request_payload(payload: JSONObject) -> JSONObject:
    normalized = dict(payload)
    context = normalized.get("context") if isinstance(normalized.get("context"), dict) else {}
    context = dict(context)
    policy = (
        context.get("poundcake_policy") if isinstance(context.get("poundcake_policy"), dict) else {}
    )
    execution_target = str(
        context.get("execution_target")
        or context.get("provider_type")
        or policy.get("destination_target")
        or context.get("destination_target")
        or ""
    ).strip()
    if execution_target:
        context["execution_target"] = execution_target
        context["provider_type"] = execution_target
    context["source"] = "poundcake_system"
    normalized["source"] = normalized.get("source") or "poundcake"
    normalized["context"] = context
    return normalized


async def ensure_monitor_credential_configured() -> BakeryMonitorCredential:
    payload = await read_adapter_credential_payload(
        service_type="bakery",
        credential_type=BAKERY_CREDENTIAL_TYPE,
        credential_key_id=BAKERY_CREDENTIAL_KEY_ID,
    )
    if payload is None:
        await mark_adapter_credential_error(
            service_type="bakery",
            error=MISSING_BAKERY_CREDENTIAL_MESSAGE,
        )
        raise ServicePluginCredentialError(MISSING_BAKERY_CREDENTIAL_MESSAGE)
    normalized = dict(payload)
    if "hmac_key_id" not in normalized and "key_id" in normalized:
        normalized["hmac_key_id"] = normalized["key_id"]
    return BakeryMonitorCredential.model_validate(normalized)


async def bootstrap_monitor_credential(
    *,
    force: bool = False,
    db: object | None = None,
) -> BakeryMonitorCredential:
    if force:
        logger.info(
            "Bakery credential bootstrap requested; verifying configured credential instead"
        )
    return await ensure_monitor_credential_configured()


async def _ensure_monitor_credential() -> BakeryMonitorCredential:
    return await ensure_monitor_credential_configured()


async def get_health() -> BakeryHealth:
    config_error = validate_transport_config()
    if config_error:
        raise RuntimeError(config_error)
    path = "/api/v1/health"
    headers = await _build_headers("GET", path, None)
    response = await request_with_retry(
        "GET",
        f"{_bakery_base_url().rstrip('/')}{path}",
        headers=headers,
        timeout=_bakery_request_timeout_seconds(),
        retries=_bakery_max_retries(),
        verify=_bakery_tls_verify(),
    )
    if response.status_code >= 400:
        response.raise_for_status()
    return BakeryHealth.model_validate(response.json())


def _ticket_accepted_from_communication(
    payload: CommunicationAcceptedResponse,
) -> BakeryTicketAccepted:
    return BakeryTicketAccepted(
        ticket_id=payload.communication_id,
        operation_id=payload.operation_id,
        action=payload.action,
        status=payload.status,
        created_at=payload.created_at,
    )


def _ticket_resource_from_communication(payload: CommunicationResponse) -> BakeryTicketResource:
    return BakeryTicketResource(
        ticket_id=payload.communication_id,
        provider_type=payload.provider_type,
        provider_ticket_id=payload.provider_reference_id,
        state=payload.state,
        latest_error=payload.latest_error,
        created_at=payload.created_at,
        updated_at=payload.updated_at,
        data_source=payload.data_source,
        ticket_data=payload.communication_data,
        last_sync_operation_id=payload.last_sync_operation_id,
        last_sync_at=payload.last_sync_at,
    )


def _ticket_operation_from_communication(
    payload: CommunicationOperationResponse,
) -> BakeryTicketOperation:
    return BakeryTicketOperation(
        operation_id=payload.operation_id,
        ticket_id=payload.communication_id,
        action=payload.action,
        status=payload.status,
        attempt_count=payload.attempt_count,
        max_attempts=payload.max_attempts,
        next_attempt_at=payload.next_attempt_at,
        started_at=payload.started_at,
        completed_at=payload.completed_at,
        last_error=payload.last_error,
        provider_response=payload.provider_response,
        created_at=payload.created_at,
        updated_at=payload.updated_at,
    )


async def open_communication(req_id: str, payload: JSONObject) -> CommunicationAcceptedResponse:
    return await open_communication_with_key(req_id=req_id, payload=payload, idempotency_key=None)


async def open_communication_with_key(
    req_id: str, payload: JSONObject, idempotency_key: str | None
) -> CommunicationAcceptedResponse:
    request_payload = CommunicationOpenRequest.model_validate(payload)
    request_payload_dict = await _prepare_managed_request_payload(_model_payload(request_payload))
    response_payload = await _request(
        "open",
        "POST",
        "/api/v1/communications",
        payload=request_payload_dict,
        idempotency_key=idempotency_key or build_idempotency_key(req_id, "open"),
    )
    return CommunicationAcceptedResponse.model_validate(response_payload)


async def close_communication(
    req_id: str, communication_id: str, payload: JSONObject
) -> CommunicationAcceptedResponse:
    return await close_communication_with_key(
        req_id=req_id,
        communication_id=communication_id,
        payload=payload,
        idempotency_key=None,
    )


async def close_communication_with_key(
    req_id: str,
    communication_id: str,
    payload: JSONObject,
    idempotency_key: str | None,
) -> CommunicationAcceptedResponse:
    request_payload = CommunicationCloseRequest.model_validate(payload)
    request_payload_dict = await _prepare_managed_request_payload(_model_payload(request_payload))
    response_payload = await _request(
        "close",
        "POST",
        f"/api/v1/communications/{communication_id}/close",
        payload=request_payload_dict,
        idempotency_key=idempotency_key or build_idempotency_key(req_id, "close"),
    )
    return CommunicationAcceptedResponse.model_validate(response_payload)


async def update_communication(
    req_id: str, communication_id: str, payload: JSONObject
) -> CommunicationAcceptedResponse:
    return await update_communication_with_key(
        req_id=req_id,
        communication_id=communication_id,
        payload=payload,
        idempotency_key=None,
    )


async def update_communication_with_key(
    req_id: str,
    communication_id: str,
    payload: JSONObject,
    idempotency_key: str | None,
) -> CommunicationAcceptedResponse:
    request_payload = CommunicationUpdateRequest.model_validate(payload)
    request_payload_dict = await _prepare_managed_request_payload(_model_payload(request_payload))
    response_payload = await _request(
        "update",
        "PATCH",
        f"/api/v1/communications/{communication_id}",
        payload=request_payload_dict,
        idempotency_key=idempotency_key or build_idempotency_key(req_id, "update"),
    )
    return CommunicationAcceptedResponse.model_validate(response_payload)


async def notify_communication(
    req_id: str, communication_id: str, payload: JSONObject
) -> CommunicationAcceptedResponse:
    return await notify_communication_with_key(
        req_id=req_id,
        communication_id=communication_id,
        payload=payload,
        idempotency_key=None,
    )


async def notify_communication_with_key(
    req_id: str,
    communication_id: str,
    payload: JSONObject,
    idempotency_key: str | None,
) -> CommunicationAcceptedResponse:
    request_payload = CommunicationNotifyRequest.model_validate(payload)
    request_payload_dict = await _prepare_managed_request_payload(_model_payload(request_payload))
    response_payload = await _request(
        "notify",
        "POST",
        f"/api/v1/communications/{communication_id}/notifications",
        payload=request_payload_dict,
        idempotency_key=idempotency_key or build_idempotency_key(req_id, "notify"),
    )
    return CommunicationAcceptedResponse.model_validate(response_payload)


async def get_communication(communication_id: str) -> CommunicationResponse:
    response_payload = await _request(
        "get_communication",
        "GET",
        f"/api/v1/communications/{communication_id}",
    )
    return CommunicationResponse.model_validate(response_payload)


async def sync_communication(communication_id: str) -> CommunicationResponse:
    response_payload = await _request(
        "sync_communication",
        "POST",
        f"/api/v1/communications/{communication_id}/sync",
    )
    return CommunicationResponse.model_validate(response_payload)


async def get_communication_operation(operation_id: str) -> CommunicationOperationResponse:
    response_payload = await _request(
        "get_communication_operation",
        "GET",
        f"/api/v1/communications/operations/{operation_id}",
    )
    return CommunicationOperationResponse.model_validate(response_payload)


async def create_ticket(req_id: str, payload: JSONObject) -> BakeryTicketAccepted:
    return await create_ticket_with_key(req_id=req_id, payload=payload, idempotency_key=None)


async def create_ticket_with_key(
    req_id: str, payload: JSONObject, idempotency_key: str | None
) -> BakeryTicketAccepted:
    return _ticket_accepted_from_communication(
        await open_communication_with_key(
            req_id=req_id,
            payload=payload,
            idempotency_key=idempotency_key,
        )
    )


async def close_ticket(req_id: str, ticket_id: str, payload: JSONObject) -> BakeryTicketAccepted:
    return await close_ticket_with_key(
        req_id=req_id, ticket_id=ticket_id, payload=payload, idempotency_key=None
    )


async def close_ticket_with_key(
    req_id: str, ticket_id: str, payload: JSONObject, idempotency_key: str | None
) -> BakeryTicketAccepted:
    return _ticket_accepted_from_communication(
        await close_communication_with_key(
            req_id=req_id,
            communication_id=ticket_id,
            payload=payload,
            idempotency_key=idempotency_key,
        )
    )


async def update_ticket(req_id: str, ticket_id: str, payload: JSONObject) -> BakeryTicketAccepted:
    return await update_ticket_with_key(
        req_id=req_id, ticket_id=ticket_id, payload=payload, idempotency_key=None
    )


async def update_ticket_with_key(
    req_id: str, ticket_id: str, payload: JSONObject, idempotency_key: str | None
) -> BakeryTicketAccepted:
    return _ticket_accepted_from_communication(
        await update_communication_with_key(
            req_id=req_id,
            communication_id=ticket_id,
            payload=payload,
            idempotency_key=idempotency_key,
        )
    )


async def add_ticket_comment(req_id: str, ticket_id: str, comment: str) -> BakeryTicketAccepted:
    return await add_ticket_comment_with_key(
        req_id=req_id, ticket_id=ticket_id, payload={"comment": comment}, idempotency_key=None
    )


async def add_ticket_comment_with_key(
    req_id: str,
    ticket_id: str,
    payload: JSONObject,
    idempotency_key: str | None,
) -> BakeryTicketAccepted:
    return _ticket_accepted_from_communication(
        await notify_communication_with_key(
            req_id=req_id,
            communication_id=ticket_id,
            payload=payload,
            idempotency_key=idempotency_key,
        )
    )


async def get_operation(operation_id: str) -> BakeryTicketOperation:
    return _ticket_operation_from_communication(await get_communication_operation(operation_id))


async def get_ticket(ticket_id: str) -> BakeryTicketResource:
    return _ticket_resource_from_communication(await get_communication(ticket_id))


async def find_ticket(ticket_id: str) -> BakeryTicketResource:
    return _ticket_resource_from_communication(await sync_communication(ticket_id))


async def poll_operation(operation_id: str) -> BakeryTicketOperation:
    deadline = time.monotonic() + _bakery_poll_timeout_seconds()
    last_payload: BakeryTicketOperation | None = None
    while time.monotonic() < deadline:
        payload = await get_operation(operation_id)
        last_payload = payload
        if payload.status in TERMINAL_OPERATION_STATUSES:
            return payload
        await asyncio.sleep(_bakery_poll_interval_seconds())
    if last_payload is None:
        raise TimeoutError("Bakery operation polling timed out without response")
    raise TimeoutError(f"Bakery operation polling timed out in status={last_payload.status}")
