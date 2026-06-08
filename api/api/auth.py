#  ___                        _  ____      _
# |  _ \ ___  _   _ _ __   __| |/ ___|__ _| | _____
# | |_) / _ \| | | | '_ \ / _` | |   / _` | |/ / _ \
# |  __/ (_) | |_| | | | | (_| | |__| (_| |   <  __/
# |_|   \___/ \__,_|_| |_|\__,_|\____\__,_|_|\_\___|
#
"""Authentication and session management for PoundCake."""

from __future__ import annotations

import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import get_settings
from api.core.database import auth_verifier_db_session, get_db
from api.core.logging import get_logger
from api.services.service_identity import internal_hmac_credential_for_key
from api.schemas.schemas import (
    AuthLoginRequest,
    AuthLogoutResponse,
    AuthMeResponse,
    AuthPrincipalResponse,
    AuthProviderResponse,
    AuthRoleBindingCreate,
    AuthRoleBindingResponse,
    AuthRoleBindingUpdate,
    DeleteResponse,
    DeviceAuthorizationPollRequest,
    DeviceAuthorizationPollResponse,
    DeviceAuthorizationStartRequest,
    DeviceAuthorizationStartResponse,
    SessionResponse,
)
from api.services.auth_service import (
    AccessDeniedError,
    _require_service_credential_scope,
    AuthContext,
    AuthIdentity,
    DeviceAuthorizationExpired,
    DeviceAuthorizationPending,
    InvalidCredentialsError,
    ProviderConfigurationError,
    authenticate_password_provider,
    authenticate_device_code,
    authenticate_oidc_authorization_code,
    browser_login_provider_names,
    build_auth_callback_url,
    build_login_context,
    create_role_binding,
    device_login_provider_names,
    delete_role_binding,
    ensure_request_authorized,
    get_enabled_provider_metadata,
    get_oidc_authorize_url,
    get_principal_by_id,
    get_role_binding,
    get_session_store,
    is_authorized_for_role,
    is_request_public,
    list_principals,
    list_role_bindings,
    permissions_for_role,
    provider_label,
    rehydrate_session_context,
    service_token_context,
    start_device_authorization,
    upsert_principal,
    update_role_binding,
)
from shared.hmac import hmac_sha256_hex
from shared.internal_hmac import (
    INTERNAL_HMAC_KEY_ID_HEADER,
    INTERNAL_HMAC_NONCE_HEADER,
    INTERNAL_HMAC_TIMESTAMP_HEADER,
    build_internal_hmac_signing_payload,
    canonical_request_path,
    parse_internal_hmac_authorization,
)

logger = get_logger(__name__)
router = APIRouter()
_HMAC_REPLAY_PROTECTED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_HMAC_NONCE_STATE_KIND = "internal-hmac-nonce"


def _request_is_secure(request: Request) -> bool:
    """Determine if request should receive a Secure cookie."""
    if get_settings().force_secure_cookie:
        return True
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    if forwarded_proto:
        first = forwarded_proto.split(",", 1)[0].strip().lower()
        if first:
            return first == "https"
    return request.url.scheme.lower() == "https"


def _set_session_cookie(request: Request, response: Response, session_id: str) -> None:
    response.set_cookie(
        key="session_token",
        value=session_id,
        httponly=True,
        samesite="strict",
        secure=_request_is_secure(request),
        path="/",
        max_age=get_settings().auth_session_timeout,
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key="session_token", path="/")


def _normalize_next_target(target: str | None) -> str:
    if not target or not target.startswith("/"):
        return "/overview"
    if target == "/login" or target.startswith("/login?"):
        return "/overview"
    return target


