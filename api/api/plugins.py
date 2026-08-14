"""Service plugin registry state APIs."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import get_settings
from api.core.database import get_db
from api.core.rate_limit import limiter
from api.core.time import utc_now_db
from api.models.models import ScheduledTask, ServicePlugin
from api.plugins.catalog import (
    get_enabled_plugin_helper_capabilities,
    get_enabled_plugins,
    missing_helper_capabilities_for,
)
from api.api.auth import require_admin, require_operator, require_reader
from api.plugins.manifest import ServicePlugin as ServicePluginManifest
from api.plugins.manifest import ServicePluginManifestError
from api.plugins.state import (
    PLUGIN_RUN_STATE_DISABLED,
    PLUGIN_RUN_STATE_UNKNOWN,
)
from api.plugins.base import ExecutionAdapter
from api.services.credential_manager import (
    ServicePluginCredentialError,
    read_adapter_credential_with_policy,
    write_adapter_credential,
)
from api.services.order_intake import (
    OperatorActionOrderSubmission,
    submit_operator_action_order,
)
from api.services.prometheus_reload import reload_prometheus_rules
from api.schemas.schemas import (
    GenestackAlertExportRequest,
    OperatorActionAcceptedResponse,
    PrometheusRuleDetailResponse,
    PrometheusRuleGroupSummary,
    PrometheusRuleListResponse,
    PrometheusRuleRuleCreateRequest,
    PrometheusRuleRuleResponse,
    PrometheusRuleRuleUpdateRequest,
    PrometheusRuleResourceResponse,
    ServicePluginConfigurationResponse,
    ServicePluginConfigurationUpdate,
    ServicePluginConnectionTestRequest,
    ServicePluginCredentialUpdate,
    ServicePluginHealthResponse,
    ServicePluginResponse,
    ServicePluginSummaryResponse,
    ServicePluginUpdate,
)

router = APIRouter()


def _enabled_plugins() -> list[ServicePluginManifest]:
    try:
        return get_enabled_plugins()
    except ServicePluginManifestError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _plugin_by_service_type(service_type: str) -> ServicePluginManifest:
    normalized = (service_type or "").strip().lower()
    for plugin in _enabled_plugins():
        if plugin.service_type.strip().lower() == normalized:
            return plugin
    raise HTTPException(status_code=404, detail=f"Enabled plugin not found: {service_type}")


def _empty_helper_metadata() -> dict[str, object]:
    return {
        "helper_available": False,
        "helper_capabilities": [],
        "required_helper_capabilities": {},
        "missing_helper_capabilities": {},
    }


def _helper_metadata(plugin: ServicePluginManifest) -> dict[str, object]:
    capabilities = get_enabled_plugin_helper_capabilities()
    return {
        "helper_available": plugin.helper_factory is not None,
        "helper_capabilities": capabilities.get(plugin.service_type.strip().lower(), []),
        "required_helper_capabilities": {
            str(provider)
            .strip()
            .lower(): sorted({str(capability).strip().lower() for capability in required})
            for provider, required in (plugin.required_helper_capabilities or {}).items()
        },
        "missing_helper_capabilities": missing_helper_capabilities_for(plugin, capabilities),
    }


def _summary_from_row(
    row: ServicePlugin,
    *,
    plugin: ServicePluginManifest | None = None,
    health_check_task: ScheduledTask | None = None,
) -> ServicePluginSummaryResponse:
    helper_metadata = _helper_metadata(plugin) if plugin is not None else _empty_helper_metadata()
    return ServicePluginSummaryResponse(
        service_type=row.service_type,
        plugin_short_id=row.plugin_short_id,
        plugin_type=row.plugin_type,
        plugin_tier=row.plugin_tier,
        plugin_log_key=row.plugin_log_key,
        enabled=bool(row.enabled),
        run_interval_seconds=row.run_interval_seconds,
        query_limit=row.query_limit,
        status_message=row.status_message,
        config_editable=row.plugin_type == "internal_plugin",
        ingredient_template_count=int(row.registered_ingredient_count),
        recipe_template_count=int(row.registered_recipe_count),
        credential_status=row.credential_status,
        credential_error=row.credential_error,
        last_credential_bootstrap_at=row.last_credential_bootstrap_at,
        last_credential_rotation_at=row.last_credential_rotation_at,
        health_status=row.health_status,
        health_message=row.health_message,
        health_error_code=row.health_error_code,
        health_latency_ms=row.health_latency_ms,
        last_health_check_at=row.last_health_check_at,
        next_health_check_at=row.next_health_check_at,
        health_check_task_id=health_check_task.id if health_check_task is not None else None,
        health_check_interval_seconds=(
            health_check_task.run_interval_seconds if health_check_task is not None else None
        ),
        health_check_enabled=(
            bool(health_check_task.is_enabled) if health_check_task is not None else False
        ),
        last_success_at=row.last_success_at,
        consecutive_failures=int(row.consecutive_failures or 0),
        health_check_state=row.health_check_state or "idle",
        health_check_order_id=row.health_check_order_id,
        health_check_started_at=row.health_check_started_at,
        health_check_grace_until=row.health_check_grace_until,
        **helper_metadata,
    )


def _summary_from_manifest(
    plugin: ServicePluginManifest,
    row: ServicePlugin | None,
    health_check_task: ScheduledTask | None = None,
) -> ServicePluginSummaryResponse:
    service_type = plugin.service_type.strip().lower()
    if row is not None:
        return _summary_from_row(row, plugin=plugin, health_check_task=health_check_task)
    template_interval = _health_check_template_interval(plugin)
    return ServicePluginSummaryResponse(
        service_type=service_type,
        plugin_short_id="unknown",
        plugin_type="external_plugin",
        plugin_tier="community",
        plugin_log_key=None,
        enabled=True,
        run_interval_seconds=None,
        query_limit=None,
        status_message=None,
        config_editable=False,
        ingredient_template_count=len(plugin.ingredient_templates),
        recipe_template_count=len(plugin.recipe_templates),
        credential_status="unknown",
        credential_error=None,
        last_credential_bootstrap_at=None,
        last_credential_rotation_at=None,
        health_status=PLUGIN_RUN_STATE_UNKNOWN,
        health_message=None,
        health_error_code=None,
        health_latency_ms=None,
        last_health_check_at=None,
        next_health_check_at=None,
        health_check_task_id=None,
        health_check_interval_seconds=template_interval,
        health_check_enabled=template_interval is not None,
        last_success_at=None,
        consecutive_failures=0,
        health_check_state="idle",
        health_check_order_id=None,
        health_check_started_at=None,
        health_check_grace_until=None,
        **_helper_metadata(plugin),
    )


def _health_check_template_interval(plugin: ServicePluginManifest) -> int | None:
    service_type = plugin.service_type.strip().lower()
    for task in plugin.scheduled_tasks:
        if (
            str(task.get("task_type") or "").strip().lower() == "plugin_health_check"
            and str(task.get("service_type") or "").strip().lower() == service_type
        ):
            try:
                return max(1, int(task.get("run_interval_seconds") or 1))
            except (TypeError, ValueError):
                return None
    return None


async def _health_check_task_for_service(
    db: AsyncSession,
    service_type: str,
) -> ScheduledTask | None:
    result = await db.execute(
        select(ScheduledTask)
        .where(
            ScheduledTask.task_type == "plugin_health_check",
            ScheduledTask.service_type == service_type.strip().lower(),
        )
        .order_by(ScheduledTask.id.asc())
    )
    return result.scalars().first()


async def _plugin_manifest_tasks_for_service(
    db: AsyncSession,
    service_type: str,
) -> list[ScheduledTask]:
    result = await db.execute(
        select(ScheduledTask)
        .where(
            ScheduledTask.source == "plugin_manifest",
            ScheduledTask.service_type == service_type.strip().lower(),
        )
        .order_by(ScheduledTask.id.asc())
    )
    return list(result.scalars().all())


async def _parse_credential_update(request: Request) -> ServicePluginCredentialUpdate:
    """Parse secret-bearing credential requests without echoing submitted values."""
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid credential request body") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid credential request body")
    try:
        return ServicePluginCredentialUpdate.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="Invalid credential request body") from exc


async def _credential_configured(
    *,
    row: ServicePlugin,
    credential_type: str,
    credential_key_id: str = "default",
) -> bool:
    try:
        result = await read_adapter_credential_with_policy(
            service_type=row.service_type,
            credential_type=credential_type,
            credential_key_id=credential_key_id,
        )
    except ServicePluginCredentialError:
        return False
    return result is not None


async def _external_plugin_row_or_404(
    db: AsyncSession,
    service_type: str,
) -> tuple[ServicePlugin, ServicePluginManifest, ExecutionAdapter]:
    normalized = service_type.strip().lower()
    result = await db.execute(select(ServicePlugin).where(ServicePlugin.service_type == normalized))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Service plugin not found: {service_type}")
    if row.plugin_type != "external_plugin":
        raise HTTPException(
            status_code=400,
            detail="Operator plugin connection configuration is only supported for external plugins",
        )
    plugin = _plugin_by_service_type(normalized)
    adapter = plugin.adapter_factory()
    return row, plugin, adapter


def _plugin_config_from_row(row: ServicePlugin, adapter: ExecutionAdapter) -> dict[str, object]:
    config = dict(adapter.default_operator_config())
    if isinstance(row.plugin_config, dict):
        config.update(row.plugin_config)
    return config


def _normalize_plugin_config(
    adapter: ExecutionAdapter, config: dict[str, object]
) -> dict[str, object]:
    try:
        return adapter.normalize_operator_config(config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _credential_requirement(
    adapter: ExecutionAdapter,
    credential_type: str | None = None,
) -> dict[str, object] | None:
    requirements = adapter.credential_requirements()
    if not requirements:
        return None
    if credential_type is None:
        return dict(requirements[0])
    normalized = credential_type.strip().lower()
    for requirement in requirements:
        if str(requirement.get("credential_type") or "").strip().lower() == normalized:
            return dict(requirement)
    return None


def _configuration_response(
    *,
    row: ServicePlugin,
    adapter: ExecutionAdapter,
    config: dict[str, object],
    credential_key_id: str,
    credential_configured: bool,
    credential_type: str | None = None,
) -> ServicePluginConfigurationResponse:
    requirement = _credential_requirement(adapter, credential_type)
    return ServicePluginConfigurationResponse(
        service_type=row.service_type,
        config=config,
        config_schema=adapter.operator_config_schema(),
        credential_requirements=adapter.credential_requirements(),
        credential_type=(
            str(requirement.get("credential_type"))
            if requirement and requirement.get("credential_type")
            else None
        ),
        credential_key_id=credential_key_id,
        credential_configured=credential_configured,
        updated_at=row.updated_at,
    )


def _prometheus_rule_resource_from_crd(crd: dict[str, object]) -> PrometheusRuleResourceResponse:
    metadata = crd.get("metadata") if isinstance(crd.get("metadata"), dict) else {}
    spec = crd.get("spec") if isinstance(crd.get("spec"), dict) else {}
    raw_groups = spec.get("groups") if isinstance(spec.get("groups"), list) else []
    groups: list[PrometheusRuleGroupSummary] = []
    for raw_group in raw_groups:
        if not isinstance(raw_group, dict):
            continue
        raw_rules = raw_group.get("rules") if isinstance(raw_group.get("rules"), list) else []
        alert_names: list[str] = []
        recording_names: list[str] = []
        for raw_rule in raw_rules:
            if not isinstance(raw_rule, dict):
                continue
            alert_name = str(raw_rule.get("alert") or "").strip()
            record_name = str(raw_rule.get("record") or "").strip()
            if alert_name:
                alert_names.append(alert_name)
            if record_name:
                recording_names.append(record_name)
        groups.append(
            PrometheusRuleGroupSummary(
                name=str(raw_group.get("name") or "unnamed"),
                rule_count=len([rule for rule in raw_rules if isinstance(rule, dict)]),
                alert_count=len(alert_names),
                recording_count=len(recording_names),
                alert_names=alert_names,
                recording_names=recording_names,
            )
        )
    labels = metadata.get("labels") if isinstance(metadata.get("labels"), dict) else {}
    annotations = (
        metadata.get("annotations") if isinstance(metadata.get("annotations"), dict) else {}
    )
    namespace = str(metadata.get("namespace") or "").strip()
    return PrometheusRuleResourceResponse(
        name=str(metadata.get("name") or "unknown"),
        namespace=namespace,
        labels={str(key): value for key, value in labels.items()},
        annotations={str(key): value for key, value in annotations.items()},
        groups=groups,
        group_count=len(groups),
        rule_count=sum(group.rule_count for group in groups),
        alert_count=sum(group.alert_count for group in groups),
        recording_count=sum(group.recording_count for group in groups),
        raw=crd,
    )


def _rule_identity(rule: object) -> str:
    if not isinstance(rule, dict):
        return ""
    return str(rule.get("alert") or rule.get("record") or "").strip()


def _rule_source_from_crd(crd: dict[str, object], rule_name: str) -> dict[str, object] | None:
    metadata = crd.get("metadata") if isinstance(crd.get("metadata"), dict) else {}
    annotations = (
        metadata.get("annotations") if isinstance(metadata.get("annotations"), dict) else {}
    )
    from api.services.alert_rule_repo import load_alert_rule_sources_from_annotations

    sources = load_alert_rule_sources_from_annotations(annotations)
    source = sources.get(rule_name)
    return source.as_annotation_value() if source is not None else None


def _rule_from_crd(
    crd: dict[str, object],
    *,
    group_name: str,
    rule_name: str,
) -> PrometheusRuleRuleResponse | None:
    metadata = crd.get("metadata") if isinstance(crd.get("metadata"), dict) else {}
    spec = crd.get("spec") if isinstance(crd.get("spec"), dict) else {}
    groups = spec.get("groups") if isinstance(spec.get("groups"), list) else []
    for group in groups:
        if not isinstance(group, dict) or str(group.get("name") or "").strip() != group_name:
            continue
        rules = group.get("rules") if isinstance(group.get("rules"), list) else []
        for raw_rule in rules:
            if not isinstance(raw_rule, dict) or _rule_identity(raw_rule) != rule_name:
                continue
            rule_kind = "record" if str(raw_rule.get("record") or "").strip() else "alert"
            return PrometheusRuleRuleResponse(
                namespace=str(metadata.get("namespace") or "").strip(),
                crd_name=str(metadata.get("name") or "").strip(),
                group_name=group_name,
                rule_name=rule_name,
                rule_kind=rule_kind,
                source=_rule_source_from_crd(crd, rule_name),
                rule_data=dict(raw_rule),
                checked_at=utc_now_db(),
            )
    return None


def _operator_action_accepted_response(
    submission: OperatorActionOrderSubmission,
    *,
    message: str,
) -> OperatorActionAcceptedResponse:
    return OperatorActionAcceptedResponse(
        status="accepted",
        message=message,
        order_id=submission.order_id,
        order_req_id=submission.order_req_id,
        service_type=submission.service_type,
        service_exec=submission.service_exec,
        submitted_at=submission.submitted_at,
    )


def _resolved_adapter_helper(adapter: ExecutionAdapter) -> object | None:
    helper = getattr(adapter, "helper", None)
    if helper is not None:
        return helper
    resolver = getattr(adapter, "_resolve_helper", None)
    if callable(resolver):
        return resolver()
    return None


@router.get("/plugins", response_model=list[ServicePluginSummaryResponse])
async def list_enabled_plugins(
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_reader),
) -> list[ServicePluginSummaryResponse]:
    """List enabled service plugins without exposing credentials or internals."""
    result = await db.execute(select(ServicePlugin))
    persisted = {row.service_type: row for row in result.scalars().all()}
    health_task_result = await db.execute(
        select(ScheduledTask).where(ScheduledTask.task_type == "plugin_health_check")
    )
    health_tasks = {
        str(row.service_type or "").strip().lower(): row
        for row in health_task_result.scalars().all()
        if row.service_type
    }
    responses: list[ServicePluginSummaryResponse] = []
    for row in sorted(persisted.values(), key=lambda item: item.service_type):
        if row.plugin_type == "internal_plugin":
            responses.append(_summary_from_row(row))
    for plugin in _enabled_plugins():
        service_type = plugin.service_type.strip().lower()
        row = persisted.get(service_type)
        responses.append(
            _summary_from_manifest(plugin, row, health_check_task=health_tasks.get(service_type))
        )
    return responses


@router.get(
    "/plugins/k8s/prometheus-rules",
    response_model=PrometheusRuleListResponse,
)
async def list_kubernetes_prometheus_rules(
    namespace: str | None = Query(default=None, min_length=1, max_length=255),
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_reader),
) -> PrometheusRuleListResponse:
    """List Prometheus Operator PrometheusRule CRDs through the Kubernetes plugin."""
    row, _plugin, adapter = await _external_plugin_row_or_404(db, "k8s")
    config = _plugin_config_from_row(row, adapter)
    if namespace is not None:
        config["namespace"] = namespace.strip()
    configured_adapter = adapter.with_operator_config(_normalize_plugin_config(adapter, config))
    helper = _resolved_adapter_helper(configured_adapter)
    if helper is None or not hasattr(helper, "list_prometheus_rules"):
        raise HTTPException(status_code=500, detail="Kubernetes PrometheusRule helper unavailable")
    try:
        crds = await helper.list_prometheus_rules()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502, detail=f"Failed to list PrometheusRule CRDs: {exc}"
        ) from exc
    items = [_prometheus_rule_resource_from_crd(crd) for crd in crds if isinstance(crd, dict)]
    resolved_namespace = str(
        _normalize_plugin_config(adapter, config).get("namespace") or namespace or ""
    )
    return PrometheusRuleListResponse(
        namespace=resolved_namespace,
        items=items,
        resource_count=len(items),
        group_count=sum(item.group_count for item in items),
        rule_count=sum(item.rule_count for item in items),
        alert_count=sum(item.alert_count for item in items),
        recording_count=sum(item.recording_count for item in items),
        checked_at=utc_now_db(),
    )


@router.get(
    "/plugins/k8s/prometheus-rules/{crd_name}",
    response_model=PrometheusRuleDetailResponse,
)
async def get_kubernetes_prometheus_rule(
    crd_name: str,
    namespace: str | None = Query(default=None, min_length=1, max_length=255),
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_reader),
) -> PrometheusRuleDetailResponse:
    row, _plugin, adapter = await _external_plugin_row_or_404(db, "k8s")
    config = _plugin_config_from_row(row, adapter)
    if namespace is not None:
        config["namespace"] = namespace.strip()
    configured_adapter = adapter.with_operator_config(_normalize_plugin_config(adapter, config))
    helper = _resolved_adapter_helper(configured_adapter)
    if helper is None or not hasattr(helper, "get_prometheus_rule"):
        raise HTTPException(status_code=500, detail="Kubernetes PrometheusRule helper unavailable")
    try:
        crd = await helper.get_prometheus_rule(crd_name.strip())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502, detail=f"Failed to read PrometheusRule CRD: {exc}"
        ) from exc
    if crd is None or not isinstance(crd, dict):
        raise HTTPException(status_code=404, detail=f"PrometheusRule CRD not found: {crd_name}")
    return PrometheusRuleDetailResponse(
        **_prometheus_rule_resource_from_crd(crd).model_dump(),
        checked_at=utc_now_db(),
    )


@router.get(
    "/plugins/k8s/prometheus-rules/{crd_name}/rules/{rule_name}",
    response_model=PrometheusRuleRuleResponse,
)
async def get_kubernetes_prometheus_rule_rule(
    crd_name: str,
    rule_name: str,
    group_name: str = Query(..., min_length=1, max_length=255),
    namespace: str | None = Query(default=None, min_length=1, max_length=255),
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_reader),
) -> PrometheusRuleRuleResponse:
    row, _plugin, adapter = await _external_plugin_row_or_404(db, "k8s")
    config = _plugin_config_from_row(row, adapter)
    if namespace is not None:
        config["namespace"] = namespace.strip()
    configured_adapter = adapter.with_operator_config(_normalize_plugin_config(adapter, config))
    helper = _resolved_adapter_helper(configured_adapter)
    if helper is None or not hasattr(helper, "get_prometheus_rule"):
        raise HTTPException(status_code=500, detail="Kubernetes PrometheusRule helper unavailable")
    crd = await helper.get_prometheus_rule(crd_name.strip())
    if crd is None or not isinstance(crd, dict):
        raise HTTPException(status_code=404, detail=f"PrometheusRule CRD not found: {crd_name}")
    response = _rule_from_crd(crd, group_name=group_name.strip(), rule_name=rule_name.strip())
    if response is None:
        raise HTTPException(
            status_code=404,
            detail=f"Rule '{rule_name}' not found in group '{group_name}' within CRD '{crd_name}'",
        )
    return response


@router.put(
    "/plugins/k8s/prometheus-rules/{crd_name}/rules/{rule_name}",
    response_model=OperatorActionAcceptedResponse,
    status_code=202,
)
async def update_kubernetes_prometheus_rule_rule(
    crd_name: str,
    rule_name: str,
    payload: PrometheusRuleRuleUpdateRequest,
    request: Request,
    namespace: str | None = Query(default=None, min_length=1, max_length=255),
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_operator),
) -> OperatorActionAcceptedResponse:
    row, _plugin, adapter = await _external_plugin_row_or_404(db, "k8s")
    config = _plugin_config_from_row(row, adapter)
    if namespace is not None:
        config["namespace"] = namespace.strip()
    configured_adapter = adapter.with_operator_config(_normalize_plugin_config(adapter, config))
    helper = _resolved_adapter_helper(configured_adapter)
    if helper is None or not hasattr(helper, "get_prometheus_rule"):
        raise HTTPException(status_code=500, detail="Kubernetes PrometheusRule helper unavailable")
    existing_crd = await helper.get_prometheus_rule(crd_name.strip())
    if existing_crd is None or not isinstance(existing_crd, dict):
        raise HTTPException(status_code=404, detail=f"PrometheusRule CRD not found: {crd_name}")
    existing_rule = _rule_from_crd(
        existing_crd,
        group_name=payload.group_name.strip(),
        rule_name=rule_name.strip(),
    )
    if existing_rule is None:
        raise HTTPException(
            status_code=404,
            detail=f"Rule '{rule_name}' not found in group '{payload.group_name}' within CRD '{crd_name}'",
        )
    service_payload = {
        "crd_name": crd_name.strip(),
        "group_name": payload.group_name.strip(),
        "rule_name": rule_name.strip(),
        "rule_data": dict(payload.rule_data),
    }
    if namespace is not None:
        service_payload["namespace"] = namespace.strip()
    submission = await submit_operator_action_order(
        db=db,
        req_id=getattr(request.state, "req_id", "plugin-k8s-prometheus-rule-update"),
        recipe_name="operator-action:k8s:prometheus-rule-apply",
        service_type="k8s",
        service_exec="prometheus_rule",
        task_key_template="k8s-prometheus-rule",
        service_payload=service_payload,
    )
    return _operator_action_accepted_response(
        submission,
        message="PrometheusRule update order accepted",
    )


@router.post(
    "/plugins/k8s/prometheus-rules/{crd_name}/rules",
    response_model=OperatorActionAcceptedResponse,
    status_code=202,
)
async def create_kubernetes_prometheus_rule_rule(
    crd_name: str,
    payload: PrometheusRuleRuleCreateRequest,
    request: Request,
    namespace: str | None = Query(default=None, min_length=1, max_length=255),
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_operator),
) -> OperatorActionAcceptedResponse:
    row, _plugin, adapter = await _external_plugin_row_or_404(db, "k8s")
    config = _plugin_config_from_row(row, adapter)
    if namespace is not None:
        config["namespace"] = namespace.strip()
    configured_adapter = adapter.with_operator_config(_normalize_plugin_config(adapter, config))
    helper = _resolved_adapter_helper(configured_adapter)
    if helper is None or not hasattr(helper, "get_prometheus_rule"):
        raise HTTPException(status_code=500, detail="Kubernetes PrometheusRule helper unavailable")
    service_payload = {
        "crd_name": crd_name.strip(),
        "group_name": payload.group_name.strip(),
        "rule_name": payload.rule_name.strip(),
        "rule_data": dict(payload.rule_data),
    }
    if namespace is not None:
        service_payload["namespace"] = namespace.strip()
    submission = await submit_operator_action_order(
        db=db,
        req_id=getattr(request.state, "req_id", "plugin-k8s-prometheus-rule-create"),
        recipe_name="operator-action:k8s:prometheus-rule-apply",
        service_type="k8s",
        service_exec="prometheus_rule",
        task_key_template="k8s-prometheus-rule",
        service_payload=service_payload,
    )
    return _operator_action_accepted_response(
        submission,
        message="PrometheusRule create order accepted",
    )


@router.post(
    "/plugins/genestack_monitoring/export-alert-updates",
    response_model=OperatorActionAcceptedResponse,
    status_code=202,
)
async def export_genestack_alert_updates(
    payload: GenestackAlertExportRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_operator),
) -> OperatorActionAcceptedResponse:
    await _external_plugin_row_or_404(db, "genestack_monitoring")
    req_id = getattr(request.state, "req_id", "plugin-genestack-export")
    submission = await submit_operator_action_order(
        db=db,
        req_id=req_id,
        recipe_name="operator-action:genestack-monitoring:export-alert-updates",
        service_type="genestack_monitoring",
        service_exec="repo_sync",
        task_key_template="genestack-monitoring-alert-export",
        service_payload=payload.model_dump(exclude_none=True),
    )
    return _operator_action_accepted_response(
        submission,
        message="Genestack alert export order accepted",
    )


@router.get("/plugins/{service_type}", response_model=ServicePluginResponse)
async def get_service_plugin(
    service_type: str,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_reader),
) -> ServicePluginResponse:
    """Return persisted registry state for one enabled service plugin."""
    normalized = service_type.strip().lower()
    result = await db.execute(select(ServicePlugin).where(ServicePlugin.service_type == normalized))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Service plugin not found: {service_type}")
    plugin: ServicePluginManifest | None = None
    if row.plugin_type != "internal_plugin":
        plugin = _plugin_by_service_type(normalized)
    payload = ServicePluginResponse.model_validate(row).model_dump()
    payload["config_editable"] = row.plugin_type == "internal_plugin"
    health_check_task = None
    if row.plugin_type != "internal_plugin":
        health_check_task = await _health_check_task_for_service(db, normalized)
    payload["health_check_task_id"] = (
        health_check_task.id if health_check_task is not None else None
    )
    payload["health_check_interval_seconds"] = (
        health_check_task.run_interval_seconds if health_check_task is not None else None
    )
    payload["health_check_enabled"] = (
        bool(health_check_task.is_enabled) if health_check_task is not None else False
    )
    payload.update(_helper_metadata(plugin) if plugin is not None else _empty_helper_metadata())
    return ServicePluginResponse.model_validate(payload)


@router.patch("/plugins/{service_type}", response_model=ServicePluginResponse)
async def update_service_plugin(
    service_type: str,
    payload: ServicePluginUpdate,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_operator),
) -> ServicePluginResponse:
    """Update operator-editable internal plugin runtime configuration."""
    normalized = service_type.strip().lower()
    result = await db.execute(select(ServicePlugin).where(ServicePlugin.service_type == normalized))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Service plugin not found: {service_type}")
    updates = payload.model_dump(exclude_unset=True)
    if row.plugin_type != "internal_plugin":
        unsupported = set(updates).difference({"enabled", "health_check_interval_seconds"})
        if unsupported:
            raise HTTPException(
                status_code=400,
                detail="External plugins only support enabled state and health check interval updates",
            )
        manifest_tasks = await _plugin_manifest_tasks_for_service(db, normalized)
        health_check_task = next(
            (task for task in manifest_tasks if task.task_type == "plugin_health_check"),
            None,
        )
        if health_check_task is None and "health_check_interval_seconds" in updates:
            raise HTTPException(
                status_code=404,
                detail=f"Health check scheduled task not found for {service_type}",
            )
        now = utc_now_db()
        if "enabled" in updates:
            row.enabled = bool(updates["enabled"])
            row.health_status = (
                PLUGIN_RUN_STATE_UNKNOWN if row.enabled else PLUGIN_RUN_STATE_DISABLED
            )
            row.status_message = None if row.enabled else "Disabled by operator"
            for task in manifest_tasks:
                task.is_enabled = row.enabled
                task.status = "idle" if row.enabled else "disabled"
                task.next_run_at = (
                    now + timedelta(seconds=max(1, int(task.run_interval_seconds or 1)))
                    if row.enabled
                    else None
                )
                task.updated_at = now
        if "health_check_interval_seconds" in updates:
            interval = int(updates["health_check_interval_seconds"])
            if health_check_task is not None:
                health_check_task.run_interval_seconds = interval
                if health_check_task.is_enabled and health_check_task.status == "idle":
                    health_check_task.next_run_at = now + timedelta(seconds=interval)
                health_check_task.updated_at = now
        if health_check_task is not None:
            row.next_health_check_at = health_check_task.next_run_at
        row.updated_at = now
        await db.commit()
        await db.refresh(row)
        response = ServicePluginResponse.model_validate(row).model_dump()
        response["config_editable"] = False
        response["health_check_task_id"] = (
            health_check_task.id if health_check_task is not None else None
        )
        response["health_check_interval_seconds"] = (
            health_check_task.run_interval_seconds if health_check_task is not None else None
        )
        response["health_check_enabled"] = (
            bool(health_check_task.is_enabled) if health_check_task is not None else False
        )
        response.update(_helper_metadata(_plugin_by_service_type(normalized)))
        return ServicePluginResponse.model_validate(response)

    if "enabled" in updates:
        row.enabled = bool(updates["enabled"])
        row.health_status = PLUGIN_RUN_STATE_UNKNOWN if row.enabled else PLUGIN_RUN_STATE_DISABLED
        row.status_message = None if row.enabled else "Paused by operator"
    if "run_interval_seconds" in updates:
        row.run_interval_seconds = updates["run_interval_seconds"]
        if row.next_health_check_at is not None and row.run_interval_seconds is not None:
            row.next_health_check_at = utc_now_db() + timedelta(seconds=row.run_interval_seconds)
    if "query_limit" in updates:
        if normalized not in {"prep-chef", "timer"}:
            raise HTTPException(
                status_code=400,
                detail="Query limit is only supported for prep-chef and timer",
            )
        row.query_limit = updates["query_limit"]
    if "status_message" in updates:
        row.status_message = updates["status_message"]
    if "health_check_interval_seconds" in updates:
        raise HTTPException(
            status_code=400,
            detail="Health check interval is only supported for external plugins",
        )
    row.updated_at = utc_now_db()
    await db.commit()
    await db.refresh(row)
    response = ServicePluginResponse.model_validate(row).model_dump()
    response["config_editable"] = True
    response["health_check_task_id"] = None
    response["health_check_interval_seconds"] = None
    response["health_check_enabled"] = False
    response.update(_empty_helper_metadata())
    return ServicePluginResponse.model_validate(response)


@router.get(
    "/plugins/{service_type}/configuration",
    response_model=ServicePluginConfigurationResponse,
)
async def get_plugin_configuration(
    service_type: str,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_operator),
) -> ServicePluginConfigurationResponse:
    """Return non-secret operator configuration and credential presence for a plugin."""
    row, _plugin, adapter = await _external_plugin_row_or_404(db, service_type)
    requirement = _credential_requirement(adapter)
    credential_type = (
        str(requirement.get("credential_type"))
        if requirement and requirement.get("credential_type")
        else None
    )
    credential_key_id = "default"
    return _configuration_response(
        row=row,
        adapter=adapter,
        config=_plugin_config_from_row(row, adapter),
        credential_type=credential_type,
        credential_key_id=credential_key_id,
        credential_configured=(
            await _credential_configured(
                row=row,
                credential_type=credential_type,
                credential_key_id=credential_key_id,
            )
            if credential_type
            else False
        ),
    )


@router.put(
    "/plugins/{service_type}/configuration",
    response_model=ServicePluginConfigurationResponse,
)
async def update_plugin_configuration(
    service_type: str,
    payload: ServicePluginConfigurationUpdate,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_operator),
) -> ServicePluginConfigurationResponse:
    """Persist non-secret operator configuration for a plugin."""
    row, _plugin, adapter = await _external_plugin_row_or_404(db, service_type)
    config = _normalize_plugin_config(adapter, payload.config)
    row.plugin_config = config
    row.updated_at = utc_now_db()
    await db.commit()
    await db.refresh(row)
    requirement = _credential_requirement(adapter)
    credential_type = (
        str(requirement.get("credential_type"))
        if requirement and requirement.get("credential_type")
        else None
    )
    return _configuration_response(
        row=row,
        adapter=adapter,
        config=_plugin_config_from_row(row, adapter),
        credential_type=credential_type,
        credential_key_id="default",
        credential_configured=(
            await _credential_configured(
                row=row,
                credential_type=credential_type,
                credential_key_id="default",
            )
            if credential_type
            else False
        ),
    )


@router.put(
    "/plugins/{service_type}/credentials",
    response_model=ServicePluginConfigurationResponse,
)
async def update_plugin_credential(
    service_type: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_admin),
) -> ServicePluginConfigurationResponse:
    """Write adapter secret material through credential-manager."""
    payload = await _parse_credential_update(request)
    row, _plugin, adapter = await _external_plugin_row_or_404(db, service_type)
    requirement = _credential_requirement(adapter, payload.credential_type)
    if requirement is None:
        raise HTTPException(
            status_code=400,
            detail=f"{row.service_type} does not support credential type {payload.credential_type}",
        )
    credential_error = adapter.validate_credential_payload(
        payload.credential_type,
        payload.credential_payload,
    )
    if credential_error:
        raise HTTPException(status_code=400, detail=credential_error)
    try:
        await write_adapter_credential(
            service_type=row.service_type,
            credential_type=payload.credential_type,
            credential_key_id=payload.credential_key_id,
            payload=payload.credential_payload,
            rotated=payload.rotate_credential,
        )
    except ServicePluginCredentialError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.rollback()
    result = await db.execute(
        select(ServicePlugin).where(ServicePlugin.service_type == service_type.strip().lower())
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Service plugin not found: {service_type}")
    return _configuration_response(
        row=row,
        adapter=adapter,
        config=_plugin_config_from_row(row, adapter),
        credential_type=payload.credential_type,
        credential_key_id=payload.credential_key_id,
        credential_configured=await _credential_configured(
            row=row,
            credential_type=payload.credential_type,
            credential_key_id=payload.credential_key_id,
        ),
    )


@router.post(
    "/plugins/{service_type}/test-connection",
    response_model=OperatorActionAcceptedResponse,
    status_code=202,
)
@limiter.limit(get_settings().rate_limit_default)
async def test_plugin_connection(
    service_type: str,
    request: Request,
    payload: ServicePluginConnectionTestRequest,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_operator),
) -> OperatorActionAcceptedResponse:
    """Queue an external plugin connection check through the health-check recipe."""
    row, _plugin, _adapter = await _external_plugin_row_or_404(db, service_type)
    if payload.config is not None:
        raise HTTPException(
            status_code=400,
            detail=(
                "connection checks run through the stored plugin health-check recipe; "
                "update plugin configuration before testing"
            ),
        )
    if payload.credential_key_id != "default":
        raise HTTPException(
            status_code=400,
            detail=(
                "connection checks run through the stored plugin health-check recipe; "
                "non-default credential overrides are not supported"
            ),
        )
    normalized = row.service_type.strip().lower()
    submission = await submit_operator_action_order(
        db=db,
        req_id=getattr(request.state, "req_id", f"plugin-{normalized}-test-connection"),
        recipe_name=f"plugin-health-check:{normalized}",
        service_type=normalized,
        service_exec="health_check",
        task_key_template=f"{normalized}-health-check",
        service_payload={},
    )
    return _operator_action_accepted_response(
        submission,
        message=f"{normalized} connection check order accepted",
    )


@router.post(
    "/plugins/prometheus/reload",
    response_model=OperatorActionAcceptedResponse,
    status_code=202,
)
@limiter.limit(get_settings().rate_limit_default)
async def reload_prometheus_plugin_rule_state(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_operator),
) -> OperatorActionAcceptedResponse:
    """Trigger a manual Prometheus rule/config reload."""

    row, _plugin, adapter = await _external_plugin_row_or_404(db, "prometheus")
    config = _plugin_config_from_row(row, adapter)
    submission = await reload_prometheus_rules(
        db=db,
        req_id=getattr(request.state, "req_id", "plugin-prometheus-reload"),
        operator_config=config,
    )
    return _operator_action_accepted_response(
        submission,
        message=f"{row.service_type} reload order accepted",
    )


@router.get("/plugins/{service_type}/health", response_model=ServicePluginHealthResponse)
async def get_plugin_health(
    service_type: str,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_reader),
) -> ServicePluginHealthResponse:
    """Return last reported health for one service plugin."""
    normalized = service_type.strip().lower()
    result = await db.execute(select(ServicePlugin).where(ServicePlugin.service_type == normalized))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"Service plugin health not found: {service_type}"
        )
    if row.plugin_type != "internal_plugin":
        _plugin_by_service_type(normalized)
    payload = ServicePluginHealthResponse.model_validate(row).model_dump()
    payload["config_editable"] = row.plugin_type == "internal_plugin"
    health_check_task = None
    if row.plugin_type != "internal_plugin":
        health_check_task = await _health_check_task_for_service(db, normalized)
    payload["health_check_task_id"] = (
        health_check_task.id if health_check_task is not None else None
    )
    payload["health_check_interval_seconds"] = (
        health_check_task.run_interval_seconds if health_check_task is not None else None
    )
    payload["health_check_enabled"] = (
        bool(health_check_task.is_enabled) if health_check_task is not None else False
    )
    return ServicePluginHealthResponse.model_validate(payload)
