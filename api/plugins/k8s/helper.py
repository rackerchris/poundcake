"""Kubernetes helper capabilities used by service plugins."""

from __future__ import annotations

import base64
import hashlib
import http.client
import socket
from datetime import UTC, datetime
from typing import Any

from cryptography import x509
from cryptography.hazmat.backends import default_backend

from api.core.config import get_settings
from api.plugins.k8s.client import KubernetesClientConfig, KubernetesClientFactory
from api.services.alert_rule_repo import (
    AlertRuleSource,
    dump_alert_rule_sources_to_annotations,
    load_alert_rule_sources_from_annotations,
)
from api.types import JSONObject


class KubernetesHelper:
    """Read and manage the Kubernetes resources exposed by the k8s adapter."""

    group = "monitoring.coreos.com"
    version = "v1"
    plural = "prometheusrules"

    def __init__(self, *, client_factory: KubernetesClientFactory | None = None) -> None:
        self.client_factory = client_factory or _default_client_factory()

    async def health_check(self) -> JSONObject:
        bundle = await self.client_factory.build()
        version = _kubernetes_api_version(bundle.api_client)
        capabilities: JSONObject = {"k8s.cluster.connect": "healthy"}
        try:
            bundle.custom_api.list_namespaced_custom_object(
                group=self.group,
                version=self.version,
                namespace=bundle.namespace,
                plural=self.plural,
                limit=1,
            )
            capabilities["k8s.prometheusrules.manage"] = "healthy"
        except Exception as exc:  # noqa: BLE001
            if _is_missing_prometheus_rule_crd_error(exc):
                capabilities["k8s.prometheusrules.manage"] = "missing_crd"
            else:
                capabilities["k8s.prometheusrules.manage"] = "unavailable"
        return {
            "success": True,
            "status": "healthy",
            "auth_mode": bundle.auth_mode,
            "namespace": bundle.namespace,
            "host": bundle.host,
            "version": version,
            "capabilities": capabilities,
        }

    async def list_prometheus_rules(self) -> list[JSONObject]:
        bundle = await self.client_factory.build()
        response = bundle.custom_api.list_namespaced_custom_object(
            group=self.group,
            version=self.version,
            namespace=bundle.namespace,
            plural=self.plural,
        )
        items = response.get("items", [])
        return items if isinstance(items, list) else []

    async def get_prometheus_rule(self, name: str) -> JSONObject | None:
        bundle = await self.client_factory.build()
        try:
            rule = bundle.custom_api.get_namespaced_custom_object(
                group=self.group,
                version=self.version,
                namespace=bundle.namespace,
                plural=self.plural,
                name=name,
            )
            return rule if isinstance(rule, dict) else None
        except Exception:
            return None

    async def get_rule_from_crd(
        self,
        *,
        crd_name: str,
        group_name: str,
        rule_name: str,
    ) -> JSONObject | None:
        crd = await self.get_prometheus_rule(crd_name)
        if crd is None:
            return None
        return _find_rule_in_crd(crd, group_name=group_name, rule_name=rule_name)

    async def update_rule_in_named_crd(
        self,
        *,
        crd_name: str,
        group_name: str,
        rule_name: str,
        rule_data: JSONObject,
        source_metadata: AlertRuleSource | None = None,
    ) -> JSONObject:
        existing = await self.get_prometheus_rule(crd_name)
        if existing is None:
            return {"status": "error", "message": f"PrometheusRule CRD '{crd_name}' not found"}
        if _find_rule_in_crd(existing, group_name=group_name, rule_name=rule_name) is None:
            return {
                "status": "error",
                "message": (
                    f"Rule '{rule_name}' not found in group '{group_name}' within CRD '{crd_name}'"
                ),
            }
        return await self._update_rule_in_crd(
            existing,
            rule_name=rule_name,
            group_name=group_name,
            rule_data=rule_data,
            source_metadata=source_metadata,
        )

    async def add_rule_to_named_crd(
        self,
        *,
        crd_name: str,
        group_name: str,
        rule_name: str,
        rule_data: JSONObject,
        source_metadata: AlertRuleSource | None = None,
    ) -> JSONObject:
        existing = await self.get_prometheus_rule(crd_name)
        if existing is None:
            return {"status": "error", "message": f"PrometheusRule CRD '{crd_name}' not found"}
        if _find_rule_in_crd(existing, group_name=group_name, rule_name=rule_name) is not None:
            return {
                "status": "error",
                "message": (
                    f"Rule '{rule_name}' already exists in group '{group_name}' within CRD '{crd_name}'"
                ),
            }
        return await self._update_rule_in_crd(
            existing,
            rule_name=rule_name,
            group_name=group_name,
            rule_data=rule_data,
            source_metadata=source_metadata,
        )

    async def create_or_update_rule(
        self,
        *,
        rule_name: str,
        group_name: str,
        crd_name: str,
        rule_data: JSONObject,
        source_metadata: AlertRuleSource | None = None,
    ) -> JSONObject:
        existing = await self.find_crd_containing_rule(rule_name, group_name)
        if existing is not None:
            return await self._update_rule_in_crd(
                existing,
                rule_name=rule_name,
                group_name=group_name,
                rule_data=rule_data,
                source_metadata=source_metadata,
            )
        crd_by_name = await self.get_prometheus_rule(crd_name)
        if crd_by_name is not None:
            return await self._update_rule_in_crd(
                crd_by_name,
                rule_name=rule_name,
                group_name=group_name,
                rule_data=rule_data,
                source_metadata=source_metadata,
            )
        return await self._create_rule_crd(
            crd_name=crd_name,
            group_name=group_name,
            rule_name=rule_name,
            rule_data=rule_data,
            source_metadata=source_metadata,
        )

    async def find_crd_containing_rule(
        self,
        rule_name: str,
        group_name: str,
    ) -> JSONObject | None:
        for crd in await self.list_prometheus_rules():
            if _find_rule_in_crd(crd, group_name=group_name, rule_name=rule_name) is not None:
                return crd
        return None

    async def list_pods(self, *, namespace: str, label_selector: str = "") -> list[JSONObject]:
        bundle = await self.client_factory.build()
        response = bundle.core_api.list_namespaced_pod(
            namespace=namespace,
            label_selector=label_selector or None,
        )
        return [_serialize(bundle.api_client, item) for item in getattr(response, "items", [])]

    async def get_pod(self, *, namespace: str, pod_name: str) -> JSONObject | None:
        bundle = await self.client_factory.build()
        try:
            pod = bundle.core_api.read_namespaced_pod(name=pod_name, namespace=namespace)
        except Exception:
            return None
        return _serialize(bundle.api_client, pod)

    async def get_pod_logs(
        self,
        *,
        namespace: str,
        pod_name: str,
        label_selector: str = "",
        container: str = "",
        tail_lines: int | None = None,
        since_seconds: int | None = None,
        previous: bool = False,
    ) -> JSONObject:
        target_pod = pod_name
        if not target_pod and label_selector:
            pods = await self.list_pods(namespace=namespace, label_selector=label_selector)
            target_pod = str(((pods[0].get("metadata") or {}) if pods else {}).get("name") or "")
        if not target_pod:
            return {"success": False, "status": "failed", "message": "Pod not found"}

        bundle = await self.client_factory.build()
        logs = bundle.core_api.read_namespaced_pod_log(
            name=target_pod,
            namespace=namespace,
            container=container or None,
            tail_lines=tail_lines,
            since_seconds=since_seconds,
            previous=previous,
        )
        return {
            "success": True,
            "status": "succeeded",
            "pod_name": target_pod,
            "namespace": namespace,
            "logs": str(logs or ""),
        }

    async def list_pod_events(self, *, namespace: str, pod_name: str) -> list[JSONObject]:
        bundle = await self.client_factory.build()
        response = bundle.core_api.list_namespaced_event(
            namespace=namespace,
            field_selector=f"involvedObject.name={pod_name}",
        )
        return [_serialize(bundle.api_client, item) for item in getattr(response, "items", [])]

    async def delete_pod(self, *, namespace: str, pod_name: str) -> JSONObject:
        bundle = await self.client_factory.build()
        bundle.core_api.delete_namespaced_pod(name=pod_name, namespace=namespace)
        return {
            "success": True,
            "status": "succeeded",
            "action": "deleted",
            "pod_name": pod_name,
            "namespace": namespace,
        }

    async def get_deployment(self, *, namespace: str, deployment_name: str) -> JSONObject | None:
        bundle = await self.client_factory.build()
        try:
            deployment = bundle.apps_api.read_namespaced_deployment(
                name=deployment_name,
                namespace=namespace,
            )
        except Exception:
            return None
        return _serialize(bundle.api_client, deployment)

    async def scale_deployment(
        self,
        *,
        namespace: str,
        deployment_name: str,
        replicas: int,
    ) -> JSONObject:
        bundle = await self.client_factory.build()
        body = {"spec": {"replicas": replicas}}
        deployment = bundle.apps_api.patch_namespaced_deployment_scale(
            name=deployment_name,
            namespace=namespace,
            body=body,
        )
        return {
            "success": True,
            "status": "succeeded",
            "action": "scaled",
            "deployment_name": deployment_name,
            "namespace": namespace,
            "replicas": replicas,
            "item": _serialize(bundle.api_client, deployment),
        }

    async def rollout_restart_deployment(
        self,
        *,
        namespace: str,
        deployment_name: str,
    ) -> JSONObject:
        bundle = await self.client_factory.build()
        restarted_at = datetime.now(UTC).isoformat()
        body = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "kubectl.kubernetes.io/restartedAt": restarted_at,
                        }
                    }
                }
            }
        }
        deployment = bundle.apps_api.patch_namespaced_deployment(
            name=deployment_name,
            namespace=namespace,
            body=body,
        )
        return {
            "success": True,
            "status": "succeeded",
            "action": "rollout_restarted",
            "deployment_name": deployment_name,
            "namespace": namespace,
            "restarted_at": restarted_at,
            "item": _serialize(bundle.api_client, deployment),
        }

    async def deployment_rollout_status(
        self,
        *,
        namespace: str,
        deployment_name: str,
    ) -> JSONObject:
        deployment = await self.get_deployment(
            namespace=namespace,
            deployment_name=deployment_name,
        )
        if deployment is None:
            return {
                "success": False,
                "status": "failed",
                "message": "Deployment not found",
                "deployment_name": deployment_name,
                "namespace": namespace,
            }
        status = deployment.get("status") if isinstance(deployment.get("status"), dict) else {}
        spec = deployment.get("spec") if isinstance(deployment.get("spec"), dict) else {}
        desired = int(spec.get("replicas") or 0)
        updated = int(status.get("updatedReplicas") or status.get("updated_replicas") or 0)
        available = int(status.get("availableReplicas") or status.get("available_replicas") or 0)
        ready = desired == updated == available
        return {
            "success": ready,
            "status": "succeeded" if ready else "running",
            "deployment_name": deployment_name,
            "namespace": namespace,
            "desired_replicas": desired,
            "updated_replicas": updated,
            "available_replicas": available,
            "item": deployment,
        }

    async def controller_status(self, *, namespace: str, kind: str, name: str) -> JSONObject:
        normalized = _normalize_controller_kind(kind)
        workload = await self.get_workload(namespace=namespace, kind=normalized, name=name)
        if workload is None:
            return {
                "success": False,
                "status": "failed",
                "message": f"{normalized} not found",
                "namespace": namespace,
                "kind": normalized,
                "name": name,
            }
        return {
            "success": True,
            "status": "succeeded",
            "namespace": namespace,
            "kind": normalized,
            "name": name,
            "item": workload,
            "rollout": _controller_rollout_summary(normalized, workload),
        }

    async def rollout_restart_controller(
        self,
        *,
        namespace: str,
        kind: str,
        name: str,
    ) -> JSONObject:
        bundle = await self.client_factory.build()
        normalized = _normalize_controller_kind(kind)
        restarted_at = datetime.now(UTC).isoformat()
        body = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "kubectl.kubernetes.io/restartedAt": restarted_at,
                        }
                    }
                }
            }
        }
        patchers: dict[str, Any] = {
            "Deployment": lambda: bundle.apps_api.patch_namespaced_deployment(
                name=name,
                namespace=namespace,
                body=body,
            ),
            "StatefulSet": lambda: bundle.apps_api.patch_namespaced_stateful_set(
                name=name,
                namespace=namespace,
                body=body,
            ),
            "DaemonSet": lambda: bundle.apps_api.patch_namespaced_daemon_set(
                name=name,
                namespace=namespace,
                body=body,
            ),
        }
        item = patchers[normalized]()
        return {
            "success": True,
            "status": "succeeded",
            "action": "rollout_restarted",
            "namespace": namespace,
            "kind": normalized,
            "name": name,
            "restarted_at": restarted_at,
            "item": _serialize(bundle.api_client, item),
        }

    async def controller_rollout_status(
        self,
        *,
        namespace: str,
        kind: str,
        name: str,
    ) -> JSONObject:
        status = await self.controller_status(namespace=namespace, kind=kind, name=name)
        if status.get("success") is False:
            return status
        rollout = status.get("rollout") if isinstance(status.get("rollout"), dict) else {}
        ready = bool(rollout.get("ready") is True)
        return {
            **status,
            "success": ready,
            "status": "succeeded" if ready else "running",
        }

    async def pod_diagnostics(
        self,
        *,
        namespace: str,
        pod_name: str,
        container: str = "",
        tail_lines: int | None = None,
        previous: bool = False,
    ) -> JSONObject:
        pod = await self.get_pod(namespace=namespace, pod_name=pod_name)
        if pod is None:
            return {
                "success": False,
                "status": "failed",
                "message": "Pod not found",
                "namespace": namespace,
                "pod_name": pod_name,
            }
        logs = await self.get_pod_logs(
            namespace=namespace,
            pod_name=pod_name,
            container=container,
            tail_lines=tail_lines,
            previous=previous,
        )
        return {
            "success": True,
            "status": "succeeded",
            "namespace": namespace,
            "pod_name": pod_name,
            "pod": pod,
            "events": await self.list_pod_events(namespace=namespace, pod_name=pod_name),
            "logs": logs,
        }

    async def workload_status(self, *, namespace: str, kind: str, name: str) -> JSONObject:
        workload = await self.get_workload(namespace=namespace, kind=kind, name=name)
        if workload is None:
            return {
                "success": False,
                "status": "failed",
                "message": "Workload not found",
                "namespace": namespace,
                "kind": kind,
                "name": name,
            }
        return {
            "success": True,
            "status": "succeeded",
            "namespace": namespace,
            "kind": _normalize_kind(kind),
            "name": name,
            "workload": workload,
            "pods": await self._pods_for_workload(
                namespace=namespace, kind=kind, workload=workload
            ),
            "events": await self._events_for_object(namespace=namespace, kind=kind, name=name),
        }

    async def workload_logs(
        self,
        *,
        namespace: str,
        kind: str = "",
        name: str = "",
        pod_name: str = "",
        label_selector: str = "",
        container: str = "",
        tail_lines: int | None = None,
        since_seconds: int | None = None,
        previous: bool = False,
        limit: int | None = None,
    ) -> JSONObject:
        pods = await self._resolve_pods(
            namespace=namespace,
            kind=kind,
            name=name,
            pod_name=pod_name,
            label_selector=label_selector,
            limit=limit,
        )
        results: list[JSONObject] = []
        for pod in pods:
            resolved_name = str((pod.get("metadata") or {}).get("name") or "")
            if not resolved_name:
                continue
            logs = await self.get_pod_logs(
                namespace=namespace,
                pod_name=resolved_name,
                container=container,
                tail_lines=tail_lines,
                since_seconds=since_seconds,
                previous=previous,
            )
            results.append(logs)
        return {
            "success": True,
            "status": "succeeded",
            "namespace": namespace,
            "kind": _normalize_kind(kind) if kind else None,
            "name": name or None,
            "pod_count": len(results),
            "items": results,
        }

    async def workload_events(
        self,
        *,
        namespace: str,
        kind: str = "",
        name: str = "",
        pod_name: str = "",
        label_selector: str = "",
        limit: int | None = None,
    ) -> JSONObject:
        events: list[JSONObject] = []
        if kind and name:
            events.extend(await self._events_for_object(namespace=namespace, kind=kind, name=name))
        pods = await self._resolve_pods(
            namespace=namespace,
            kind=kind,
            name=name,
            pod_name=pod_name,
            label_selector=label_selector,
            limit=limit,
        )
        for pod in pods:
            resolved_name = str((pod.get("metadata") or {}).get("name") or "")
            if resolved_name:
                events.extend(
                    await self.list_pod_events(namespace=namespace, pod_name=resolved_name)
                )
        return {
            "success": True,
            "status": "succeeded",
            "namespace": namespace,
            "kind": _normalize_kind(kind) if kind else None,
            "name": name or pod_name or None,
            "items": events,
        }

    async def job_diagnostics(
        self,
        *,
        namespace: str,
        kind: str,
        name: str,
        tail_lines: int | None = None,
        previous: bool = False,
        limit: int | None = None,
    ) -> JSONObject:
        normalized = _normalize_kind(kind)
        workload = await self.get_workload(namespace=namespace, kind=normalized, name=name)
        if workload is None:
            return {
                "success": False,
                "status": "failed",
                "message": "Job or CronJob not found",
                "namespace": namespace,
                "kind": normalized,
                "name": name,
            }
        pods = await self._pods_for_workload(
            namespace=namespace, kind=normalized, workload=workload
        )
        logs = await self.workload_logs(
            namespace=namespace,
            kind=normalized,
            name=name,
            tail_lines=tail_lines,
            previous=previous,
            limit=limit,
        )
        return {
            "success": True,
            "status": "succeeded",
            "namespace": namespace,
            "kind": normalized,
            "name": name,
            "job": workload,
            "pods": pods[: _limit(limit)],
            "logs": logs,
            "events": await self.workload_events(
                namespace=namespace,
                kind=normalized,
                name=name,
                limit=limit,
            ),
        }

    async def cleanup_failed_job(self, *, namespace: str, job_name: str) -> JSONObject:
        diagnostics = await self.job_diagnostics(
            namespace=namespace,
            kind="Job",
            name=job_name,
            limit=10,
        )
        if diagnostics.get("success") is False:
            return diagnostics
        job = diagnostics.get("job") if isinstance(diagnostics.get("job"), dict) else {}
        summary = _job_terminal_summary(job)
        if not summary["failed"]:
            return {
                "success": False,
                "status": "failed",
                "message": "Job is not in a failed terminal state",
                "namespace": namespace,
                "job_name": job_name,
                "job": job,
                "summary": summary,
                "diagnostics": diagnostics,
            }

        bundle = await self.client_factory.build()
        bundle.batch_api.delete_namespaced_job(
            name=job_name,
            namespace=namespace,
            propagation_policy="Background",
        )
        return {
            "success": True,
            "status": "succeeded",
            "action": "deleted",
            "namespace": namespace,
            "job_name": job_name,
            "summary": summary,
            "diagnostics": diagnostics,
        }

    async def list_nodes(
        self,
        *,
        label_selector: str = "",
        limit: int | None = None,
    ) -> list[JSONObject]:
        bundle = await self.client_factory.build()
        response = bundle.core_api.list_node(label_selector=label_selector or None)
        return [_serialize(bundle.api_client, item) for item in getattr(response, "items", [])][
            : _limit(limit)
        ]

    async def get_node(self, *, node_name: str) -> JSONObject | None:
        bundle = await self.client_factory.build()
        try:
            node = bundle.core_api.read_node(name=node_name)
        except Exception:
            return None
        return _serialize(bundle.api_client, node)

    async def node_diagnostics(self, *, node_name: str, limit: int | None = None) -> JSONObject:
        node = await self.get_node(node_name=node_name)
        if node is None:
            return {
                "success": False,
                "status": "failed",
                "message": "Node not found",
                "node": node_name,
            }
        pods = await self.node_pods(node_name=node_name, limit=None)
        events = await self.node_events(node_name=node_name, limit=None)
        pod_items = pods.get("items", [])[: _limit(limit)]
        event_items = events.get("items", [])[: _limit(limit)]
        return {
            "success": True,
            "status": "succeeded",
            "node": node_name,
            "item": node,
            "summary": _node_summary(node),
            "pod_summary": _node_pod_summary(node, pods.get("items", [])),
            "event_summary": _event_summary(events.get("items", [])),
            "pods": pod_items,
            "events": event_items,
        }

    async def node_capacity(self, *, node_name: str) -> JSONObject:
        node = await self.get_node(node_name=node_name)
        if node is None:
            return {
                "success": False,
                "status": "failed",
                "message": "Node not found",
                "node": node_name,
            }
        metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
        spec = node.get("spec") if isinstance(node.get("spec"), dict) else {}
        status = node.get("status") if isinstance(node.get("status"), dict) else {}
        return {
            "success": True,
            "status": "succeeded",
            "node": node_name,
            "labels": metadata.get("labels") or {},
            "annotations": metadata.get("annotations") or {},
            "capacity": status.get("capacity") or {},
            "allocatable": status.get("allocatable") or {},
            "conditions": status.get("conditions") or [],
            "taints": spec.get("taints") or [],
            "addresses": status.get("addresses") or [],
            "node_info": status.get("nodeInfo") or status.get("node_info") or {},
            "images": status.get("images") or [],
        }

    async def node_pressure(self, *, node_name: str, limit: int | None = None) -> JSONObject:
        node = await self.get_node(node_name=node_name)
        if node is None:
            return {
                "success": False,
                "status": "failed",
                "message": "Node not found",
                "node": node_name,
            }
        pods = await self.node_pods(
            node_name=node_name,
            include_succeeded=True,
            limit=None,
        )
        events = await self.node_events(node_name=node_name, limit=None)
        non_running_pods = [
            pod
            for pod in pods.get("items", [])
            if str((pod.get("status") or {}).get("phase") or "") not in {"Running", "Succeeded"}
        ]
        warning_events = [
            event
            for event in events.get("items", [])
            if str(event.get("type") or "").lower() == "warning"
        ]
        summary = _node_summary(node)
        pod_items = pods.get("items", [])
        event_items = events.get("items", [])
        bounded_non_running_pods = non_running_pods[: _limit(limit)]
        bounded_warning_events = warning_events[: _limit(limit)]
        return {
            "success": True,
            "status": "succeeded",
            "node": node_name,
            "summary": summary,
            "pressure_conditions": summary["pressure_conditions"],
            "ready_condition": summary["ready_condition"],
            "taints": summary["taints"],
            "capacity": summary["capacity"],
            "allocatable": summary["allocatable"],
            "pod_summary": _node_pod_summary(node, pod_items),
            "event_summary": _event_summary(event_items),
            "non_running_pod_summaries": [_pod_summary(pod) for pod in bounded_non_running_pods],
            "warning_event_summaries": [
                _event_item_summary(event) for event in bounded_warning_events
            ],
            "non_running_pods": bounded_non_running_pods,
            "warning_events": bounded_warning_events,
        }

    async def node_pods(
        self,
        *,
        node_name: str,
        namespace: str = "",
        label_selector: str = "",
        include_succeeded: bool = False,
        limit: int | None = None,
    ) -> JSONObject:
        bundle = await self.client_factory.build()
        field_selector = f"spec.nodeName={node_name}"
        if namespace:
            response = bundle.core_api.list_namespaced_pod(
                namespace=namespace,
                field_selector=field_selector,
                label_selector=label_selector or None,
            )
        else:
            response = bundle.core_api.list_pod_for_all_namespaces(
                field_selector=field_selector,
                label_selector=label_selector or None,
            )
        pods = [_serialize(bundle.api_client, item) for item in getattr(response, "items", [])]
        if not include_succeeded:
            pods = [
                pod
                for pod in pods
                if str((pod.get("status") or {}).get("phase") or "") != "Succeeded"
            ]
        return {
            "success": True,
            "status": "succeeded",
            "node": node_name,
            "namespace": namespace or None,
            "pod_count": len(pods),
            "items": pods[: _limit(limit)],
        }

    async def node_events(self, *, node_name: str, limit: int | None = None) -> JSONObject:
        events = await self._events_for_object(
            namespace="",
            kind="Node",
            name=node_name,
            all_namespaces=True,
        )
        return {
            "success": True,
            "status": "succeeded",
            "node": node_name,
            "event_count": len(events),
            "items": events[: _limit(limit)],
        }

    async def pvc_diagnostics(
        self,
        *,
        namespace: str = "",
        pvc_name: str = "",
        pv_name: str = "",
    ) -> JSONObject:
        bundle = await self.client_factory.build()
        serialized_pvc: JSONObject | None = None
        pv: JSONObject | None = None

        if pvc_name and namespace:
            try:
                pvc = bundle.core_api.read_namespaced_persistent_volume_claim(
                    name=pvc_name,
                    namespace=namespace,
                )
                serialized_pvc = _serialize(bundle.api_client, pvc)
            except Exception:
                serialized_pvc = None
        if serialized_pvc is None and pv_name:
            try:
                pv = _serialize(bundle.api_client, bundle.core_api.read_persistent_volume(pv_name))
                claim_ref = pv.get("spec", {}).get("claimRef") or pv.get("spec", {}).get(
                    "claim_ref"
                )
                if isinstance(claim_ref, dict):
                    namespace = str(claim_ref.get("namespace") or namespace)
                    pvc_name = str(claim_ref.get("name") or pvc_name)
                    if namespace and pvc_name:
                        try:
                            serialized_pvc = _serialize(
                                bundle.api_client,
                                bundle.core_api.read_namespaced_persistent_volume_claim(
                                    name=pvc_name,
                                    namespace=namespace,
                                ),
                            )
                        except Exception:
                            serialized_pvc = None
            except Exception:
                pv = None
        if serialized_pvc is None and pv is None:
            return {
                "success": False,
                "status": "failed",
                "message": "PersistentVolumeClaim or PersistentVolume not found",
                "namespace": namespace or None,
                "persistentvolumeclaim": pvc_name or None,
                "persistentvolume": pv_name or None,
            }
        if serialized_pvc is not None:
            namespace = str((serialized_pvc.get("metadata") or {}).get("namespace") or namespace)
            pvc_name = str((serialized_pvc.get("metadata") or {}).get("name") or pvc_name)
        volume_name = str((((serialized_pvc or {}).get("spec") or {}).get("volumeName")) or "")
        if pv is None and volume_name:
            try:
                pv = _serialize(
                    bundle.api_client, bundle.core_api.read_persistent_volume(volume_name)
                )
            except Exception:
                pv = None
        mounted_pods = []
        if namespace and pvc_name:
            for pod in await self.list_pods(namespace=namespace):
                volumes = (pod.get("spec") or {}).get("volumes") or []
                if any(
                    ((volume.get("persistentVolumeClaim") or {}).get("claimName")) == pvc_name
                    for volume in volumes
                ):
                    mounted_pods.append(pod)
        pvc_events = (
            await self._events_for_object(
                namespace=namespace,
                kind="PersistentVolumeClaim",
                name=pvc_name,
            )
            if namespace and pvc_name
            else []
        )
        pv_events = (
            await self._events_for_object(
                namespace="",
                kind="PersistentVolume",
                name=str((pv.get("metadata") or {}).get("name") or pv_name),
                all_namespaces=True,
            )
            if pv is not None
            else []
        )
        pv_resolved_name = str((pv.get("metadata") or {}).get("name") or pv_name) if pv else pv_name
        return {
            "success": True,
            "status": "succeeded",
            "namespace": namespace or None,
            "persistentvolumeclaim": pvc_name or None,
            "persistentvolume_name": pv_resolved_name or None,
            "summary": _storage_summary(serialized_pvc, pv, mounted_pods, pvc_events + pv_events),
            "pvc": serialized_pvc,
            "persistentvolume": pv,
            "mounted_pods": mounted_pods,
            "mounted_pod_summaries": [_pod_summary(pod) for pod in mounted_pods],
            "events": pvc_events + pv_events,
            "event_summary": _event_summary(pvc_events + pv_events),
        }

    async def service_diagnostics(self, *, namespace: str, service_name: str) -> JSONObject:
        bundle = await self.client_factory.build()
        try:
            service = bundle.core_api.read_namespaced_service(
                name=service_name,
                namespace=namespace,
            )
        except Exception:
            return {
                "success": False,
                "status": "failed",
                "message": "Service not found",
                "namespace": namespace,
                "service": service_name,
            }
        serialized_service = _serialize(bundle.api_client, service)
        selector = _selector_from_match_labels(
            (serialized_service.get("spec") or {}).get("selector")
        )
        endpoints: JSONObject | None = None
        endpoint_slices: list[JSONObject] = []
        try:
            endpoints = _serialize(
                bundle.api_client,
                bundle.core_api.read_namespaced_endpoints(name=service_name, namespace=namespace),
            )
        except Exception:
            endpoints = None
        try:
            response = bundle.discovery_api.list_namespaced_endpoint_slice(
                namespace=namespace,
                label_selector=f"kubernetes.io/service-name={service_name}",
            )
            endpoint_slices = [
                _serialize(bundle.api_client, item) for item in getattr(response, "items", [])
            ]
        except Exception:
            endpoint_slices = []
        pods = (
            await self.list_pods(namespace=namespace, label_selector=selector) if selector else []
        )
        events = await self._events_for_object(
            namespace=namespace,
            kind="Service",
            name=service_name,
        )
        return {
            "success": True,
            "status": "succeeded",
            "namespace": namespace,
            "service": service_name,
            "item": serialized_service,
            "selector": selector,
            "summary": _service_summary(
                serialized_service, pods, endpoints, endpoint_slices, events
            ),
            "pods": pods,
            "pod_summary": _workload_pod_summary(pods),
            "endpoints": endpoints,
            "endpoint_slices": endpoint_slices,
            "events": events,
            "event_summary": _event_summary(events),
        }

    async def remediate_resource_pressure_with_deployment_scale(
        self,
        *,
        namespace: str,
        deployment_name: str,
        replicas: int,
    ) -> JSONObject:
        before = await self.get_deployment(namespace=namespace, deployment_name=deployment_name)
        if before is None:
            return {
                "success": False,
                "status": "failed",
                "message": "Deployment not found",
                "namespace": namespace,
                "deployment_name": deployment_name,
            }
        result = await self.scale_deployment(
            namespace=namespace,
            deployment_name=deployment_name,
            replicas=replicas,
        )
        after = await self.get_deployment(namespace=namespace, deployment_name=deployment_name)
        return {
            **result,
            "before": before,
            "after": after,
            "rollout": _controller_rollout_summary("Deployment", after or before),
        }

    async def pdb_hpa_diagnostics(
        self,
        *,
        namespace: str,
        pdb_name: str = "",
        hpa_name: str = "",
        name: str = "",
    ) -> JSONObject:
        bundle = await self.client_factory.build()
        resolved_pdb = pdb_name or name
        resolved_hpa = hpa_name or name
        pdb: JSONObject | None = None
        hpa: JSONObject | None = None
        if resolved_pdb:
            try:
                pdb = _serialize(
                    bundle.api_client,
                    bundle.policy_api.read_namespaced_pod_disruption_budget(
                        name=resolved_pdb,
                        namespace=namespace,
                    ),
                )
            except Exception:
                pdb = None
        if resolved_hpa:
            try:
                hpa = _serialize(
                    bundle.api_client,
                    bundle.autoscaling_api.read_namespaced_horizontal_pod_autoscaler(
                        name=resolved_hpa,
                        namespace=namespace,
                    ),
                )
            except Exception:
                hpa = None
        pdb_pods = await self._pods_for_selector(
            namespace=namespace,
            selector=(pdb.get("spec") or {}).get("selector") if pdb is not None else None,
        )
        hpa_target: JSONObject | None = None
        if hpa is not None:
            target_ref = (hpa.get("spec") or {}).get("scaleTargetRef") or (
                hpa.get("spec") or {}
            ).get("scale_target_ref")
            if isinstance(target_ref, dict):
                target_kind = str(target_ref.get("kind") or "")
                target_name = str(target_ref.get("name") or "")
                if target_kind and target_name:
                    hpa_target = await self.get_workload(
                        namespace=namespace,
                        kind=target_kind,
                        name=target_name,
                    )
        events = []
        if resolved_pdb:
            events.extend(
                await self._events_for_object(
                    namespace=namespace,
                    kind="PodDisruptionBudget",
                    name=resolved_pdb,
                )
            )
        if resolved_hpa:
            events.extend(
                await self._events_for_object(
                    namespace=namespace,
                    kind="HorizontalPodAutoscaler",
                    name=resolved_hpa,
                )
            )
        return {
            "success": pdb is not None or hpa is not None,
            "status": "succeeded" if pdb is not None or hpa is not None else "failed",
            "namespace": namespace,
            "poddisruptionbudget": pdb,
            "horizontalpodautoscaler": hpa,
            "summary": _pdb_hpa_summary(pdb, hpa, pdb_pods, hpa_target, events),
            "pdb_pods": pdb_pods,
            "hpa_target": hpa_target,
            "events": events,
            "event_summary": _event_summary(events),
            "message": None if pdb is not None or hpa is not None else "PDB or HPA not found",
        }

    async def remediate_resource_pressure_with_hpa_patch(
        self,
        *,
        namespace: str,
        hpa_name: str,
        min_replicas: int | None = None,
        max_replicas: int | None = None,
    ) -> JSONObject:
        bundle = await self.client_factory.build()
        try:
            before_raw = bundle.autoscaling_api.read_namespaced_horizontal_pod_autoscaler(
                name=hpa_name,
                namespace=namespace,
            )
        except Exception:
            return {
                "success": False,
                "status": "failed",
                "message": "HorizontalPodAutoscaler not found",
                "namespace": namespace,
                "hpa_name": hpa_name,
            }
        before = _serialize(bundle.api_client, before_raw)
        current_spec = before.get("spec") if isinstance(before.get("spec"), dict) else {}
        resolved_min = (
            min_replicas
            if min_replicas is not None
            else _optional_int_value(
                current_spec.get("minReplicas"),
                default=1,
            )
        )
        resolved_max = (
            max_replicas
            if max_replicas is not None
            else _optional_int_value(
                current_spec.get("maxReplicas"),
                default=1,
            )
        )
        if resolved_min > resolved_max:
            return {
                "success": False,
                "status": "failed",
                "message": "min_replicas cannot exceed max_replicas",
                "namespace": namespace,
                "hpa_name": hpa_name,
            }
        body = {"spec": {"minReplicas": resolved_min, "maxReplicas": resolved_max}}
        updated_raw = bundle.autoscaling_api.patch_namespaced_horizontal_pod_autoscaler(
            name=hpa_name,
            namespace=namespace,
            body=body,
        )
        after = _serialize(bundle.api_client, updated_raw)
        target_ref = (after.get("spec") or {}).get("scaleTargetRef") or (
            after.get("spec") or {}
        ).get("scale_target_ref")
        target: JSONObject | None = None
        if isinstance(target_ref, dict):
            target_kind = str(target_ref.get("kind") or "").strip()
            target_name = str(target_ref.get("name") or "").strip()
            if target_kind and target_name:
                target = await self.get_workload(
                    namespace=namespace,
                    kind=target_kind,
                    name=target_name,
                )
        return {
            "success": True,
            "status": "succeeded",
            "action": "patched_hpa_bounds",
            "namespace": namespace,
            "hpa_name": hpa_name,
            "before": before,
            "after": after,
            "target": target,
            "applied_min_replicas": resolved_min,
            "applied_max_replicas": resolved_max,
        }

    async def config_diagnostics(self, *, namespace: str, configmap_name: str) -> JSONObject:
        bundle = await self.client_factory.build()
        try:
            configmap = _serialize(
                bundle.api_client,
                bundle.core_api.read_namespaced_config_map(
                    name=configmap_name,
                    namespace=namespace,
                ),
            )
        except Exception:
            return {
                "success": False,
                "status": "failed",
                "message": "ConfigMap not found",
                "namespace": namespace,
                "configmap": configmap_name,
            }
        events = await self._events_for_object(
            namespace=namespace,
            kind="ConfigMap",
            name=configmap_name,
        )
        return {
            "success": True,
            "status": "succeeded",
            "namespace": namespace,
            "configmap": configmap_name,
            "summary": _configmap_summary(configmap),
            "events": events,
            "event_summary": _event_summary(events),
        }

    async def certificate_diagnostics(
        self,
        *,
        namespace: str = "",
        secret_name: str = "",
        label_selector: str = "",
        limit: int | None = None,
    ) -> JSONObject:
        bundle = await self.client_factory.build()
        secrets: list[JSONObject] = []
        if secret_name:
            if not namespace:
                return {
                    "success": False,
                    "status": "failed",
                    "message": "certificate_diagnostics requires namespace when secret is set",
                    "secret": secret_name,
                }
            try:
                secret = bundle.core_api.read_namespaced_secret(
                    name=secret_name,
                    namespace=namespace,
                )
            except Exception:
                return {
                    "success": False,
                    "status": "failed",
                    "message": "Secret not found",
                    "namespace": namespace,
                    "secret": secret_name,
                }
            secrets = [_serialize(bundle.api_client, secret)]
        elif namespace:
            response = bundle.core_api.list_namespaced_secret(
                namespace=namespace,
                label_selector=label_selector or None,
            )
            secrets = [
                _serialize(bundle.api_client, item) for item in getattr(response, "items", [])
            ]
        else:
            response = bundle.core_api.list_secret_for_all_namespaces(
                label_selector=label_selector or None,
            )
            secrets = [
                _serialize(bundle.api_client, item) for item in getattr(response, "items", [])
            ]

        inspected = [
            item
            for item in (_certificate_secret_summary(secret) for secret in secrets[: _limit(limit)])
            if item is not None
        ]
        return {
            "success": True,
            "status": "succeeded",
            "namespace": namespace or None,
            "secret": secret_name or None,
            "secret_count": len(inspected),
            "items": inspected,
        }

    async def service_probe(
        self,
        *,
        namespace: str,
        service_name: str,
        port: object,
        operation: str,
        path: str = "",
        scheme: str = "",
        timeout_seconds: int = 5,
        expected_status_codes: list[int] | None = None,
    ) -> JSONObject:
        diagnostics = await self.service_diagnostics(namespace=namespace, service_name=service_name)
        if diagnostics.get("success") is False:
            return diagnostics
        service = diagnostics.get("item") if isinstance(diagnostics.get("item"), dict) else {}
        resolved_port = _resolve_service_port(service, port)
        if operation in {"tcp", "http"} and resolved_port is None:
            return {
                "success": False,
                "status": "failed",
                "message": "Service port not found",
                "namespace": namespace,
                "service": service_name,
                "requested_port": port,
            }
        endpoint_host = _resolve_probe_host(diagnostics, service)
        dns_name = f"{service_name}.{namespace}.svc"
        probe_timeout = max(1, timeout_seconds)

        if operation == "dns":
            dns_result = _probe_dns(dns_name, probe_timeout)
            return {
                "success": dns_result["success"],
                "status": "succeeded" if dns_result["success"] else "failed",
                "namespace": namespace,
                "service": service_name,
                "operation": operation,
                "dns_name": dns_name,
                "service_host": endpoint_host,
                **dns_result,
            }
        if endpoint_host is None or resolved_port is None:
            return {
                "success": False,
                "status": "failed",
                "message": "No routable Service target found",
                "namespace": namespace,
                "service": service_name,
                "operation": operation,
            }
        if operation == "tcp":
            tcp_result = _probe_tcp(endpoint_host, resolved_port, probe_timeout)
            return {
                "success": tcp_result["success"],
                "status": "succeeded" if tcp_result["success"] else "failed",
                "namespace": namespace,
                "service": service_name,
                "operation": operation,
                "host": endpoint_host,
                "port": resolved_port,
                **tcp_result,
            }
        http_result = _probe_http(
            host=endpoint_host,
            port=resolved_port,
            timeout_seconds=probe_timeout,
            path=path or "/",
            scheme=scheme or "http",
            expected_status_codes=expected_status_codes or [200, 204, 301, 302, 401, 403],
        )
        return {
            "success": http_result["success"],
            "status": "succeeded" if http_result["success"] else "failed",
            "namespace": namespace,
            "service": service_name,
            "operation": operation,
            "host": endpoint_host,
            "port": resolved_port,
            **http_result,
        }

    async def get_workload(self, *, namespace: str, kind: str, name: str) -> JSONObject | None:
        bundle = await self.client_factory.build()
        normalized = _normalize_kind(kind)
        readers: dict[str, Any] = {
            "Pod": lambda: bundle.core_api.read_namespaced_pod(name=name, namespace=namespace),
            "Deployment": lambda: bundle.apps_api.read_namespaced_deployment(
                name=name, namespace=namespace
            ),
            "StatefulSet": lambda: bundle.apps_api.read_namespaced_stateful_set(
                name=name, namespace=namespace
            ),
            "DaemonSet": lambda: bundle.apps_api.read_namespaced_daemon_set(
                name=name, namespace=namespace
            ),
            "ReplicaSet": lambda: bundle.apps_api.read_namespaced_replica_set(
                name=name, namespace=namespace
            ),
            "Job": lambda: bundle.batch_api.read_namespaced_job(name=name, namespace=namespace),
            "CronJob": lambda: bundle.batch_api.read_namespaced_cron_job(
                name=name, namespace=namespace
            ),
            "Service": lambda: bundle.core_api.read_namespaced_service(
                name=name, namespace=namespace
            ),
        }
        reader = readers.get(normalized)
        if reader is None:
            return None
        try:
            return _serialize(bundle.api_client, reader())
        except Exception:
            return None

    async def _resolve_pods(
        self,
        *,
        namespace: str,
        kind: str = "",
        name: str = "",
        pod_name: str = "",
        label_selector: str = "",
        limit: int | None = None,
    ) -> list[JSONObject]:
        if pod_name:
            pod = await self.get_pod(namespace=namespace, pod_name=pod_name)
            return [pod] if pod is not None else []
        if label_selector:
            return (await self.list_pods(namespace=namespace, label_selector=label_selector))[
                : _limit(limit)
            ]
        if kind and name:
            workload = await self.get_workload(namespace=namespace, kind=kind, name=name)
            if workload is None:
                return []
            return (
                await self._pods_for_workload(namespace=namespace, kind=kind, workload=workload)
            )[: _limit(limit)]
        return []

    async def _pods_for_selector(
        self,
        *,
        namespace: str,
        selector: object,
    ) -> list[JSONObject]:
        match_labels = selector.get("matchLabels") if isinstance(selector, dict) else None
        rendered = _selector_from_match_labels(match_labels)
        return (
            await self.list_pods(namespace=namespace, label_selector=rendered) if rendered else []
        )

    async def _pods_for_workload(
        self,
        *,
        namespace: str,
        kind: str,
        workload: JSONObject,
    ) -> list[JSONObject]:
        normalized = _normalize_kind(kind)
        if normalized == "Pod":
            return [workload]
        if normalized == "CronJob":
            return await self._pods_for_cronjob(namespace=namespace, cronjob=workload)
        selector = _selector_from_match_labels(
            ((workload.get("spec") or {}).get("selector") or {}).get("matchLabels")
        )
        if normalized == "Service":
            selector = _selector_from_match_labels((workload.get("spec") or {}).get("selector"))
        return (
            await self.list_pods(namespace=namespace, label_selector=selector) if selector else []
        )

    async def _pods_for_cronjob(self, *, namespace: str, cronjob: JSONObject) -> list[JSONObject]:
        bundle = await self.client_factory.build()
        cronjob_name = str((cronjob.get("metadata") or {}).get("name") or "")
        jobs_response = bundle.batch_api.list_namespaced_job(namespace=namespace)
        pods: list[JSONObject] = []
        for job in getattr(jobs_response, "items", []):
            serialized_job = _serialize(bundle.api_client, job)
            if _has_owner(serialized_job, kind="CronJob", name=cronjob_name):
                pods.extend(
                    await self._pods_for_workload(
                        namespace=namespace,
                        kind="Job",
                        workload=serialized_job,
                    )
                )
        return pods

    async def _events_for_object(
        self,
        *,
        namespace: str,
        kind: str,
        name: str,
        all_namespaces: bool = False,
    ) -> list[JSONObject]:
        bundle = await self.client_factory.build()
        field_selector = f"involvedObject.kind={_normalize_kind(kind)},involvedObject.name={name}"
        if all_namespaces:
            response = bundle.core_api.list_event_for_all_namespaces(field_selector=field_selector)
        else:
            response = bundle.core_api.list_namespaced_event(
                namespace=namespace,
                field_selector=field_selector,
            )
        return [_serialize(bundle.api_client, item) for item in getattr(response, "items", [])]

    async def _update_rule_in_crd(
        self,
        existing_crd: JSONObject,
        *,
        rule_name: str,
        group_name: str,
        rule_data: JSONObject,
        source_metadata: AlertRuleSource | None,
    ) -> JSONObject:
        bundle = await self.client_factory.build()
        crd_name = existing_crd["metadata"]["name"]
        groups = existing_crd.setdefault("spec", {}).setdefault("groups", [])
        target_group = None
        for group in groups:
            if group.get("name") == group_name:
                target_group = group
                break
        if target_group is None:
            target_group = {"name": group_name, "rules": []}
            groups.append(target_group)

        rules = target_group.setdefault("rules", [])
        for idx, rule in enumerate(rules):
            if _rule_identity(rule) == rule_name:
                rules[idx] = rule_data
                break
        else:
            rules.append(rule_data)

        metadata = existing_crd.setdefault("metadata", {})
        sources = load_alert_rule_sources_from_annotations(metadata.get("annotations"))
        if source_metadata is not None:
            sources[rule_name] = source_metadata
        metadata["annotations"] = dump_alert_rule_sources_to_annotations(
            metadata.get("annotations"),
            sources,
        )

        bundle.custom_api.patch_namespaced_custom_object(
            group=self.group,
            version=self.version,
            namespace=bundle.namespace,
            plural=self.plural,
            name=crd_name,
            body=existing_crd,
        )
        return {
            "status": "success",
            "message": "Rule updated in CRD",
            "crd_name": crd_name,
            "action": "updated",
        }

    async def _create_rule_crd(
        self,
        *,
        crd_name: str,
        group_name: str,
        rule_name: str,
        rule_data: JSONObject,
        source_metadata: AlertRuleSource | None,
    ) -> JSONObject:
        bundle = await self.client_factory.build()
        annotations = dump_alert_rule_sources_to_annotations(
            {},
            {rule_name: source_metadata} if source_metadata is not None else {},
        )
        body = {
            "apiVersion": f"{self.group}/{self.version}",
            "kind": "PrometheusRule",
            "metadata": {
                "name": crd_name,
                "namespace": bundle.namespace,
                "labels": {**get_settings().prometheus_crd_labels, "managed-by": "poundcake"},
                "annotations": annotations,
            },
            "spec": {"groups": [{"name": group_name, "rules": [rule_data]}]},
        }
        bundle.custom_api.create_namespaced_custom_object(
            group=self.group,
            version=self.version,
            namespace=bundle.namespace,
            plural=self.plural,
            body=body,
        )
        return {
            "status": "success",
            "message": "Rule created in new CRD",
            "crd_name": crd_name,
            "action": "created",
        }

    async def delete_rule(self, rule_name: str, group_name: str, crd_name: str) -> JSONObject:
        bundle = await self.client_factory.build()
        existing = await self.get_prometheus_rule(crd_name)
        if existing is None:
            return {"status": "error", "message": f"PrometheusRule CRD '{crd_name}' not found"}

        groups = existing.get("spec", {}).get("groups", [])
        found = False
        for group in list(groups):
            if group.get("name") != group_name:
                continue
            rules = group.get("rules", [])
            for idx, rule in enumerate(list(rules)):
                if _rule_identity(rule) == rule_name:
                    del rules[idx]
                    found = True
                    break
            if found and not rules:
                groups.remove(group)
            break

        if not found:
            return {
                "status": "error",
                "message": f"Rule '{rule_name}' not found in group '{group_name}'",
            }

        if not groups:
            bundle.custom_api.delete_namespaced_custom_object(
                group=self.group,
                version=self.version,
                namespace=bundle.namespace,
                plural=self.plural,
                name=crd_name,
            )
            return {
                "status": "success",
                "message": "Rule deleted, CRD removed (was empty)",
                "crd_name": crd_name,
                "action": "deleted_crd",
            }

        existing["spec"]["groups"] = groups
        metadata = existing.setdefault("metadata", {})
        sources = load_alert_rule_sources_from_annotations(metadata.get("annotations"))
        sources.pop(rule_name, None)
        metadata["annotations"] = dump_alert_rule_sources_to_annotations(
            metadata.get("annotations"),
            sources,
        )
        bundle.custom_api.patch_namespaced_custom_object(
            group=self.group,
            version=self.version,
            namespace=bundle.namespace,
            plural=self.plural,
            name=crd_name,
            body=existing,
        )
        return {
            "status": "success",
            "message": "Rule deleted from CRD",
            "crd_name": crd_name,
            "action": "updated",
        }