def _resolve_sso_provider(requested_provider: str | None, *, mode: str) -> str:
    settings = get_settings()
    capability = "browser login" if mode == "browser" else "CLI device login"
    enabled = (
        browser_login_provider_names(settings)
        if mode == "browser"
        else device_login_provider_names(settings)
    )
    provider = str(requested_provider or "").strip().lower()

    if provider:
        if provider not in {"auth0", "azure_ad"}:
            raise HTTPException(
                status_code=400,
                detail=f"Provider '{provider}' does not support {capability}",
            )
        if provider not in enabled:
            raise HTTPException(
                status_code=404,
                detail=f"{provider_label(provider)} {capability} is not enabled",
            )
        return provider

    if len(enabled) == 1:
        return enabled[0]
    if len(enabled) > 1:
        raise HTTPException(
            status_code=400,
            detail=f"provider is required when multiple {capability} providers are enabled",
        )

    configured_candidates: list[str] = []
    if settings.auth_auth0_enabled:
        configured_candidates.append("auth0")
    if settings.auth_azure_ad_enabled:
        configured_candidates.append("azure_ad")
    if len(configured_candidates) == 1:
        raise HTTPException(
            status_code=404,
            detail=f"{provider_label(configured_candidates[0])} {capability} is not enabled",
        )
    raise HTTPException(status_code=404, detail=f"No {capability} providers are enabled")


def _session_response(context: AuthContext) -> SessionResponse:
    return SessionResponse(
        session_id=str(context.session_id or ""),
        username=context.username,
        expires_at=str(context.expires_at or ""),
        provider=context.provider,
        role=context.role,
        display_name=context.display_name,
        is_superuser=context.is_superuser,
        permissions=context.permissions,
        token_type="Bearer",
    )


def _principal_response(principal: Any) -> AuthPrincipalResponse:
    return AuthPrincipalResponse(
        id=principal.id,
        provider=principal.provider,
        subject_id=principal.subject_id,
        username=principal.username,
        display_name=principal.display_name,
        principal_type=principal.principal_type,
        groups=list(principal.groups_json or []),
        last_seen_at=principal.last_seen_at,
        created_at=principal.created_at,
        updated_at=principal.updated_at,
    )


def _binding_response(binding: Any) -> AuthRoleBindingResponse:
    return AuthRoleBindingResponse(
        id=binding.id,
        provider=binding.provider,
        binding_type=binding.binding_type,
        role=binding.role,
        principal_id=binding.principal_id,
        external_group=binding.external_group,
        created_by=binding.created_by,
        created_at=binding.created_at,
        updated_at=binding.updated_at,
        principal=(None if binding.principal is None else _principal_response(binding.principal)),
    )


async def _remember_observed_principal(
    db: AsyncSession,
    identity: AuthIdentity | None,
) -> None:
    """Persist an external principal so admins can bind it after first login."""
    if identity is None or identity.provider in {"local", "service"}:
        return
    await upsert_principal(db, identity)
    await db.commit()


async def _persist_session(
    request: Request, response: Response, context: AuthContext
) -> SessionResponse:
    store = get_session_store()
    stored = await store.create_session(
        context,
        ttl_seconds=get_settings().auth_session_timeout,
    )
    if not stored.session_id:
        raise HTTPException(status_code=500, detail="Could not create session")
    _set_session_cookie(request, response, stored.session_id)
    return _session_response(stored)


async def require_auth_if_enabled(
    request: Request,
    session_token: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> AuthContext | None:
    """Global dependency that authenticates and authorizes API requests."""
    settings = get_settings()
    if (
        os.getenv("TESTING", "").strip().lower() in {"1", "true", "yes"}
        or settings.testing
        or not settings.auth_enabled
    ):
        context = AuthContext(
            provider="local",
            subject_id="auth-disabled",
            username="poundcake",
            display_name="PoundCake Local",
            groups=[],
            role="admin",
            principal_type="user",
            is_superuser=True,
            permissions=permissions_for_role("admin", is_superuser=True),
            principal_id=None,
        )
        request.state.auth_context = context
        return context

    existing = getattr(request.state, "auth_context", None)
    if isinstance(existing, AuthContext):
        return existing

    if is_request_public(request.url.path, request.method):
        return None

    context: AuthContext | None = None
    context = await _internal_hmac_context(request)

    if context is None:
        context, resolution_error = await rehydrate_session_context(db, session_token)
        if context is None:
            if resolution_error:
                raise HTTPException(status_code=403, detail=resolution_error)
            if "text/html" in request.headers.get("accept", ""):
                raise HTTPException(
                    status_code=status.HTTP_307_TEMPORARY_REDIRECT,
                    headers={"Location": "/login"},
                )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Valid session required",
                headers={"WWW-Authenticate": "Bearer"},
            )

    if request.url.path in {"/api/v1/auth/me", "/api/v1/auth/logout"}:
        if not context.is_human():
            raise HTTPException(status_code=403, detail="Service tokens cannot use this endpoint")

    try:
        ensure_request_authorized(context, request.url.path, request.method)
    except AccessDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    request.state.auth_context = context
    return context


