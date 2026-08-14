"""Alertmanager-backed suppression lifecycle order submission."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from api.models.models import AlertSuppression
from api.schemas.schemas import SuppressionCreate, SuppressionUpdate
from api.services.order_intake import (
    OperatorActionOrderSubmission,
    submit_operator_action_order,
)
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
    req_id: str,
    payload: SuppressionCreate,
) -> OperatorActionOrderSubmission:
    """Submit an Alertmanager suppression create order."""

    if not payload.matchers:
        raise SuppressionLifecycleError("matchers are required", status_code=400)

    return await submit_operator_action_order(
        db=db,
        req_id=req_id,
        recipe_name="operator-action:alertmanager:create-suppression",
        service_type="alertmanager",
        service_exec="suppression",
        task_key_template="alertmanager-create-suppression",
        service_payload={
            "name": payload.name,
            "reason": payload.reason,
            "starts_at": payload.starts_at.isoformat(),
            "ends_at": payload.ends_at.isoformat(),
            "created_by": payload.created_by,
            "summary_ticket_enabled": payload.summary_ticket_enabled,
            "matchers": [matcher.model_dump() for matcher in payload.matchers],
        },
    )


async def update_alertmanager_suppression(
    *,
    db: AsyncSession,
    req_id: str,
    suppression: AlertSuppression,
    payload: SuppressionUpdate,
) -> OperatorActionOrderSubmission:
    """Submit an Alertmanager suppression update order."""

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

    return await submit_operator_action_order(
        db=db,
        req_id=req_id,
        recipe_name="operator-action:alertmanager:update-suppression",
        service_type="alertmanager",
        service_exec="suppression",
        task_key_template="alertmanager-update-suppression",
        service_payload={
            "source_ref": source_ref,
            "name": payload.name or suppression.name,
            "reason": payload.reason if payload.reason is not None else suppression.reason,
            "starts_at": (
                payload.starts_at.isoformat()
                if payload.starts_at
                else suppression.starts_at.isoformat()
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
    )


async def expire_alertmanager_suppression(
    *,
    db: AsyncSession,
    req_id: str,
    suppression: AlertSuppression,
) -> OperatorActionOrderSubmission:
    """Submit an Alertmanager suppression expire order."""

    source_ref = str(suppression.source_ref or "").strip()
    if suppression.source_service_type != "alertmanager" or not source_ref:
        raise SuppressionLifecycleError(
            "Only Alertmanager-backed suppressions can be canceled",
            status_code=400,
        )
    return await submit_operator_action_order(
        db=db,
        req_id=req_id,
        recipe_name="operator-action:alertmanager:expire-suppression",
        service_type="alertmanager",
        service_exec="suppression",
        task_key_template="alertmanager-expire-suppression",
        service_payload={"source_ref": source_ref},
    )


def _matcher_to_payload(matcher: object) -> JSONObject:
    return {
        "label_key": str(getattr(matcher, "label_key", "") or ""),
        "operator": str(getattr(matcher, "operator", "eq") or "eq"),
        "value": getattr(matcher, "value", None),
    }
