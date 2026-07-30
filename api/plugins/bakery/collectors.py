"""Bakery collection jobs executed by PoundCake monitors."""

from __future__ import annotations

import importlib
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select, text

from api.core.config import get_settings
from api.core.logging import get_logger
from api.models.models import Dish, DishIngredient, Order, ServicePlugin

logger = get_logger(__name__)

ALLOWED_COLLECTOR_TYPES = {
    "monitor_diagnostics",
    "cluster_inventory",
    "ticket_context",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Quantity parsing (ported from original bakery_collectors)
# ---------------------------------------------------------------------------

_QUANTITY_MULTIPLIERS: dict[str, Decimal] = {
    "": Decimal(1),
    "n": Decimal("0.000000001"),
    "u": Decimal("0.000001"),
    "m": Decimal("0.001"),
    "k": Decimal(1000),
    "K": Decimal(1000),
    "M": Decimal(1000**2),
    "G": Decimal(1000**3),
    "T": Decimal(1000**4),
    "P": Decimal(1000**5),
    "E": Decimal(1000**6),
    "Ki": Decimal(1024),
    "Mi": Decimal(1024**2),
    "Gi": Decimal(1024**3),
    "Ti": Decimal(1024**4),
    "Pi": Decimal(1024**5),
    "Ei": Decimal(1024**6),
}


def _metadata_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}


def _parse_quantity(value: Any) -> Decimal:
    text_val = str(value or "").strip()
    if not text_val:
        return Decimal(0)
    suffix = ""
    numeric = text_val
    for candidate in sorted(_QUANTITY_MULTIPLIERS, key=len, reverse=True):
        if candidate and text_val.endswith(candidate):
            suffix = candidate
            numeric = text_val[: -len(candidate)]
            break
    try:
        return Decimal(numeric) * _QUANTITY_MULTIPLIERS[suffix]
    except (InvalidOperation, KeyError):
        return Decimal(0)


def _parse_cpu_millicores(value: Any) -> int:
    return int((_parse_quantity(value) * Decimal(1000)).to_integral_value())


def _parse_int_quantity(value: Any) -> int:
    return int(_parse_quantity(value).to_integral_value())


def _parse_storage_bytes(value: Any) -> int:
    return int(_parse_quantity(value).to_integral_value())


def _node_roles(labels: dict[str, str]) -> list[str]:
    roles: list[str] = []
    for key, value in labels.items():
        if key.startswith("node-role.kubernetes.io/"):
            suffix = key.split("/", 1)[1].strip()
            roles.append(suffix or "worker")
        elif key == "kubernetes.io/role" and value:
            roles.append(str(value))
    return sorted(set(roles))


def _node_conditions(status: Any) -> tuple[list[dict[str, Any]], bool]:
    rows: list[dict[str, Any]] = []
    ready = False
    for item in getattr(status, "conditions", []) or []:
        payload = {
            "type": getattr(item, "type", None),
            "status": getattr(item, "status", None),
            "reason": getattr(item, "reason", None),
            "message": getattr(item, "message", None),
            "last_transition_time": getattr(item, "last_transition_time", None),
            "last_heartbeat_time": getattr(item, "last_heartbeat_time", None),
        }
        rows.append(payload)
        if payload["type"] == "Ready" and str(payload["status"]).lower() == "true":
            ready = True
    return rows, ready


def _address_rows(status: Any) -> list[dict[str, Any]]:
    return [
        {"type": getattr(item, "type", None), "address": getattr(item, "address", None)}
        for item in (getattr(status, "addresses", []) or [])
    ]


def _taint_rows(spec: Any) -> list[dict[str, Any]]:
    return [
        {
            "key": getattr(item, "key", None),
            "value": getattr(item, "value", None),
            "effect": getattr(item, "effect", None),
        }
        for item in (getattr(spec, "taints", []) or [])
    ]


