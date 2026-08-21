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
    MonitorHeartbeatResponse,
)
from api.plugins.transport import is_secure_plugin_transport
from api.services.credential_manager import (
    ServicePluginCredentialError,
    mark_adapter_credential_error,
    read_adapter_credential_payload,
    write_adapter_credential,
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
MONITOR_REGISTER_PATH = "/api/v1/monitors/register"
MONITOR_HEARTBEAT_PATH = "/api/v1/monitors/heartbeat"
MISSING_BAKERY_CREDENTIAL_MESSAGE = (
    "Bakery monitor HMAC credential is not configured; provide a Bakery "
    "bootstrap HMAC via POUNDCAKE_BAKERY_BOOTSTRAP_HMAC_KEY_ID and "
    "POUNDCAKE_BAKERY_BOOTSTRAP_HMAC_KEY, or configure "
    "bakery_monitor_hmac/default through Credential Manager"
)
_REGISTER_LOCK = asyncio.Lock()


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


def _bakery_account_number() -> str:
    return os.getenv("POUNDCAKE_BAKERY_ACCOUNT_NUMBER", "").strip()


def _bakery_queue() -> str:
    return os.getenv("POUNDCAKE_BAKERY_QUEUE", "").strip()


def _bakery_subcategory() -> str:
    return os.getenv("POUNDCAKE_BAKERY_SUBCATEGORY", "").strip()


def _bootstrap_hmac_key_id() -> str:
    return os.getenv("POUNDCAKE_BAKERY_BOOTSTRAP_HMAC_KEY_ID", "").strip()


def _bootstrap_hmac_key() -> str:
    return os.getenv("POUNDCAKE_BAKERY_BOOTSTRAP_HMAC_KEY", "").strip()


def _monitor_id() -> str:
    explicit = os.getenv("POUNDCAKE_BAKERY_MONITOR_ID", "").strip()
    if explicit:
        return explicit
    config = current_bakery_config()
    namespace = config.namespace.strip()
    release_name = config.release_name.strip()
    if namespace and release_name:
        return f"{namespace}/{release_name}"
    plugin_id = _bakery_plugin_id()
    if plugin_id:
        return plugin_id
    raise RuntimeError("POUNDCAKE_BAKERY_MONITOR_ID is required for Bakery registration")


def _monitor_registration_payload() -> JSONObject:
    config = current_bakery_config()
    payload: JSONObject = {"monitor_id": _monitor_id()}
    installation_id = os.getenv("POUNDCAKE_INSTANCE_ID", "").strip()
    app_version = os.getenv("POUNDCAKE_APP_VERSION", "").strip()
    optional_fields: JSONObject = {
        "installation_id": installation_id,
        "app_version": app_version,
        "environment_label": config.environment_label.strip(),
        "region": config.region.strip(),
        "cluster_name": config.cluster_name.strip(),
        "namespace": config.namespace.strip(),
        "release_name": config.release_name.strip(),
    }
    for key, value in optional_fields.items():
        if value:
            payload[key] = value
    if config.tags:
        payload["tags"] = list(config.tags)
    return payload


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
    if config_error:
        return config_error
    if _bootstrap_hmac_key_id() and _bootstrap_hmac_key():
        try:
            _monitor_id()
        except RuntimeError as exc:
            return str(exc)
        return None
    return None


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


async def _prepare_managed_request_payload(
    payload: JSONObject, *, include_source: bool = True
) -> JSONObject:
    normalized = dict(payload)
    context = normalized.get("context") if isinstance(normalized.get("context"), dict) else {}
    context = dict(context)
    policy = (
        context.get("poundcake_policy") if isinstance(context.get("poundcake_policy"), dict) else {}
    )

    # Extract route metadata from context for Bakery catalog validation
    if not policy:
        execution_target = str(
            context.get("execution_target")
            or context.get("provider_type")
            or context.get("destination_target")
            or ""
        ).strip()
        destination_target = str(
            context.get("destination_target") or execution_target or ""
        ).strip()
        if execution_target:
            policy = {
                "scope": "global",
                "owner_key": "managed:global:communications",
                "route_id": "bakery.communication.open.default",
                "label": f"Bakery - {destination_target}" if destination_target else "Bakery",
                "execution_target": execution_target,
                "destination_target": destination_target,
                "provider_config": {},
            }

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
    if include_source:
        normalized["source"] = normalized.get("source") or "poundcake"

    provider_config = (
        context.get("provider_config") if isinstance(context.get("provider_config"), dict) else {}
    )
    provider_config = dict(provider_config)
    if _bakery_account_number() and not provider_config.get("account_number"):
        provider_config["account_number"] = _bakery_account_number()
    if _bakery_queue() and not provider_config.get("queue"):
        provider_config["queue"] = _bakery_queue()
    if _bakery_subcategory() and not provider_config.get("subcategory"):
        provider_config["subcategory"] = _bakery_subcategory()
    if provider_config:
        context["provider_config"] = provider_config

    if policy:
        context["poundcake_policy"] = policy

    normalized["context"] = context
    return normalized


def _normalize_monitor_credential_payload(payload: JSONObject) -> JSONObject:
    normalized = dict(payload)
    if "hmac_key_id" not in normalized and "key_id" in normalized:
        normalized["hmac_key_id"] = normalized["key_id"]
    return normalized


async def _read_configured_monitor_credential() -> BakeryMonitorCredential | None:
    payload = await read_adapter_credential_payload(
        service_type="bakery",
        credential_type=BAKERY_CREDENTIAL_TYPE,
        credential_key_id=BAKERY_CREDENTIAL_KEY_ID,
    )
    if payload is None:
        return None
    return BakeryMonitorCredential.model_validate(_normalize_monitor_credential_payload(payload))


async def _register_monitor_with_bootstrap() -> BakeryMonitorCredential:
    config_error = validate_transport_config()
    if config_error:
        raise RuntimeError(config_error)

    key_id = _bootstrap_hmac_key_id()
    secret = _bootstrap_hmac_key()
    if not key_id or not secret:
        raise ServicePluginCredentialError(MISSING_BAKERY_CREDENTIAL_MESSAGE)

    request_payload = _monitor_registration_payload()
    body = _canonical_body(request_payload)
    headers = {
        "Content-Type": "application/json",
        **_signed_headers(
            method="POST",
            path=MONITOR_REGISTER_PATH,
            payload=request_payload,
            key_id=key_id,
            secret=secret,
        ),
    }
    url = f"{_bakery_base_url().rstrip('/')}{MONITOR_REGISTER_PATH}"
    try:
        response = await request_with_retry(
            "POST",
            url,
            headers=headers,
            content=body.encode("utf-8") if body else None,
            timeout=_bakery_request_timeout_seconds(),
            retries=_bakery_max_retries(),
            verify=_bakery_tls_verify(),
        )
    except Exception as exc:  # noqa: BLE001
        record_bakery_request_failure("register_monitor", "transport_exception")
        logger.error(
            "Bakery monitor registration transport failure",
            extra={"path": MONITOR_REGISTER_PATH, "error": str(exc)},
        )
        raise

    if response.status_code >= 400:
        record_bakery_request_failure("register_monitor", f"http_{response.status_code}")
        logger.error(
            "Bakery monitor registration failed",
            extra={
                "path": MONITOR_REGISTER_PATH,
                "status_code": response.status_code,
                "response": response.text,
            },
        )
        response.raise_for_status()

    credential = BakeryMonitorCredential.model_validate(response.json())
    await write_adapter_credential(
        service_type="bakery",
        credential_type=BAKERY_CREDENTIAL_TYPE,
        credential_key_id=BAKERY_CREDENTIAL_KEY_ID,
        payload={
            "monitor_id": credential.monitor_id,
            "monitor_uuid": credential.monitor_uuid,
            "hmac_key_id": credential.hmac_key_id,
            "hmac_secret": credential.hmac_secret,
        },
        rotated=True,
    )
    logger.info(
        "Registered PoundCake monitor with remote Bakery",
        extra={
            "monitor_id": credential.monitor_id,
            "monitor_uuid": credential.monitor_uuid,
            "hmac_key_id": credential.hmac_key_id,
        },
    )
    return credential


async def ensure_monitor_credential_configured() -> BakeryMonitorCredential:
    return await bootstrap_monitor_credential(force=False)


async def bootstrap_monitor_credential(
    *,
    force: bool = False,
    db: object | None = None,
) -> BakeryMonitorCredential:
    _ = db
    async with _REGISTER_LOCK:
        existing = await _read_configured_monitor_credential()
        if existing is not None and not force:
            return existing
        has_bootstrap = bool(_bootstrap_hmac_key_id() and _bootstrap_hmac_key())
        if existing is not None and force and not has_bootstrap:
            logger.info(
                "Bakery credential re-register requested without bootstrap HMAC; "
                "reusing configured monitor credential"
            )
            return existing
        try:
            return await _register_monitor_with_bootstrap()
        except ServicePluginCredentialError as exc:
            await mark_adapter_credential_error(
                service_type="bakery",
                error=str(exc),
            )
            raise
        except Exception as exc:  # noqa: BLE001
            await mark_adapter_credential_error(
                service_type="bakery",
                error=str(exc),
            )
            raise


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


async def send_heartbeat(payload: JSONObject) -> MonitorHeartbeatResponse:
    config_error = validate_transport_config()
    if config_error:
        raise RuntimeError(config_error)
    response_payload = await _request(
        "monitor_heartbeat",
        "POST",
        MONITOR_HEARTBEAT_PATH,
        payload=payload,
    )
    return MonitorHeartbeatResponse.model_validate(response_payload)


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
    request_payload_dict = await _prepare_managed_request_payload(
        _model_payload(request_payload), include_source=False
    )
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
