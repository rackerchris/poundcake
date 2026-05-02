"""Bakery execution adapter using PoundCake Bakery client."""

from __future__ import annotations

from api.types import JSONObject

import asyncio
import hashlib
import time
from typing import Any
from uuid import uuid4

from api.core.logging import get_logger
from api.services.communications import normalize_communication_operation
from api.plugins.bakery.client import (
    BAKERY_BOOTSTRAP_CREDENTIAL_TYPE,
    BAKERY_CREDENTIAL_TYPE,
    BakeryClientConfig,
    BakeryTicketAccepted,
    add_ticket_comment_with_key,
    bootstrap_monitor_credential,
    close_ticket_with_key,
    create_ticket_with_key,
    current_bakery_config,
    get_health,
    poll_operation,
    reset_bakery_client_config,
    set_bakery_client_config,
    update_ticket_with_key,
    validate_transport_config,
)
from api.plugins.base import ExecutionAdapter
from api.services.credential_manager import ServicePluginCredentialError
from api.plugins.state import (
    PLUGIN_CALLABLE_RUN_STATES,
    PLUGIN_RUN_STATE_DEGRADED,
    PLUGIN_RUN_STATE_FAILED,
    PLUGIN_RUN_STATE_INITIALIZING,
    normalize_plugin_run_state,
)
from api.plugins.types import (
    ExecutionContext,
    ExecutionResult,
    PluginBootstrapResult,
    PluginHealthResult,
)

logger = get_logger(__name__)

TICKET_CAPABLE_TARGETS = {"rackspace_core"}
CANONICAL_TO_BAKERY_ACTION = {
    "open": "create",
    "notify": "comment",
    "update": "update",
    "close": "close",
    "ticket_create": "create",
    "ticket_comment": "comment",
    "ticket_update": "update",
    "ticket_close": "close",
}


def _bakery_action(value: str) -> str:
    normalized = normalize_communication_operation(value)
    return CANONICAL_TO_BAKERY_ACTION.get(normalized, normalized)


def _is_ticket_capable_target(value: str | None) -> bool:
    return (value or "").strip().lower() in TICKET_CAPABLE_TARGETS


def _execution_target(ctx: ExecutionContext) -> str:
    target = (
        str(
            ctx.context.get("destination_target")
            or (ctx.service_payload or {}).get("destination_target")
            or (ctx.service_payload or {}).get("provider_type")
            or ""
        )
        .strip()
        .lower()
    )
    if target:
        return target
    return str(ctx.service_exec or "").strip().lower()


def _payload_with_dish_evidence(payload: JSONObject, ctx: ExecutionContext) -> JSONObject:
    dish_context = ctx.context.get("dish")
    if not isinstance(dish_context, dict):
        return dict(payload)
    evidence = dish_context.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return dict(payload)

    enriched = dict(payload)
    payload_context = enriched.get("context")
    payload_context = dict(payload_context) if isinstance(payload_context, dict) else {}
    payload_context.setdefault("evidence", evidence)

    context_updates = dish_context.get("context_updates")
    if isinstance(context_updates, dict) and context_updates:
        payload_context.setdefault("execution_context", context_updates)

    enriched["context"] = payload_context
    return enriched


def _validate_bakery_payload(
    *,
    service_exec: str,
    payload: JSONObject,
    service_exec_parameters: JSONObject | None = None,
    ticket_id: str | None = None,
) -> str | None:
    target = (service_exec or "").strip().lower()
    params = service_exec_parameters if isinstance(service_exec_parameters, dict) else {}
    operation = _bakery_action(str(params.get("operation") or target))

    if operation not in {"create", "comment", "update", "close"}:
        return (
            "Bakery plugin operation must be one of: "
            "open, notify, update, close, ticket_create, ticket_update, "
            "ticket_comment, ticket_close"
        )
    if operation == "create":
        if _is_ticket_capable_target(target):
            if not isinstance(payload.get("title"), str) or not payload.get("title"):
                return "Bakery create requires payload.title"
            if not isinstance(payload.get("description"), str) or not payload.get("description"):
                return "Bakery create requires payload.description"
        elif not any(
            isinstance(payload.get(key), str) and str(payload.get(key)).strip()
            for key in ("message", "comment", "description", "title")
        ):
            return "Bakery create requires a message-style payload"
    if operation in {"comment", "update", "close"} and not ticket_id:
        return "Bakery operation requires a ticket_id or context ticket_id"
    return None