async def _check_nonce_in_session(db: AsyncSession, nonce_key: str, ttl_seconds: int) -> bool:
    """Atomically check-and-set a nonce using the auth-verifier DB session."""
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=ttl_seconds)
    result = await db.execute(
        text("""
            INSERT INTO hmac_nonces (kind, `key`, created_at, expires_at)
            SELECT :kind, :key, :created_at, :expires_at
            FROM DUAL
            WHERE NOT EXISTS (
                SELECT 1 FROM hmac_nonces
                WHERE kind = :kind AND `key` = :key AND expires_at > :now
            )
        """),
        {
            "kind": _HMAC_NONCE_STATE_KIND,
            "key": nonce_key,
            "created_at": now,
            "expires_at": expires_at,
            "now": now,
        },
    )
    await db.commit()
    return result.rowcount > 0


async def _check_nonce(nonce_key: str, ttl_seconds: int) -> bool:
    """Atomically check-and-set a nonce for HMAC replay protection."""
    if get_settings().internal_hmac_nonce_store == "database":
        async with auth_verifier_db_session() as auth_db:
            return await _check_nonce_in_session(auth_db, nonce_key, ttl_seconds)
    return await get_session_store().put_if_absent(
        _HMAC_NONCE_STATE_KIND,
        nonce_key,
        {"key_id": nonce_key.split(":")[0]},
        ttl_seconds=ttl_seconds,
    )


async def _internal_hmac_context(
    request: Request,
    db: AsyncSession | None = None,
) -> AuthContext | None:
    """Authenticate an internal control-plane request signed with PoundCake HMAC."""
    settings = get_settings()

    parsed = parse_internal_hmac_authorization(request.headers.get("Authorization", ""))
    if parsed is None:
        return None
    key_id, signature = parsed
    header_key_id = request.headers.get(INTERNAL_HMAC_KEY_ID_HEADER, "")
    if header_key_id and not secrets.compare_digest(header_key_id, key_id):
        return None

    # Parse fields needed for verification before acquiring a DB session.
    timestamp = request.headers.get(INTERNAL_HMAC_TIMESTAMP_HEADER, "")
    try:
        timestamp_seconds = int(timestamp)
    except (TypeError, ValueError):
        return None
    clock_skew = max(1, int(settings.internal_hmac_clock_skew_seconds))
    if abs(int(time.time()) - timestamp_seconds) > clock_skew:
        return None

    body = await request.body()
    path = canonical_request_path(str(request.url))
    nonce = request.headers.get(INTERNAL_HMAC_NONCE_HEADER, "").strip()
    normalized_method = request.method.upper()
    if normalized_method in _HMAC_REPLAY_PROTECTED_METHODS:
        if not nonce or len(nonce) > 256:
            return None

    async def _verify(session: AsyncSession) -> AuthContext | None:
        credential = await internal_hmac_credential_for_key(session, key_id)
        if credential is None or not credential.enabled:
            return None

        signing_payload = build_internal_hmac_signing_payload(
            timestamp,
            request.method,
            path,
            body,
            nonce=nonce,
        )
        expected_signature = hmac_sha256_hex(credential.secret, signing_payload)
        if not secrets.compare_digest(signature, expected_signature):
            return None

        if normalized_method in _HMAC_REPLAY_PROTECTED_METHODS:
            nonce_key = f"{key_id}:{nonce}"
            if not await _check_nonce(nonce_key, clock_skew):
                return None

        context = service_token_context(
            service_plugin_id=credential.service_plugin_id,
            service_type=credential.service_type,
            plugin_type=credential.plugin_type,
            credential_scope=credential.auth_scope,
        )
        try:
            _require_service_credential_scope(context, "poundcake_control_plane")
        except AccessDeniedError:
            return None
        return context

    if db is not None:
        return await _verify(db)
    async with auth_verifier_db_session() as auth_db:
        return await _verify(auth_db)