def _is_missing_prometheus_rule_crd_error(exc: Exception) -> bool:
    status = getattr(exc, "status", None)
    if status == 404:
        return True
    reason = str(getattr(exc, "reason", "") or "").lower()
    body = str(getattr(exc, "body", "") or "").lower()
    message = str(exc).lower()
    combined = " ".join((reason, body, message))
    return (
        "prometheusrules" in combined
        and "monitoring.coreos.com" in combined
        and ("not found" in combined or "notfound" in combined)
    )


def _rule_identity(rule: object) -> str:
    if not isinstance(rule, dict):
        return ""
    return str(rule.get("alert") or rule.get("record") or "").strip()


def _find_rule_in_crd(
    crd: JSONObject,
    *,
    group_name: str,
    rule_name: str,
) -> JSONObject | None:
    spec = crd.get("spec")
    if not isinstance(spec, dict):
        return None
    groups = spec.get("groups")
    if not isinstance(groups, list):
        return None
    for group in groups:
        if not isinstance(group, dict) or group.get("name") != group_name:
            continue
        rules = group.get("rules")
        if not isinstance(rules, list):
            return None
        for rule in rules:
            if isinstance(rule, dict) and _rule_identity(rule) == rule_name:
                return dict(rule)
        return None
    return None


def _kubernetes_api_version(api_client: object) -> str | None:
    try:
        response = api_client.call_api(
            "/version",
            "GET",
            auth_settings=["BearerToken"],
            response_type="object",
            _return_http_data_only=True,
        )
        if isinstance(response, dict):
            git_version = response.get("gitVersion")
            return str(git_version) if git_version else None
        return None
    except Exception:
        return None


