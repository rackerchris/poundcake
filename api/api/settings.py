#  ___                        _  ____      _
# |  _ \ ___  _   _ _ __   __| |/ ___|__ _| | _____
# | |_) / _ \| | | | '_ \ / _` | |   / _` | |/ / _ \
# |  __/ (_) | |_| | | | | (_| | |__| (_| |   <  __/
# |_|   \___/ \__,_|_| |_|\__,_|\____\__,_|_|\_\___|
#
"""Settings API endpoints."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import get_db
from api.core.config import get_settings
from api.core.logging import get_logger
from api.api.auth import require_auth_if_enabled
from api.schemas.schemas import SettingsResponse
from api.services.auth_service import get_enabled_provider_metadata
from api.services.communications_policy import global_policy_configured

logger = get_logger(__name__)
router = APIRouter(tags=["settings"])


@router.get("/settings", response_model=SettingsResponse)
async def get_application_settings(
    request: Request,
    _user: str | None = Depends(require_auth_if_enabled),
    db: AsyncSession = Depends(get_db),
) -> SettingsResponse:
    """Get application settings for UI configuration.

    Returns settings that the UI needs to configure itself properly.
    """
    settings = get_settings()
    req_id = request.state.req_id

    logger.debug("Settings requested", extra={"req_id": req_id})

    communications_configured = await global_policy_configured(db)

    return SettingsResponse.model_validate(
        {
            "auth_enabled": settings.auth_enabled,
            "rbac_enabled": settings.auth_rbac_enabled,
            "auth_providers": get_enabled_provider_metadata(),
            "prometheus_use_crds": settings.prometheus_use_crds,
            "prometheus_crd_namespace": settings.prometheus_crd_namespace,
            "prometheus_url": settings.prometheus_url,
            "git_enabled": settings.git_enabled,
            "git_provider": settings.git_provider if settings.git_enabled else None,
            "git_repo_url": settings.git_repo_url if settings.git_enabled else None,
            "git_branch": settings.git_branch if settings.git_enabled else None,
            "git_rules_path": settings.git_rules_path if settings.git_enabled else None,
            "git_workflows_path": settings.git_workflows_path if settings.git_enabled else None,
            "git_actions_path": settings.git_actions_path if settings.git_enabled else None,
            "version": settings.app_version,
            "global_communications_configured": communications_configured,
        }
    )