async def require_reader(
    context: AuthContext | None = Depends(require_auth_if_enabled),
) -> AuthContext:
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid session required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if context.role == "service":
        return context
    if not is_authorized_for_role(context, "reader"):
        raise HTTPException(status_code=403, detail="Reader access required")
    return context


async def require_operator(
    context: AuthContext | None = Depends(require_auth_if_enabled),
) -> AuthContext:
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid session required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if context.role == "service":
        return context
    if not is_authorized_for_role(context, "operator"):
        raise HTTPException(status_code=403, detail="Operator access required")
    return context


async def require_admin(
    context: AuthContext | None = Depends(require_auth_if_enabled),
) -> AuthContext:
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid session required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if context.role == "service":
        return context
    if not is_authorized_for_role(context, "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return context


async def require_service(
    context: AuthContext | None = Depends(require_auth_if_enabled),
) -> AuthContext:
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid session required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if context is None or context.role != "service":
        raise HTTPException(status_code=403, detail="Service access required")
    return context


@router.get("/auth/providers", response_model=list[AuthProviderResponse])
async def get_auth_providers() -> list[AuthProviderResponse]:
    """Return enabled auth provider metadata for UI and CLI discovery."""
    return [AuthProviderResponse.model_validate(item) for item in get_enabled_provider_metadata()]


@router.post("/auth/login", response_model=SessionResponse)
async def login(
    request: Request,
    payload: AuthLoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    """Log in with a password-based provider and set a session cookie."""
    req_id = getattr(request.state, "req_id", "AUTH-LOGIN")
    metadata = get_enabled_provider_metadata()
    password_providers = [str(item["name"]) for item in metadata if item.get("password_login")]
    provider = str(payload.provider or "").strip().lower()
    if not provider:
        if len(password_providers) == 1:
            provider = password_providers[0]
        else:
            raise HTTPException(status_code=400, detail="provider is required")

    logger.info(
        "Login attempt",
        extra={"req_id": req_id, "provider": provider, "username": payload.username},
    )

    try:
        identity = await authenticate_password_provider(
            provider, payload.username, payload.password
        )
        context = await build_login_context(db, identity)
        await db.commit()
        return await _persist_session(request, response, context)
    except InvalidCredentialsError as exc:
        await db.rollback()
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except AccessDeniedError as exc:
        await db.rollback()
        await _remember_observed_principal(db, locals().get("identity"))
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        await db.rollback()
        logger.error("Login failed", extra={"req_id": req_id}, exc_info=True)
        raise HTTPException(status_code=500, detail="an unexpected error occurred") from exc


@router.get("/auth/me", response_model=AuthMeResponse)
async def auth_me(
    context: AuthContext = Depends(require_auth_if_enabled),
) -> AuthMeResponse:
    """Return the resolved principal behind the current session."""
    return AuthMeResponse(
        username=context.username,
        display_name=context.display_name,
        provider=context.provider,
        role=context.role,
        principal_type=context.principal_type,
        principal_id=context.principal_id,
        is_superuser=context.is_superuser,
        permissions=context.permissions,
        groups=context.groups,
        expires_at=context.expires_at,
    )


@router.post("/auth/logout", response_model=AuthLogoutResponse)
async def logout(
    request: Request,
    response: Response,
) -> AuthLogoutResponse:
    """Destroy the current session."""
    session_token = request.cookies.get("session_token")
    await get_session_store().delete_session(session_token)
    _clear_session_cookie(response)
    return AuthLogoutResponse(message="Logged out successfully")


@router.post("/auth/refresh", response_model=SessionResponse)
async def refresh_session(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    """Extend the current session if it has not reached its absolute maximum lifetime.

    This endpoint does not require authentication (it reads the cookie directly)
    so that a proactive client can renew before receiving a 401 on an arbitrary
    API call.
    """
    session_token = request.cookies.get("session_token")
    if not session_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="No session cookie present"
        )

    settings = get_settings()
    if settings.auth_session_timeout == 0:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Session refresh disabled"
        )

    # Rehydrate without extending — we extend explicitly below.
    # Manually rehydrate to avoid the implicit sliding TTL logic.
    store = get_session_store()
    stored_context_or_error, resolution_error = await rehydrate_session_context(db, session_token)

    if stored_context_or_error is None:
        if resolution_error:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=resolution_error)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session"
        )

    # Ensure context is a human context (service tokens should not touch this endpoint)
    if not stored_context_or_error.is_human():
        raise HTTPException(
            status_code=status.HTTP_403, detail="Service tokens cannot use this endpoint"
        )

    refresh_at = utc_now() + timedelta(seconds=settings.auth_session_timeout)
    context = stored_context_or_error
    context.expires_at = refresh_at.isoformat()

    # Update stored session with new expiry and TTL, preserving _created_at
    payload = context.to_session_payload() if hasattr(context, "to_session_payload") else {}
    payload.setdefault(
        "_created_at", (utc_now() - timedelta(seconds=settings.auth_session_timeout)).isoformat()
    )
    payload["expires_at"] = refresh_at.isoformat()
    await store.set_value("session", session_token, payload, settings.auth_session_timeout)

    _set_session_cookie(request, response, session_token)
    return _session_response(context)