def _normalize_kind(kind: str) -> str:
    normalized = str(kind or "").strip().lower()
    return {
        "pod": "Pod",
        "pods": "Pod",
        "deployment": "Deployment",
        "deployments": "Deployment",
        "statefulset": "StatefulSet",
        "statefulsets": "StatefulSet",
        "daemonset": "DaemonSet",
        "daemonsets": "DaemonSet",
        "replicaset": "ReplicaSet",
        "replicasets": "ReplicaSet",
        "job": "Job",
        "jobs": "Job",
        "cronjob": "CronJob",
        "cronjobs": "CronJob",
        "service": "Service",
        "services": "Service",
        "node": "Node",
        "persistentvolumeclaim": "PersistentVolumeClaim",
        "pvc": "PersistentVolumeClaim",
    }.get(normalized, str(kind or "").strip())


def _normalize_controller_kind(kind: str) -> str:
    normalized = _normalize_kind(kind)
    if normalized not in {"Deployment", "StatefulSet", "DaemonSet"}:
        raise ValueError(
            "Kubernetes controller action kind must be one of: Deployment, StatefulSet, DaemonSet"
        )
    return normalized


def _controller_rollout_summary(kind: str, workload: JSONObject) -> JSONObject:
    spec = workload.get("spec") if isinstance(workload.get("spec"), dict) else {}
    status = workload.get("status") if isinstance(workload.get("status"), dict) else {}
    desired = _int_value(spec.get("replicas"), default=1 if kind == "DaemonSet" else 0)
    generation = _int_value((workload.get("metadata") or {}).get("generation"))
    observed_generation = _int_value(
        status.get("observedGeneration") or status.get("observed_generation")
    )
    if kind == "DaemonSet":
        desired = _int_value(
            status.get("desiredNumberScheduled") or status.get("desired_number_scheduled"),
            default=desired,
        )
        updated = _int_value(
            status.get("updatedNumberScheduled") or status.get("updated_number_scheduled"),
        )
        available = _int_value(
            status.get("numberAvailable") or status.get("number_available"),
        )
        ready = updated >= desired and available >= desired
    else:
        updated = _int_value(status.get("updatedReplicas") or status.get("updated_replicas"))
        available = _int_value(status.get("availableReplicas") or status.get("available_replicas"))
        ready = desired == updated == available
    if generation and observed_generation and observed_generation < generation:
        ready = False
    return {
        "ready": ready,
        "desired_replicas": desired,
        "updated_replicas": updated,
        "available_replicas": available,
        "generation": generation or None,
        "observed_generation": observed_generation or None,
        "conditions": (
            status.get("conditions") if isinstance(status.get("conditions"), list) else []
        ),
    }