def _resource_totals(values: list[dict[str, Any]]) -> dict[str, int]:
    totals = {
        "cpu_millicores": 0,
        "memory_bytes": 0,
        "ephemeral_storage_bytes": 0,
        "pods": 0,
    }
    for value in values:
        totals["cpu_millicores"] += int(value.get("cpu_millicores") or 0)
        totals["memory_bytes"] += int(value.get("memory_bytes") or 0)
        totals["ephemeral_storage_bytes"] += int(value.get("ephemeral_storage_bytes") or 0)
        totals["pods"] += int(value.get("pods") or 0)
    return totals


def _node_resource_summary(raw: dict[str, Any]) -> dict[str, int]:
    return {
        "cpu_millicores": _parse_cpu_millicores(raw.get("cpu")),
        "memory_bytes": _parse_storage_bytes(raw.get("memory")),
        "ephemeral_storage_bytes": _parse_storage_bytes(raw.get("ephemeral-storage")),
        "pods": _parse_int_quantity(raw.get("pods")),
    }


def _service_ports(service: Any) -> list[str]:
    ports: list[str] = []
    for item in getattr(service.spec, "ports", []) or []:
        base = f"{getattr(item, 'port', '?')}/{getattr(item, 'protocol', 'TCP')}"
        target_port = getattr(item, "target_port", None)
        ports.append(f"{base} -> {target_port}" if target_port else base)
    return ports


# ---------------------------------------------------------------------------
# Health snapshot
# ---------------------------------------------------------------------------


async def _collector_health_snapshot() -> dict[str, Any]:
    from api.core.database import SessionLocal

    settings = get_settings()
    components: dict[str, dict[str, Any]] = {
        "database": {"status": "unknown", "message": ""},
        "stackstorm": {
            "status": "healthy" if getattr(settings, "stackstorm_url", None) else "unhealthy",
            "message": getattr(settings, "stackstorm_url", None) or "stackstorm url not configured",
        },
        "redis": {
            "status": "healthy" if getattr(settings, "redis_url", None) else "unhealthy",
            "message": getattr(settings, "redis_url", None) or "redis url not configured",
        },
    }
    async with SessionLocal() as db:
        try:
            await db.execute(text("SELECT 1"))
            components["database"] = {"status": "healthy", "message": "Connected"}
        except Exception as exc:
            components["database"] = {"status": "unhealthy", "message": str(exc)}

    overall = "healthy"
    if any(item["status"] == "unhealthy" for item in components.values()):
        overall = "unhealthy"
    return {
        "status": overall,
        "version": settings.app_version,
        "instance_id": getattr(settings, "instance_id", None) or _resolve_instance_id(),
        "components": components,
    }


def _resolve_instance_id() -> str:
    import os

    return os.getenv("POD_NAME") or os.getenv("HOSTNAME") or "local"


# ---------------------------------------------------------------------------
# monitor_diagnostics
# ---------------------------------------------------------------------------


async def _monitor_diagnostics(parameters: dict[str, Any]) -> dict[str, Any]:
    from api.core.database import SessionLocal

    settings = get_settings()
    health = await _collector_health_snapshot()

    plugin_state = None
    async with SessionLocal() as db:
        result = await db.execute(
            select(ServicePlugin).where(ServicePlugin.service_type == "bakery")
        )
        plugin = result.scalars().first()

    if plugin is not None:
        plugin_state = {
            "service_type": plugin.service_type,
            "plugin_short_id": plugin.plugin_short_id,
            "enabled": plugin.enabled,
            "health_status": plugin.health_status,
            "health_message": plugin.health_message,
            "health_error_code": plugin.health_error_code,
            "credential_status": plugin.credential_status,
            "credential_error": plugin.credential_error,
            "consecutive_failures": plugin.consecutive_failures,
            "last_health_check_at": plugin.last_health_check_at,
            "last_success_at": plugin.last_success_at,
            "last_credential_bootstrap_at": plugin.last_credential_bootstrap_at,
            "updated_at": plugin.updated_at,
        }

    return {
        "collector_type": "monitor_diagnostics",
        "collected_at": _now(),
        "instance_id": health.get("instance_id", _resolve_instance_id()),
        "monitor_id": getattr(settings, "bakery_monitor_id", None) or "default",
        "environment_label": getattr(settings, "bakery_monitor_environment_label", None) or None,
        "region": getattr(settings, "bakery_monitor_region", None) or None,
        "cluster_name": getattr(settings, "bakery_monitor_cluster_name", None) or None,
        "namespace": getattr(settings, "bakery_monitor_namespace", None) or None,
        "release_name": getattr(settings, "bakery_monitor_release_name", None) or None,
        "tags": list(getattr(settings, "bakery_monitor_tags", []) or []),
        "app_version": settings.app_version,
        "health": health,
        "plugin_state": plugin_state,
    }


