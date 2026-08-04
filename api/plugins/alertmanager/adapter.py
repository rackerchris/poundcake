"""Alertmanager service adapter for inspection and suppression lifecycle actions."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx

from api.core.config import get_settings
from api.plugins.alertmanager.templates import ALERTMANAGER_INSPECT_OPERATIONS
from api.plugins.base import ExecutionAdapter
from api.services.credential_manager import read_adapter_credential_payload
from api.plugins.state import (
    PLUGIN_CALLABLE_RUN_STATES,
    PLUGIN_RUN_STATE_DEGRADED,
    PLUGIN_RUN_STATE_FAILED,
    PLUGIN_RUN_STATE_HEALTHY,
)
from api.plugins.transport import (
    PluginHttpTransportConfig,
    http_operator_config_schema,
    normalize_http_operator_config,
)
from api.plugins.types import ExecutionContext, ExecutionResult, PluginHealthResult
from api.types import JSONObject

DEFAULT_INSPECT_LIMIT = 50
MAX_INSPECT_LIMIT = 200
ALERTMANAGER_CREDENTIAL_TYPE = "alertmanager_http_auth"
ALERTMANAGER_SUPPRESSION_OPERATIONS = ("create", "update", "expire", "get")
POUNDCAKE_COMMENT_PREFIX = "PoundCake suppression: "
SERVICE_PAYLOAD_OBJECT_ERROR = "service_payload must be an object when provided"


class AlertmanagerExecutionAdapter(ExecutionAdapter):
    """Translate PoundCake service plugin calls into Alertmanager API v2 calls."""

    service_type = "alertmanager"

    def __init__(self, transport: PluginHttpTransportConfig | None = None) -> None:
        if transport is not None:
            self.transport = transport
            return
        settings = get_settings()
        self.transport = PluginHttpTransportConfig(
            service_label="Alertmanager",
            base_url=settings.alertmanager_url.rstrip("/"),
            verify_ssl=settings.alertmanager_verify_ssl,
            timeout_seconds=settings.alertmanager_timeout_seconds,
        )

    def credential_requirements(self) -> list[JSONObject]:
        return [
            {
                "credential_type": ALERTMANAGER_CREDENTIAL_TYPE,
                "credential_key_id": "default",
                "required": False,
                "usage": "Optional Alertmanager API credentials for authenticated alert management.",
            }
        ]

    def operator_config_schema(self) -> JSONObject:
        return http_operator_config_schema(service_label="Alertmanager")

    def default_operator_config(self) -> JSONObject:
        return {
            "url": self.transport.base_url,
            "verify_ssl": self.transport.verify_ssl,
            "timeout_seconds": self.transport.timeout_seconds,
        }

    def normalize_operator_config(self, config: JSONObject | None) -> JSONObject:
        return normalize_http_operator_config(
            config,
            default_url=self.transport.base_url,
            default_verify_ssl=self.transport.verify_ssl,
            default_timeout_seconds=self.transport.timeout_seconds,
            service_label="Alertmanager",
        )

    def with_operator_config(self, config: JSONObject | None) -> "AlertmanagerExecutionAdapter":
        return AlertmanagerExecutionAdapter(
            self.transport.with_operator_config(self.normalize_operator_config(config))
        )

    def validate_credential_payload(self, credential_type: str, payload: JSONObject) -> str | None:
        if credential_type != ALERTMANAGER_CREDENTIAL_TYPE:
            return "Unsupported Alertmanager credential type"
        token = str(
            payload.get("bearer_token")
            or payload.get("token")
            or payload.get("api_key")
            or payload.get("access_token")
            or ""
        ).strip()
        username = str(payload.get("username") or payload.get("user") or "").strip()
        password = str(payload.get("password") or "").strip()
        if token or (username and password):
            return None
        return "Alertmanager credential requires bearer_token/token/api_key/access_token or username/password"

    def validate(self, ctx: ExecutionContext) -> str | None:
        service_exec = (ctx.service_exec or "").strip().lower()
        if service_exec not in {"health_check", "inspect", "sync_silences", "suppression"}:
            return f"Unsupported alertmanager service_exec: {ctx.service_exec}"
        if ctx.service_payload is not None and not isinstance(ctx.service_payload, dict):
            return SERVICE_PAYLOAD_OBJECT_ERROR
        if service_exec == "inspect":
            operation = _operation(ctx)
            if operation not in ALERTMANAGER_INSPECT_OPERATIONS:
                return "alertmanager inspect operation must be one of: " + ", ".join(
                    ALERTMANAGER_INSPECT_OPERATIONS
            )
            if operation == "find_inhibited_by_source":
                fingerprint = str(_payload(ctx).get("fingerprint") or "").strip()
                if not fingerprint:
                    return (
                        "alertmanager find_inhibited_by_source requires service_payload.fingerprint"
                    )
        if service_exec == "suppression":
            operation = _operation(ctx)
            if operation not in ALERTMANAGER_SUPPRESSION_OPERATIONS:
                return "alertmanager suppression operation must be one of: " + ", ".join(
                    ALERTMANAGER_SUPPRESSION_OPERATIONS
                )
            payload = _payload(ctx)
            if operation in {"create", "update"}:
                if not isinstance(payload.get("matchers"), list) or not payload.get("matchers"):
                    return "alertmanager suppression create/update requires service_payload.matchers"
                if not str(payload.get("name") or "").strip():
                    return "alertmanager suppression create/update requires service_payload.name"
                if not str(payload.get("starts_at") or "").strip():
                    return "alertmanager suppression create/update requires service_payload.starts_at"
                if not str(payload.get("ends_at") or "").strip():
                    return "alertmanager suppression create/update requires service_payload.ends_at"
                if operation == "update" and not str(payload.get("source_ref") or "").strip():
                    return "alertmanager suppression update requires service_payload.source_ref"
            if operation in {"expire", "get"} and not str(payload.get("source_ref") or "").strip():
                return f"alertmanager suppression {operation} requires service_payload.source_ref"
        if not self.transport.base_url:
            return "POUNDCAKE_ALERTMANAGER_URL is required for alertmanager plugin"
        transport_error = self.transport.validate_security()
        if transport_error:
            return transport_error
        return None

    def health_check(self) -> PluginHealthResult:
        start = time.time()
        transport_error = self.transport.validate_security()
        if transport_error:
            return PluginHealthResult(
                service_type=self.service_type,
                status=PLUGIN_RUN_STATE_FAILED,
                message=transport_error,
                error_code="TransportSecurityError",
                latency_ms=0,
                details=self.transport.safe_details(),
            )
        try:
            response = httpx.get(
                f"{self.transport.base_url}/api/v2/status",
                timeout=self.transport.timeout_seconds,
                **self.transport.request_kwargs(),
            )
            latency_ms = int((time.time() - start) * 1000)
            if response.status_code >= 400:
                status = _alertmanager_unhealthy_status(response.status_code)
                return PluginHealthResult(
                    service_type=self.service_type,
                    status=status,
                    message=f"Alertmanager status check returned HTTP {response.status_code}",
                    error_code=str(response.status_code),
                    latency_ms=latency_ms,
                    details={**self.transport.safe_details(), "body": response.text[:500]},
                )
            return PluginHealthResult(
                service_type=self.service_type,
                status=PLUGIN_RUN_STATE_HEALTHY,
                message="Alertmanager API reachable",
                latency_ms=latency_ms,
                details={**self.transport.safe_details(), "status": self._json_or_text(response)},
            )
        except Exception as exc:
            return PluginHealthResult(
                service_type=self.service_type,
                status=PLUGIN_RUN_STATE_DEGRADED,
                message="Alertmanager API health check failed",
                error_code=exc.__class__.__name__,
                latency_ms=int((time.time() - start) * 1000),
                details={**self.transport.safe_details(), "error": str(exc)},
            )

    async def test_connection(self, *, credential_key_id: str = "default") -> PluginHealthResult:
        adapter = await self._adapter_with_credentials(credential_key_id=credential_key_id)
        return await adapter._async_health_check()

    async def dispatch(self, ctx: ExecutionContext) -> ExecutionResult:
        service_exec = (ctx.service_exec or "").strip().lower()
        service_exec_id = f"alertmanager:{service_exec}:{uuid4()}"
        if ctx.service_payload is not None and not isinstance(ctx.service_payload, dict):
            return _payload_contract_error(
                service_exec_id=service_exec_id,
                message=SERVICE_PAYLOAD_OBJECT_ERROR,
            )
        adapter = await self._adapter_with_credentials()
        if service_exec == "health_check":
            health = await adapter._async_health_check()
            outcome: JSONObject = {
                "success": health.status in PLUGIN_CALLABLE_RUN_STATES,
                "status": health.status,
                "message": health.message or "Alertmanager health checked",
                "service_type": self.service_type,
                "latency_ms": health.latency_ms,
                "error_code": health.error_code,
                "details": health.details or {},
            }
            return ExecutionResult(
                service_type=self.service_type,
                status="succeeded" if health.status in PLUGIN_CALLABLE_RUN_STATES else "failed",
                service_exec_id=service_exec_id,
                result=outcome,
                raw=outcome,
            )
        if service_exec == "sync_silences":
            return await adapter._execute_sync_silences(service_exec_id)
        if service_exec == "inspect":
            return await adapter._execute_inspect(ctx, service_exec_id)
        if service_exec == "suppression":
            return await adapter._execute_suppression(ctx, service_exec_id)
        return ExecutionResult(
            service_type=self.service_type,
            status="errored",
            service_exec_id=service_exec_id,
            service_exec_error=f"Unknown alertmanager service_exec: {service_exec}",
            result={"success": False, "status": "errored"},
            raw={"success": False, "status": "errored"},
        )

    async def poll(self, ctx: ExecutionContext, service_exec_id: str) -> ExecutionResult:
        # All alertmanager operations complete synchronously during dispatch.
        # Return a successful replay of the dispatch result since no async state
        # exists to observe. This is a read-only confirmation, not a mutation.
        message = "Alertmanager executions complete during dispatch; no pollable state exists"
        return ExecutionResult(
            service_type=self.service_type,
            status="succeeded",
            service_exec_id=service_exec_id,
            service_exec_error=message,
            result={"success": True, "status": "succeeded", "message": message},
            raw={"success": True, "status": "succeeded", "message": message},
            retryable=False,
        )

    async def _adapter_with_credentials(
        self,
        credential_key_id: str = "default",
    ) -> "AlertmanagerExecutionAdapter":
        if self.transport.auth_mode != "none":
            return self
        try:
            credential = await read_adapter_credential_payload(
                service_type=self.service_type,
                credential_type=ALERTMANAGER_CREDENTIAL_TYPE,
                credential_key_id=credential_key_id,
            )
        except Exception:
            credential = None
        return AlertmanagerExecutionAdapter(self.transport.with_credentials(credential))

    async def _async_health_check(self) -> PluginHealthResult:
        start = time.time()
        transport_error = self.transport.validate_security()
        if transport_error:
            return PluginHealthResult(
                service_type=self.service_type,
                status=PLUGIN_RUN_STATE_FAILED,
                message=transport_error,
                error_code="TransportSecurityError",
                latency_ms=0,
                details=self.transport.safe_details(),
            )
        try:
            async with httpx.AsyncClient(
                timeout=self.transport.timeout_seconds,
                verify=self.transport.verify_ssl,
            ) as client:
                response = await client.get(
                    f"{self.transport.base_url}/api/v2/status",
                    **{k: v for k, v in self.transport.request_kwargs().items() if k != "verify"},
                )
            latency_ms = int((time.time() - start) * 1000)
            if response.status_code >= 400:
                status = _alertmanager_unhealthy_status(response.status_code)
                return PluginHealthResult(
                    service_type=self.service_type,
                    status=status,
                    message=f"Alertmanager status check returned HTTP {response.status_code}",
                    error_code=str(response.status_code),
                    latency_ms=latency_ms,
                    details={**self.transport.safe_details(), "body": response.text[:500]},
                )
            return PluginHealthResult(
                service_type=self.service_type,
                status=PLUGIN_RUN_STATE_HEALTHY,
                message="Alertmanager API reachable",
                latency_ms=latency_ms,
                details={**self.transport.safe_details(), "status": self._json_or_text(response)},
            )
        except Exception as exc:
            return PluginHealthResult(
                service_type=self.service_type,
                status=PLUGIN_RUN_STATE_DEGRADED,
                message="Alertmanager API health check failed",
                error_code=exc.__class__.__name__,
                latency_ms=int((time.time() - start) * 1000),
                details={**self.transport.safe_details(), "error": str(exc)},
            )

    @staticmethod
    def _json_or_text(response: httpx.Response) -> object:
        try:
            return response.json()
        except ValueError:
            return response.text[:500]

    async def _execute_sync_silences(self, service_exec_id: str) -> ExecutionResult:
        try:
            request_kwargs = self.transport.request_kwargs()
            request_kwargs.pop("verify", None)
            async with httpx.AsyncClient(
                timeout=self.transport.timeout_seconds,
                verify=self.transport.verify_ssl,
            ) as client:
                response = await client.get(
                    f"{self.transport.base_url}/api/v2/silences",
                    **request_kwargs,
                )
            if response.status_code >= 400:
                outcome: JSONObject = {
                    "success": False,
                    "status": "failed",
                    "message": f"Alertmanager silences returned HTTP {response.status_code}",
                    "body": response.text[:500],
                }
                return ExecutionResult(
                    service_type=self.service_type,
                    status="failed",
                    service_exec_id=service_exec_id,
                    service_exec_error=str(outcome["message"]),
                    result=outcome,
                    raw=outcome,
                )
            silences = response.json()
            normalized = [
                self._normalize_silence(item) for item in silences if isinstance(item, dict)
            ]
            outcome = {
                "success": True,
                "status": "succeeded",
                "silences": normalized,
                "synced_at": datetime.now(timezone.utc).isoformat(),
            }
            return ExecutionResult(
                service_type=self.service_type,
                status="succeeded",
                service_exec_id=service_exec_id,
                result=outcome,
                raw=outcome,
            )
        except Exception as exc:
            outcome = {
                "success": False,
                "status": "errored",
                "message": "Alertmanager silence sync failed",
                "error": str(exc),
            }
            return ExecutionResult(
                service_type=self.service_type,
                status="errored",
                service_exec_id=service_exec_id,
                service_exec_error=str(exc),
                result=outcome,
                raw=outcome,
            )

    async def _execute_suppression(
        self,
        ctx: ExecutionContext,
        service_exec_id: str,
    ) -> ExecutionResult:
        operation = _operation(ctx)
        if operation not in ALERTMANAGER_SUPPRESSION_OPERATIONS:
            outcome: JSONObject = {
                "success": False,
                "status": "failed",
                "operation": operation or None,
                "message": (
                    "alertmanager suppression operation must be one of: "
                    + ", ".join(ALERTMANAGER_SUPPRESSION_OPERATIONS)
                ),
            }
            return ExecutionResult(
                service_type=self.service_type,
                status="failed",
                service_exec_id=service_exec_id,
                service_exec_error=str(outcome["message"]),
                result=outcome,
                raw=outcome,
            )
        try:
            if operation == "create":
                return await self._execute_create_suppression(ctx, service_exec_id)
            if operation == "update":
                return await self._execute_update_suppression(ctx, service_exec_id)
            if operation == "expire":
                return await self._execute_expire_suppression(ctx, service_exec_id)
            return await self._execute_get_suppression(ctx, service_exec_id)
        except Exception as exc:
            outcome = {
                "success": False,
                "status": "errored",
                "operation": operation,
                "message": "Alertmanager suppression request failed",
                "error": str(exc),
            }
            return ExecutionResult(
                service_type=self.service_type,
                status="errored",
                service_exec_id=service_exec_id,
                service_exec_error=str(exc),
                result=outcome,
                raw=outcome,
            )

    async def _execute_inspect(
        self,
        ctx: ExecutionContext,
        service_exec_id: str,
    ) -> ExecutionResult:
        operation = _operation(ctx)
        if operation not in ALERTMANAGER_INSPECT_OPERATIONS:
            outcome: JSONObject = {
                "success": False,
                "status": "failed",
                "operation": operation or None,
                "message": (
                    "alertmanager inspect operation must be one of: "
                    + ", ".join(ALERTMANAGER_INSPECT_OPERATIONS)
                ),
            }
            return ExecutionResult(
                service_type=self.service_type,
                status="failed",
                service_exec_id=service_exec_id,
                service_exec_error=str(outcome["message"]),
                result=outcome,
                raw=outcome,
            )

        try:
            if operation == "list_groups":
                return await self._execute_list_groups(ctx, service_exec_id)
            if operation == "verify_firing":
                return await self._execute_verify_firing(ctx, service_exec_id)
            return await self._execute_list_alerts(ctx, service_exec_id, operation=operation)
        except Exception as exc:
            outcome = {
                "success": False,
                "status": "errored",
                "operation": operation,
                "message": "Alertmanager inspect failed",
                "error": str(exc),
            }
            return ExecutionResult(
                service_type=self.service_type,
                status="errored",
                service_exec_id=service_exec_id,
                service_exec_error=str(exc),
                result=outcome,
                raw=outcome,
            )

    async def _execute_create_suppression(
        self,
        ctx: ExecutionContext,
        service_exec_id: str,
    ) -> ExecutionResult:
        payload = _payload(ctx)
        response = await self._alertmanager_post(
            "/api/v2/silences",
            json=_silence_write_payload(payload),
        )
        if response.status_code >= 400:
            return self._http_failure_result(
                service_exec_id=service_exec_id,
                operation="create",
                endpoint="silences",
                response=response,
            )
        silence_id = _silence_id_from_response(response)
        silence = await self._fetch_silence(silence_id)
        normalized = self._normalize_silence(silence)
        outcome: JSONObject = {
            "success": True,
            "status": "succeeded",
            "operation": "create",
            "suppression": normalized,
            "silence_id": silence_id,
        }
        return ExecutionResult(
            service_type=self.service_type,
            status="succeeded",
            service_exec_id=service_exec_id,
            result=outcome,
            raw=outcome,
        )

    async def _execute_update_suppression(
        self,
        ctx: ExecutionContext,
        service_exec_id: str,
    ) -> ExecutionResult:
        payload = _payload(ctx)
        response = await self._alertmanager_post(
            "/api/v2/silences",
            json=_silence_write_payload(payload, silence_id=str(payload.get("source_ref") or "")),
        )
        if response.status_code >= 400:
            return self._http_failure_result(
                service_exec_id=service_exec_id,
                operation="update",
                endpoint="silences",
                response=response,
            )
        silence_id = _silence_id_from_response(response, fallback=str(payload.get("source_ref") or ""))
        silence = await self._fetch_silence(silence_id)
        normalized = self._normalize_silence(silence)
        outcome: JSONObject = {
            "success": True,
            "status": "succeeded",
            "operation": "update",
            "suppression": normalized,
            "silence_id": silence_id,
        }
        return ExecutionResult(
            service_type=self.service_type,
            status="succeeded",
            service_exec_id=service_exec_id,
            result=outcome,
            raw=outcome,
        )

    async def _execute_expire_suppression(
        self,
        ctx: ExecutionContext,
        service_exec_id: str,
    ) -> ExecutionResult:
        payload = _payload(ctx)
        source_ref = str(payload.get("source_ref") or "").strip()
        silence = await self._fetch_silence(source_ref)
        updated = _silence_expire_payload(silence)
        response = await self._alertmanager_post("/api/v2/silences", json=updated)
        if response.status_code >= 400:
            return self._http_failure_result(
                service_exec_id=service_exec_id,
                operation="expire",
                endpoint="silences",
                response=response,
            )
        refreshed = await self._wait_for_expired_silence(source_ref)
        normalized = self._normalize_silence(refreshed)
        outcome: JSONObject = {
            "success": True,
            "status": "succeeded",
            "operation": "expire",
            "suppression": normalized,
            "silence_id": source_ref,
        }
        return ExecutionResult(
            service_type=self.service_type,
            status="succeeded",
            service_exec_id=service_exec_id,
            result=outcome,
            raw=outcome,
        )

    async def _wait_for_expired_silence(self, source_ref: str) -> JSONObject:
        silence = await self._fetch_silence(source_ref)
        for _ in range(8):
            state = str(((silence.get("status") or {}) if isinstance(silence, dict) else {}).get("state") or "").strip().lower()
            if state and state != "active":
                return silence
            await asyncio.sleep(0.5)
            silence = await self._fetch_silence(source_ref)
        return silence

    async def _execute_get_suppression(
        self,
        ctx: ExecutionContext,
        service_exec_id: str,
    ) -> ExecutionResult:
        source_ref = str(_payload(ctx).get("source_ref") or "").strip()
        silence = await self._fetch_silence(source_ref)
        normalized = self._normalize_silence(silence)
        outcome: JSONObject = {
            "success": True,
            "status": "succeeded",
            "operation": "get",
            "suppression": normalized,
            "silence_id": source_ref,
        }
        return ExecutionResult(
            service_type=self.service_type,
            status="succeeded",
            service_exec_id=service_exec_id,
            result=outcome,
            raw=outcome,
        )

    async def _execute_list_alerts(
        self,
        ctx: ExecutionContext,
        service_exec_id: str,
        *,
        operation: str,
    ) -> ExecutionResult:
        payload = _payload(ctx)
        source_fingerprint = str(payload.get("fingerprint") or "").strip()
        response = await self._alertmanager_get("/api/v2/alerts", params=_alert_query_params(ctx))
        if response.status_code >= 400:
            return self._http_failure_result(
                service_exec_id=service_exec_id,
                operation=operation,
                endpoint="alerts",
                response=response,
            )

        raw_alerts = response.json()
        alerts = [self._normalize_alert(item) for item in raw_alerts if isinstance(item, dict)]
        if operation == "find_inhibited_by_source":
            alerts = [
                alert
                for alert in alerts
                if source_fingerprint
                and source_fingerprint in _string_list(alert["suppression"]["inhibited_by"])
            ]
        alerts = alerts[: _limit(ctx)]
        outcome: JSONObject = {
            "success": True,
            "status": "succeeded",
            "operation": operation,
            "source_fingerprint": source_fingerprint or None,
            "alert_count": len(alerts),
            "alerts": alerts,
            "suppression": _suppression_summary(alerts),
            "queried_at": datetime.now(timezone.utc).isoformat(),
        }
        return ExecutionResult(
            service_type=self.service_type,
            status="succeeded",
            service_exec_id=service_exec_id,
            result=outcome,
            raw=outcome,
        )

    async def _execute_verify_firing(
        self,
        ctx: ExecutionContext,
        service_exec_id: str,
    ) -> ExecutionResult:
        payload = _payload(ctx)
        source_fingerprint = _clean_template_value(payload.get("fingerprint"))
        response = await self._alertmanager_get(
            "/api/v2/alerts",
            params=_alert_query_params(ctx, include_matchers=not bool(source_fingerprint)),
        )
        if response.status_code >= 400:
            return self._http_failure_result(
                service_exec_id=service_exec_id,
                operation="verify_firing",
                endpoint="alerts",
                response=response,
            )

        raw_alerts = response.json()
        alerts = [self._normalize_alert(item) for item in raw_alerts if isinstance(item, dict)]
        if source_fingerprint:
            alerts = [alert for alert in alerts if alert.get("fingerprint") == source_fingerprint]
        alerts = alerts[: _limit(ctx)]
        is_firing = bool(alerts)
        status = "firing" if is_firing else "resolved"
        outcome: JSONObject = {
            "success": is_firing,
            "status": status,
            "operation": "verify_firing",
            "is_firing": is_firing,
            "alert_count": len(alerts),
            "source_fingerprint": source_fingerprint or None,
            "alerts": alerts,
            "queried_at": datetime.now(timezone.utc).isoformat(),
        }
        return ExecutionResult(
            service_type=self.service_type,
            status="succeeded",
            service_exec_id=service_exec_id,
            result=outcome,
            raw=outcome,
        )

    async def _execute_list_groups(
        self,
        ctx: ExecutionContext,
        service_exec_id: str,
    ) -> ExecutionResult:
        response = await self._alertmanager_get(
            "/api/v2/alerts/groups",
            params=_alert_query_params(ctx, include_muted=True),
        )
        if response.status_code >= 400:
            return self._http_failure_result(
                service_exec_id=service_exec_id,
                operation="list_groups",
                endpoint="alerts/groups",
                response=response,
            )

        raw_groups = response.json()
        groups = [self._normalize_group(item) for item in raw_groups if isinstance(item, dict)][
            : _limit(ctx)
        ]
        alerts = [
            alert
            for group in groups
            for alert in group.get("alerts", [])
            if isinstance(alert, dict)
        ]
        outcome: JSONObject = {
            "success": True,
            "status": "succeeded",
            "operation": "list_groups",
            "group_count": len(groups),
            "alert_count": len(alerts),
            "groups": groups,
            "suppression": _suppression_summary(alerts),
            "queried_at": datetime.now(timezone.utc).isoformat(),
        }
        return ExecutionResult(
            service_type=self.service_type,
            status="succeeded",
            service_exec_id=service_exec_id,
            result=outcome,
            raw=outcome,
        )

    async def _alertmanager_get(
        self,
        path: str,
        *,
        params: list[tuple[str, object]],
    ) -> httpx.Response:
        request_kwargs = self.transport.request_kwargs()
        request_kwargs.pop("verify", None)
        async with httpx.AsyncClient(
            timeout=self.transport.timeout_seconds,
            verify=self.transport.verify_ssl,
        ) as client:
            return await client.get(
                f"{self.transport.base_url}{path}",
                params=params,
                **request_kwargs,
            )

    async def _alertmanager_post(
        self,
        path: str,
        *,
        json: JSONObject,
    ) -> httpx.Response:
        request_kwargs = self.transport.request_kwargs()
        request_kwargs.pop("verify", None)
        async with httpx.AsyncClient(
            timeout=self.transport.timeout_seconds,
            verify=self.transport.verify_ssl,
        ) as client:
            return await client.post(
                f"{self.transport.base_url}{path}",
                json=json,
                **request_kwargs,
            )

    async def _fetch_silence(self, source_ref: str) -> JSONObject:
        response = await self._alertmanager_get(
            f"/api/v2/silence/{source_ref}",
            params=[],
        )
        if response.status_code >= 400:
            raise ValueError(f"Alertmanager silence lookup returned HTTP {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Alertmanager silence lookup returned an invalid payload")
        return payload

    def _http_failure_result(
        self,
        *,
        service_exec_id: str,
        operation: str,
        endpoint: str,
        response: httpx.Response,
    ) -> ExecutionResult:
        outcome: JSONObject = {
            "success": False,
            "status": "failed",
            "operation": operation,
            "message": f"Alertmanager {endpoint} returned HTTP {response.status_code}",
            "body": response.text[:500],
        }
        return ExecutionResult(
            service_type=self.service_type,
            status="failed",
            service_exec_id=service_exec_id,
            service_exec_error=str(outcome["message"]),
            result=outcome,
            raw=outcome,
        )

    def _normalize_alert(self, item: JSONObject) -> JSONObject:
        status = item.get("status") if isinstance(item.get("status"), dict) else {}
        return {
            "fingerprint": str(item.get("fingerprint") or ""),
            "labels": _object_or_empty(item.get("labels")),
            "annotations": _object_or_empty(item.get("annotations")),
            "starts_at": item.get("startsAt"),
            "ends_at": item.get("endsAt"),
            "updated_at": item.get("updatedAt"),
            "receivers": [
                _object_or_empty(receiver)
                for receiver in item.get("receivers") or []
                if isinstance(receiver, dict)
            ],
            "suppression": _normalize_alert_status(status),
        }

    def _normalize_group(self, item: JSONObject) -> JSONObject:
        alerts = [
            self._normalize_alert(alert)
            for alert in item.get("alerts") or []
            if isinstance(alert, dict)
        ]
        return {
            "receiver": _object_or_empty(item.get("receiver")),
            "labels": _object_or_empty(item.get("labels")),
            "alerts": alerts,
            "alert_count": len(alerts),
            "suppression": _suppression_summary(alerts),
        }

    def _normalize_silence(self, item: JSONObject) -> JSONObject:
        status = item.get("status") if isinstance(item.get("status"), dict) else {}
        comment = str(item.get("comment") or "")
        name, reason = _decode_comment(comment, fallback_name=str(item.get("id") or "alertmanager silence"))
        return {
            "source_ref": str(item.get("id") or ""),
            "name": name,
            "starts_at": item.get("startsAt"),
            "ends_at": item.get("endsAt"),
            "created_by": item.get("createdBy"),
            "reason": reason,
            "status": str(status.get("state") or "active").strip().lower(),
            "matchers": [
                self._normalize_matcher(matcher)
                for matcher in item.get("matchers") or []
                if isinstance(matcher, dict)
            ],
            "source_payload": item,
        }

    @staticmethod
    def _normalize_matcher(matcher: JSONObject) -> JSONObject:
        is_regex = bool(matcher.get("isRegex"))
        is_equal = bool(matcher.get("isEqual", True))
        if is_regex and is_equal:
            operator = "regex"
        elif is_regex:
            operator = "nregex"
        elif is_equal:
            operator = "eq"
        else:
            operator = "neq"
        return {
            "label_key": str(matcher.get("name") or ""),
            "operator": operator,
            "value": str(matcher.get("value") or ""),
        }


def _alertmanager_unhealthy_status(status_code: int) -> str:
    if status_code in {401, 403}:
        return PLUGIN_RUN_STATE_FAILED
    return PLUGIN_RUN_STATE_DEGRADED


def _operation(ctx: ExecutionContext) -> str:
    params = ctx.service_exec_parameters if isinstance(ctx.service_exec_parameters, dict) else {}
    return str(params.get("operation") or "list_alerts").strip().lower()


def _payload(ctx: ExecutionContext) -> JSONObject:
    return {} if ctx.service_payload is None else ctx.service_payload


def _payload_contract_error(*, service_exec_id: str, message: str) -> ExecutionResult:
    outcome: JSONObject = {"success": False, "status": "errored", "message": message}
    return ExecutionResult(
        service_type=AlertmanagerExecutionAdapter.service_type,
        status="errored",
        service_exec_id=service_exec_id,
        service_exec_error=message,
        result=outcome,
        raw=outcome,
        retryable=False,
    )


def _decode_comment(comment: str, *, fallback_name: str) -> tuple[str, str | None]:
    stripped = comment.strip()
    if not stripped:
        return fallback_name, None
    if not stripped.startswith(POUNDCAKE_COMMENT_PREFIX):
        return stripped, comment or None
    lines = stripped.splitlines()
    name = lines[0][len(POUNDCAKE_COMMENT_PREFIX) :].strip() or fallback_name
    remainder = "\n".join(lines[1:]).strip()
    if remainder.startswith("---"):
        remainder = remainder[3:].strip()
    return name, remainder or None


def _encode_comment(name: str, reason: str | None) -> str:
    headline = f"{POUNDCAKE_COMMENT_PREFIX}{name.strip()}"
    detail = str(reason or "").strip()
    if not detail:
        return headline
    return f"{headline}\n---\n{detail}"


def _silence_matchers(payload: JSONObject) -> list[JSONObject]:
    raw = payload.get("matchers")
    if not isinstance(raw, list):
        return []
    matchers: list[JSONObject] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        operator = str(item.get("operator") or "eq").strip().lower()
        label_key = str(item.get("label_key") or "").strip()
        if not label_key:
            continue
        matchers.append(
            {
                "name": label_key,
                "value": "" if item.get("value") is None else str(item.get("value")),
                "isRegex": operator in {"regex", "nregex"},
                "isEqual": operator not in {"neq", "nregex", "not_exists"},
            }
        )
    return matchers


def _silence_write_payload(payload: JSONObject, *, silence_id: str | None = None) -> JSONObject:
    result: JSONObject = {
        "matchers": _silence_matchers(payload),
        "startsAt": str(payload.get("starts_at") or ""),
        "endsAt": str(payload.get("ends_at") or ""),
        "createdBy": str(payload.get("created_by") or "poundcake"),
        "comment": _encode_comment(
            str(payload.get("name") or "PoundCake suppression"),
            str(payload.get("reason") or "").strip() or None,
        ),
    }
    if silence_id:
        result["id"] = silence_id
    return result


def _silence_expire_payload(silence: JSONObject) -> JSONObject:
    now_dt = datetime.now(timezone.utc)
    ends_at_dt = now_dt + timedelta(seconds=2)
    starts_at_dt = _parse_alertmanager_datetime(silence.get("startsAt"))
    if starts_at_dt is None or starts_at_dt > ends_at_dt:
        starts_at_dt = now_dt
    return {
        "id": str(silence.get("id") or ""),
        "matchers": [item for item in silence.get("matchers") or [] if isinstance(item, dict)],
        "startsAt": starts_at_dt.isoformat(),
        "endsAt": ends_at_dt.isoformat(),
        "createdBy": str(silence.get("createdBy") or "poundcake"),
        "comment": str(silence.get("comment") or ""),
    }


def _parse_alertmanager_datetime(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _silence_id_from_response(response: httpx.Response, *, fallback: str = "") -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if isinstance(payload, dict):
        silence_id = str(payload.get("silenceID") or payload.get("silenceId") or payload.get("id") or "").strip()
        if silence_id:
            return silence_id
    if fallback:
        return fallback
    raise ValueError("Alertmanager silence write response did not include a silence id")


def _alert_query_params(
    ctx: ExecutionContext,
    *,
    include_muted: bool = False,
    include_matchers: bool = True,
) -> list[tuple[str, object]]:
    payload = _payload(ctx)
    params: list[tuple[str, object]] = []
    receiver = str(payload.get("receiver") or "").strip()
    if receiver:
        params.append(("receiver", receiver))
    for field in ("active", "silenced", "inhibited"):
        if isinstance(payload.get(field), bool):
            params.append((field, str(payload[field]).lower()))
    if include_muted:
        params.append(("muted", "true"))
    if include_matchers:
        for matcher in _matchers(payload):
            params.append(("filter", matcher))
    return params


def _matchers(payload: JSONObject) -> list[str]:
    raw_matchers = payload.get("matchers")
    if isinstance(raw_matchers, list):
        return [
            value for item in raw_matchers if (value := _clean_template_value(item)) is not None
        ]
    labels = payload.get("labels")
    if isinstance(labels, dict):
        return [
            f'{key}="{value}"'
            for key, value in sorted(labels.items())
            if str(key).strip() and _clean_template_value(value) is not None
        ]
    return []


def _limit(ctx: ExecutionContext) -> int:
    raw = _payload(ctx).get("limit")
    try:
        limit = int(raw or DEFAULT_INSPECT_LIMIT)
    except (TypeError, ValueError):
        limit = DEFAULT_INSPECT_LIMIT
    return max(1, min(MAX_INSPECT_LIMIT, limit))


def _object_or_empty(value: object) -> JSONObject:
    return dict(value) if isinstance(value, dict) else {}


def _clean_template_value(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or "{{" in text or "}}" in text:
        return None
    return text


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _normalize_alert_status(status: JSONObject) -> JSONObject:
    return {
        "state": str(status.get("state") or "unknown").strip().lower(),
        "silenced_by": _string_list(status.get("silencedBy")),
        "inhibited_by": _string_list(status.get("inhibitedBy")),
        "muted_by": _string_list(status.get("mutedBy")),
    }


def _suppression_summary(alerts: list[JSONObject]) -> JSONObject:
    silenced: set[str] = set()
    inhibited: set[str] = set()
    muted: set[str] = set()
    suppressed_count = 0
    for alert in alerts:
        suppression = alert.get("suppression") if isinstance(alert, dict) else {}
        if not isinstance(suppression, dict):
            continue
        silenced.update(_string_list(suppression.get("silenced_by")))
        inhibited.update(_string_list(suppression.get("inhibited_by")))
        muted.update(_string_list(suppression.get("muted_by")))
        if (
            str(suppression.get("state") or "").lower() == "suppressed"
            or suppression.get("silenced_by")
            or suppression.get("inhibited_by")
            or suppression.get("muted_by")
        ):
            suppressed_count += 1
    return {
        "suppressed_alert_count": suppressed_count,
        "silenced_by": sorted(silenced),
        "inhibited_by": sorted(inhibited),
        "muted_by": sorted(muted),
    }