def _int_value(value: object, *, default: int = 0) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _optional_int_value(value: object, *, default: int = 0) -> int:
    return _int_value(value, default=default)


def _job_terminal_summary(job: JSONObject) -> JSONObject:
    status = job.get("status") if isinstance(job.get("status"), dict) else {}
    conditions = status.get("conditions") if isinstance(status.get("conditions"), list) else []
    failed = _int_value(status.get("failed"))
    succeeded = _int_value(status.get("succeeded"))
    active = _int_value(status.get("active"))
    failed_condition = any(
        str(item.get("type") or "") == "Failed" and str(item.get("status") or "") == "True"
        for item in conditions
        if isinstance(item, dict)
    )
    complete_condition = any(
        str(item.get("type") or "") == "Complete" and str(item.get("status") or "") == "True"
        for item in conditions
        if isinstance(item, dict)
    )
    return {
        "failed": failed > 0 or failed_condition,
        "succeeded": succeeded > 0 or complete_condition,
        "active": active > 0,
        "failed_pods": failed,
        "succeeded_pods": succeeded,
        "active_pods": active,
        "conditions": conditions,
    }


def _certificate_secret_summary(secret: JSONObject) -> JSONObject | None:
    metadata = secret.get("metadata") if isinstance(secret.get("metadata"), dict) else {}
    data = secret.get("data") if isinstance(secret.get("data"), dict) else {}
    certs: list[JSONObject] = []
    for key in ("tls.crt", "ca.crt", "certificate.crt"):
        raw = data.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        certs.extend(_certificate_summaries_from_b64(raw, key))
    if not certs:
        return None
    return {
        "namespace": metadata.get("namespace"),
        "name": metadata.get("name"),
        "type": secret.get("type"),
        "labels": metadata.get("labels") or {},
        "annotations": metadata.get("annotations") or {},
        "certificates": certs,
        "private_key_present": bool(data.get("tls.key")),
        "private_key_returned": False,
    }