# ---------------------------------------------------------------------------
# cluster_inventory
# ---------------------------------------------------------------------------


def _build_inventory_report(
    *,
    namespace: str,
    limit: int,
    summary: dict[str, Any],
) -> dict[str, Any]:
    return {
        "title": "Cluster inventory report",
        "generated_at": _now().isoformat(),
        "scope": {
            "namespace": namespace,
            "namespace_row_limit": limit,
            "cluster_wide_sections": ["nodes", "storage_classes", "persistent_volumes"],
            "namespace_scoped_sections": [
                "persistent_volume_claims",
                "pods",
                "deployments",
                "statefulsets",
                "services",
            ],
        },
        "highlights": [
            f"{summary['ready_node_count']} of {summary['node_count']} nodes ready",
            (
                f"{summary['persistent_volume_count']} persistent volumes across "
                f"{summary['storage_class_count']} storage classes"
            ),
            (
                f"{summary['pod_count']} pods, {summary['deployment_count']} deployments, "
                f"{summary['statefulset_count']} statefulsets in namespace {namespace}"
            ),
        ],
        "sections": [
            {
                "id": "nodes",
                "title": "Node inventory",
                "summary": (
                    f"{summary['node_count']} nodes, {summary['ready_node_count']} ready, "
                    f"{summary.get('unschedulable_node_count', 0)} unschedulable"
                ),
            },
            {
                "id": "storage",
                "title": "Storage topology",
                "summary": (
                    f"{summary['storage_class_count']} storage classes, "
                    f"{summary['persistent_volume_count']} persistent volumes, "
                    f"{summary['persistent_volume_claim_count']} persistent volume claims"
                ),
            },
            {
                "id": "workloads",
                "title": "Namespace workload snapshot",
                "summary": (
                    f"{summary['pod_count']} pods, {summary['deployment_count']} deployments, "
                    f"{summary['statefulset_count']} statefulsets, {summary['service_count']} services"
                ),
            },
        ],
    }


def _load_kubernetes_clients():
    k8s_client_module = importlib.import_module("kubernetes.client")
    k8s_config_module = importlib.import_module("kubernetes.config")
    try:
        k8s_config_module.load_incluster_config()
    except Exception:
        k8s_config_module.load_kube_config()
    return (
        k8s_client_module.CoreV1Api(),
        k8s_client_module.AppsV1Api(),
        k8s_client_module.StorageV1Api(),
    )


