#  ___                        _  ____      _
# |  _ \ ___  _   _ _ __   __| |/ ___|__ _| | _____
# | |_) / _ \| | | | '_ \ / _` | |   / _` | |/ / _ \
# |  __/ (_) | |_| | | | | (_| | |__| (_| |   <  __/
# |_|   \___/ \__,_|_| |_|\__,_|\____\__,_|_|\_\___|
#
"""Webhook ingestion routes."""

import secrets

from fastapi import APIRouter, Depends, Body, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import get_settings
from api.core.database import get_db
from api.core.logging import get_logger
from api.core.rate_limit import limiter
from api.schemas.schemas import AlertmanagerWebhookRequest, WebhookResponse
from api.services.auth_service import (
    AuthContext,
    _require_service_credential_scope,
    permissions_for_role,
)
from api.services.pre_heat import pre_heat

router = APIRouter()
logger = get_logger(__name__)


async def require_webhook_bearer(
    request: Request,
    authorization: str | None = Header(default=None),
) -> AuthContext:
    """Authenticate external Alertmanager webhook ingress."""
    configured_token = get_settings().webhook_bearer_token.strip()
    if not configured_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook bearer token is not configured",
        )

    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Webhook bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not secrets.compare_digest(token.strip(), configured_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid webhook bearer token",
        )

    context = AuthContext(
        provider="service",
        subject_id="webhook:alertmanager",
        username="webhook:alertmanager",
        display_name="Alertmanager Webhook",
        groups=[],
        role="service",
        principal_type="service",
        permissions=permissions_for_role("service"),
        service_type="webhook",
        plugin_type="external_webhook",
        credential_scope="alertmanager_webhook",
    )
    _require_service_credential_scope(context, "alertmanager_webhook")
    request.state.auth_context = context
    return context


@router.post("/webhook", response_model=WebhookResponse, status_code=202)
@limiter.limit(get_settings().rate_limit_webhook)
async def alertmanager_webhook(
    request: Request,
    payload: AlertmanagerWebhookRequest = Body(...),
    _webhook_context: AuthContext = Depends(require_webhook_bearer),
    db: AsyncSession = Depends(get_db),
) -> WebhookResponse:
    """Entry point for Alertmanager webhooks. Handled by pre_heat service.

    Returns 202 Accepted - webhook received and queued for asynchronous processing.
    """
    req_id = request.state.req_id
    payload_dict = payload.model_dump()
    alert_count = len(payload_dict.get("alerts", []))

    logger.info(
        "Received webhook from Alertmanager",
        extra={"req_id": req_id, "alert_count": alert_count},
    )

    result = await pre_heat(payload_dict, db, req_id)

    logger.info(
        "Webhook processed successfully",
        extra={
            "req_id": req_id,
            "status": result.get("status"),
            "order_id": result.get("order_id"),
        },
    )

    return WebhookResponse(
        status=result["status"],
        order_id=result.get("order_id"),
        message=result.get("message") or f"Order {result['status']}",
        results=result.get("results"),
    )
