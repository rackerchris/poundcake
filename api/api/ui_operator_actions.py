"""Operator action logging endpoint for UI compliance events."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.api.auth import require_operator, require_reader
from api.core.database import get_db
from api.core.logging import get_logger
from api.models.models import OperatorAuditEvent
from api.schemas.schemas import (
    OperatorAuditEventResponse,
    UIOperatorActionRequest,
    UIOperatorActionResponse,
)
from api.services.auth_service import AuthContext

router = APIRouter()
logger = get_logger(__name__)

_SENSITIVE_KEY_PARTS = (
    "auth",
    "cookie",
    "credential",
    "header",
    "password",
    "secret",
    "token",
)
_NOISY_DETAIL_KEYS = {
    "browser",
    "browser_name",
    "browser_version",
    "client",
    "device",
    "os",
    "os_name",
    "os_version",
    "platform",
    "screen",
    "user_agent",
    "useragent",
    "viewport",
}


def _redact_details(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = str(key).lower()
            if normalized_key in _NOISY_DETAIL_KEYS:
                continue
            if any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
                redacted[str(key)] = "[redacted]"
            else:
                redacted[str(key)] = _redact_details(item)
        return redacted
    if isinstance(value, list):
        return [_redact_details(item) for item in value[:25]]
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:500]


@router.post("/ui/operator-actions", response_model=UIOperatorActionResponse)
async def log_ui_operator_action(
    request: Request,
    payload: UIOperatorActionRequest,
    db: AsyncSession = Depends(get_db),
    auth_context: AuthContext = Depends(require_operator),
) -> UIOperatorActionResponse:
    """Log a safe, structured record of an operator action initiated in the UI."""

    context = auth_context or getattr(request.state, "auth_context", None)
    details = _redact_details(payload.details)
    db.add(
        OperatorAuditEvent(
            req_id=getattr(request.state, "req_id", None),
            action=payload.action,
            surface=payload.surface,
            status=payload.status,
            target=payload.target,
            actor_username=getattr(context, "username", None),
            actor_role=getattr(context, "role", None),
            details=details if isinstance(details, dict) else {},
        )
    )
    await db.commit()
    logger.info(
        "UI operator action",
        extra={
            "req_id": "UI-OPERATOR-ACTION",
            "event_type": "ui_operator_action",
            "action": payload.action,
            "surface": payload.surface,
            "status": payload.status,
            "target": payload.target,
            "user": getattr(context, "username", None),
            "role": getattr(context, "role", None),
            "details": details,
        },
    )
    return UIOperatorActionResponse()


@router.get("/ui/operator-actions", response_model=list[OperatorAuditEventResponse])
async def list_ui_operator_actions(
    surface: str | None = Query(default=None, max_length=120),
    status: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=100, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _context: AuthContext = Depends(require_reader),
) -> list[OperatorAuditEventResponse]:
    """Return reader-safe UI operator audit events ordered newest first."""

    statement = select(OperatorAuditEvent).order_by(
        OperatorAuditEvent.created_at.desc(), OperatorAuditEvent.id.desc()
    )
    if surface:
        statement = statement.where(OperatorAuditEvent.surface == surface.strip())
    if status:
        statement = statement.where(OperatorAuditEvent.status == status.strip())
    statement = statement.limit(limit)
    result = await db.execute(statement)
    return [OperatorAuditEventResponse.model_validate(row) for row in result.scalars().all()]