async def _cluster_inventory(parameters: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    namespace = (
        str(parameters.get("namespace") or "").strip()
        or getattr(settings, "bakery_monitor_namespace", None)
        or getattr(settings, "prometheus_crd_namespace", None)
        or "default"
    )
    limit = max(1, min(int(parameters.get("limit") or 50), 200))

    v1, apps_v1, storage_v1 = _load_kubernetes_clients()

    nodes = v1.list_node().items
    storage_classes = storage_v1.list_storage_class().items
    persistent_volumes = v1.list_persistent_volume().items
    persistent_volume_claims = v1.list_namespaced_persistent_volume_claim(
        namespace=namespace,
        limit=limit,
    ).items
    pods = v1.list_namespaced_pod(namespace=namespace, limit=limit).items
    deployments = apps_v1.list_namespaced_deployment(namespace=namespace, limit=limit).items
    statefulsets = apps_v1.list_namespaced_stateful_set(namespace=namespace, limit=limit).items
    services = v1.list_namespaced_service(namespace=namespace, limit=limit).items

    node_rows: list[dict[str, Any]] = []
    capacity_rows: list[dict[str, Any]] = []
    allocatable_rows: list[dict[str, Any]] = []
    ready_node_count = 0
    unschedulable_node_count = 0
    for item in nodes:
        labels = _metadata_map(getattr(item.metadata, "labels", None))
        annotations = _metadata_map(getattr(item.metadata, "annotations", None))
        conditions, ready = _node_conditions(item.status)
        roles = _node_roles(labels)
        schedulable = not bool(getattr(item.spec, "unschedulable", False))
        capacity = _metadata_map(getattr(item.status, "capacity", None))
        allocatable = _metadata_map(getattr(item.status, "allocatable", None))
        node_info = getattr(item.status, "node_info", None)
        if ready:
            ready_node_count += 1
        if not schedulable:
            unschedulable_node_count += 1
        capacity_summary = _node_resource_summary(capacity)
        allocatable_summary = _node_resource_summary(allocatable)
        capacity_rows.append(capacity_summary)
        allocatable_rows.append(allocatable_summary)
        node_rows.append(
            {
                "name": item.metadata.name,
                "ready": ready,
                "schedulable": schedulable,
                "roles": roles,
                "labels": labels,
                "annotations": annotations,
                "taints": _taint_rows(item.spec),
                "addresses": _address_rows(item.status),
                "conditions": conditions,
                "kubelet_version": getattr(node_info, "kubelet_version", None),
                "container_runtime_version": getattr(node_info, "container_runtime_version", None),
                "operating_system": getattr(node_info, "operating_system", None),
                "os_image": getattr(node_info, "os_image", None),
                "architecture": getattr(node_info, "architecture", None),
                "kernel_version": getattr(node_info, "kernel_version", None),
                "capacity_cpu": capacity.get("cpu"),
                "capacity_memory": capacity.get("memory"),
                "capacity_ephemeral_storage": capacity.get("ephemeral-storage"),
                "capacity_pods": capacity.get("pods"),
                "allocatable_cpu": allocatable.get("cpu"),
                "allocatable_memory": allocatable.get("memory"),
                "allocatable_ephemeral_storage": allocatable.get("ephemeral-storage"),
                "allocatable_pods": allocatable.get("pods"),
            }
        )

    storage_class_rows = [
        {
            "name": item.metadata.name,
            "provisioner": item.provisioner,
            "reclaim_policy": item.reclaim_policy,
            "volume_binding_mode": item.volume_binding_mode,
            "allow_volume_expansion": bool(item.allow_volume_expansion),
            "mount_options": list(item.mount_options or []),
            "labels": _metadata_map(getattr(item.metadata, "labels", None)),
            "annotations": _metadata_map(getattr(item.metadata, "annotations", None)),
        }
        for item in storage_classes
    ]
    persistent_volume_rows = [
        {
            "name": item.metadata.name,
            "phase": getattr(item.status, "phase", None),
            "storage_class_name": getattr(item.spec, "storage_class_name", None),
            "capacity": _metadata_map(getattr(item.spec, "capacity", None)).get("storage"),
            "access_modes": list(getattr(item.spec, "access_modes", []) or []),
            "reclaim_policy": getattr(item.spec, "persistent_volume_reclaim_policy", None),
            "claim_ref": (
                f"{item.spec.claim_ref.namespace}/{item.spec.claim_ref.name}"
                if getattr(item.spec, "claim_ref", None)
                else None
            ),
            "volume_mode": getattr(item.spec, "volume_mode", None),
            "csi_driver": getattr(getattr(item.spec, "csi", None), "driver", None),
            "labels": _metadata_map(getattr(item.metadata, "labels", None)),
            "annotations": _metadata_map(getattr(item.metadata, "annotations", None)),
        }
        for item in persistent_volumes
    ]
    persistent_volume_claim_rows = [
        {
            "name": item.metadata.name,
            "namespace": item.metadata.namespace,
            "phase": getattr(item.status, "phase", None),
            "storage_class_name": getattr(item.spec, "storage_class_name", None),
            "requested_storage": _metadata_map(
                getattr(getattr(item.spec, "resources", None), "requests", None)
            ).get("storage"),
            "access_modes": list(getattr(item.spec, "access_modes", []) or []),
            "volume_name": getattr(item.spec, "volume_name", None),
            "volume_mode": getattr(item.spec, "volume_mode", None),
            "labels": _metadata_map(getattr(item.metadata, "labels", None)),
            "annotations": _metadata_map(getattr(item.metadata, "annotations", None)),
        }
        for item in persistent_volume_claims
    ]
    pod_rows = [
        {
            "name": item.metadata.name,
            "phase": item.status.phase,
            "pod_ip": item.status.pod_ip,
            "node_name": item.spec.node_name,
            "restart_count": sum(
                int(getattr(status, "restart_count", 0))
                for status in (getattr(item.status, "container_statuses", None) or [])
            ),
            "start_time": item.status.start_time,
            "qos_class": getattr(item.status, "qos_class", None),
            "labels": _metadata_map(getattr(item.metadata, "labels", None)),
        }
        for item in pods
    ]
    deployment_rows = [
        {
            "name": item.metadata.name,
            "ready_replicas": item.status.ready_replicas or 0,
            "available_replicas": item.status.available_replicas or 0,
            "replicas": item.spec.replicas or 0,
            "labels": _metadata_map(getattr(item.metadata, "labels", None)),
        }
        for item in deployments
    ]
    statefulset_rows = [
        {
            "name": item.metadata.name,
            "ready_replicas": item.status.ready_replicas or 0,
            "replicas": item.spec.replicas or 0,
            "service_name": item.spec.service_name,
            "labels": _metadata_map(getattr(item.metadata, "labels", None)),
        }
        for item in statefulsets
    ]
    service_rows = [
        {
            "name": item.metadata.name,
            "type": item.spec.type,
            "cluster_ip": item.spec.cluster_ip,
            "ports": _service_ports(item),
            "labels": _metadata_map(getattr(item.metadata, "labels", None)),
        }
        for item in services
    ]
    cluster_summary = {
        "namespace": namespace,
        "limit": limit,
        "node_count": len(node_rows),
        "ready_node_count": ready_node_count,
        "unschedulable_node_count": unschedulable_node_count,
        "storage_class_count": len(storage_class_rows),
        "persistent_volume_count": len(persistent_volume_rows),
        "persistent_volume_claim_count": len(persistent_volume_claim_rows),
        "pod_count": len(pod_rows),
        "deployment_count": len(deployment_rows),
        "statefulset_count": len(statefulset_rows),
        "service_count": len(service_rows),
        "capacity": _resource_totals(capacity_rows),
        "allocatable": _resource_totals(allocatable_rows),
    }

    return {
        "collector_type": "cluster_inventory",
        "collected_at": _now().isoformat(),
        "namespace": namespace,
        "limit": limit,
        "node_count": len(node_rows),
        "ready_node_count": ready_node_count,
        "storage_class_count": len(storage_class_rows),
        "persistent_volume_count": len(persistent_volume_rows),
        "persistent_volume_claim_count": len(persistent_volume_claim_rows),
        "pod_count": len(pod_rows),
        "deployment_count": len(deployment_rows),
        "statefulset_count": len(statefulset_rows),
        "service_count": len(service_rows),
        "cluster_summary": cluster_summary,
        "report": _build_inventory_report(
            namespace=namespace, limit=limit, summary=cluster_summary
        ),
        "nodes": node_rows,
        "storage_classes": storage_class_rows,
        "persistent_volumes": persistent_volume_rows,
        "persistent_volume_claims": persistent_volume_claim_rows,
        "pods": pod_rows,
        "deployments": deployment_rows,
        "statefulsets": statefulset_rows,
        "services": service_rows,
    }


# ---------------------------------------------------------------------------
# ticket_context
# ---------------------------------------------------------------------------


async def _ticket_context(parameters: dict[str, Any]) -> dict[str, Any]:
    from api.core.database import SessionLocal

    order_id = int(parameters["order_id"]) if parameters.get("order_id") is not None else None
    req_id = str(parameters.get("req_id") or "").strip()
    bakery_ticket_id = str(parameters.get("bakery_ticket_id") or "").strip()
    limit = max(1, min(int(parameters.get("limit") or 20), 100))

    criteria = {
        "order_id": order_id,
        "req_id": req_id or None,
        "bakery_ticket_id": bakery_ticket_id or None,
        "limit": limit,
    }

    async with SessionLocal() as db:
        order_query = select(Order)
        if order_id is not None:
            order_query = order_query.where(Order.id == order_id)
        if req_id:
            order_query = order_query.where(Order.req_id == req_id)
        if bakery_ticket_id:
            order_query = order_query.where(Order.fingerprint == bakery_ticket_id)
        orders = (
            (await db.execute(order_query.order_by(Order.updated_at.desc()).limit(limit)))
            .scalars()
            .all()
        )

        order_ids = [item.id for item in orders]
        req_ids = [item.req_id for item in orders]

        ingredients: list[DishIngredient] = []
        if order_ids:
            ingredient_query = (
                select(DishIngredient)
                .where(DishIngredient.req_id.in_(req_ids))
                .order_by(DishIngredient.updated_at.desc())
            )
            ingredients = (await db.execute(ingredient_query.limit(limit))).scalars().all()

        dish_query = select(Dish)
        if order_ids:
            dish_query = dish_query.where(
                or_(Dish.order_id.in_(order_ids), Dish.req_id.in_(req_ids))
            )
        elif req_id:
            dish_query = dish_query.where(Dish.req_id == req_id)
        dishes = (
            (await db.execute(dish_query.order_by(Dish.updated_at.desc()).limit(limit)))
            .scalars()
            .all()
        )

    return {
        "collector_type": "ticket_context",
        "collected_at": _now().isoformat(),
        "criteria": criteria,
        "orders": [
            {
                "id": item.id,
                "req_id": item.req_id,
                "alert_group_name": item.alert_group_name,
                "alert_status": item.alert_status,
                "processing_status": item.processing_status,
                "remediation_outcome": item.remediation_outcome,
                "fingerprint": item.fingerprint,
                "is_active": item.is_active,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
            }
            for item in orders
        ],
        "ingredients": [
            {
                "id": item.id,
                "req_id": item.req_id,
                "dish_id": item.dish_id,
                "task_key": item.task_key,
                "service_type": item.service_type,
                "service_exec": item.service_exec,
                "destination_target": item.destination_target,
                "service_exec_id": item.service_exec_id,
                "service_exec_status": item.service_exec_status,
                "service_exec_error": item.service_exec_error,
                "updated_at": item.updated_at,
            }
            for item in ingredients
        ],
        "dishes": [
            {
                "id": item.id,
                "req_id": item.req_id,
                "order_id": item.order_id,
                "recipe_id": item.recipe_id,
                "run_phase": item.run_phase,
                "processing_status": item.processing_status,
                "dish_exec_status": item.dish_exec_status,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
            }
            for item in dishes
        ],
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run_collection_job(
    collector_type: str,
    parameters: dict[str, Any],
    *,
    req_id: str,
) -> dict[str, Any]:
    normalized = str(collector_type or "").strip()
    payload = dict(parameters or {})
    if normalized not in ALLOWED_COLLECTOR_TYPES:
        raise ValueError(f"Unsupported collector type: {normalized}")

    logger.info(
        "Bakery collection job started",
        extra={
            "req_id": req_id,
            "collector_type": normalized,
        },
    )

    if normalized == "monitor_diagnostics":
        return await _monitor_diagnostics(payload)
    if normalized == "cluster_inventory":
        return await _cluster_inventory(payload)
    if normalized == "ticket_context":
        return await _ticket_context(payload)
    raise ValueError(f"Unsupported collector type: {normalized}")
