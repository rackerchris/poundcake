"""StackStorm execution adapter."""

from __future__ import annotations

from uuid import uuid4

from api.types import CanonicalExecutionStatus, JSONObject

from api.core.logging import get_logger
from api.plugins.base import ExecutionAdapter
from api.plugins.state import PLUGIN_RUN_STATE_FAILED, PLUGIN_RUN_STATE_HEALTHY
from api.plugins.stackstorm.service import (
    STACKSTORM_API_KEY_CREDENTIAL_TYPE,
    StackStormActionManager,
    StackStormClient,
    StackStormError,
)
from api.plugins.stackstorm.content_sync import sync_stackstorm_content
from api.plugins.types import (
    ExecutionContext,
    ExecutionResult,
    PluginHealthResult,
)

logger = get_logger(__name__)

STACKSTORM_SERVICE_EXECS = {
    "health_check",
    "action_execution",
    "workflow_execution",
    "content_sync",
}
STACKSTORM_ACTION_OPERATIONS = {"execute_action"}
STACKSTORM_WORKFLOW_OPERATIONS = {"execute_workflow"}
STACKSTORM_CONTENT_OPERATIONS = {"sync_content"}
POUNDCAKE_TERMINAL_TO_STACKSTORM_STATUS = {
    "complete": "succeeded",
    "succeeded": "succeeded",
    "failed": "failed",
    "errored": "failed",
    "timeout": "timeout",
    "canceled": "canceled",
}
SERVICE_PAYLOAD_OBJECT_ERROR = "service_payload must be an object when provided"


def _map_stackstorm_status(raw_status: str) -> CanonicalExecutionStatus:
    status = (raw_status or "").strip().lower()
    if status in {"requested", "scheduled", "pending", "pausing", "resuming"}:
        return "dispatched"
    if status in {"running"}:
        return "running"
    if status in {"succeeded"}:
        return "succeeded"
    if status in {"canceled", "canceling"}:
        return "canceled"
    if status in {"abandoned"}:
        return "errored"
    if status in {"timeout"}:
        return "timeout"
    if status in {"failed"}:
        return "failed"
    return "errored"


def _map_poundcake_terminal_status(raw_status: str) -> str:
    """Map PoundCake terminal state names onto StackStorm terminal states."""
    status = (raw_status or "").strip().lower()
    mapped = POUNDCAKE_TERMINAL_TO_STACKSTORM_STATUS.get(status)
    if mapped is None:
        raise ValueError(f"Unsupported PoundCake terminal status for StackStorm: {raw_status}")
    return mapped


