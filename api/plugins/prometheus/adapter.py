"""Prometheus execution adapter."""

from __future__ import annotations

from uuid import uuid4

from api.plugins.base import ExecutionAdapter
from api.plugins.state import (
    PLUGIN_CALLABLE_RUN_STATES,
    PLUGIN_RUN_STATE_FAILED,
    PLUGIN_RUN_STATE_HEALTHY,
)
from api.plugins.types import ExecutionContext, ExecutionResult, PluginHealthResult
from api.services.credential_manager import read_adapter_credential_payload
from api.services.prometheus_service import PrometheusClient
from api.types import JSONObject

PROMETHEUS_SERVICE_EXECS = {
    "health_check",
    "inspect",
    "reload_config",
    "watchdog",
}

PROMETHEUS_INSPECT_OPERATIONS = {
    "alert_evidence",
    "list_rules",
    "list_rule_groups",
    "list_metrics",
    "list_labels",
    "list_label_values",
}
PROMETHEUS_WATCHDOG_OPERATIONS = {
    "check_heartbeat",
}
PROMETHEUS_CREDENTIAL_TYPE = "prometheus_http_auth"
SERVICE_PAYLOAD_OBJECT_ERROR = "service_payload must be an object when provided"


class PrometheusExecutionAdapter(ExecutionAdapter):
    """Expose Prometheus operations through the order execution boundary."""

    service_type = "prometheus"

    def __init__(self, client: PrometheusClient | None = None) -> None:
        self.client = client or PrometheusClient()

    def credential_requirements(self) -> list[JSONObject]:
        return [
            {
                "credential_type": PROMETHEUS_CREDENTIAL_TYPE,
                "credential_key_id": "default",
                "required": False,
                "usage": "Optional Prometheus API credentials for authenticated monitoring endpoints.",
            }
        ]

    def operator_config_schema(self) -> JSONObject:
        return self.client.operator_config_schema()

    def default_operator_config(self) -> JSONObject:
        return self.client.default_operator_config()

    def normalize_operator_config(self, config: JSONObject | None) -> JSONObject:
        return self.client.normalize_operator_config(config)

    def with_operator_config(self, config: JSONObject | None) -> "PrometheusExecutionAdapter":
        return PrometheusExecutionAdapter(self.client.with_operator_config(config))

    def validate_credential_payload(self, credential_type: str, payload: JSONObject) -> str | None:
        if credential_type != PROMETHEUS_CREDENTIAL_TYPE:
            return "Unsupported Prometheus credential type"
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
        return "Prometheus credential requires bearer_token/token/api_key/access_token or username/password"

    def validate(self, ctx: ExecutionContext) -> str | None:
        service_exec = (ctx.service_exec or "").strip().lower()
        if service_exec not in PROMETHEUS_SERVICE_EXECS:
            return f"Unsupported prometheus service_exec: {ctx.service_exec}"
        if ctx.service_payload is not None and not isinstance(ctx.service_payload, dict):
            return SERVICE_PAYLOAD_OBJECT_ERROR
        if service_exec == "watchdog":
            operation = _operation(ctx)
            if not operation:
                return "prometheus watchdog requires service_exec_parameters.operation"
            if operation not in PROMETHEUS_WATCHDOG_OPERATIONS:
                return (
                    f"prometheus watchdog operation must be one of: "
                    f"{', '.join(sorted(PROMETHEUS_WATCHDOG_OPERATIONS))}"
                )
            return None
        transport_error = self.client.validate_transport_security()
        if transport_error:
            return transport_error
        operation = _operation(ctx)
        if service_exec == "inspect" and operation not in PROMETHEUS_INSPECT_OPERATIONS:
            return (
                "prometheus inspect operation must be one of: "
                "alert_evidence, list_label_values, list_labels, list_metrics, "
                "list_rule_groups, list_rules"
            )
        payload = {} if ctx.service_payload is None else ctx.service_payload
        if operation == "list_label_values" and not str(payload.get("label_name") or "").strip():
            return "prometheus list_label_values requires service_payload.label_name"
        if operation == "alert_evidence":
            if not str(payload.get("alert_name") or "").strip():
                return "prometheus alert_evidence requires service_payload.alert_name"
            if str(payload.get("query") or "").strip():
                return "prometheus alert_evidence does not accept service_payload.query"
        return None

    def health_check(self) -> PluginHealthResult:
        return PluginHealthResult(
            service_type=self.service_type,
            status=PLUGIN_RUN_STATE_HEALTHY,
            message="Prometheus plugin configured",
            details={
                "mode": "prometheus-api",
                "url": self.client.base_url,
                "verify_ssl": self.client.verify_ssl,
                "auth_mode": self.client.auth_mode,
                "secure_transport": self.client.secure_transport,
            },
        )

    async def test_connection(self, *, credential_key_id: str = "default") -> PluginHealthResult:
        client = await self._client_with_credentials(credential_key_id=credential_key_id)
        health = await client.health_check()
        status = str(health.get("status") or PLUGIN_RUN_STATE_FAILED)
        latency = health.get("latency_ms")
        return PluginHealthResult(
            service_type=self.service_type,
            status=status,
            message=(
                "Prometheus API reachable"
                if status == PLUGIN_RUN_STATE_HEALTHY
                else "Prometheus API health check failed"
            ),
            error_code=str(health.get("status_code") or health.get("error") or "") or None,
            latency_ms=int(latency) if isinstance(latency, (int, float)) else None,
            details={
                "mode": "prometheus-api",
                "url": client.base_url,
                "verify_ssl": client.verify_ssl,
                "auth_mode": client.auth_mode,
                "secure_transport": client.secure_transport,
                **health,
            },
        )

    async def dispatch(self, ctx: ExecutionContext) -> ExecutionResult:
        service_exec = (ctx.service_exec or "").strip().lower()

        if service_exec == "watchdog":
            watchdog_operation = _operation(ctx)
            service_exec_id = f"prometheus:watchdog:{watchdog_operation}:{uuid4()}"
            if ctx.service_payload is not None and not isinstance(ctx.service_payload, dict):
                return _payload_contract_error(
                    service_type=self.service_type,
                    service_exec_id=service_exec_id,
                    message=SERVICE_PAYLOAD_OBJECT_ERROR,
                )
            try:
                result = await self._execute_watchdog(watchdog_operation, _payload(ctx), ctx.req_id)
                status = self._status_from_result(result)
                return ExecutionResult(
                    service_type=self.service_type,
                    status=status,
                    service_exec_id=service_exec_id,
                    service_exec_error=(
                        None if status == "succeeded" else str(result.get("message") or "")
                    ),
                    result=result,
                    raw=result,
                    retryable=False,
                )
            except Exception as exc:  # noqa: BLE001
                outcome: JSONObject = {
                    "success": False,
                    "status": "errored",
                    "message": str(exc),
                }
                return ExecutionResult(
                    service_type=self.service_type,
                    status="errored",
                    service_exec_id=service_exec_id,
                    service_exec_error=str(exc),
                    result=outcome,
                    raw=outcome,
                    retryable=False,
                )

        operation = _operation(ctx) if service_exec == "inspect" else service_exec
        service_exec_id = f"prometheus:{operation}:{uuid4()}"
        if ctx.service_payload is not None and not isinstance(ctx.service_payload, dict):
            return _payload_contract_error(
                service_type=self.service_type,
                service_exec_id=service_exec_id,
                message=SERVICE_PAYLOAD_OBJECT_ERROR,
            )
        client = await self._client_with_credentials()
        try:
            result = await self._execute(client, operation, _payload(ctx))
            status = self._status_from_result(result)
            return ExecutionResult(
                service_type=self.service_type,
                status=status,
                service_exec_id=service_exec_id,
                service_exec_error=(
                    None if status == "succeeded" else str(result.get("message") or "")
                ),
                result=result,
                raw=result,
                retryable=False,
            )
        except Exception as exc:  # noqa: BLE001
            outcome: JSONObject = {"success": False, "status": "errored", "message": str(exc)}
            return ExecutionResult(
                service_type=self.service_type,
                status="errored",
                service_exec_id=service_exec_id,
                service_exec_error=str(exc),
                result=outcome,
                raw=outcome,
                retryable=False,
            )

    async def _execute_watchdog(
        self,
        operation: str,
        payload: JSONObject,
        req_id: str,
    ) -> JSONObject:
        from api.services.adapter_runtime import check_prometheus_watchdog_heartbeat_once

        if operation == "check_heartbeat":
            result = await check_prometheus_watchdog_heartbeat_once()
            return {**result, "success": True, "status": "succeeded"}
        raise ValueError(f"Unsupported watchdog operation: {operation}")

    async def poll(self, ctx: ExecutionContext, service_exec_id: str) -> ExecutionResult:
        # All prometheus operations complete synchronously during dispatch.
        # Return a successful replay of the dispatch result since no async state
        # exists to observe. This is a read-only confirmation, not a mutation.
        message = "Prometheus executions complete during dispatch; no pollable state exists"
        return ExecutionResult(
            service_type=self.service_type,
            status="succeeded",
            service_exec_id=service_exec_id,
            service_exec_error=message,
            result={"success": True, "status": "succeeded", "message": message},
            raw={"success": True, "status": "succeeded", "message": message},
            retryable=False,
        )

    async def _client_with_credentials(
        self,
        credential_key_id: str = "default",
    ) -> PrometheusClient:
        try:
            credential = await read_adapter_credential_payload(
                service_type=self.service_type,
                credential_type=PROMETHEUS_CREDENTIAL_TYPE,
                credential_key_id=credential_key_id,
            )
        except Exception:
            credential = None
        if hasattr(self.client, "with_credentials"):
            return self.client.with_credentials(credential)
        return self.client

    async def _execute(
        self,
        client: PrometheusClient,
        service_exec: str,
        payload: JSONObject,
    ) -> JSONObject:
        if service_exec == "health_check":
            health = await client.health_check()
            return {
                "success": health.get("status") in PLUGIN_CALLABLE_RUN_STATES,
                **health,
            }
        if service_exec == "list_rules":
            return {"success": True, "status": "succeeded", "rules": await client.get_rules()}
        if service_exec == "list_rule_groups":
            return {
                "success": True,
                "status": "succeeded",
                "groups": await client.get_rule_groups(),
            }
        if service_exec == "list_metrics":
            return {
                "success": True,
                "status": "succeeded",
                "metrics": await client.get_metric_names(),
            }
        if service_exec == "list_labels":
            return {
                "success": True,
                "status": "succeeded",
                "labels": await client.get_label_names(metric=_optional_str(payload.get("metric"))),
            }
        if service_exec == "list_label_values":
            label_name = str(payload.get("label_name") or "")
            return {
                "success": True,
                "status": "succeeded",
                "label": label_name,
                "values": await client.get_label_values(
                    label_name,
                    metric=_optional_str(payload.get("metric")),
                ),
            }
        if service_exec == "alert_evidence":
            labels = payload.get("labels") if isinstance(payload.get("labels"), dict) else {}
            result = await client.alert_evidence(
                alert_name=str(payload.get("alert_name") or ""),
                labels=labels,
                lookback_seconds=_optional_int(payload.get("lookback_seconds"), default=3600),
                step_seconds=_optional_int(payload.get("step_seconds"), default=60),
            )
            return {"success": True, "status": "succeeded", "evidence": result}
        if service_exec == "reload_config":
            result = await client.reload_config()
            status = str(result.get("status") or "").strip().lower()
            return {
                "success": status in {"success", "succeeded"},
                **result,
            }
        raise ValueError(f"Unknown Prometheus receipt operation: {service_exec}")

    @staticmethod
    def _status_from_result(result: JSONObject) -> str:
        if result.get("success") is False:
            return "failed"
        status = str(result.get("status") or "").strip().lower()
        if status in {"error", PLUGIN_RUN_STATE_FAILED}:
            return "failed"
        return "succeeded"


def _optional_str(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _payload(ctx: ExecutionContext) -> JSONObject:
    return {} if ctx.service_payload is None else ctx.service_payload


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


def _optional_int(value: object, *, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return default


def _operation(ctx: ExecutionContext) -> str:
    params = ctx.service_exec_parameters if isinstance(ctx.service_exec_parameters, dict) else {}
    return str(params.get("operation") or "").strip().lower()