def _deterministic_idempotency_key(
    *,
    order_id: int | None,
    recipe_ingredient_id: int | None,
    action: str,
) -> str | None:
    if order_id is None or recipe_ingredient_id is None:
        return None
    seed = f"resolve:{order_id}:{recipe_ingredient_id}:{action}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _coerce_optional_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _safe_error_message(exc: Exception) -> str:
    message = str(exc)
    for marker in ("Authorization", "HMAC", "hmac_secret", "signature", "encrypted_payload"):
        message = message.replace(marker, "redacted")
    return message[:500]


def _bool_setting(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _number_setting(
    value: Any,
    *,
    name: str,
    minimum: float,
    maximum: float,
) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Bakery {name} must be a number") from exc
    if normalized < minimum or normalized > maximum:
        raise ValueError(f"Bakery {name} must be between {minimum:g} and {maximum:g}")
    return normalized


def _tag_string(value: Any) -> str:
    if isinstance(value, list | tuple):
        return ",".join(str(item).strip() for item in value if str(item).strip())
    return ",".join(item.strip() for item in str(value or "").split(",") if item.strip())


class BakeryExecutionAdapter(ExecutionAdapter):
    service_type = "bakery"

    def __init__(self, config: BakeryClientConfig | None = None) -> None:
        self.config = config or current_bakery_config()

    def _activate_config(self):
        return set_bakery_client_config(self.config)

    def operator_config_schema(self) -> JSONObject:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "title": "Bakery URL",
                    "format": "uri",
                },
                "verify_ssl": {"type": "boolean", "title": "Verify SSL"},
                "timeout_seconds": {
                    "type": "number",
                    "title": "Timeout seconds",
                    "minimum": 1,
                    "maximum": 300,
                },
                "max_retries": {
                    "type": "number",
                    "title": "Max retries",
                    "minimum": 0,
                    "maximum": 10,
                },
                "poll_interval_seconds": {
                    "type": "number",
                    "title": "Poll interval seconds",
                    "minimum": 0.1,
                    "maximum": 60,
                },
                "poll_timeout_seconds": {
                    "type": "number",
                    "title": "Poll timeout seconds",
                    "minimum": 1,
                    "maximum": 3600,
                },
                "allow_insecure_http": {
                    "type": "boolean",
                    "title": "Allow insecure HTTP",
                },
                "plugin_id": {"type": "string", "title": "Plugin ID"},
                "environment_label": {"type": "string", "title": "Environment label"},
                "region": {"type": "string", "title": "Region"},
                "cluster_name": {"type": "string", "title": "Cluster name"},
                "namespace": {"type": "string", "title": "Namespace"},
                "release_name": {"type": "string", "title": "Release name"},
                "tags": {"type": "string", "title": "Tags"},
            },
            "required": ["url"],
            "additionalProperties": False,
        }

    def default_operator_config(self) -> JSONObject:
        return {
            "url": self.config.base_url,
            "verify_ssl": self.config.verify_ssl,
            "timeout_seconds": self.config.timeout_seconds,
            "max_retries": self.config.max_retries,
            "poll_interval_seconds": self.config.poll_interval_seconds,
            "poll_timeout_seconds": self.config.poll_timeout_seconds,
            "allow_insecure_http": self.config.allow_insecure_http,
            "plugin_id": self.config.plugin_id,
            "environment_label": self.config.environment_label,
            "region": self.config.region,
            "cluster_name": self.config.cluster_name,
            "namespace": self.config.namespace,
            "release_name": self.config.release_name,
            "tags": ",".join(self.config.tags),
        }

    def normalize_operator_config(self, config: JSONObject | None) -> JSONObject:
        raw = dict(config or {})
        defaults = self.default_operator_config()
        url = str(raw.get("url") or defaults["url"] or "").strip().rstrip("/")
        if not url:
            raise ValueError("Bakery URL is required")
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ValueError("Bakery URL must start with http:// or https://")
        timeout_seconds = _number_setting(
            raw.get("timeout_seconds", defaults["timeout_seconds"]),
            name="timeout_seconds",
            minimum=1,
            maximum=300,
        )
        max_retries = _number_setting(
            raw.get("max_retries", defaults["max_retries"]),
            name="max_retries",
            minimum=0,
            maximum=10,
        )
        poll_interval_seconds = _number_setting(
            raw.get("poll_interval_seconds", defaults["poll_interval_seconds"]),
            name="poll_interval_seconds",
            minimum=0.1,
            maximum=60,
        )
        poll_timeout_seconds = _number_setting(
            raw.get("poll_timeout_seconds", defaults["poll_timeout_seconds"]),
            name="poll_timeout_seconds",
            minimum=1,
            maximum=3600,
        )
        return {
            "url": url,
            "verify_ssl": _bool_setting(raw.get("verify_ssl", defaults["verify_ssl"])),
            "timeout_seconds": int(timeout_seconds),
            "max_retries": int(max_retries),
            "poll_interval_seconds": poll_interval_seconds,
            "poll_timeout_seconds": int(poll_timeout_seconds),
            "allow_insecure_http": _bool_setting(
                raw.get("allow_insecure_http", defaults["allow_insecure_http"])
            ),
            "plugin_id": str(raw.get("plugin_id") or defaults["plugin_id"] or "").strip(),
            "environment_label": str(
                raw.get("environment_label") or defaults["environment_label"] or ""
            ).strip(),
            "region": str(raw.get("region") or defaults["region"] or "").strip(),
            "cluster_name": str(raw.get("cluster_name") or defaults["cluster_name"] or "").strip(),
            "namespace": str(raw.get("namespace") or defaults["namespace"] or "").strip(),
            "release_name": str(raw.get("release_name") or defaults["release_name"] or "").strip(),
            "tags": _tag_string(raw.get("tags", defaults["tags"])),
        }

    def with_operator_config(self, config: JSONObject | None) -> "BakeryExecutionAdapter":
        normalized = self.normalize_operator_config(config)
        return BakeryExecutionAdapter(
            BakeryClientConfig(
                base_url=str(normalized["url"]),
                verify_ssl=bool(normalized["verify_ssl"]),
                timeout_seconds=int(normalized["timeout_seconds"]),
                max_retries=int(normalized["max_retries"]),
                poll_interval_seconds=float(normalized["poll_interval_seconds"]),
                poll_timeout_seconds=int(normalized["poll_timeout_seconds"]),
                allow_insecure_http=bool(normalized["allow_insecure_http"]),
                plugin_id=str(normalized["plugin_id"]),
                environment_label=str(normalized["environment_label"]),
                region=str(normalized["region"]),
                cluster_name=str(normalized["cluster_name"]),
                namespace=str(normalized["namespace"]),
                release_name=str(normalized["release_name"]),
                tags=tuple(item for item in str(normalized["tags"]).split(",") if item),
            )
        )

    @staticmethod
    def _payload_comment(payload: JSONObject) -> str:
        for key in ("comment", "message", "description", "title"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return "PoundCake updated an existing communication route."

    @staticmethod
    def _reopen_payload(target: str) -> JSONObject:
        if target == "rackspace_core":
            return {"context": {"attributes": {"status": "New"}}}
        return {"state": "open"}

    def validate(self, ctx: ExecutionContext) -> str | None:
        token = self._activate_config()
        try:
            service_exec = (ctx.service_exec or "").strip().lower()
            if service_exec == "health_check":
                return validate_transport_config()
            if service_exec != "communication":
                return f"Unsupported bakery service_exec: {ctx.service_exec}"
            config_error = validate_transport_config()
            if config_error:
                return config_error
            payload = ctx.service_payload if isinstance(ctx.service_payload, dict) else {}
            parameters = (
                ctx.service_exec_parameters if isinstance(ctx.service_exec_parameters, dict) else {}
            )
            ticket_id = str(
                payload.get("ticket_id")
                or ctx.context.get("ticket_id")
                or ctx.context.get("bakery_comms_id")
                or ""
            ).strip()
            return _validate_bakery_payload(
                service_exec=_execution_target(ctx),
                payload=payload,
                service_exec_parameters=parameters,
                ticket_id=ticket_id or None,
            )
        finally:
            reset_bakery_client_config(token)

    def credential_requirements(self) -> list[JSONObject]:
        return [
            {
                "credential_type": BAKERY_BOOTSTRAP_CREDENTIAL_TYPE,
                "credential_key_id": "default",
                "required": True,
                "usage": "Bootstrap HMAC credential used to register this PoundCake monitor with Bakery.",
                "credential_schema": {
                    "type": "object",
                    "properties": {
                        "hmac_key_id": {"type": "string", "title": "Bootstrap HMAC key ID"},
                        "hmac_secret": {"type": "string", "title": "Bootstrap HMAC secret"},
                    },
                    "required": ["hmac_key_id", "hmac_secret"],
                    "additionalProperties": False,
                },
            },
            {
                "credential_type": BAKERY_CREDENTIAL_TYPE,
                "credential_key_id": "default",
                "required": False,
                "managed": True,
                "usage": (
                    "Adapter-managed Bakery monitor HMAC credential returned by "
                    "remote Bakery registration."
                ),
                "credential_schema": {
                    "type": "object",
                    "properties": {
                        "monitor_uuid": {"type": "string", "title": "Monitor UUID"},
                        "monitor_id": {"type": "string", "title": "Monitor ID"},
                        "hmac_key_id": {"type": "string", "title": "HMAC key ID"},
                        "hmac_secret": {"type": "string", "title": "HMAC secret"},
                    },
                    "required": ["monitor_uuid", "monitor_id", "hmac_key_id", "hmac_secret"],
                    "additionalProperties": False,
                },
            },
        ]

    def validate_credential_payload(self, credential_type: str, payload: JSONObject) -> str | None:
        if credential_type == BAKERY_BOOTSTRAP_CREDENTIAL_TYPE:
            hmac_key_id = str(payload.get("hmac_key_id") or payload.get("key_id") or "").strip()
            hmac_secret = str(payload.get("hmac_secret") or "").strip()
            if hmac_key_id and hmac_secret:
                return None
            return "Bakery bootstrap credential requires hmac_key_id and hmac_secret"
        if credential_type != BAKERY_CREDENTIAL_TYPE:
            return "Unsupported Bakery credential type"
        monitor_uuid = str(payload.get("monitor_uuid") or "").strip()
        monitor_id = str(payload.get("monitor_id") or "").strip()
        hmac_key_id = str(payload.get("hmac_key_id") or "").strip()
        hmac_secret = str(payload.get("hmac_secret") or "").strip()
        if monitor_uuid and monitor_id and hmac_key_id and hmac_secret:
            return None
        return "Bakery credential requires monitor_uuid, monitor_id, hmac_key_id, and hmac_secret"

    async def bootstrap_credentials(
        self,
        *,
        force: bool = False,
    ) -> None:
        token = self._activate_config()
        try:
            await bootstrap_monitor_credential(
                force=force,
            )
        finally:
            reset_bakery_client_config(token)

    async def bootstrap_plugin(
        self,
        ctx: ExecutionContext,
        *,
        force: bool = False,
    ) -> PluginBootstrapResult:
        token = self._activate_config()
        try:
            credential = await bootstrap_monitor_credential(
                force=force,
            )
            return PluginBootstrapResult(
                service_type=self.service_type,
                status="ready",
                message="Bakery plugin bootstrap complete",
                details={
                    "bootstrap_status": "ready",
                    "credential_status": "ready",
                    "monitor_uuid_present": bool(credential.monitor_uuid),
                    "hmac_key_id_present": bool(credential.hmac_key_id),
                    "request_service_exec": ctx.service_exec,
                },
            )
        except ServicePluginCredentialError as exc:
            return PluginBootstrapResult(
                service_type=self.service_type,
                status="failed",
                message="Bakery plugin bootstrap configuration is invalid",
                error_code=exc.__class__.__name__,
                details={
                    "bootstrap_status": "failed",
                    "credential_status": "error",
                    "error": _safe_error_message(exc),
                },
            )
        except Exception as exc:  # noqa: BLE001
            return PluginBootstrapResult(
                service_type=self.service_type,
                status="initializing",
                message="Bakery plugin bootstrap is still initializing",
                error_code=exc.__class__.__name__,
                details={
                    "bootstrap_status": "initializing",
                    "credential_status": "pending",
                    "error": _safe_error_message(exc),
                },
            )
        finally:
            reset_bakery_client_config(token)

    async def _execute_health_check(
        self,
        ctx: ExecutionContext,
        *,
        service_exec_id: str | None = None,
    ) -> ExecutionResult:
        token = self._activate_config()
        start = time.time()
        try:
            receipt = service_exec_id or f"bakery:health_check:{uuid4()}"
            bootstrap = await self.bootstrap_plugin(ctx)
            bootstrap_details = bootstrap.model_dump(mode="json", exclude_none=True)
            if bootstrap.status != "ready":
                outcome: JSONObject = {
                    "success": False,
                    "status": (
                        PLUGIN_RUN_STATE_INITIALIZING
                        if bootstrap.status == "initializing"
                        else PLUGIN_RUN_STATE_FAILED
                    ),
                    "message": bootstrap.message or "Bakery plugin bootstrap is not ready",
                    "error_code": bootstrap.error_code,
                    "latency_ms": int((time.time() - start) * 1000),
                    "details": {
                        "bootstrap_status": bootstrap.status,
                        "credential_status": (
                            (bootstrap.details or {}).get("credential_status") or "unknown"
                        ),
                        "bootstrap": bootstrap_details,
                    },
                }
                return ExecutionResult(
                    service_type=self.service_type,
                    status="succeeded",
                    service_exec_id=receipt,
                    result=outcome,
                    raw=outcome,
                    retryable=False,
                )
            health = await get_health()
            raw = health.model_dump(mode="json", exclude_none=True)
            remote_status = str(raw.get("status") or "").strip().lower()
            try:
                remote_status = normalize_plugin_run_state(remote_status)
            except ValueError:
                remote_status = PLUGIN_RUN_STATE_DEGRADED
            outcome = {
                "success": remote_status in PLUGIN_CALLABLE_RUN_STATES,
                "status": remote_status,
                "message": "Bakery plugin health checked",
                "service_type": self.service_type,
                "latency_ms": int((time.time() - start) * 1000),
                "details": {
                    "bootstrap_status": "ready",
                    "credential_status": "ready",
                    "remote_health_status": remote_status,
                    "remote": raw,
                },
            }
            return ExecutionResult(
                service_type=self.service_type,
                status="succeeded",
                service_exec_id=receipt,
                result=outcome,
                raw=outcome,
                retryable=False,
            )
        except Exception as exc:  # noqa: BLE001
            outcome = {
                "success": False,
                "status": PLUGIN_RUN_STATE_FAILED,
                "message": "Bakery remote health check failed after bootstrap",
                "error_code": exc.__class__.__name__,
                "latency_ms": int((time.time() - start) * 1000),
                "details": {
                    "bootstrap_status": "ready",
                    "credential_status": "ready",
                    "remote_health_status": PLUGIN_RUN_STATE_FAILED,
                    "error": _safe_error_message(exc),
                },
            }
            return ExecutionResult(
                service_type=self.service_type,
                status="succeeded",
                service_exec_id=receipt,
                result=outcome,
                raw=outcome,
                retryable=False,
            )
        finally:
            reset_bakery_client_config(token)

    def health_check(self) -> PluginHealthResult:
        token = self._activate_config()
        start = time.time()
        try:
            config_error = validate_transport_config()
            if config_error:
                return PluginHealthResult(
                    service_type=self.service_type,
                    status=PLUGIN_RUN_STATE_FAILED,
                    message=config_error,
                    error_code="configuration_error",
                    latency_ms=int((time.time() - start) * 1000),
                )
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                result = asyncio.run(
                    self._execute_health_check(
                        ExecutionContext(
                            service_type=self.service_type,
                            service_exec="health_check",
                            req_id="SYSTEM-PLUGIN-HEALTH",
                        )
                    )
                )
            else:
                return PluginHealthResult(
                    service_type=self.service_type,
                    status="initializing",
                    message="Bakery health is checked by scheduled plugin execution",
                    error_code="event_loop_active",
                    latency_ms=int((time.time() - start) * 1000),
                )
            outcome = result.result if isinstance(result.result, dict) else {}
            status = str(outcome.get("status") or "unknown").strip().lower()
            try:
                status = normalize_plugin_run_state(status)
            except ValueError:
                status = PLUGIN_RUN_STATE_DEGRADED
            return PluginHealthResult(
                service_type=self.service_type,
                status=status,  # type: ignore[arg-type]
                message=str(outcome.get("message") or "Bakery plugin health checked"),
                latency_ms=int((time.time() - start) * 1000),
                details=(
                    outcome.get("details") if isinstance(outcome.get("details"), dict) else outcome
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return PluginHealthResult(
                service_type=self.service_type,
                status=PLUGIN_RUN_STATE_FAILED,
                message="Bakery API health check failed",
                error_code=exc.__class__.__name__,
                latency_ms=int((time.time() - start) * 1000),
                details={"error": _safe_error_message(exc)},
            )
        finally:
            reset_bakery_client_config(token)

    async def dispatch(self, ctx: ExecutionContext) -> ExecutionResult:
        token = self._activate_config()
        try:
            return await self._dispatch_with_config(ctx)
        finally:
            reset_bakery_client_config(token)

    async def _dispatch_with_config(self, ctx: ExecutionContext) -> ExecutionResult:
        if (ctx.service_exec or "").strip().lower() == "health_check":
            return await self._execute_health_check(ctx)
        payload = _payload_with_dish_evidence(
            ctx.service_payload if isinstance(ctx.service_payload, dict) else {},
            ctx,
        )
        target = _execution_target(ctx)
        parameters = (
            ctx.service_exec_parameters if isinstance(ctx.service_exec_parameters, dict) else {}
        )
        operation = normalize_communication_operation(parameters.get("operation") or "open")
        bakery_action = _bakery_action(operation)

        order_id = _coerce_optional_int(ctx.context.get("order_id"))
        recipe_ingredient_id = _coerce_optional_int(ctx.context.get("recipe_ingredient_id"))
        idem_key = _deterministic_idempotency_key(
            order_id=order_id,
            recipe_ingredient_id=recipe_ingredient_id,
            action=operation or target,
        )

        ticket_id = str(
            payload.get("ticket_id")
            or ctx.context.get("ticket_id")
            or ctx.context.get("bakery_comms_id")
            or ""
        ).strip()

        try:
            accepted: BakeryTicketAccepted
            context_updates: JSONObject = {}
            if operation == "open":
                reuse_mode = str(ctx.context.get("communication_reuse_mode") or "").strip().lower()
                if ticket_id and _is_ticket_capable_target(target):
                    if reuse_mode == "reopen":
                        await update_ticket_with_key(
                            req_id=ctx.req_id,
                            ticket_id=ticket_id,
                            payload=self._reopen_payload(target),
                            idempotency_key=idem_key,
                        )
                    accepted = await add_ticket_comment_with_key(
                        req_id=ctx.req_id,
                        ticket_id=ticket_id,
                        payload={"comment": self._payload_comment(payload)},
                        idempotency_key=idem_key,
                    )
                else:
                    accepted = await create_ticket_with_key(
                        req_id=ctx.req_id,
                        payload=payload,
                        idempotency_key=idem_key,
                    )
                    created_ticket_id = accepted.ticket_id.strip()
                    if created_ticket_id:
                        context_updates["bakery_comms_id"] = created_ticket_id
                        ticket_id = created_ticket_id
            elif operation == "update":
                accepted = await update_ticket_with_key(
                    req_id=ctx.req_id,
                    ticket_id=ticket_id,
                    payload=payload,
                    idempotency_key=idem_key,
                )
            elif operation == "notify":
                comment_payload = (
                    payload if "comment" in payload else {"comment": self._payload_comment(payload)}
                )
                accepted = await add_ticket_comment_with_key(
                    req_id=ctx.req_id,
                    ticket_id=ticket_id,
                    payload=comment_payload,
                    idempotency_key=idem_key,
                )
            elif operation == "close":
                accepted = await close_ticket_with_key(
                    req_id=ctx.req_id,
                    ticket_id=ticket_id,
                    payload=payload,
                    idempotency_key=idem_key,
                )
            elif bakery_action == "create":
                accepted = await create_ticket_with_key(
                    req_id=ctx.req_id,
                    payload=payload,
                    idempotency_key=idem_key,
                )
                created_ticket_id = accepted.ticket_id.strip()
                if created_ticket_id:
                    context_updates["bakery_comms_id"] = created_ticket_id
                    ticket_id = created_ticket_id
            elif bakery_action == "update":
                accepted = await update_ticket_with_key(
                    req_id=ctx.req_id,
                    ticket_id=ticket_id,
                    payload=payload,
                    idempotency_key=idem_key,
                )
            elif bakery_action == "comment":
                comment_payload = payload if "comment" in payload else {"comment": str(payload)}
                accepted = await add_ticket_comment_with_key(
                    req_id=ctx.req_id,
                    ticket_id=ticket_id,
                    payload=comment_payload,
                    idempotency_key=idem_key,
                )
            elif bakery_action == "close":
                accepted = await close_ticket_with_key(
                    req_id=ctx.req_id,
                    ticket_id=ticket_id,
                    payload=payload,
                    idempotency_key=idem_key,
                )
            else:
                return ExecutionResult(
                    service_type=self.service_type,
                    status="errored",
                    service_exec_error=(
                        "Unsupported bakery operation. "
                        "Expected service_exec_parameters.operation to be one of: "
                        "open, notify, update, close, "
                        "ticket_create, ticket_update, ticket_comment, ticket_close"
                    ),
                    retryable=False,
                )

            service_exec_id = accepted.operation_id or None

            return ExecutionResult(
                service_type=self.service_type,
                status="dispatched" if service_exec_id else "succeeded",
                service_exec_id=service_exec_id,
                result=accepted.model_dump(mode="json"),
                raw=accepted.model_dump(mode="json"),
                retryable=False,
                context_updates=context_updates,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Bakery execution attempt failed",
                extra={
                    "req_id": ctx.req_id,
                    "target": ctx.service_exec,
                    "error": str(exc),
                },
            )
            return ExecutionResult(
                service_type=self.service_type,
                status="errored",
                service_exec_error=str(exc),
                retryable=True,
            )

    async def poll(self, ctx: ExecutionContext, service_exec_id: str) -> ExecutionResult:
        token = self._activate_config()
        try:
            if self._service_exec_from_receipt(service_exec_id) == "health_check":
                return ExecutionResult(
                    service_type=self.service_type,
                    status="errored",
                    service_exec_id=service_exec_id,
                    service_exec_error=(
                        "Bakery health checks complete during dispatch; no pollable state exists"
                    ),
                    retryable=False,
                )
            operation = await poll_operation(service_exec_id)
            raw = operation.model_dump(mode="json")
            status = _map_bakery_status(operation.status)
            return ExecutionResult(
                service_type=self.service_type,
                status=status,
                service_exec_id=service_exec_id,
                service_exec_error=operation.last_error,
                result=raw,
                raw=raw,
                retryable=status in {"dispatched", "running"},
            )
        except Exception as exc:  # noqa: BLE001
            return ExecutionResult(
                service_type=self.service_type,
                status="errored",
                service_exec_id=service_exec_id,
                service_exec_error=str(exc),
                retryable=True,
            )
        finally:
            reset_bakery_client_config(token)

    @staticmethod
    def _service_exec_from_receipt(service_exec_id: str) -> str:
        parts = service_exec_id.split(":", 2)
        if len(parts) == 3 and parts[0] == "bakery" and parts[1]:
            return parts[1].strip().lower()
        return "unknown"


def _map_bakery_status(status: str) -> str:
    normalized = (status or "").strip().lower()
    if normalized in {"requested", "scheduled", "pending", "queued", "accepted"}:
        return "dispatched"
    if normalized in {"running", "processing"}:
        return "running"
    if normalized in {"succeeded", "success", "completed"}:
        return "succeeded"
    if normalized in {"canceled", "cancelled"}:
        return "canceled"
    if normalized in {"timeout", "timed_out"}:
        return "timeout"
    return "failed"