class StackStormExecutionAdapter(ExecutionAdapter):
    """Expose StackStorm operations through the order execution boundary."""

    service_type = "stackstorm"

    def __init__(self, manager: StackStormActionManager | None = None) -> None:
        self._manager = manager or StackStormActionManager()
        self._credential_payload: JSONObject | None = None

    def credential_requirements(self) -> list[JSONObject]:
        return [
            {
                "credential_type": STACKSTORM_API_KEY_CREDENTIAL_TYPE,
                "credential_key_id": "default",
                "required": True,
                "usage": "StackStorm API key or auth token for action execution.",
            }
        ]

    def operator_config_schema(self) -> JSONObject:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "title": "StackStorm API URL", "format": "uri"},
                "verify_ssl": {"type": "boolean", "title": "Verify SSL"},
                "capabilities_enabled": {
                    "type": "object",
                    "title": "Capability enablement overrides",
                    "additionalProperties": {"type": "boolean"},
                },
                "capability_overrides": {
                    "type": "object",
                    "title": "Capability workflow overrides",
                    "additionalProperties": {"type": "object"},
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        }

    def default_operator_config(self) -> JSONObject:
        return {
            "url": self._manager._client.base_url,
            "verify_ssl": self._manager._client.verify_ssl,
            "capabilities_enabled": {},
            "capability_overrides": {},
        }

    def normalize_operator_config(self, config: JSONObject | None) -> JSONObject:
        raw = dict(config or {})
        url = str(raw.get("url") or self._manager._client.base_url or "").strip().rstrip("/")
        if not url:
            raise ValueError("StackStorm API URL is required")
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ValueError("StackStorm API URL must start with http:// or https://")
        capabilities_enabled = raw.get("capabilities_enabled")
        capability_overrides = raw.get("capability_overrides")
        return {
            "url": url,
            "verify_ssl": bool(raw.get("verify_ssl", self._manager._client.verify_ssl)),
            "capabilities_enabled": (
                dict(capabilities_enabled) if isinstance(capabilities_enabled, dict) else {}
            ),
            "capability_overrides": (
                dict(capability_overrides) if isinstance(capability_overrides, dict) else {}
            ),
        }

    def with_operator_config(self, config: JSONObject | None) -> "StackStormExecutionAdapter":
        normalized = self.normalize_operator_config(config)
        return StackStormExecutionAdapter(
            StackStormActionManager(
                self._manager._client.__class__(
                    base_url=str(normalized["url"]),
                    verify_ssl=bool(normalized["verify_ssl"]),
                )
            )
        )

    def _get_operator_config(self, ctx: ExecutionContext) -> JSONObject:
        return ctx.context.get("operator_config") or {}

    def validate_credential_payload(self, credential_type: str, payload: JSONObject) -> str | None:
        if credential_type != STACKSTORM_API_KEY_CREDENTIAL_TYPE:
            return "Unsupported StackStorm credential type"
        api_key = str(payload.get("api_key") or payload.get("st2_api_key") or "").strip()
        auth_token = str(payload.get("auth_token") or "").strip()
        if api_key or auth_token:
            return None
        return "StackStorm credential requires api_key, st2_api_key, or auth_token"

    async def test_connection(self, *, credential_key_id: str = "default") -> PluginHealthResult:
        self._manager._client._credential_key_id = credential_key_id.strip() or "default"
        ok = await self._manager._client.health_check(req_id="plugin-config-test")
        health_details = self.health_check().details or {}
        return PluginHealthResult(
            service_type=self.service_type,
            status=PLUGIN_RUN_STATE_HEALTHY if ok else PLUGIN_RUN_STATE_FAILED,
            message=(
                "StackStorm API accepted the configured credential"
                if ok
                else "StackStorm API health check failed"
            ),
            error_code=None if ok else "stackstorm_health_check_failed",
            details={**health_details, "url": self._manager._client.base_url},
        )

    def validate(self, ctx: ExecutionContext) -> str | None:
        service_exec = (ctx.service_exec or "").strip().lower()
        if service_exec not in STACKSTORM_SERVICE_EXECS:
            return f"Unsupported stackstorm service_exec: {ctx.service_exec}"
        if ctx.service_payload is not None and not isinstance(ctx.service_payload, dict):
            return SERVICE_PAYLOAD_OBJECT_ERROR
        if service_exec == "health_check":
            return None
        operation = _operation(ctx)
        if service_exec == "action_execution" and operation not in STACKSTORM_ACTION_OPERATIONS:
            return "stackstorm action_execution operation must be: execute_action"
        if service_exec == "workflow_execution" and operation not in STACKSTORM_WORKFLOW_OPERATIONS:
            return "stackstorm workflow_execution operation must be: execute_workflow"
        if service_exec == "content_sync" and operation not in STACKSTORM_CONTENT_OPERATIONS:
            return "stackstorm content_sync operation must be: sync_content"
        if service_exec == "content_sync":
            return None
        payload = {} if ctx.service_payload is None else ctx.service_payload
        if service_exec == "action_execution" and not str(payload.get("action_ref") or "").strip():
            return "stackstorm execute_action requires service_payload.action_ref"
        if (
            service_exec == "workflow_execution"
            and not str(payload.get("workflow_ref") or "").strip()
        ):
            return "stackstorm execute_workflow requires service_payload.workflow_ref"
        parameters = payload.get("parameters")
        if (
            service_exec == "action_execution"
            and parameters is not None
            and not isinstance(parameters, dict)
        ):
            return "stackstorm execute_action service_payload.parameters must be an object"
        inputs = payload.get("inputs")
        if (
            service_exec == "workflow_execution"
            and inputs is not None
            and not isinstance(inputs, dict)
        ):
            return "stackstorm execute_workflow service_payload.inputs must be an object"
        return None

    def health_check(self) -> PluginHealthResult:
        return PluginHealthResult(
            service_type=self.service_type,
            status="healthy",
            message="StackStorm plugin configured",
            details={
                "mode": "stackstorm-api",
                "credential_type": STACKSTORM_API_KEY_CREDENTIAL_TYPE,
            },
        )

    async def bootstrap_credentials(
        self,
        *,
        force: bool = False,
    ) -> None:
        del force
        return None

    async def dispatch(self, ctx: ExecutionContext) -> ExecutionResult:
        service_exec = (ctx.service_exec or "").strip().lower()
        if ctx.service_payload is not None and not isinstance(ctx.service_payload, dict):
            return _payload_contract_error(
                service_type=self.service_type,
                service_exec_id=f"stackstorm:{service_exec}:{uuid4()}",
                message=SERVICE_PAYLOAD_OBJECT_ERROR,
            )
        operator_config = self._get_operator_config(ctx)
        credential_payload = self._credential_payload or {}
        manager = self._manager
        if operator_config or credential_payload:
            client = StackStormClient(
                base_url=operator_config.get("url"),
                verify_ssl=operator_config.get("verify_ssl"),
                credential_payload=credential_payload or None,
                credential_key_id="default",
            )
            manager = StackStormActionManager(client)
        if service_exec == "health_check":
            service_exec_id = f"stackstorm:health_check:{uuid4()}"
            healthy = await manager._client.health_check(req_id=ctx.req_id)
            result: JSONObject = {
                "success": healthy,
                "status": PLUGIN_RUN_STATE_HEALTHY if healthy else PLUGIN_RUN_STATE_FAILED,
            }
            return ExecutionResult(
                service_type=self.service_type,
                status="succeeded" if healthy else "failed",
                service_exec_id=service_exec_id,
                service_exec_error=None if healthy else "StackStorm health check failed",
                result=result,
                raw=result,
                retryable=not healthy,
            )
        if service_exec == "content_sync":
            service_exec_id = f"stackstorm:content_sync:{uuid4()}"
            result = await sync_stackstorm_content(manager)
            return ExecutionResult(
                service_type=self.service_type,
                status="succeeded",
                service_exec_id=service_exec_id,
                result={"success": True, "status": "succeeded", **result},
                raw={"success": True, "status": "succeeded", **result},
                retryable=False,
            )

        try:
            payload = {} if ctx.service_payload is None else ctx.service_payload
            if service_exec == "workflow_execution":
                action_ref = str(payload.get("workflow_ref") or "").strip()
                parameters = (
                    payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
                )
            else:
                action_ref = str(payload.get("action_ref") or "").strip()
                parameters = (
                    payload.get("parameters") if isinstance(payload.get("parameters"), dict) else {}
                )
            raw = await manager._client.execute_action(
                req_id=ctx.req_id,
                action_ref=action_ref,
                parameters=parameters,
                timeout=ctx.service_exec_timeout,
                action_is_workflow=service_exec == "workflow_execution",
            )
            mapped_status = _map_stackstorm_status(str(raw.get("status") or ""))
            return ExecutionResult(
                service_type=self.service_type,
                status=mapped_status,
                service_exec_id=str(raw.get("id") or "") or None,
                result=raw.get("result") if isinstance(raw.get("result"), dict) else None,
                raw=raw,
            )
        except StackStormError as exc:
            logger.warning(
                "StackStorm execution attempt failed",
                extra={"req_id": ctx.req_id, "service_exec": ctx.service_exec, "error": str(exc)},
            )
            return ExecutionResult(
                service_type=self.service_type,
                status="errored",
                service_exec_error=str(exc),
                retryable=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "StackStorm adapter unexpected failure",
                extra={"req_id": ctx.req_id, "service_exec": ctx.service_exec, "error": str(exc)},
            )
            return ExecutionResult(
                service_type=self.service_type,
                status="errored",
                service_exec_error=str(exc),
                retryable=False,
            )

    async def poll(self, ctx: ExecutionContext, service_exec_id: str) -> ExecutionResult:
        try:
            operation = self._operation_from_receipt(service_exec_id)
            if operation in {"health_check", "content_sync"}:
                return ExecutionResult(
                    service_type=self.service_type,
                    status="errored",
                    service_exec_id=service_exec_id,
                    service_exec_error=(
                        "StackStorm health_check and content_sync complete during dispatch; "
                        "no pollable state exists"
                    ),
                    retryable=False,
                )

            raw = await self._manager._client.get_execution(service_exec_id)
            payload = raw.model_dump(mode="json") if hasattr(raw, "model_dump") else raw
            status = _map_stackstorm_status(str(payload.get("status") or ""))
            result = payload.get("result")
            return ExecutionResult(
                service_type=self.service_type,
                status=status,
                service_exec_id=service_exec_id,
                result=result if isinstance(result, dict) else None,
                raw=payload,
            )
        except StackStormError as exc:
            return ExecutionResult(
                service_type=self.service_type,
                status="errored",
                service_exec_id=service_exec_id,
                service_exec_error=str(exc),
                retryable=True,
            )

    async def cancel(self, ctx: ExecutionContext, service_exec_id: str) -> ExecutionResult:
        try:
            stackstorm_status = _map_poundcake_terminal_status("canceled")
            ok = await self._manager._client.cancel_execution(
                service_exec_id,
                status=stackstorm_status,
            )
            return ExecutionResult(
                service_type=self.service_type,
                status=_map_stackstorm_status(stackstorm_status) if ok else "failed",
                service_exec_id=service_exec_id,
                raw={"status": stackstorm_status, "cancel_requested": True},
            )
        except StackStormError as exc:
            return ExecutionResult(
                service_type=self.service_type,
                status="errored",
                service_exec_id=service_exec_id,
                service_exec_error=str(exc),
                retryable=False,
            )

    @staticmethod
    def _operation_from_receipt(service_exec_id: str) -> str:
        parts = service_exec_id.split(":", 2)
        if len(parts) == 3 and parts[0] == "stackstorm" and parts[1]:
            return parts[1].strip().lower()
        return "action_execution"


def _operation(ctx: ExecutionContext) -> str:
    params = ctx.service_exec_parameters if isinstance(ctx.service_exec_parameters, dict) else {}
    return str(params.get("operation") or "").strip().lower()


def _payload_contract_error(
    *, service_type: str, service_exec_id: str, message: str
) -> ExecutionResult:
    outcome: JSONObject = {"success": False, "status": "errored", "message": message}
    return ExecutionResult(
        service_type=service_type,
        status="errored",
        service_exec_id=service_exec_id,
        service_exec_error=message,
        result=outcome,
        raw=outcome,
        retryable=False,
    )
