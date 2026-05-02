"""Operator action logging endpoint for UI compliance events."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from api.api.auth import require_auth_if_enabled
from api.core.logging import get_logger
from api.schemas.schemas import UIOperatorActionRequest, UIOperatorActionResponse
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
    auth_context: AuthContext | None = Depends(require_auth_if_enabled),
) -> UIOperatorActionResponse:
    """Log a safe, structured record of an operator action initiated in the UI."""

    context = auth_context or getattr(request.state, "auth_context", None)
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
            "details": _redact_details(payload.details),
        },
    )
    return UIOperatorActionResponse()
