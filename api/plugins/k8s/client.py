"""Credential-aware Kubernetes API helpers for service plugins."""

from __future__ import annotations

import importlib
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from api.core.logging import get_logger
from api.services.credential_manager import (
    ServicePluginCredentialError,
    read_adapter_credential_payload,
)
from api.types import JSONObject

logger = get_logger(__name__)

K8S_SERVICE_TYPE = "k8s"
KUBECONFIG_CREDENTIAL_TYPE = "kubernetes_kubeconfig"


@dataclass(frozen=True, slots=True)
class KubernetesClientBundle:
    """Initialized Kubernetes API clients and non-secret connection metadata."""

    api_client: Any
    core_api: Any
    apps_api: Any
    autoscaling_api: Any
    batch_api: Any
    discovery_api: Any
    policy_api: Any
    custom_api: Any
    auth_mode: str
    namespace: str
    host: str | None = None


@dataclass(frozen=True, slots=True)
class KubernetesClientConfig:
    """Runtime connection settings supplied by the adapter/helper layer."""

    namespace: str
    allow_local_kubeconfig: bool = False
    credential_key_id: str = "default"


class KubernetesClientFactory:
    """Build Kubernetes clients from adapter credentials, in-cluster auth, or dev kubeconfig."""

    def __init__(self, *, config: KubernetesClientConfig) -> None:
        self.config = config

    async def build(self) -> KubernetesClientBundle:
        k8s_client_module, k8s_config_module = self._import_kubernetes()

        credential = await self._load_kubeconfig_credential()
        if credential is not None:
            return self._from_credential(k8s_client_module, k8s_config_module, credential)

        try:
            k8s_config_module.load_incluster_config()
            api_client = k8s_client_module.ApiClient()
            logger.info("Loaded in-cluster Kubernetes config")
            return KubernetesClientBundle(
                api_client=api_client,
                core_api=k8s_client_module.CoreV1Api(api_client),
                apps_api=k8s_client_module.AppsV1Api(api_client),
                autoscaling_api=k8s_client_module.AutoscalingV1Api(api_client),
                batch_api=k8s_client_module.BatchV1Api(api_client),
                discovery_api=k8s_client_module.DiscoveryV1Api(api_client),
                policy_api=k8s_client_module.PolicyV1Api(api_client),
                custom_api=k8s_client_module.CustomObjectsApi(api_client),
                auth_mode="in_cluster",
                namespace=self.config.namespace,
                host=self._api_host(api_client),
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("In-cluster Kubernetes config unavailable", extra={"error": str(exc)})

        if self.config.allow_local_kubeconfig:
            k8s_config_module.load_kube_config()
            api_client = k8s_client_module.ApiClient()
            logger.info("Loaded local Kubernetes kubeconfig")
            return KubernetesClientBundle(
                api_client=api_client,
                core_api=k8s_client_module.CoreV1Api(api_client),
                apps_api=k8s_client_module.AppsV1Api(api_client),
                autoscaling_api=k8s_client_module.AutoscalingV1Api(api_client),
                batch_api=k8s_client_module.BatchV1Api(api_client),
                discovery_api=k8s_client_module.DiscoveryV1Api(api_client),
                policy_api=k8s_client_module.PolicyV1Api(api_client),
                custom_api=k8s_client_module.CustomObjectsApi(api_client),
                auth_mode="local_kubeconfig",
                namespace=self.config.namespace,
                host=self._api_host(api_client),
            )

        raise RuntimeError(
            "Kubernetes client not configured; store a kubernetes_kubeconfig credential "
            "for service_type=k8s or run in a Kubernetes cluster"
        )

    def _import_kubernetes(self) -> tuple[Any, Any]:
        try:
            return (
                importlib.import_module("kubernetes.client"),
                importlib.import_module("kubernetes.config"),
            )
        except ImportError as exc:
            raise RuntimeError("kubernetes package is not installed") from exc

    async def _load_kubeconfig_credential(self) -> JSONObject | None:
        try:
            return await read_adapter_credential_payload(
                service_type=K8S_SERVICE_TYPE,
                credential_type=KUBECONFIG_CREDENTIAL_TYPE,
                credential_key_id=self.config.credential_key_id,
            )
        except ServicePluginCredentialError as exc:
            logger.info("Kubernetes adapter credential unavailable", extra={"error": str(exc)})
            return None

    def _from_credential(
        self,
        k8s_client_module: Any,
        k8s_config_module: Any,
        credential: JSONObject,
    ) -> KubernetesClientBundle:
        config_dict = _kubeconfig_dict(credential)
        namespace = (
            str(credential.get("namespace") or self.config.namespace).strip()
            or self.config.namespace
        )
        current_context = str(credential.get("context") or "").strip() or None
        k8s_config_module.load_kube_config_from_dict(
            config_dict=config_dict,
            context=current_context,
        )
        api_client = k8s_client_module.ApiClient()
        logger.info("Loaded Kubernetes config from adapter credential")
        return KubernetesClientBundle(
            api_client=api_client,
            core_api=k8s_client_module.CoreV1Api(api_client),
            apps_api=k8s_client_module.AppsV1Api(api_client),
            autoscaling_api=k8s_client_module.AutoscalingV1Api(api_client),
            batch_api=k8s_client_module.BatchV1Api(api_client),
            discovery_api=k8s_client_module.DiscoveryV1Api(api_client),
            policy_api=k8s_client_module.PolicyV1Api(api_client),
            custom_api=k8s_client_module.CustomObjectsApi(api_client),
            auth_mode="adapter_credentials",
            namespace=namespace,
            host=self._api_host(api_client),
        )

    @staticmethod
    def _api_host(api_client: Any) -> str | None:
        configuration = getattr(api_client, "configuration", None)
        host = getattr(configuration, "host", None)
        return str(host) if host else None


def _kubeconfig_dict(credential: JSONObject) -> JSONObject:
    raw = credential.get("kubeconfig")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        parsed = yaml.safe_load(raw)
        if not isinstance(parsed, dict):
            raise RuntimeError("kubernetes_kubeconfig credential kubeconfig must be an object")
        return parsed

    server = str(credential.get("server") or "").strip()
    token = str(credential.get("token") or "").strip()
    ca_data = str(credential.get("certificate_authority_data") or "").strip()
    if server and token:
        cluster: JSONObject = {"server": server}
        if ca_data:
            cluster["certificate-authority-data"] = ca_data
        return {
            "apiVersion": "v1",
            "kind": "Config",
            "clusters": [{"name": "poundcake", "cluster": cluster}],
            "users": [{"name": "poundcake", "user": {"token": token}}],
            "contexts": [
                {
                    "name": "poundcake",
                    "context": {
                        "cluster": "poundcake",
                        "user": "poundcake",
                        "namespace": str(credential.get("namespace") or "").strip() or "default",
                    },
                }
            ],
            "current-context": "poundcake",
        }
    raise RuntimeError(
        "kubernetes_kubeconfig credential requires kubeconfig or server/token fields"
    )


def safe_kubeconfig_tempfile(kubeconfig: str) -> str:
    """Write kubeconfig to a temporary file for libraries requiring a path."""
    handle = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    with handle:
        handle.write(kubeconfig)
    return str(Path(handle.name))