def _resolve_service_port(service: JSONObject, requested_port: object) -> int | None:
    spec = service.get("spec") if isinstance(service.get("spec"), dict) else {}
    ports = spec.get("ports") if isinstance(spec.get("ports"), list) else []
    if isinstance(requested_port, int):
        for item in ports:
            if not isinstance(item, dict):
                continue
            if _int_value(item.get("port")) == requested_port:
                return requested_port
            if _int_value(item.get("targetPort")) == requested_port:
                return requested_port
        return requested_port if requested_port > 0 else None
    if isinstance(requested_port, str):
        value = requested_port.strip()
        if not value:
            return None
        if value.isdigit():
            return int(value)
        for item in ports:
            if not isinstance(item, dict):
                continue
            if str(item.get("name") or "").strip() == value:
                return _int_value(item.get("port")) or None
    return None


def _resolve_probe_host(diagnostics: JSONObject, service: JSONObject) -> str | None:
    endpoint_slices = diagnostics.get("endpoint_slices")
    if isinstance(endpoint_slices, list):
        for slice_item in endpoint_slices:
            if not isinstance(slice_item, dict):
                continue
            endpoints = slice_item.get("endpoints")
            if not isinstance(endpoints, list):
                continue
            for endpoint in endpoints:
                if not isinstance(endpoint, dict):
                    continue
                conditions = (
                    endpoint.get("conditions")
                    if isinstance(endpoint.get("conditions"), dict)
                    else {}
                )
                if conditions.get("ready") is False:
                    continue
                addresses = endpoint.get("addresses")
                if isinstance(addresses, list):
                    for address in addresses:
                        if isinstance(address, str) and address.strip():
                            return address.strip()
    endpoints = diagnostics.get("endpoints")
    if isinstance(endpoints, dict):
        subsets = endpoints.get("subsets")
        if isinstance(subsets, list):
            for subset in subsets:
                if not isinstance(subset, dict):
                    continue
                for key in ("addresses", "notReadyAddresses", "not_ready_addresses"):
                    addresses = subset.get(key)
                    if isinstance(addresses, list):
                        for address in addresses:
                            if not isinstance(address, dict):
                                continue
                            ip = str(address.get("ip") or "").strip()
                            if ip:
                                return ip
    spec = service.get("spec") if isinstance(service.get("spec"), dict) else {}
    cluster_ip = str(spec.get("clusterIP") or spec.get("cluster_ip") or "").strip()
    if cluster_ip and cluster_ip.lower() != "none":
        return cluster_ip
    return None


