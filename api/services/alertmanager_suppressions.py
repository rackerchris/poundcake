"""Alertmanager-backed suppression lifecycle orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.models.models import AlertSuppression, ServicePlugin
from api.plugins.types import ExecutionContext
from api.schemas.schemas import SuppressionCreate, SuppressionUpdate
from api.services.plugin_orchestrator import ExecutionOrchestrator
from api.services.suppression_sync import upsert_plugin_suppressions
from api.types import JSONObject


@dataclass(frozen=True)
class SuppressionLifecycleError(Exception):
    """Operator-safe suppression lifecycle failure."""

    message: str
    status_code: int = 400

    def __str__(self) -> str:
        return self.message


async def create_alertmanager_suppression(
    *,
    db: AsyncSession,
    orchestrator: ExecutionOrchestrator,
    req_id: str,
    payload: SuppressionCreate,
) -> AlertSuppression:
    """Create an Alertmanager silence, then persist its PoundCake record."""

    if not payload.matchers:
        raise SuppressionLifecycleError("matchers are required", status_code=400)

    result = await orchestrator.dispatch(
        ExecutionContext.model_validate(
            {
                "service_type": "alertmanager",
                "service_exec": "suppression",
                "service_payload": {
                    "name": payload.name,
                    "reason": payload.reason,
                    "starts_at": payload.starts_at.isoformat(),
                    "ends_at": payload.ends_at.isoformat(),
                    "created_by": payload.created_by,
                    "summary_ticket_enabled": payload.summary_ticket_enabled,
                    "matchers": [matcher.model_dump() for matcher in payload.matchers],
                },
                "service_exec_parameters": {"operation": "create"},
                "retry_count": 0,
                "retry_delay": 0,
                "service_exec_timeout": 60,
                "context": await _execution_context_for_service(db=db, service_type="alertmanager"),
                "req_id": req_id,
            }
        )
    )
    suppression = _normalized_suppression_from_result(result.result)
    suppression["summary_ticket_enabled"] = payload.summary_ticket_enabled
    await upsert_plugin_suppressions(
        db,
        service_type="alertmanager",
        suppressions=[suppression],
    )
    await db.commit()
    refreshed = await _suppression_by_source_ref(db, str(suppression["source_ref"]))
    if refreshed is None:
        raise SuppressionLifecycleError(
            "Alertmanager suppression was created but could not be persisted",
            status_code=500,
        )
    return refreshed


async def update_alertmanager_suppression(
    *,
    db: AsyncSession,
    orchestrator: ExecutionOrchestrator,
    req_id: str,
    suppression: AlertSuppression,
    payload: SuppressionUpdate,
) -> AlertSuppression:
    """Update an Alertmanager-backed suppression and reconcile local state."""

    source_ref = str(suppression.source_ref or "").strip()
    if suppression.source_service_type != "alertmanager" or not source_ref:
        raise SuppressionLifecycleError(
            "Only Alertmanager-backed suppressions can be updated",
            status_code=400,
        )
    matchers = payload.matchers if payload.matchers is not None else suppression.matchers
    matcher_payloads = [
        matcher.model_dump() if hasattr(matcher, "model_dump") else _matcher_to_payload(matcher)
        for matcher in matchers or []
    ]
    if not matcher_payloads:
        raise SuppressionLifecycleError("matchers are required", status_code=400)

    result = await orchestrator.dispatch(
        ExecutionContext.model_validate(
            {
                "service_type": "alertmanager",
                "service_exec": "suppression",
                "service_payload": {
                    "source_ref": source_ref,
                    "name": payload.name or suppression.name,
                    "reason": payload.reason if payload.reason is not None else suppression.reason,
                    "starts_at": (
                        payload.starts_at.isoformat() if payload.starts_at else suppression.starts_at.isoformat()
                    ),
                    "ends_at": (
                        payload.ends_at.isoformat() if payload.ends_at else suppression.ends_at.isoformat()
                    ),
                    "created_by": suppression.created_by,
                    "summary_ticket_enabled": (
                        payload.summary_ticket_enabled
                        if payload.summary_ticket_enabled is not None
                        else suppression.summary_ticket_enabled
                    ),
                    "matchers": matcher_payloads,
                },
                "service_exec_parameters": {"operation": "update"},
                "retry_count": 0,
                "retry_delay": 0,
                "service_exec_timeout": 60,
                "context": await _execution_context_for_service(db=db, service_type="alertmanager"),
                "req_id": req_id,
            }
        )
    )
    normalized = _normalized_suppression_from_result(result.result)
    normalized["summary_ticket_enabled"] = (
        payload.summary_ticket_enabled
        if payload.summary_ticket_enabled is not None
        else suppression.summary_ticket_enabled
    )
    await upsert_plugin_suppressions(
        db,
        service_type="alertmanager",
        suppressions=[normalized],
    )
    await db.commit()
    refreshed = await _suppression_by_source_ref(db, source_ref)
    if refreshed is None:
        raise SuppressionLifecycleError("Updated suppression could not be reloaded", status_code=500)
    return refreshed


async def expire_alertmanager_suppression(
    *,
    db: AsyncSession,
    orchestrator: ExecutionOrchestrator,
    req_id: str,
    suppression: AlertSuppression,
) -> AlertSuppression:
    """Expire an Alertmanager-backed suppression and reconcile local state."""

    source_ref = str(suppression.source_ref or "").strip()
    if suppression.source_service_type != "alertmanager" or not source_ref:
        raise SuppressionLifecycleError(
            "Only Alertmanager-backed suppressions can be canceled",
            status_code=400,
        )
    result = await orchestrator.dispatch(
        ExecutionContext.model_validate(
            {
                "service_type": "alertmanager",
                "service_exec": "suppression",
                "service_payload": {"source_ref": source_ref},
                "service_exec_parameters": {"operation": "expire"},
                "retry_count": 0,
                "retry_delay": 0,
                "service_exec_timeout": 60,
                "context": await _execution_context_for_service(db=db, service_type="alertmanager"),
                "req_id": req_id,
            }
        )
    )
    normalized = _normalized_suppression_from_result(result.result)
    normalized["summary_ticket_enabled"] = suppression.summary_ticket_enabled
    await upsert_plugin_suppressions(
        db,
        service_type="alertmanager",
        suppressions=[normalized],
    )
    await db.commit()
    refreshed = await _suppression_by_source_ref(db, source_ref)
    if refreshed is None:
        raise SuppressionLifecycleError("Canceled suppression could not be reloaded", status_code=500)
    return refreshed


def _normalized_suppression_from_result(result: object) -> JSONObject:
    if not isinstance(result, dict):
        raise SuppressionLifecycleError("Alertmanager suppression returned no payload", status_code=502)
    if not bool(result.get("success")):
        raise SuppressionLifecycleError(
            str(result.get("message") or "Alertmanager suppression request failed"),
            status_code=502,
        )
    suppression = result.get("suppression")
    if not isinstance(suppression, dict):
        raise SuppressionLifecycleError(
            "Alertmanager suppression response was missing normalized suppression data",
            status_code=502,
        )
    source_ref = str(suppression.get("source_ref") or "").strip()
    if not source_ref:
        raise SuppressionLifecycleError(
            "Alertmanager suppression response was missing source_ref",
            status_code=502,
        )
    return dict(suppression)


async def _execution_context_for_service(
    *,
    db: AsyncSession,
    service_type: str,
) -> dict[str, object]:
    result = await db.execute(
        select(ServicePlugin.plugin_config).where(ServicePlugin.service_type == service_type.strip().lower())
    )
    config = result.scalar_one_or_none()
    return {"operator_config": dict(config)} if isinstance(config, dict) else {}


async def _suppression_by_source_ref(
    db: AsyncSession,
    source_ref: str,
) -> AlertSuppression | None:
    result = await db.execute(
        select(AlertSuppression)
        .options(
            selectinload(AlertSuppression.matchers),
            selectinload(AlertSuppression.summary),
        )
        .where(
            AlertSuppression.source_service_type == "alertmanager",
            AlertSuppression.source_ref == source_ref,
        )
    )
    return result.scalar_one_or_none()


def _matcher_to_payload(matcher: object) -> JSONObject:
    return {
        "label_key": str(getattr(matcher, "label_key", "") or ""),
        "operator": str(getattr(matcher, "operator", "eq") or "eq"),
        "value": getattr(matcher, "value", None),
    }
