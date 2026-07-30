"""API routes for global communications policy management."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from api.api.auth import require_admin, require_reader
from api.core.database import get_db
from api.core.logging import get_logger
from api.schemas.schemas import (
    CommunicationPolicyResponse,
    CommunicationPolicyUpdate,
    CommunicationRouteResponse,
)
from api.plugins.catalog import get_enabled_plugin_communication_routes
from api.services.communications_policy import (
    get_global_policy_routes,
    lifecycle_summary,
    policy_has_enabled_routes,
    serialize_route,
    sync_fallback_policy_recipe,
    sync_global_policy_routes,
)

router = APIRouter()
logger = get_logger(__name__)


def _response_routes(routes: list[Any]) -> list[CommunicationRouteResponse]:
    return [CommunicationRouteResponse(**serialize_route(route)) for route in routes]


def _available_routes() -> list[CommunicationRouteResponse]:
    return [
        CommunicationRouteResponse(**route) for route in get_enabled_plugin_communication_routes()
    ]


@router.get("/communications/policy", response_model=CommunicationPolicyResponse)
async def get_communications_policy(
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_reader),
) -> CommunicationPolicyResponse:
    routes = await get_global_policy_routes(db)
    return CommunicationPolicyResponse(
        configured=policy_has_enabled_routes(routes),
        routes=_response_routes(routes),
        available_routes=_available_routes(),
        lifecycle_summary=lifecycle_summary(),
    )


@router.put("/communications/policy", response_model=CommunicationPolicyResponse)
async def put_communications_policy(
    request: Request,
    response: Response,
    payload: CommunicationPolicyUpdate,
    db: AsyncSession = Depends(get_db),
    _context: object = Depends(require_admin),
) -> CommunicationPolicyResponse:
    req_id = request.state.req_id
    before_routes = await get_global_policy_routes(db)
    before_contract = [serialize_route(route) for route in before_routes]
    try:
        routes = await sync_global_policy_routes(
            db, routes=[item.model_dump() for item in payload.routes]
        )
        # Also sync the fallback recipe with the updated routes
        await sync_fallback_policy_recipe(db, routes=routes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()

    after_contract = [serialize_route(route) for route in routes]
    changed = before_contract != after_contract
    response.headers["X-PoundCake-Changed"] = "true" if changed else "false"
    log = logger.info if changed else logger.debug
    log(
        "Updated global communications policy",
        extra={
            "req_id": req_id,
            "configured": policy_has_enabled_routes(routes),
            "changed": changed,
            "route_count": len(routes),
        },
    )
    return CommunicationPolicyResponse(
        configured=policy_has_enabled_routes(routes),
        routes=_response_routes(routes),
        available_routes=_available_routes(),
        lifecycle_summary=lifecycle_summary(),
    )