def _probe_dns(dns_name: str, timeout_seconds: int) -> JSONObject:
    prior_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout_seconds)
        answers = socket.getaddrinfo(dns_name, None)
    except Exception as exc:
        return {"success": False, "message": str(exc), "answers": []}
    finally:
        socket.setdefaulttimeout(prior_timeout)
    resolved = sorted({str(item[4][0]) for item in answers if item and len(item) > 4 and item[4]})
    return {"success": bool(resolved), "answers": resolved}


def _probe_tcp(host: str, port: int, timeout_seconds: int) -> JSONObject:
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return {"success": True, "message": "TCP connection succeeded"}
    except Exception as exc:
        return {"success": False, "message": str(exc)}


def _probe_http(
    *,
    host: str,
    port: int,
    timeout_seconds: int,
    path: str,
    scheme: str,
    expected_status_codes: list[int],
) -> JSONObject:
    connection_cls = (
        http.client.HTTPSConnection
        if scheme.strip().lower() == "https"
        else http.client.HTTPConnection
    )
    connection: http.client.HTTPConnection | http.client.HTTPSConnection | None = None
    try:
        connection = connection_cls(host, port, timeout=timeout_seconds)
        connection.request("GET", path if path.startswith("/") else f"/{path}")
        response = connection.getresponse()
        response.read()
        status_code = int(response.status)
        success = status_code in expected_status_codes
        return {
            "success": success,
            "status_code": status_code,
            "reason": str(response.reason or ""),
            "expected_status_codes": expected_status_codes,
        }
    except Exception as exc:
        return {
            "success": False,
            "message": str(exc),
            "expected_status_codes": expected_status_codes,
        }
    finally:
        try:
            connection.close()
        except Exception:
            pass