@router.get("/auth/oidc/login")
async def oidc_login(
    request: Request,
    next: str = Query(default="/overview"),
    provider: str | None = Query(default=None),
) -> RedirectResponse:
    """Start an external browser login flow."""
    resolved_provider = _resolve_sso_provider(provider, mode="browser")
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24) if resolved_provider == "azure_ad" else ""
    target = _normalize_next_target(next)
    callback_url = build_auth_callback_url(str(request.base_url).rstrip("/"), resolved_provider)
    try:
        url = await get_oidc_authorize_url(
            resolved_provider,
            state=state,
            redirect_uri=callback_url,
            nonce=(nonce or None),
        )
    except ProviderConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    store = get_session_store()
    await store.put_state(
        "oidc_state",
        state,
        {"next": target, "provider": resolved_provider, "nonce": nonce},
        ttl_seconds=get_settings().auth_oidc_state_ttl,
    )
    return RedirectResponse(url=url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get("/auth/oidc/callback")
async def oidc_callback(
    request: Request,
    code: str = Query(..., min_length=1),
    state: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Complete an external browser login flow and redirect back to the UI."""
    store = get_session_store()
    state_payload = await store.pop_state("oidc_state", state)
    if not state_payload:
        raise HTTPException(status_code=400, detail="Invalid or expired login state")

    provider = str(state_payload.get("provider") or "auth0").strip().lower()
    nonce = str(state_payload.get("nonce") or "").strip() or None
    callback_url = build_auth_callback_url(str(request.base_url).rstrip("/"), provider)
    try:
        identity = await authenticate_oidc_authorization_code(
            provider,
            code=code,
            redirect_uri=callback_url,
            nonce=nonce,
        )
        context = await build_login_context(db, identity)
        await db.commit()
    except InvalidCredentialsError as exc:
        await db.rollback()
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except AccessDeniedError as exc:
        await db.rollback()
        await _remember_observed_principal(db, locals().get("identity"))
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ProviderConfigurationError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    redirect = RedirectResponse(
        url=_normalize_next_target(str(state_payload.get("next") or "/overview")),
        status_code=status.HTTP_303_SEE_OTHER,
    )
    session_response = await _persist_session(request, redirect, context)
    if not session_response.session_id:
        raise HTTPException(status_code=500, detail="Could not create session")
    return redirect


@router.post("/auth/device/start", response_model=DeviceAuthorizationStartResponse)
async def device_start(
    payload: DeviceAuthorizationStartRequest | None = None,
) -> DeviceAuthorizationStartResponse:
    """Start an external device login flow for CLI users."""
    resolved_provider = _resolve_sso_provider(
        None if payload is None else payload.provider,
        mode="device",
    )
    try:
        result = await start_device_authorization(resolved_provider)
    except ProviderConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return DeviceAuthorizationStartResponse(
        provider=result.provider,
        device_code=result.device_code,
        user_code=result.user_code,
        verification_uri=result.verification_uri,
        verification_uri_complete=result.verification_uri_complete,
        expires_in=result.expires_in,
        interval=result.interval,
    )


@router.post("/auth/device/poll", response_model=DeviceAuthorizationPollResponse)
async def device_poll(
    request: Request,
    payload: DeviceAuthorizationPollRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> DeviceAuthorizationPollResponse:
    """Poll an external device flow until the user approves it."""
    resolved_provider = _resolve_sso_provider(payload.provider, mode="device")
    try:
        identity = await authenticate_device_code(resolved_provider, payload.device_code)
        context = await build_login_context(db, identity)
        await db.commit()
        session = await _persist_session(request, response, context)
        return DeviceAuthorizationPollResponse(status="authorized", session=session)
    except DeviceAuthorizationPending:
        await db.rollback()
        return DeviceAuthorizationPollResponse(status="pending", interval=5)
    except DeviceAuthorizationExpired as exc:
        await db.rollback()
        return DeviceAuthorizationPollResponse(status="expired", detail=str(exc))
    except InvalidCredentialsError as exc:
        await db.rollback()
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except AccessDeniedError as exc:
        await db.rollback()
        await _remember_observed_principal(db, locals().get("identity"))
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ProviderConfigurationError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/auth/principals", response_model=list[AuthPrincipalResponse])
async def get_principals(
    provider: str | None = Query(default=None),
    search: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _context: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[AuthPrincipalResponse]:
    """List observed principals for access management."""
    principals = await list_principals(
        db,
        provider=provider,
        search=search,
        limit=limit,
        offset=offset,
    )
    return [_principal_response(principal) for principal in principals]


@router.get("/auth/bindings", response_model=list[AuthRoleBindingResponse])
async def get_bindings(
    provider: str | None = Query(default=None),
    _context: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[AuthRoleBindingResponse]:
    """List configured RBAC bindings."""
    bindings = await list_role_bindings(db, provider=provider)
    return [_binding_response(binding) for binding in bindings]


@router.post("/auth/bindings", response_model=AuthRoleBindingResponse, status_code=201)
async def create_binding(
    payload: AuthRoleBindingCreate,
    context: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AuthRoleBindingResponse:
    """Create a new RBAC binding."""
    if payload.provider not in {"auth0", "active_directory", "azure_ad"}:
        raise HTTPException(
            status_code=400,
            detail="Bindings are only supported for Auth0, Azure AD, and Active Directory",
        )
    if payload.binding_type == "user":
        principal = await get_principal_by_id(db, int(payload.principal_id or 0))
        if principal is None:
            raise HTTPException(status_code=404, detail="Principal not found")
        if principal.provider != payload.provider:
            raise HTTPException(
                status_code=400, detail="Principal provider does not match binding provider"
            )

    binding = await create_role_binding(
        db,
        provider=payload.provider,
        binding_type=payload.binding_type,
        role=payload.role,
        principal_id=payload.principal_id,
        external_group=payload.external_group,
        created_by=payload.created_by or context.username,
    )
    await db.commit()
    await db.refresh(binding)
    if binding.principal_id:
        binding.principal = await get_principal_by_id(db, binding.principal_id)
    return _binding_response(binding)


@router.patch("/auth/bindings/{binding_id}", response_model=AuthRoleBindingResponse)
async def update_binding(
    binding_id: int,
    payload: AuthRoleBindingUpdate,
    _context: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AuthRoleBindingResponse:
    """Update an existing RBAC binding."""
    binding = await get_role_binding(db, binding_id)
    if binding is None:
        raise HTTPException(status_code=404, detail="Binding not found")
    binding = await update_role_binding(
        db,
        binding,
        role=payload.role,
        external_group=payload.external_group,
    )
    await db.commit()
    await db.refresh(binding)
    if binding.principal_id:
        binding.principal = await get_principal_by_id(db, binding.principal_id)
    return _binding_response(binding)


@router.delete("/auth/bindings/{binding_id}", response_model=DeleteResponse)
async def delete_binding(
    binding_id: int,
    _context: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> DeleteResponse:
    """Delete an RBAC binding."""
    binding = await get_role_binding(db, binding_id)
    if binding is None:
        raise HTTPException(status_code=404, detail="Binding not found")
    await delete_role_binding(db, binding)
    await db.commit()
    return DeleteResponse(id=binding_id, message="Binding deleted")
