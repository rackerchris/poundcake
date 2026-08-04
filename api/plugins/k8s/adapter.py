"""Kubernetes execution adapter."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from api.core.config import get_settings
from api.plugins.base import ExecutionAdapter
from api.plugins.k8s.client import (
    KUBECONFIG_CREDENTIAL_TYPE,
    KubernetesClientConfig,
    KubernetesClientFactory,
)
from api.plugins.k8s.helper import KubernetesHelper
from api.plugins.types import ExecutionContext, ExecutionResult, PluginHealthResult
from api.types import JSONObject

K8S_SERVICE_EXECS = {
    "health_check",
    "prometheus_rule",
    "pod_action",
    "deployment_action",
    "workload_action",
    "workload_triage",
    "node_triage",
    "failed_job_cleanup",
    "resource_pressure_remediation",
    "service_probe",
}
SERVICE_PAYLOAD_OBJECT_ERROR = "service_payload must be an object when provided"
PROMETHEUS_RULE_OPERATIONS = {"get", "list", "apply", "delete"}
POD_ACTION_OPERATIONS = {"list", "get", "logs", "events", "delete"}
DEPLOYMENT_ACTION_OPERATIONS = {"get", "scale", "rollout_restart", "rollout_status"}
WORKLOAD_ACTION_OPERATIONS = {"get", "rollout_restart", "rollout_status"}
WORKLOAD_TRIAGE_OPERATIONS = {
    "pod_diagnostics",
    "workload_status",
    "logs",
    "events",
    "job_diagnostics",
    "node_diagnostics",
    "pvc_diagnostics",
    "service_diagnostics",
    "config_diagnostics",
    "pdb_hpa_diagnostics",
    "certificate_diagnostics",
}
NODE_TRIAGE_OPERATIONS = {
    "list_nodes",
    "node_diagnostics",
    "node_capacity",
    "node_pressure",
    "node_pods",
    "node_events",
}
FAILED_JOB_CLEANUP_OPERATIONS = {"delete"}
RESOURCE_PRESSURE_REMEDIATION_OPERATIONS = {"scale_deployment", "patch_hpa_bounds"}
SERVICE_PROBE_OPERATIONS = {"dns", "tcp", "http"}


@dataclass(frozen=True, slots=True)
class KubernetesOperatorConfig:
    """Non-secret operator configuration for the Kubernetes plugin."""

    namespace: str = ""
    capabilities_enabled: JSONObject | None = None
    capability_overrides: JSONObject | None = None


class KubernetesExecutionAdapter(ExecutionAdapter):
    """Expose Kubernetes operations through the order execution boundary."""

    service_type = "k8s"

    def __init__(
        self,
        helper: KubernetesHelper | None = None,
        *,
        operator_config: KubernetesOperatorConfig | None = None,
    ) -> None:
        self.helper = helper
        self._operator_config = operator_config

    def _resolve_operator_config(self) -> KubernetesOperatorConfig:
        if self._operator_config is None:
            normalized = self.normalize_operator_config(None)
            self._operator_config = KubernetesOperatorConfig(
                namespace=str(normalized["namespace"]),
                capabilities_enabled=dict(normalized.get("capabilities_enabled") or {}),
                capability_overrides=dict(normalized.get("capability_overrides") or {}),
            )
        return self._operator_config

    def _build_helper(self, *, credential_key_id: str = "default") -> KubernetesHelper:
        config = self._resolve_operator_config()
        settings = get_settings()
        return KubernetesHelper(
            client_factory=KubernetesClientFactory(
                config=KubernetesClientConfig(
                    namespace=config.namespace,
                    allow_local_kubeconfig=bool(
                        getattr(settings, "k8s_allow_local_kubeconfig", False)
                    ),
                    credential_key_id=credential_key_id.strip() or "default",
                )
            )
        )

    def _resolve_helper(self) -> KubernetesHelper:
        if self.helper is None:
            self.helper = self._build_helper()
        return self.helper

    def credential_requirements(self) -> list[JSONObject]:
        return [
            {
                "credential_type": KUBECONFIG_CREDENTIAL_TYPE,
                "credential_key_id": "default",
                "required": False,
                "usage": (
                    "Optional kubeconfig for Kubernetes API access; falls back to "
                    "in-cluster service account when absent."
                ),
            }
        ]

    def operator_config_schema(self) -> JSONObject:
        return {
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "title": "Namespace"},
                "capabilities_enabled": {
                    "type": "object",
                    "title": "Capability enablement overrides",
                    "additionalProperties": {"type": "boolean"},
                },
                "capability_overrides": {
                    "type": "object",
                    "title": "Capability payload overrides",
                    "additionalProperties": {"type": "object"},
                },
            },
            "required": [],
            "additionalProperties": False,
        }

    def default_operator_config(self) -> JSONObject:
        settings = get_settings()
        namespace = str(settings.prometheus_crd_namespace or "").strip()
        if not namespace:
            raise ValueError("Kubernetes namespace is required")
        return {
            "namespace": namespace,
            "capabilities_enabled": {},
            "capability_overrides": {},
        }

    def normalize_operator_config(self, config: JSONObject | None) -> JSONObject:
        raw = dict(config or {})
        settings = get_settings()
        namespace = str(raw.get("namespace") or settings.prometheus_crd_namespace or "").strip()
        if not namespace:
            raise ValueError("Kubernetes namespace is required")
        capabilities_enabled = raw.get("capabilities_enabled")
        capability_overrides = raw.get("capability_overrides")
        return {
            "namespace": namespace,
            "capabilities_enabled": (
                dict(capabilities_enabled) if isinstance(capabilities_enabled, dict) else {}
            ),
            "capability_overrides": (
                dict(capability_overrides) if isinstance(capability_overrides, dict) else {}
            ),
        }

    def with_operator_config(self, config: JSONObject | None) -> "KubernetesExecutionAdapter":
        normalized = self.normalize_operator_config(config)
        op_config = KubernetesOperatorConfig(
            namespace=str(normalized["namespace"]),
            capabilities_enabled=dict(normalized.get("capabilities_enabled") or {}),
            capability_overrides=dict(normalized.get("capability_overrides") or {}),
        )
        return KubernetesExecutionAdapter(operator_config=op_config)

    def validate_credential_payload(self, credential_type: str, payload: JSONObject) -> str | None:
        if credential_type != KUBECONFIG_CREDENTIAL_TYPE:
            return "Unsupported Kubernetes credential type"
        if isinstance(payload.get("kubeconfig"), (dict, str)):
            return None
        if str(payload.get("server") or "").strip() and str(payload.get("token") or "").strip():
            return None
        return "Kubernetes credential requires kubeconfig or server/token"

    def validate(self, ctx: ExecutionContext) -> str | None:
        service_exec = (ctx.service_exec or "").strip().lower()
        if service_exec not in K8S_SERVICE_EXECS:
            return f"Unsupported k8s service_exec: {ctx.service_exec}"
        if ctx.service_payload is not None and not isinstance(ctx.service_payload, dict):
            return SERVICE_PAYLOAD_OBJECT_ERROR
        if service_exec == "health_check":
            return None

        operation = _operation(ctx)
        payload = {} if ctx.service_payload is None else ctx.service_payload
        if service_exec == "prometheus_rule":
            return _validate_prometheus_rule(operation, payload)
        if service_exec == "pod_action":
            return _validate_pod_action(operation, payload)
        if service_exec == "deployment_action":
            return _validate_deployment_action(operation, payload)
        if service_exec == "workload_action":
            return _validate_workload_action(operation, payload)
        if service_exec == "workload_triage":
            return _validate_workload_triage(operation, payload)
        if service_exec == "node_triage":
            return _validate_node_triage(operation, payload)
        if service_exec == "failed_job_cleanup":
            return _validate_failed_job_cleanup(operation, payload)
        if service_exec == "resource_pressure_remediation":
            return _validate_resource_pressure_remediation(operation, payload)
        if service_exec == "service_probe":
            return _validate_service_probe(operation, payload)
        return f"Unsupported k8s service_exec: {ctx.service_exec}"

    def health_check(self) -> PluginHealthResult:
        return PluginHealthResult(
            service_type=self.service_type,
            status="healthy",
            message="Kubernetes plugin configured",
            details={
                "mode": "kubernetes-api",
                "credential_type": KUBECONFIG_CREDENTIAL_TYPE,
                "fallback": "in_cluster",
            },
        )

    async def test_connection(self, *, credential_key_id: str = "default") -> PluginHealthResult:
        try:
            result = await self._build_helper(credential_key_id=credential_key_id).health_check()
        except Exception as exc:  # noqa: BLE001
            return PluginHealthResult(
                service_type=self.service_type,
                status="failed",
                message="Kubernetes API connection test failed",
                error_code="k8s_connection_test_failed",
                details={"error": str(exc)},
            )
        connected = (result.get("capabilities") or {}).get("k8s.cluster.connect") == "healthy"
        return PluginHealthResult(
            service_type=self.service_type,
            status="healthy" if connected else "failed",
            message=(
                "Kubernetes API accepted the configured credential"
                if connected
                else "Kubernetes API connection test failed"
            ),
            error_code=None if connected else "k8s_connection_test_failed",
            details=result,
        )

    async def dispatch(self, ctx: ExecutionContext) -> ExecutionResult:
        service_exec = (ctx.service_exec or "").strip().lower()
        operation = _operation(ctx) if service_exec != "health_check" else service_exec
        if ctx.service_payload is not None and not isinstance(ctx.service_payload, dict):
            service_exec_id = _build_receipt(
                service_exec=service_exec,
                operation=operation,
                status="errored",
            )
            return _payload_contract_error(
                service_type=self.service_type,
                service_exec_id=service_exec_id,
                message=SERVICE_PAYLOAD_OBJECT_ERROR,
            )
        try:
            payload = {} if ctx.service_payload is None else ctx.service_payload
            result = await self._execute(service_exec, operation, payload)
            status = "succeeded" if result.get("success") is not False else "failed"
            service_exec_id = _build_receipt(
                service_exec=service_exec, operation=operation, status=status
            )
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
            result: JSONObject = {"success": False, "status": "errored", "message": str(exc)}
            service_exec_id = _build_receipt(
                service_exec=service_exec,
                operation=operation,
                status="errored",
            )
            return ExecutionResult(
                service_type=self.service_type,
                status="errored",
                service_exec_id=service_exec_id,
                service_exec_error=str(exc),
                result=result,
                raw=result,
                retryable=False,
            )

    async def poll(self, ctx: ExecutionContext, service_exec_id: str) -> ExecutionResult:
        del ctx
        service_exec = _service_exec_from_id(service_exec_id)
        if service_exec == "unknown":
            return ExecutionResult(
                service_type=self.service_type,
                status="errored",
                service_exec_id=service_exec_id,
                service_exec_error=f"Invalid k8s execution receipt: {service_exec_id}",
                result={"success": False, "status": "errored"},
                raw={"success": False, "status": "errored"},
                retryable=False,
            )

        status = _status_from_receipt(service_exec_id)
        operation = KubernetesExecutionAdapter._operation_from_receipt(service_exec_id)
        success = status == "succeeded"
        message = f"Kubernetes execution completed during dispatch with status={status}"
        result = {
            "success": success,
            "status": status,
            "service_exec": service_exec,
            "operation": operation,
            "message": message,
        }
        return ExecutionResult(
            service_type=self.service_type,
            status=status,
            service_exec_id=service_exec_id,
            service_exec_error=None if success else message,
            result=result,
            raw=result,
            retryable=False,
        )

    async def cancel(self, ctx: ExecutionContext, service_exec_id: str) -> ExecutionResult:
        del ctx
        service_exec = _service_exec_from_id(service_exec_id)
        operation = KubernetesExecutionAdapter._operation_from_receipt(service_exec_id)
        message = "Cancellation is not supported by the Kubernetes plugin"
        result = {
            "success": False,
            "status": "unsupported",
            "service_exec": service_exec,
            "operation": operation,
            "message": message,
        }
        return ExecutionResult(
            service_type=self.service_type,
            status="failed",
            service_exec_id=service_exec_id,
            service_exec_error=message,
            result=result,
            raw=result,
            retryable=False,
        )

    async def _execute(self, service_exec: str, operation: str, payload: JSONObject) -> JSONObject:
        helper = self._resolve_helper()
        if service_exec == "prometheus_rule":
            namespace = str(payload.get("namespace") or "").strip()
            if namespace:
                base_config = self._resolve_operator_config()
                helper = self.with_operator_config(
                    {
                        "namespace": namespace,
                        "capabilities_enabled": base_config.capabilities_enabled or {},
                        "capability_overrides": base_config.capability_overrides or {},
                    }
                )._resolve_helper()
        if operation == "health_check":
            return await helper.health_check()
        if service_exec == "prometheus_rule":
            if operation == "list":
                return {
                    "success": True,
                    "status": "succeeded",
                    "items": await helper.list_prometheus_rules(),
                }
            if operation == "get":
                rule = await helper.get_prometheus_rule(str(payload.get("crd_name") or "").strip())
                return {
                    "success": rule is not None,
                    "status": "succeeded" if rule is not None else "failed",
                    "item": rule,
                }
            if operation == "apply":
                result = await helper.create_or_update_rule(
                    rule_name=str(payload.get("rule_name") or "").strip(),
                    group_name=str(payload.get("group_name") or "").strip(),
                    crd_name=str(payload.get("crd_name") or "").strip(),
                    rule_data=dict(payload.get("rule_data") or {}),
                )
                result.setdefault("success", True)
                return result
            if operation == "delete":
                result = await helper.delete_rule(
                    rule_name=str(payload.get("rule_name") or "").strip(),
                    group_name=str(payload.get("group_name") or "").strip(),
                    crd_name=str(payload.get("crd_name") or "").strip(),
                )
                result.setdefault("success", result.get("status") != "error")
                return result
        if service_exec == "pod_action":
            namespace = str(payload.get("namespace") or "").strip()
            if operation == "list":
                return {
                    "success": True,
                    "status": "succeeded",
                    "items": await helper.list_pods(
                        namespace=namespace,
                        label_selector=str(payload.get("label_selector") or "").strip(),
                    ),
                }
            if operation == "get":
                pod = await helper.get_pod(
                    namespace=namespace,
                    pod_name=str(payload.get("pod_name") or "").strip(),
                )
                return {
                    "success": pod is not None,
                    "status": "succeeded" if pod is not None else "failed",
                    "item": pod,
                }
            if operation == "logs":
                return await helper.get_pod_logs(
                    namespace=namespace,
                    pod_name=str(payload.get("pod_name") or "").strip(),
                    label_selector=str(payload.get("label_selector") or "").strip(),
                    container=str(payload.get("container") or "").strip(),
                    tail_lines=_optional_int(payload.get("tail_lines")),
                    since_seconds=_optional_int(payload.get("since_seconds")),
                    previous=bool(payload.get("previous", False)),
                )
            if operation == "events":
                return {
                    "success": True,
                    "status": "succeeded",
                    "items": await helper.list_pod_events(
                        namespace=namespace,
                        pod_name=str(payload.get("pod_name") or "").strip(),
                    ),
                }
            if operation == "delete":
                return await helper.delete_pod(
                    namespace=namespace,
                    pod_name=str(payload.get("pod_name") or "").strip(),
                )
        if service_exec == "deployment_action":
            namespace = str(payload.get("namespace") or "").strip()
            deployment_name = str(payload.get("deployment_name") or "").strip()
            if operation == "get":
                deployment = await helper.get_deployment(
                    namespace=namespace,
                    deployment_name=deployment_name,
                )
                return {
                    "success": deployment is not None,
                    "status": "succeeded" if deployment is not None else "failed",
                    "item": deployment,
                }
            if operation == "scale":
                return await helper.scale_deployment(
                    namespace=namespace,
                    deployment_name=deployment_name,
                    replicas=int(payload.get("replicas") or 0),
                )
            if operation == "rollout_restart":
                return await helper.rollout_restart_deployment(
                    namespace=namespace,
                    deployment_name=deployment_name,
                )
            if operation == "rollout_status":
                return await helper.deployment_rollout_status(
                    namespace=namespace,
                    deployment_name=deployment_name,
                )
        if service_exec == "workload_action":
            namespace = str(payload.get("namespace") or "").strip()
            kind = str(payload.get("kind") or "").strip()
            name = str(payload.get("name") or "").strip()
            if operation == "get":
                return await helper.controller_status(namespace=namespace, kind=kind, name=name)
            if operation == "rollout_restart":
                return await helper.rollout_restart_controller(
                    namespace=namespace,
                    kind=kind,
                    name=name,
                )
            if operation == "rollout_status":
                return await helper.controller_rollout_status(
                    namespace=namespace,
                    kind=kind,
                    name=name,
                )
        if service_exec == "workload_triage":
            namespace = str(payload.get("namespace") or "").strip()
            if operation == "pod_diagnostics":
                return await helper.pod_diagnostics(
                    namespace=namespace,
                    pod_name=str(payload.get("pod_name") or payload.get("name") or "").strip(),
                    container=str(payload.get("container") or "").strip(),
                    tail_lines=_optional_int(payload.get("tail_lines")),
                    previous=bool(payload.get("previous", False)),
                )
            if operation == "workload_status":
                return await helper.workload_status(
                    namespace=namespace,
                    kind=str(payload.get("kind") or "").strip(),
                    name=str(payload.get("name") or "").strip(),
                )
            if operation == "logs":
                return await helper.workload_logs(
                    namespace=namespace,
                    kind=str(payload.get("kind") or "").strip(),
                    name=str(payload.get("name") or "").strip(),
                    pod_name=str(payload.get("pod_name") or "").strip(),
                    label_selector=str(payload.get("label_selector") or "").strip(),
                    container=str(payload.get("container") or "").strip(),
                    tail_lines=_optional_int(payload.get("tail_lines")),
                    since_seconds=_optional_int(payload.get("since_seconds")),
                    previous=bool(payload.get("previous", False)),
                    limit=_optional_int(payload.get("limit")),
                )
            if operation == "events":
                return await helper.workload_events(
                    namespace=namespace,
                    kind=str(payload.get("kind") or "").strip(),
                    name=str(payload.get("name") or "").strip(),
                    pod_name=str(payload.get("pod_name") or "").strip(),
                    label_selector=str(payload.get("label_selector") or "").strip(),
                    limit=_optional_int(payload.get("limit")),
                )
            if operation == "job_diagnostics":
                return await helper.job_diagnostics(
                    namespace=namespace,
                    kind=str(payload.get("kind") or "").strip(),
                    name=str(payload.get("name") or "").strip(),
                    tail_lines=_optional_int(payload.get("tail_lines")),
                    previous=bool(payload.get("previous", False)),
                    limit=_optional_int(payload.get("limit")),
                )
            if operation == "node_diagnostics":
                return await helper.node_diagnostics(
                    node_name=str(payload.get("node") or payload.get("name") or "").strip(),
                    limit=_optional_int(payload.get("limit")),
                )
            if operation == "pvc_diagnostics":
                return await helper.pvc_diagnostics(
                    namespace=namespace,
                    pvc_name=str(
                        payload.get("persistentvolumeclaim") or payload.get("name") or ""
                    ).strip(),
                    pv_name=str(payload.get("persistentvolume") or "").strip(),
                )
            if operation == "service_diagnostics":
                return await helper.service_diagnostics(
                    namespace=namespace,
                    service_name=str(payload.get("service") or payload.get("name") or "").strip(),
                )
            if operation == "config_diagnostics":
                return await helper.config_diagnostics(
                    namespace=namespace,
                    configmap_name=str(
                        payload.get("configmap") or payload.get("name") or ""
                    ).strip(),
                )
            if operation == "pdb_hpa_diagnostics":
                return await helper.pdb_hpa_diagnostics(
                    namespace=namespace,
                    pdb_name=str(payload.get("poddisruptionbudget") or "").strip(),
                    hpa_name=str(payload.get("horizontalpodautoscaler") or "").strip(),
                    name=str(payload.get("name") or "").strip(),
                )
            if operation == "certificate_diagnostics":
                return await helper.certificate_diagnostics(
                    namespace=namespace,
                    secret_name=str(payload.get("secret") or payload.get("name") or "").strip(),
                    label_selector=str(payload.get("label_selector") or "").strip(),
                    limit=_optional_int(payload.get("limit")),
                )
        if service_exec == "node_triage":
            if operation == "list_nodes":
                return {
                    "success": True,
                    "status": "succeeded",
                    "items": await helper.list_nodes(
                        label_selector=str(payload.get("label_selector") or "").strip(),
                        limit=_optional_int(payload.get("limit")),
                    ),
                }
            node_name = str(payload.get("node") or payload.get("name") or "").strip()
            if operation == "node_diagnostics":
                return await helper.node_diagnostics(
                    node_name=node_name,
                    limit=_optional_int(payload.get("limit")),
                )
            if operation == "node_capacity":
                return await helper.node_capacity(node_name=node_name)
            if operation == "node_pressure":
                return await helper.node_pressure(
                    node_name=node_name,
                    limit=_optional_int(payload.get("limit")),
                )
            if operation == "node_pods":
                return await helper.node_pods(
                    node_name=node_name,
                    namespace=str(payload.get("namespace") or "").strip(),
                    label_selector=str(payload.get("label_selector") or "").strip(),
                    include_succeeded=bool(payload.get("include_succeeded", False)),
                    limit=_optional_int(payload.get("limit")),
                )
            if operation == "node_events":
                return await helper.node_events(
                    node_name=node_name,
                    limit=_optional_int(payload.get("limit")),
                )
        if service_exec == "failed_job_cleanup":
            if operation == "delete":
                return await helper.cleanup_failed_job(
                    namespace=str(payload.get("namespace") or "").strip(),
                    job_name=str(payload.get("job_name") or payload.get("name") or "").strip(),
                )
        if service_exec == "resource_pressure_remediation":
            namespace = str(payload.get("namespace") or "").strip()
            if operation == "scale_deployment":
                return await helper.remediate_resource_pressure_with_deployment_scale(
                    namespace=namespace,
                    deployment_name=str(
                        payload.get("deployment_name") or payload.get("name") or ""
                    ).strip(),
                    replicas=int(payload.get("replicas") or 0),
                )
            if operation == "patch_hpa_bounds":
                return await helper.remediate_resource_pressure_with_hpa_patch(
                    namespace=namespace,
                    hpa_name=str(payload.get("hpa_name") or payload.get("name") or "").strip(),
                    min_replicas=_optional_int(payload.get("min_replicas")),
                    max_replicas=_optional_int(payload.get("max_replicas")),
                )
        if service_exec == "service_probe":
            return await helper.service_probe(
                namespace=str(payload.get("namespace") or "").strip(),
                service_name=str(payload.get("service") or payload.get("name") or "").strip(),
                port=payload.get("port"),
                operation=operation,
                path=str(payload.get("path") or "").strip(),
                scheme=str(payload.get("scheme") or "").strip(),
                timeout_seconds=_optional_int(payload.get("timeout_seconds")) or 5,
                expected_status_codes=_int_list(payload.get("expected_status_codes")),
            )
        raise ValueError(f"Unknown Kubernetes service execution: {service_exec}")

    @staticmethod
    def _operation_from_receipt(service_exec_id: str) -> str:
        parts = service_exec_id.split(":")
        if len(parts) >= 5 and parts[0] == "k8s" and parts[2]:
            return parts[2].strip().lower()
        if len(parts) >= 4 and parts[0] == "k8s" and parts[2]:
            return parts[2].strip().lower()
        if len(parts) == 3 and parts[0] == "k8s" and parts[1]:
            return parts[1].strip().lower()
        return "unknown"


def _service_exec_from_id(service_exec_id: str) -> str:
    parts = service_exec_id.split(":")
    if len(parts) >= 3 and parts[0] == "k8s" and parts[1]:
        return parts[1].strip().lower()
    return "unknown"


def _status_from_receipt(service_exec_id: str) -> str:
    parts = service_exec_id.split(":")
    if len(parts) >= 5 and parts[0] == "k8s":
        status = parts[3].strip().lower()
        if status in {"succeeded", "failed", "errored"}:
            return status
    return "succeeded"


def _build_receipt(*, service_exec: str, operation: str, status: str) -> str:
    return f"k8s:{service_exec}:{operation}:{status}:{uuid4()}"


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


def _validate_prometheus_rule(operation: str, payload: JSONObject) -> str | None:
    if operation not in PROMETHEUS_RULE_OPERATIONS:
        return "k8s prometheus_rule operation must be one of: apply, delete, get, list"

    if operation == "list":
        return None
    for key in ("rule_name", "group_name", "crd_name"):
        if not str(payload.get(key) or "").strip():
            return f"k8s prometheus_rule {operation} requires service_payload.{key}"
    if operation == "apply" and not isinstance(payload.get("rule_data"), dict):
        return "k8s prometheus_rule apply requires service_payload.rule_data"
    return None


def _validate_pod_action(operation: str, payload: JSONObject) -> str | None:
    if operation not in POD_ACTION_OPERATIONS:
        return "k8s pod_action operation must be one of: delete, events, get, list, logs"
    if not str(payload.get("namespace") or "").strip():
        return f"k8s pod_action {operation} requires service_payload.namespace"
    if operation in {"get", "events", "delete"} and not str(payload.get("pod_name") or "").strip():
        return f"k8s pod_action {operation} requires service_payload.pod_name"
    if operation == "logs" and not (
        str(payload.get("pod_name") or "").strip()
        or str(payload.get("label_selector") or "").strip()
    ):
        return "k8s pod_action logs requires service_payload.pod_name or service_payload.label_selector"
    return None


def _validate_deployment_action(operation: str, payload: JSONObject) -> str | None:
    if operation not in DEPLOYMENT_ACTION_OPERATIONS:
        return (
            "k8s deployment_action operation must be one of: "
            "get, rollout_restart, rollout_status, scale"
        )
    if not str(payload.get("namespace") or "").strip():
        return f"k8s deployment_action {operation} requires service_payload.namespace"
    if not str(payload.get("deployment_name") or "").strip():
        return f"k8s deployment_action {operation} requires service_payload.deployment_name"
    if operation == "scale" and not isinstance(payload.get("replicas"), int):
        return "k8s deployment_action scale requires service_payload.replicas"
    return None


def _validate_workload_action(operation: str, payload: JSONObject) -> str | None:
    if operation not in WORKLOAD_ACTION_OPERATIONS:
        return "k8s workload_action operation must be one of: get, rollout_restart, rollout_status"
    if not str(payload.get("namespace") or "").strip():
        return f"k8s workload_action {operation} requires service_payload.namespace"
    kind = str(payload.get("kind") or "").strip()
    if kind not in {"Deployment", "StatefulSet", "DaemonSet"}:
        return (
            f"k8s workload_action {operation} requires service_payload.kind "
            "to be one of: Deployment, StatefulSet, DaemonSet"
        )
    if not str(payload.get("name") or "").strip():
        return f"k8s workload_action {operation} requires service_payload.name"
    return None


def _validate_workload_triage(operation: str, payload: JSONObject) -> str | None:
    if operation not in WORKLOAD_TRIAGE_OPERATIONS:
        return (
            "k8s workload_triage operation must be one of: "
            "certificate_diagnostics, config_diagnostics, events, job_diagnostics, logs, node_diagnostics, "
            "pdb_hpa_diagnostics, "
            "pod_diagnostics, pvc_diagnostics, service_diagnostics, workload_status"
        )
    if operation == "node_diagnostics":
        if not str(payload.get("node") or payload.get("name") or "").strip():
            return "k8s workload_triage node_diagnostics requires service_payload.node or service_payload.name"
        return None
    if operation == "certificate_diagnostics":
        return None
    if operation == "pvc_diagnostics" and str(payload.get("persistentvolume") or "").strip():
        return None
    if not str(payload.get("namespace") or "").strip():
        return f"k8s workload_triage {operation} requires service_payload.namespace"
    if operation == "pod_diagnostics":
        if not str(payload.get("pod_name") or payload.get("name") or "").strip():
            return "k8s workload_triage pod_diagnostics requires service_payload.pod_name or service_payload.name"
        return None
    if operation in {"workload_status", "job_diagnostics"}:
        if not str(payload.get("kind") or "").strip():
            return f"k8s workload_triage {operation} requires service_payload.kind"
        if not str(payload.get("name") or "").strip():
            return f"k8s workload_triage {operation} requires service_payload.name"
        return None
    if operation in {"logs", "events"}:
        if not (
            str(payload.get("name") or "").strip()
            or str(payload.get("pod_name") or "").strip()
            or str(payload.get("label_selector") or "").strip()
        ):
            return (
                f"k8s workload_triage {operation} requires service_payload.name, "
                "service_payload.pod_name, or service_payload.label_selector"
            )
        if str(payload.get("name") or "").strip() and not str(payload.get("kind") or "").strip():
            return f"k8s workload_triage {operation} requires service_payload.kind when name is set"
        return None
    if operation == "pvc_diagnostics":
        if not str(
            payload.get("persistentvolumeclaim")
            or payload.get("persistentvolume")
            or payload.get("name")
            or ""
        ).strip():
            return (
                "k8s workload_triage pvc_diagnostics requires "
                "service_payload.persistentvolumeclaim, service_payload.persistentvolume, "
                "or service_payload.name"
            )
        return None
    if operation == "service_diagnostics":
        if not str(payload.get("service") or payload.get("name") or "").strip():
            return "k8s workload_triage service_diagnostics requires service_payload.service or service_payload.name"
        return None
    if operation == "config_diagnostics":
        if not str(payload.get("configmap") or payload.get("name") or "").strip():
            return "k8s workload_triage config_diagnostics requires service_payload.configmap or service_payload.name"
        return None
    if operation == "pdb_hpa_diagnostics":
        if not (
            str(payload.get("poddisruptionbudget") or "").strip()
            or str(payload.get("horizontalpodautoscaler") or "").strip()
            or str(payload.get("name") or "").strip()
        ):
            return (
                "k8s workload_triage pdb_hpa_diagnostics requires service_payload.name, "
                "service_payload.poddisruptionbudget, or service_payload.horizontalpodautoscaler"
            )
        return None
    return None


def _validate_node_triage(operation: str, payload: JSONObject) -> str | None:
    if operation not in NODE_TRIAGE_OPERATIONS:
        return (
            "k8s node_triage operation must be one of: "
            "list_nodes, node_capacity, node_diagnostics, node_events, node_pods, node_pressure"
        )
    if operation == "list_nodes":
        return None
    if not str(payload.get("node") or payload.get("name") or "").strip():
        return f"k8s node_triage {operation} requires service_payload.node or service_payload.name"
    return None


def _validate_failed_job_cleanup(operation: str, payload: JSONObject) -> str | None:
    if operation not in FAILED_JOB_CLEANUP_OPERATIONS:
        return "k8s failed_job_cleanup operation must be: delete"
    if not str(payload.get("namespace") or "").strip():
        return "k8s failed_job_cleanup delete requires service_payload.namespace"
    if not str(payload.get("job_name") or payload.get("name") or "").strip():
        return "k8s failed_job_cleanup delete requires service_payload.job_name"
    return None


def _validate_resource_pressure_remediation(operation: str, payload: JSONObject) -> str | None:
    if operation not in RESOURCE_PRESSURE_REMEDIATION_OPERATIONS:
        return (
            "k8s resource_pressure_remediation operation must be one of: "
            "patch_hpa_bounds, scale_deployment"
        )
    if not str(payload.get("namespace") or "").strip():
        return f"k8s resource_pressure_remediation {operation} requires service_payload.namespace"
    if operation == "scale_deployment":
        if not str(payload.get("deployment_name") or payload.get("name") or "").strip():
            return (
                "k8s resource_pressure_remediation scale_deployment requires "
                "service_payload.deployment_name"
            )
        if not isinstance(payload.get("replicas"), int):
            return (
                "k8s resource_pressure_remediation scale_deployment requires "
                "service_payload.replicas"
            )
        return None
    if not str(payload.get("hpa_name") or payload.get("name") or "").strip():
        return (
            "k8s resource_pressure_remediation patch_hpa_bounds requires service_payload.hpa_name"
        )
    min_replicas = payload.get("min_replicas")
    max_replicas = payload.get("max_replicas")
    if not isinstance(min_replicas, int) and not isinstance(max_replicas, int):
        return (
            "k8s resource_pressure_remediation patch_hpa_bounds requires "
            "service_payload.min_replicas or service_payload.max_replicas"
        )
    if isinstance(min_replicas, int) and min_replicas < 0:
        return (
            "k8s resource_pressure_remediation patch_hpa_bounds requires "
            "service_payload.min_replicas >= 0"
        )
    if isinstance(max_replicas, int) and max_replicas < 1:
        return (
            "k8s resource_pressure_remediation patch_hpa_bounds requires "
            "service_payload.max_replicas >= 1"
        )
    if (
        isinstance(min_replicas, int)
        and isinstance(max_replicas, int)
        and min_replicas > max_replicas
    ):
        return (
            "k8s resource_pressure_remediation patch_hpa_bounds requires "
            "service_payload.min_replicas <= service_payload.max_replicas"
        )
    return None


def _validate_service_probe(operation: str, payload: JSONObject) -> str | None:
    if operation not in SERVICE_PROBE_OPERATIONS:
        return "k8s service_probe operation must be one of: dns, http, tcp"
    if not str(payload.get("namespace") or "").strip():
        return f"k8s service_probe {operation} requires service_payload.namespace"
    if not str(payload.get("service") or payload.get("name") or "").strip():
        return f"k8s service_probe {operation} requires service_payload.service"
    if operation in {"tcp", "http"} and not _is_port_value(payload.get("port")):
        return f"k8s service_probe {operation} requires service_payload.port"
    if operation == "http" and payload.get("expected_status_codes") is not None:
        values = payload.get("expected_status_codes")
        if (
            not isinstance(values, list)
            or not values
            or not all(isinstance(item, int) for item in values)
        ):
            return (
                "k8s service_probe http requires service_payload.expected_status_codes "
                "to be a non-empty integer array when provided"
            )
    return None


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _int_list(value: object) -> list[int] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        return None
    items = [int(item) for item in value]
    return items or None


def _is_port_value(value: object) -> bool:
    if isinstance(value, int):
        return value > 0
    if isinstance(value, str) and value.strip():
        return True
    return False


def _operation(ctx: ExecutionContext) -> str:
    params = ctx.service_exec_parameters if isinstance(ctx.service_exec_parameters, dict) else {}
    return str(params.get("operation") or "").strip().lower()