def _certificate_summaries_from_b64(value: str, key: str) -> list[JSONObject]:
    try:
        raw = base64.b64decode(value)
    except Exception:
        return [{"key": key, "parse_error": "invalid base64 certificate data"}]
    certs: list[JSONObject] = []
    for cert in _load_pem_certificates(raw) or _load_der_certificates(raw):
        certs.append(_certificate_summary(cert, key))
    if not certs:
        certs.append({"key": key, "parse_error": "no certificate data found"})
    return certs


def _load_pem_certificates(raw: bytes) -> list[x509.Certificate]:
    marker = b"-----BEGIN CERTIFICATE-----"
    chunks = raw.split(marker)
    certs: list[x509.Certificate] = []
    for chunk in chunks[1:]:
        pem = (
            marker
            + chunk.split(b"-----END CERTIFICATE-----", 1)[0]
            + b"-----END CERTIFICATE-----\n"
        )
        try:
            certs.append(x509.load_pem_x509_certificate(pem, default_backend()))
        except Exception:
            continue
    return certs


def _load_der_certificates(raw: bytes) -> list[x509.Certificate]:
    try:
        return [x509.load_der_x509_certificate(raw, default_backend())]
    except Exception:
        return []


def _certificate_summary(cert: x509.Certificate, key: str) -> JSONObject:
    now = datetime.now(UTC)
    not_before = cert.not_valid_before_utc
    not_after = cert.not_valid_after_utc
    sans: list[str] = []
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        sans = [str(item) for item in san]
    except Exception:
        sans = []
    return {
        "key": key,
        "subject": cert.subject.rfc4514_string(),
        "issuer": cert.issuer.rfc4514_string(),
        "serial_number": str(cert.serial_number),
        "not_before": not_before.isoformat(),
        "not_after": not_after.isoformat(),
        "days_remaining": int((not_after - now).total_seconds() // 86400),
        "expired": not_after < now,
        "dns_names": sans,
    }


def _selector_from_match_labels(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    parts = []
    for key in sorted(value):
        rendered_key = str(key).strip()
        rendered_value = str(value[key]).strip()
        if rendered_key and rendered_value:
            parts.append(f"{rendered_key}={rendered_value}")
    return ",".join(parts)


def _limit(value: int | None) -> int:
    if value is None:
        return 5
    return max(1, min(int(value), 20))


def _node_summary(node: JSONObject) -> JSONObject:
    spec = node.get("spec") if isinstance(node.get("spec"), dict) else {}
    status = node.get("status") if isinstance(node.get("status"), dict) else {}
    conditions = status.get("conditions") if isinstance(status.get("conditions"), list) else []
    ready_condition = None
    pressure_conditions = []
    for condition in conditions:
        if not isinstance(condition, dict):
            continue
        condition_type = str(condition.get("type") or "")
        if condition_type == "Ready":
            ready_condition = condition
        if condition_type in {
            "DiskPressure",
            "MemoryPressure",
            "PIDPressure",
            "NetworkUnavailable",
        }:
            pressure_conditions.append(condition)
    return {
        "ready_condition": ready_condition,
        "pressure_conditions": pressure_conditions,
        "taints": spec.get("taints") or [],
        "capacity": status.get("capacity") or {},
        "allocatable": status.get("allocatable") or {},
        "node_info": status.get("nodeInfo") or status.get("node_info") or {},
    }


def _node_pod_summary(node: JSONObject, pods: list[JSONObject]) -> JSONObject:
    capacity = ((node.get("status") or {}) if isinstance(node.get("status"), dict) else {}).get(
        "capacity"
    )
    pod_capacity = _int_or_none(
        (capacity or {}).get("pods") if isinstance(capacity, dict) else None
    )
    by_phase: dict[str, int] = {}
    waiting_reasons: dict[str, int] = {}
    restart_count_total = 0
    for pod in pods:
        status = pod.get("status") if isinstance(pod.get("status"), dict) else {}
        phase = str(status.get("phase") or "Unknown")
        by_phase[phase] = by_phase.get(phase, 0) + 1
        for container_status in (
            status.get("containerStatuses") or status.get("container_statuses") or []
        ):
            if not isinstance(container_status, dict):
                continue
            restart_count_total += int(container_status.get("restartCount") or 0)
            state = (
                container_status.get("state")
                if isinstance(container_status.get("state"), dict)
                else {}
            )
            waiting = state.get("waiting") if isinstance(state.get("waiting"), dict) else {}
            reason = str(waiting.get("reason") or "")
            if reason:
                waiting_reasons[reason] = waiting_reasons.get(reason, 0) + 1
    used = len(pods)
    return {
        "scheduled_pod_count": used,
        "pod_capacity": pod_capacity,
        "pod_capacity_usage_ratio": (round(used / pod_capacity, 4) if pod_capacity else None),
        "by_phase": by_phase,
        "waiting_reasons": waiting_reasons,
        "restart_count_total": restart_count_total,
    }


def _workload_pod_summary(pods: list[JSONObject]) -> JSONObject:
    by_phase: dict[str, int] = {}
    ready = 0
    for pod in pods:
        status = pod.get("status") if isinstance(pod.get("status"), dict) else {}
        phase = str(status.get("phase") or "Unknown")
        by_phase[phase] = by_phase.get(phase, 0) + 1
        conditions = status.get("conditions") if isinstance(status.get("conditions"), list) else []
        if any(
            condition.get("type") == "Ready" and str(condition.get("status")) == "True"
            for condition in conditions
            if isinstance(condition, dict)
        ):
            ready += 1
    return {
        "pod_count": len(pods),
        "ready_pod_count": ready,
        "by_phase": by_phase,
    }


def _pod_summary(pod: JSONObject) -> JSONObject:
    metadata = pod.get("metadata") if isinstance(pod.get("metadata"), dict) else {}
    status = pod.get("status") if isinstance(pod.get("status"), dict) else {}
    container_summaries = []
    for container_status in (
        status.get("containerStatuses") or status.get("container_statuses") or []
    ):
        if not isinstance(container_status, dict):
            continue
        state = (
            container_status.get("state") if isinstance(container_status.get("state"), dict) else {}
        )
        waiting = state.get("waiting") if isinstance(state.get("waiting"), dict) else {}
        terminated = state.get("terminated") if isinstance(state.get("terminated"), dict) else {}
        container_summaries.append(
            {
                "name": container_status.get("name"),
                "ready": container_status.get("ready"),
                "restart_count": container_status.get("restartCount") or 0,
                "waiting_reason": waiting.get("reason"),
                "terminated_reason": terminated.get("reason"),
            }
        )
    return {
        "namespace": metadata.get("namespace"),
        "name": metadata.get("name"),
        "phase": status.get("phase"),
        "reason": status.get("reason"),
        "message": status.get("message"),
        "containers": container_summaries,
    }


def _storage_summary(
    pvc: JSONObject | None,
    pv: JSONObject | None,
    mounted_pods: list[JSONObject],
    events: list[JSONObject],
) -> JSONObject:
    pvc_status = pvc.get("status") if isinstance((pvc or {}).get("status"), dict) else {}
    pvc_spec = pvc.get("spec") if isinstance((pvc or {}).get("spec"), dict) else {}
    pv_status = pv.get("status") if isinstance((pv or {}).get("status"), dict) else {}
    pv_spec = pv.get("spec") if isinstance((pv or {}).get("spec"), dict) else {}
    volume_name = pvc_spec.get("volumeName")
    if not volume_name and pv is not None:
        volume_name = (pv.get("metadata") or {}).get("name")
    return {
        "pvc_phase": pvc_status.get("phase"),
        "pv_phase": pv_status.get("phase"),
        "storage_class": pvc_spec.get("storageClassName") or pv_spec.get("storageClassName"),
        "volume_name": volume_name,
        "access_modes": pvc_spec.get("accessModes") or pv_spec.get("accessModes") or [],
        "requested_storage": ((pvc_spec.get("resources") or {}).get("requests") or {}).get(
            "storage"
        ),
        "capacity": (pv_spec.get("capacity") or {}).get("storage"),
        "claim_ref": pv_spec.get("claimRef") or pv_spec.get("claim_ref"),
        "mounted_pod_count": len(mounted_pods),
        "mounted_pod_summary": _workload_pod_summary(mounted_pods),
        "event_summary": _event_summary(events),
    }


def _service_summary(
    service: JSONObject,
    pods: list[JSONObject],
    endpoints: JSONObject | None,
    endpoint_slices: list[JSONObject],
    events: list[JSONObject],
) -> JSONObject:
    spec = service.get("spec") if isinstance(service.get("spec"), dict) else {}
    endpoint_counts = _endpoint_counts(endpoints, endpoint_slices)
    return {
        "type": spec.get("type"),
        "cluster_ip": spec.get("clusterIP") or spec.get("cluster_ip"),
        "ports": spec.get("ports") or [],
        "selector": spec.get("selector") or {},
        "selected_pod_summary": _workload_pod_summary(pods),
        "ready_endpoint_count": endpoint_counts["ready"],
        "not_ready_endpoint_count": endpoint_counts["not_ready"],
        "endpoint_slice_count": len(endpoint_slices),
        "event_summary": _event_summary(events),
    }


def _endpoint_counts(
    endpoints: JSONObject | None,
    endpoint_slices: list[JSONObject],
) -> dict[str, int]:
    ready = 0
    not_ready = 0
    for subset in (endpoints or {}).get("subsets") or []:
        if not isinstance(subset, dict):
            continue
        ready += len(subset.get("addresses") or [])
        not_ready += len(subset.get("notReadyAddresses") or subset.get("not_ready_addresses") or [])
    for endpoint_slice in endpoint_slices:
        for endpoint in endpoint_slice.get("endpoints") or []:
            if not isinstance(endpoint, dict):
                continue
            conditions = (
                endpoint.get("conditions") if isinstance(endpoint.get("conditions"), dict) else {}
            )
            if conditions.get("ready") is False:
                not_ready += 1
            else:
                ready += 1
    return {"ready": ready, "not_ready": not_ready}


def _pdb_hpa_summary(
    pdb: JSONObject | None,
    hpa: JSONObject | None,
    pdb_pods: list[JSONObject],
    hpa_target: JSONObject | None,
    events: list[JSONObject],
) -> JSONObject:
    pdb_status = pdb.get("status") if isinstance((pdb or {}).get("status"), dict) else {}
    hpa_status = hpa.get("status") if isinstance((hpa or {}).get("status"), dict) else {}
    hpa_spec = hpa.get("spec") if isinstance((hpa or {}).get("spec"), dict) else {}
    return {
        "pdb": (
            {
                "desired_healthy": pdb_status.get("desiredHealthy")
                or pdb_status.get("desired_healthy"),
                "current_healthy": pdb_status.get("currentHealthy")
                or pdb_status.get("current_healthy"),
                "disruptions_allowed": pdb_status.get("disruptionsAllowed")
                or pdb_status.get("disruptions_allowed"),
                "expected_pods": pdb_status.get("expectedPods") or pdb_status.get("expected_pods"),
                "selected_pod_summary": _workload_pod_summary(pdb_pods),
            }
            if pdb is not None
            else None
        ),
        "hpa": (
            {
                "min_replicas": hpa_spec.get("minReplicas") or hpa_spec.get("min_replicas"),
                "max_replicas": hpa_spec.get("maxReplicas") or hpa_spec.get("max_replicas"),
                "current_replicas": hpa_status.get("currentReplicas")
                or hpa_status.get("current_replicas"),
                "desired_replicas": hpa_status.get("desiredReplicas")
                or hpa_status.get("desired_replicas"),
                "conditions": hpa_status.get("conditions") or [],
                "target_rollout": (
                    _controller_rollout_summary(
                        str(((hpa_spec.get("scaleTargetRef") or {}).get("kind")) or ""),
                        hpa_target,
                    )
                    if hpa_target is not None
                    else None
                ),
            }
            if hpa is not None
            else None
        ),
        "event_summary": _event_summary(events),
    }


def _configmap_summary(configmap: JSONObject) -> JSONObject:
    metadata = configmap.get("metadata") if isinstance(configmap.get("metadata"), dict) else {}
    data = configmap.get("data") if isinstance(configmap.get("data"), dict) else {}
    binary_data = (
        configmap.get("binaryData") if isinstance(configmap.get("binaryData"), dict) else {}
    )
    return {
        "name": metadata.get("name"),
        "namespace": metadata.get("namespace"),
        "resource_version": metadata.get("resourceVersion") or metadata.get("resource_version"),
        "labels": metadata.get("labels") or {},
        "annotation_keys": sorted(
            str(key)
            for key in (
                metadata.get("annotations") if isinstance(metadata.get("annotations"), dict) else {}
            )
        ),
        "annotation_fingerprints": {
            str(key): {
                "length": len(str(value)),
                "sha256": hashlib.sha256(str(value).encode("utf-8")).hexdigest(),
            }
            for key, value in sorted(
                (
                    metadata.get("annotations")
                    if isinstance(metadata.get("annotations"), dict)
                    else {}
                ).items()
            )
        },
        "data_keys": sorted(str(key) for key in data),
        "binary_data_keys": sorted(str(key) for key in binary_data),
        "data_key_count": len(data),
        "binary_data_key_count": len(binary_data),
        "data_fingerprints": {
            str(key): {
                "length": len(str(value)),
                "sha256": hashlib.sha256(str(value).encode("utf-8")).hexdigest(),
            }
            for key, value in sorted(data.items())
        },
        "binary_data_lengths": {
            str(key): len(str(value)) for key, value in sorted(binary_data.items())
        },
    }


def _event_summary(events: list[JSONObject]) -> JSONObject:
    by_type: dict[str, int] = {}
    warning_reasons: dict[str, int] = {}
    for event in events:
        event_type = str(event.get("type") or "Normal")
        by_type[event_type] = by_type.get(event_type, 0) + 1
        if event_type.lower() == "warning":
            reason = str(event.get("reason") or "Unknown")
            warning_reasons[reason] = warning_reasons.get(reason, 0) + 1
    return {
        "event_count": len(events),
        "by_type": by_type,
        "warning_reasons": warning_reasons,
    }


def _event_item_summary(event: JSONObject) -> JSONObject:
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    return {
        "namespace": metadata.get("namespace"),
        "name": metadata.get("name"),
        "type": event.get("type"),
        "reason": event.get("reason"),
        "message": event.get("message"),
        "count": event.get("count"),
        "first_timestamp": event.get("firstTimestamp") or event.get("first_timestamp"),
        "last_timestamp": event.get("lastTimestamp") or event.get("last_timestamp"),
    }


def _int_or_none(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _has_owner(item: JSONObject, *, kind: str, name: str) -> bool:
    for owner in (item.get("metadata") or {}).get("ownerReferences") or []:
        if owner.get("kind") == kind and owner.get("name") == name:
            return True
    return False


def _serialize(api_client: object, value: object) -> JSONObject:
    if isinstance(value, dict):
        return value
    sanitizer = getattr(api_client, "sanitize_for_serialization", None)
    if callable(sanitizer):
        serialized = sanitizer(value)
        return serialized if isinstance(serialized, dict) else {"value": serialized}
    if hasattr(value, "to_dict"):
        serialized = value.to_dict()
        return serialized if isinstance(serialized, dict) else {"value": serialized}
    return {"value": str(value)}


def get_kubernetes_helper() -> KubernetesHelper:
    """Return the Kubernetes helper advertised by the plugin manifest."""
    return KubernetesHelper()


def _default_client_factory() -> KubernetesClientFactory:
    settings = get_settings()
    namespace = str(settings.prometheus_crd_namespace or "").strip()
    if not namespace:
        raise ValueError("Kubernetes namespace is required")
    return KubernetesClientFactory(
        config=KubernetesClientConfig(
            namespace=namespace,
            allow_local_kubeconfig=bool(getattr(settings, "k8s_allow_local_kubeconfig", False)),
        )
    )
