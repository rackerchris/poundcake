#  ___                        _  ____      _
# |  _ \ ___  _   _ _ __   __| |/ ___|__ _| | _____
# | |_) / _ \| | | | '_ \ / _` | |   / _` | |/ / _ \
# |  __/ (_) | |_| | | | | (_| | |__| (_| |   <  __/
# |_|   \___/ \__,_|_| |_|\__,_|\____\__,_|_|\_\___|
#
"""Authentication and authorization services for PoundCake."""

from __future__ import annotations

import asyncio
import re
import secrets
import ssl
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.core.config import get_settings
from api.core.http_client import request_with_retry
from api.core.logging import get_logger
from api.models.models import AuthPrincipal, AuthRoleBinding
from api.types import AuthBindingType, AuthPrincipalType, AuthProvider, AuthRole, JSONObject

logger = get_logger(__name__)

ROLE_PRECEDENCE: dict[AuthRole, int] = {
    "reader": 0,
    "operator": 1,
    "admin": 2,
    "service": 3,
}

ROLE_PERMISSIONS: dict[AuthRole, list[str]] = {
    "reader": ["read"],
    "operator": [
        "read",
        "manage_alert_rules",
        "manage_ingredients",
        "manage_recipes",
        "manage_suppressions",
    ],
    "admin": [
        "read",
        "manage_access",
        "manage_alert_rules",
        "manage_auth",
        "manage_global_communications",
        "manage_ingredients",
        "manage_recipes",
        "manage_suppressions",
    ],
    "service": ["read", "service"],
}

HUMAN_ROLES: set[AuthRole] = {"reader", "operator", "admin"}

_MEMORY_STATE: dict[str, tuple[JSONObject, datetime]] = {}
_SESSION_STORE: SessionStore | None = None
_SESSION_STORE_KEY: tuple[Any, ...] | None = None
_OIDC_DISCOVERY_CACHE: dict[str, JSONObject] = {}
_OIDC_JWKS_CACHE: dict[str, JSONObject] = {}

AUTH_PROVIDER_LABELS: dict[str, str] = {
    "local": "Local Superuser",
    "active_directory": "Active Directory",
    "auth0": "Auth0",
    "azure_ad": "Azure AD",
    "service": "Internal Service",
}


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


def normalize_groups(groups: list[str] | None) -> list[str]:
    """Return a stable, de-duplicated group list."""
    if not groups:
        return []
    seen: dict[str, str] = {}
    for group in groups:
        value = str(group or "").strip()
        if not value:
            continue
        seen[value.casefold()] = value
    return sorted(seen.values(), key=str.casefold)


def highest_role(roles: list[str]) -> AuthRole | None:
    """Pick the highest-precedence role from a list."""
    best: AuthRole | None = None
    for role in roles:
        normalized = str(role or "").strip().lower()
        if normalized not in ROLE_PRECEDENCE:
            continue
        candidate = normalized  # type: ignore[assignment]
        if best is None or ROLE_PRECEDENCE[candidate] > ROLE_PRECEDENCE[best]:
            best = candidate
    return best


def permissions_for_role(role: AuthRole, *, is_superuser: bool = False) -> list[str]:
    """Expand a role into concrete permissions."""
    permissions = list(ROLE_PERMISSIONS[role])
    if is_superuser and "superuser" not in permissions:
        permissions.append("superuser")
    return permissions


@dataclass
class AuthIdentity:
    """Authenticated identity before RBAC resolution."""

    provider: AuthProvider
    subject_id: str
    username: str
    display_name: str | None = None
    groups: list[str] = field(default_factory=list)
    principal_type: AuthPrincipalType = "user"
    is_superuser: bool = False

    def normalized_groups(self) -> list[str]:
        return normalize_groups(self.groups)


@dataclass
class AuthContext:
    """Resolved principal metadata stored in sessions and request state."""

    provider: AuthProvider
    subject_id: str
    username: str
    display_name: str | None
    groups: list[str]
    role: AuthRole
    principal_type: AuthPrincipalType
    is_superuser: bool = False
    permissions: list[str] = field(default_factory=list)
    principal_id: int | None = None
    session_id: str | None = None
    expires_at: str | None = None
    service_plugin_id: int | None = None
    service_type: str | None = None
    plugin_type: str | None = None
    credential_scope: str | None = None

    def is_human(self) -> bool:
        return self.principal_type == "user"

    def to_session_payload(self) -> JSONObject:
        return asdict(self)


@dataclass
class DeviceAuthorizationStart:
    """External device authorization payload."""

    provider: AuthProvider
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None
    expires_in: int
    interval: int


class AuthError(RuntimeError):
    """Base auth service error."""


class InvalidCredentialsError(AuthError):
    """Raised when provider credentials are rejected."""


class ProviderConfigurationError(AuthError):
    """Raised when a configured auth provider is incomplete."""


class AccessDeniedError(AuthError):
    """Raised when login succeeded but no role binding matches."""


class DeviceAuthorizationPending(AuthError):
    """Raised when an external device flow has not completed yet."""


class DeviceAuthorizationExpired(AuthError):
    """Raised when an external device flow expired."""


class SessionStore:
    """Persist auth sessions and short-lived auth state."""

    def __init__(self) -> None:
        self.prefix = "poundcake:auth"

    def _state_key(self, kind: str, key: str) -> str:
        return f"{self.prefix}:{kind}:{key}"

    async def create_session(self, context: AuthContext, *, ttl_seconds: int) -> AuthContext:
        context.session_id = secrets.token_urlsafe(32)
        context.expires_at = (utc_now() + timedelta(seconds=ttl_seconds)).isoformat()
        payload = context.to_session_payload()
        payload["_created_at"] = utc_now().isoformat()
        await self.set_value("session", context.session_id, payload, ttl_seconds)
        return context

    async def get_session(self, session_id: str | None) -> AuthContext | None:
        if not session_id:
            return None
        payload = await self.get_value("session", session_id)
        if not isinstance(payload, dict):
            return None
        try:
            return AuthContext(
                provider=str(payload["provider"]),  # type: ignore[arg-type]
                subject_id=str(payload["subject_id"]),
                username=str(payload["username"]),
                display_name=(
                    None
                    if payload.get("display_name") is None
                    else str(payload.get("display_name"))
                ),
                groups=normalize_groups(payload.get("groups")),
                role=str(payload["role"]),  # type: ignore[arg-type]
                principal_type=str(payload["principal_type"]),  # type: ignore[arg-type]
                is_superuser=bool(payload.get("is_superuser")),
                permissions=[str(item) for item in payload.get("permissions") or []],
                principal_id=(
                    int(payload["principal_id"])
                    if payload.get("principal_id") is not None
                    else None
                ),
                session_id=str(payload.get("session_id") or session_id),
                expires_at=(
                    None if payload.get("expires_at") is None else str(payload.get("expires_at"))
                ),
            )
        except KeyError:
            return None

    async def delete_session(self, session_id: str | None) -> None:
        if not session_id:
            return
        await self.delete_value("session", session_id)

    async def put_state(
        self, kind: str, key: str, payload: JSONObject, *, ttl_seconds: int
    ) -> None:
        await self.set_value(kind, key, payload, ttl_seconds)

    async def pop_state(self, kind: str, key: str) -> JSONObject | None:
        payload = await self.get_value(kind, key)
        await self.delete_value(kind, key)
        if isinstance(payload, dict):
            return payload
        return None

    async def set_value(self, kind: str, key: str, payload: JSONObject, ttl_seconds: int) -> None:
        _MEMORY_STATE[self._state_key(kind, key)] = (
            payload,
            utc_now() + timedelta(seconds=ttl_seconds),
        )

    async def put_if_absent(
        self,
        kind: str,
        key: str,
        payload: JSONObject,
        ttl_seconds: int,
    ) -> bool:
        storage_key = self._state_key(kind, key)
        entry = _MEMORY_STATE.get(storage_key)
        if entry:
            _, expires_at = entry
            if expires_at > utc_now():
                return False
            _MEMORY_STATE.pop(storage_key, None)
        _MEMORY_STATE[storage_key] = (
            payload,
            utc_now() + timedelta(seconds=ttl_seconds),
        )
        return True

    async def get_value(self, kind: str, key: str) -> JSONObject | None:
        storage_key = self._state_key(kind, key)
        entry = _MEMORY_STATE.get(storage_key)
        if not entry:
            return None
        payload, expires_at = entry
        if expires_at <= utc_now():
            _MEMORY_STATE.pop(storage_key, None)
            return None
        return dict(payload)

    async def delete_value(self, kind: str, key: str) -> None:
        _MEMORY_STATE.pop(self._state_key(kind, key), None)


def get_session_store() -> SessionStore:
    """Return the active auth session store."""
    global _SESSION_STORE, _SESSION_STORE_KEY
    key = ("memory",)
    if _SESSION_STORE is None or _SESSION_STORE_KEY != key:
        _SESSION_STORE = SessionStore()
        _SESSION_STORE_KEY = key
    return _SESSION_STORE


def get_local_superuser_credentials() -> tuple[str, str] | None:
    """Load the bootstrap superuser credentials from configured secret-backed env."""
    settings = get_settings()

    if not settings.auth_enabled or not settings.auth_local_enabled:
        return None

    if settings.auth_username and settings.auth_password:
        return (settings.auth_username, settings.auth_password)

    return None


def get_enabled_provider_metadata() -> list[JSONObject]:
    """Describe enabled auth providers for UI and CLI discovery."""
    settings = get_settings()
    providers: list[JSONObject] = []

    if get_local_superuser_credentials():
        providers.append(
            {
                "name": "local",
                "label": AUTH_PROVIDER_LABELS["local"],
                "login_mode": "password",
                "cli_login_mode": "password",
                "browser_login": False,
                "device_login": False,
                "password_login": True,
            }
        )

    if settings.auth_ad_enabled and settings.auth_ad_server_uri:
        providers.append(
            {
                "name": "active_directory",
                "label": AUTH_PROVIDER_LABELS["active_directory"],
                "login_mode": "password",
                "cli_login_mode": "password",
                "browser_login": False,
                "device_login": False,
                "password_login": True,
            }
        )

    auth0_browser = auth0_browser_login_enabled(settings)
    auth0_device = auth0_device_login_enabled(settings)
    if auth0_browser or auth0_device:
        providers.append(
            {
                "name": "auth0",
                "label": AUTH_PROVIDER_LABELS["auth0"],
                "login_mode": "oidc" if auth0_browser else "device",
                "cli_login_mode": "device" if auth0_device else "unavailable",
                "browser_login": auth0_browser,
                "device_login": auth0_device,
                "password_login": False,
            }
        )

    azure_browser = azure_ad_browser_login_enabled(settings)
    azure_device = azure_ad_device_login_enabled(settings)
    if azure_browser or azure_device:
        providers.append(
            {
                "name": "azure_ad",
                "label": AUTH_PROVIDER_LABELS["azure_ad"],
                "login_mode": "oidc" if azure_browser else "device",
                "cli_login_mode": "device" if azure_device else "unavailable",
                "browser_login": azure_browser,
                "device_login": azure_device,
                "password_login": False,
            }
        )

    return providers


def provider_names() -> list[str]:
    """Return enabled provider names."""
    return [str(item["name"]) for item in get_enabled_provider_metadata()]


def provider_label(provider: str) -> str:
    """Return the human label for an auth provider."""
    return AUTH_PROVIDER_LABELS.get(provider, provider)


def browser_login_provider_names(settings: Any | None = None) -> list[str]:
    """Return enabled browser-capable SSO providers."""
    settings = settings or get_settings()
    providers: list[str] = []
    if auth0_browser_login_enabled(settings):
        providers.append("auth0")
    if azure_ad_browser_login_enabled(settings):
        providers.append("azure_ad")
    return providers


def device_login_provider_names(settings: Any | None = None) -> list[str]:
    """Return enabled CLI device-login providers."""
    settings = settings or get_settings()
    providers: list[str] = []
    if auth0_device_login_enabled(settings):
        providers.append("auth0")
    if azure_ad_device_login_enabled(settings):
        providers.append("azure_ad")
    return providers


def auth0_browser_login_enabled(settings: Any | None = None) -> bool:
    """Return whether Auth0 browser login is configured."""
    settings = settings or get_settings()
    return bool(
        settings.auth_auth0_enabled
        and settings.auth_auth0_domain
        and settings.auth_auth0_ui_enabled
        and settings.auth_auth0_ui_client_id
    )


def auth0_device_login_enabled(settings: Any | None = None) -> bool:
    """Return whether Auth0 CLI device login is configured."""
    settings = settings or get_settings()
    return bool(
        settings.auth_auth0_enabled
        and settings.auth_auth0_domain
        and settings.auth_auth0_cli_enabled
        and settings.auth_auth0_cli_client_id
    )


def azure_ad_browser_login_enabled(settings: Any | None = None) -> bool:
    """Return whether Azure AD browser login is configured."""
    settings = settings or get_settings()
    return bool(
        settings.auth_azure_ad_enabled
        and settings.auth_azure_ad_tenant
        and settings.auth_azure_ad_ui_enabled
        and settings.auth_azure_ad_ui_client_id
    )


def azure_ad_device_login_enabled(settings: Any | None = None) -> bool:
    """Return whether Azure AD CLI device login is configured."""
    settings = settings or get_settings()
    return bool(
        settings.auth_azure_ad_enabled
        and settings.auth_azure_ad_tenant
        and settings.auth_azure_ad_cli_enabled
        and settings.auth_azure_ad_cli_client_id
    )


async def authenticate_password_provider(
    provider: str,
    username: str,
    password: str,
) -> AuthIdentity:
    """Authenticate a username/password against the selected provider."""
    normalized_provider = str(provider or "").strip().lower()
    if normalized_provider == "local":
        return await authenticate_local_superuser(username, password)
    if normalized_provider == "active_directory":
        return await authenticate_active_directory(username, password)
    raise ProviderConfigurationError(f"Provider '{provider}' does not support password login")


async def authenticate_local_superuser(username: str, password: str) -> AuthIdentity:
    """Validate the bootstrap superuser credentials."""
    credentials = get_local_superuser_credentials()
    if not credentials:
        raise ProviderConfigurationError("Local superuser credentials are not configured")
    configured_username, configured_password = credentials
    if not (
        secrets.compare_digest(username, configured_username)
        and secrets.compare_digest(password, configured_password)
    ):
        raise InvalidCredentialsError("Invalid username or password")
    return AuthIdentity(
        provider="local",
        subject_id=configured_username,
        username=configured_username,
        display_name=configured_username,
        groups=[],
        principal_type="user",
        is_superuser=True,
    )


def _sync_authenticate_active_directory(username: str, password: str) -> AuthIdentity:
    from ldap3 import ALL, Connection, Server, Tls  # type: ignore[import-untyped]
    from ldap3.utils.conv import escape_filter_chars  # type: ignore[import-untyped]

    settings = get_settings()
    if not settings.auth_ad_enabled or not settings.auth_ad_server_uri:
        raise ProviderConfigurationError("Active Directory is not enabled")

    tls = None
    if settings.auth_ad_use_ssl:
        tls_validate = ssl.CERT_REQUIRED if settings.auth_ad_validate_tls else ssl.CERT_NONE
        tls = Tls(
            validate=tls_validate,
            ca_certs_file=settings.auth_ad_ca_certs_file or None,
        )

    server = Server(
        settings.auth_ad_server_uri,
        use_ssl=settings.auth_ad_use_ssl,
        get_info=ALL,
        tls=tls,
    )

    service_user = settings.auth_ad_bind_dn or username
    service_password = settings.auth_ad_bind_password or password
    service_conn = Connection(server, user=service_user, password=service_password, auto_bind=True)

    safe_username = escape_filter_chars(username)
    search_filter = settings.auth_ad_user_filter.format(username=safe_username)
    attributes = [
        settings.auth_ad_username_attribute,
        settings.auth_ad_display_name_attribute,
        settings.auth_ad_subject_attribute,
        settings.auth_ad_group_attribute,
    ]
    found = service_conn.search(
        search_base=settings.auth_ad_user_base_dn,
        search_filter=search_filter,
        attributes=attributes,
    )
    if not found or not service_conn.entries:
        raise InvalidCredentialsError("Invalid username or password")

    entry = service_conn.entries[0]
    user_dn = str(entry.entry_dn)

    user_conn = Connection(server, user=user_dn, password=password, auto_bind=True)
    user_conn.unbind()

    username_value = getattr(entry, settings.auth_ad_username_attribute, None)
    display_value = getattr(entry, settings.auth_ad_display_name_attribute, None)
    subject_value = getattr(entry, settings.auth_ad_subject_attribute, None)
    raw_groups = getattr(entry, settings.auth_ad_group_attribute, None)

    username_text = str(getattr(username_value, "value", "") or "").strip() or username
    display_text = str(getattr(display_value, "value", "") or "").strip() or username_text
    subject_text = str(getattr(subject_value, "value", "") or "").strip() or user_dn

    group_values = getattr(raw_groups, "values", None)
    if group_values is None:
        value = getattr(raw_groups, "value", None)
        if isinstance(value, list):
            group_values = value
        elif value:
            group_values = [value]
        else:
            group_values = []

    group_name_pattern = re.compile(settings.auth_ad_group_name_regex)
    groups: list[str] = []
    for raw_group in group_values:
        value = str(raw_group or "").strip()
        if not value:
            continue
        match = group_name_pattern.search(value)
        groups.append(match.group(1) if match else value)

    return AuthIdentity(
        provider="active_directory",
        subject_id=subject_text,
        username=username_text,
        display_name=display_text,
        groups=normalize_groups(groups),
        principal_type="user",
        is_superuser=False,
    )


async def authenticate_active_directory(username: str, password: str) -> AuthIdentity:
    """Validate Active Directory credentials and group membership."""
    try:
        return await asyncio.to_thread(_sync_authenticate_active_directory, username, password)
    except InvalidCredentialsError:
        raise
    except ProviderConfigurationError:
        raise
    except Exception as exc:
        raise InvalidCredentialsError(str(exc)) from exc


def _auth0_base_url() -> str:
    settings = get_settings()
    if not (settings.auth_auth0_enabled and settings.auth_auth0_domain):
        raise ProviderConfigurationError("Auth0 is not enabled")
    domain = settings.auth_auth0_domain.strip().rstrip("/")
    if domain.startswith("https://"):
        return domain
    return f"https://{domain}"


def _auth0_common_form_fields(*, client_id: str, client_secret: str = "") -> dict[str, str]:
    settings = get_settings()
    payload = {"client_id": client_id}
    if client_secret:
        payload["client_secret"] = client_secret
    if settings.auth_auth0_audience:
        payload["audience"] = settings.auth_auth0_audience
    if settings.auth_auth0_scope:
        payload["scope"] = settings.auth_auth0_scope
    return payload


def _azure_ad_tenant() -> str:
    settings = get_settings()
    if not (settings.auth_azure_ad_enabled and settings.auth_azure_ad_tenant):
        raise ProviderConfigurationError("Azure AD is not enabled")
    tenant = settings.auth_azure_ad_tenant.strip().strip("/")
    if tenant.lower() in {"common", "organizations", "consumers"}:
        raise ProviderConfigurationError("Azure AD tenant must be a single-tenant identifier")
    return tenant


def _azure_ad_authority_base() -> str:
    return f"https://login.microsoftonline.com/{_azure_ad_tenant()}"


def _azure_ad_scope() -> str:
    settings = get_settings()
    configured_items = [item for item in str(settings.auth_azure_ad_scope or "").split() if item]
    existing = {item.casefold() for item in configured_items}
    for required in ("openid", "profile", "email"):
        if required.casefold() not in existing:
            configured_items.append(required)
    return " ".join(configured_items)


async def _azure_ad_discovery_document() -> JSONObject:
    authority = _azure_ad_authority_base()
    cached = _OIDC_DISCOVERY_CACHE.get(authority)
    if cached is not None:
        return dict(cached)

    response = await request_with_retry(
        "GET",
        f"{authority}/v2.0/.well-known/openid-configuration",
    )
    if response.status_code >= 400:
        raise ProviderConfigurationError("Azure AD OIDC discovery failed")
    data = response.json()
    if not isinstance(data, dict):
        raise ProviderConfigurationError("Azure AD OIDC discovery returned an invalid payload")

    for required_key in (
        "issuer",
        "authorization_endpoint",
        "token_endpoint",
        "jwks_uri",
        "device_authorization_endpoint",
    ):
        if not data.get(required_key):
            raise ProviderConfigurationError("Azure AD OIDC discovery response was incomplete")

    _OIDC_DISCOVERY_CACHE[authority] = dict(data)
    return dict(data)


async def _azure_ad_jwks() -> JSONObject:
    discovery = await _azure_ad_discovery_document()
    jwks_uri = str(discovery["jwks_uri"])
    cached = _OIDC_JWKS_CACHE.get(jwks_uri)
    if cached is not None:
        return dict(cached)

    response = await request_with_retry("GET", jwks_uri)
    if response.status_code >= 400:
        raise ProviderConfigurationError("Azure AD JWKS lookup failed")
    data = response.json()
    if not isinstance(data, dict) or not isinstance(data.get("keys"), list):
        raise ProviderConfigurationError("Azure AD JWKS response was invalid")

    _OIDC_JWKS_CACHE[jwks_uri] = dict(data)
    return dict(data)


def _normalize_claim_groups(groups_value: Any) -> list[str]:
    if isinstance(groups_value, str):
        return normalize_groups([groups_value])
    if isinstance(groups_value, list):
        return normalize_groups([str(item) for item in groups_value])
    return []


def _auth0_identity_from_profile(profile: JSONObject) -> AuthIdentity:
    settings = get_settings()
    groups = _normalize_claim_groups(profile.get(settings.auth_auth0_groups_claim) or [])

    username = str(
        profile.get(settings.auth_auth0_username_claim)
        or profile.get("email")
        or profile.get("nickname")
        or profile.get("name")
        or profile.get("sub")
        or ""
    ).strip()
    subject_id = str(
        profile.get(settings.auth_auth0_subject_claim) or profile.get("sub") or ""
    ).strip()
    if not username or not subject_id:
        raise InvalidCredentialsError("Auth0 profile did not include a usable identity")

    display_name = str(
        profile.get(settings.auth_auth0_display_name_claim) or profile.get("name") or username
    ).strip()

    return AuthIdentity(
        provider="auth0",
        subject_id=subject_id,
        username=username,
        display_name=display_name or username,
        groups=groups,
        principal_type="user",
        is_superuser=False,
    )


def _azure_ad_groups_from_claims(claims: JSONObject) -> list[str]:
    settings = get_settings()
    groups_claim = settings.auth_azure_ad_groups_claim
    claim_names = claims.get("_claim_names")
    if claims.get("hasgroups"):
        return []
    if isinstance(claim_names, dict) and (groups_claim in claim_names or "groups" in claim_names):
        return []
    return _normalize_claim_groups(claims.get(groups_claim) or claims.get("groups") or [])


def _azure_ad_identity_from_claims(
    claims: JSONObject,
    *,
    expected_nonce: str | None = None,
) -> AuthIdentity:
    settings = get_settings()

    if expected_nonce is not None:
        token_nonce = str(claims.get("nonce") or "").strip()
        if not token_nonce or not secrets.compare_digest(token_nonce, expected_nonce):
            raise InvalidCredentialsError("Azure AD token nonce did not match the login request")

    configured_tenant = _azure_ad_tenant()
    token_tenant = str(claims.get("tid") or "").strip()
    if re.fullmatch(r"[0-9a-fA-F-]{36}", configured_tenant) and token_tenant:
        if token_tenant.casefold() != configured_tenant.casefold():
            raise InvalidCredentialsError(
                "Azure AD token tenant did not match the configured tenant"
            )

    username = str(
        claims.get(settings.auth_azure_ad_username_claim)
        or claims.get("email")
        or claims.get("upn")
        or claims.get("preferred_username")
        or claims.get("name")
        or claims.get("sub")
        or ""
    ).strip()
    subject_id = str(
        claims.get(settings.auth_azure_ad_subject_claim) or claims.get("sub") or ""
    ).strip()
    if not username or not subject_id:
        raise InvalidCredentialsError("Azure AD token did not include a usable identity")

    display_name = str(
        claims.get(settings.auth_azure_ad_display_name_claim) or claims.get("name") or username
    ).strip()

    return AuthIdentity(
        provider="azure_ad",
        subject_id=subject_id,
        username=username,
        display_name=display_name or username,
        groups=_azure_ad_groups_from_claims(claims),
        principal_type="user",
        is_superuser=False,
    )


async def get_auth0_authorize_url(state: str, redirect_uri: str) -> str:
    """Construct the Auth0 browser login URL."""
    settings = get_settings()
    if not auth0_browser_login_enabled(settings):
        raise ProviderConfigurationError("Auth0 browser login is not enabled")
    params = {
        "response_type": "code",
        "client_id": settings.auth_auth0_ui_client_id,
        "redirect_uri": redirect_uri,
        "scope": settings.auth_auth0_scope,
        "state": state,
    }
    if settings.auth_auth0_audience:
        params["audience"] = settings.auth_auth0_audience
    if settings.auth_auth0_organization:
        params["organization"] = settings.auth_auth0_organization
    if settings.auth_auth0_connection:
        params["connection"] = settings.auth_auth0_connection
    return f"{_auth0_base_url()}/authorize?{urlencode(params)}"


async def get_azure_ad_authorize_url(state: str, redirect_uri: str, nonce: str) -> str:
    """Construct the Azure AD browser login URL."""
    settings = get_settings()
    if not azure_ad_browser_login_enabled(settings):
        raise ProviderConfigurationError("Azure AD browser login is not enabled")
    discovery = await _azure_ad_discovery_document()
    params = {
        "response_type": "code",
        "client_id": settings.auth_azure_ad_ui_client_id,
        "redirect_uri": redirect_uri,
        "scope": _azure_ad_scope(),
        "state": state,
        "nonce": nonce,
    }
    return f"{str(discovery['authorization_endpoint'])}?{urlencode(params)}"


async def get_oidc_authorize_url(
    provider: str,
    *,
    state: str,
    redirect_uri: str,
    nonce: str | None = None,
) -> str:
    """Construct the provider-specific browser login URL."""
    if provider == "auth0":
        return await get_auth0_authorize_url(state, redirect_uri)
    if provider == "azure_ad":
        if not nonce:
            raise ProviderConfigurationError("Azure AD browser login requires a nonce")
        return await get_azure_ad_authorize_url(state, redirect_uri, nonce)
    raise ProviderConfigurationError(f"Provider '{provider}' does not support browser login")


async def start_auth0_device_authorization() -> DeviceAuthorizationStart:
    """Begin an Auth0 device flow."""
    settings = get_settings()
    if not auth0_device_login_enabled(settings):
        raise ProviderConfigurationError("Auth0 CLI device login is not enabled")
    payload = _auth0_common_form_fields(
        client_id=settings.auth_auth0_cli_client_id,
        client_secret=settings.auth_auth0_cli_client_secret,
    )
    response = await request_with_retry(
        "POST",
        f"{_auth0_base_url()}/oauth/device/code",
        data=payload,
    )
    if response.status_code >= 400:
        raise ProviderConfigurationError("Auth0 device authorization could not be started")
    data = response.json()
    return DeviceAuthorizationStart(
        provider="auth0",
        device_code=str(data["device_code"]),
        user_code=str(data["user_code"]),
        verification_uri=str(data["verification_uri"]),
        verification_uri_complete=(
            None
            if data.get("verification_uri_complete") is None
            else str(data.get("verification_uri_complete"))
        ),
        expires_in=int(data.get("expires_in") or 0),
        interval=int(data.get("interval") or 5),
    )


async def start_azure_ad_device_authorization() -> DeviceAuthorizationStart:
    """Begin an Azure AD device flow."""
    settings = get_settings()
    if not azure_ad_device_login_enabled(settings):
        raise ProviderConfigurationError("Azure AD CLI device login is not enabled")
    discovery = await _azure_ad_discovery_document()
    response = await request_with_retry(
        "POST",
        str(discovery["device_authorization_endpoint"]),
        data={
            "client_id": settings.auth_azure_ad_cli_client_id,
            "scope": _azure_ad_scope(),
        },
    )
    if response.status_code >= 400:
        raise ProviderConfigurationError("Azure AD device authorization could not be started")
    data = response.json()
    return DeviceAuthorizationStart(
        provider="azure_ad",
        device_code=str(data["device_code"]),
        user_code=str(data["user_code"]),
        verification_uri=str(data["verification_uri"]),
        verification_uri_complete=(
            None
            if data.get("verification_uri_complete") is None
            else str(data.get("verification_uri_complete"))
        ),
        expires_in=int(data.get("expires_in") or 0),
        interval=int(data.get("interval") or 5),
    )


async def start_device_authorization(provider: str) -> DeviceAuthorizationStart:
    """Begin a provider-specific device login flow."""
    if provider == "auth0":
        return await start_auth0_device_authorization()
    if provider == "azure_ad":
        return await start_azure_ad_device_authorization()
    raise ProviderConfigurationError(f"Provider '{provider}' does not support device login")


async def _auth0_fetch_userinfo(access_token: str) -> JSONObject:
    response = await request_with_retry(
        "GET",
        f"{_auth0_base_url()}/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if response.status_code >= 400:
        raise InvalidCredentialsError("Auth0 user profile lookup failed")
    data = response.json()
    return data if isinstance(data, dict) else {}


async def authenticate_auth0_authorization_code(code: str, redirect_uri: str) -> AuthIdentity:
    """Exchange an Auth0 browser auth code for an identity."""
    settings = get_settings()
    if not auth0_browser_login_enabled(settings):
        raise ProviderConfigurationError("Auth0 browser login is not enabled")
    payload = {
        "grant_type": "authorization_code",
        "client_id": settings.auth_auth0_ui_client_id,
        "client_secret": settings.auth_auth0_ui_client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
    }
    response = await request_with_retry(
        "POST",
        f"{_auth0_base_url()}/oauth/token",
        json=payload,
    )
    if response.status_code >= 400:
        raise InvalidCredentialsError("Auth0 code exchange failed")
    data = response.json()
    access_token = str(data.get("access_token") or "").strip()
    if not access_token:
        raise InvalidCredentialsError("Auth0 response did not include an access token")
    profile = await _auth0_fetch_userinfo(access_token)
    return _auth0_identity_from_profile(profile)


async def authenticate_azure_ad_authorization_code(
    code: str,
    redirect_uri: str,
    *,
    nonce: str,
) -> AuthIdentity:
    """Exchange an Azure AD browser auth code for an identity."""
    settings = get_settings()
    if not azure_ad_browser_login_enabled(settings):
        raise ProviderConfigurationError("Azure AD browser login is not enabled")
    discovery = await _azure_ad_discovery_document()
    payload = {
        "grant_type": "authorization_code",
        "client_id": settings.auth_azure_ad_ui_client_id,
        "code": code,
        "redirect_uri": redirect_uri,
    }
    if settings.auth_azure_ad_ui_client_secret:
        payload["client_secret"] = settings.auth_azure_ad_ui_client_secret
    response = await request_with_retry(
        "POST",
        str(discovery["token_endpoint"]),
        data=payload,
    )
    if response.status_code >= 400:
        raise InvalidCredentialsError("Azure AD code exchange failed")
    data = response.json()
    id_token = str(data.get("id_token") or "").strip() if isinstance(data, dict) else ""
    if not id_token:
        raise InvalidCredentialsError("Azure AD response did not include an id_token")
    return await _decode_azure_ad_id_token(
        id_token,
        client_id=settings.auth_azure_ad_ui_client_id,
        expected_nonce=nonce,
    )


async def authenticate_oidc_authorization_code(
    provider: str,
    *,
    code: str,
    redirect_uri: str,
    nonce: str | None = None,
) -> AuthIdentity:
    """Exchange a provider-specific browser auth code for an identity."""
    if provider == "auth0":
        return await authenticate_auth0_authorization_code(code, redirect_uri)
    if provider == "azure_ad":
        if not nonce:
            raise ProviderConfigurationError("Azure AD browser login requires a nonce")
        return await authenticate_azure_ad_authorization_code(
            code,
            redirect_uri,
            nonce=nonce,
        )
    raise ProviderConfigurationError(f"Provider '{provider}' does not support browser login")


async def _decode_azure_ad_id_token(
    id_token: str,
    *,
    client_id: str,
    expected_nonce: str | None = None,
) -> AuthIdentity:
    discovery = await _azure_ad_discovery_document()
    jwks = await _azure_ad_jwks()

    from jose import jwt  # type: ignore[import-untyped]
    from jose.exceptions import (  # type: ignore[import-untyped]
        ExpiredSignatureError,
        JWTClaimsError,
        JWTError,
    )

    try:
        claims = jwt.decode(
            id_token,
            jwks,
            algorithms=["RS256"],
            audience=client_id,
            issuer=str(discovery["issuer"]),
            options={"verify_at_hash": False},
        )
    except ExpiredSignatureError as exc:
        raise InvalidCredentialsError("Azure AD token has expired") from exc
    except JWTClaimsError as exc:
        raise InvalidCredentialsError(str(exc)) from exc
    except JWTError as exc:
        raise InvalidCredentialsError("Azure AD token validation failed") from exc

    if not isinstance(claims, dict):
        raise InvalidCredentialsError("Azure AD token validation failed")
    return _azure_ad_identity_from_claims(claims, expected_nonce=expected_nonce)


async def authenticate_auth0_device_code(device_code: str) -> AuthIdentity:
    """Poll an Auth0 device code until the user authorizes it."""
    settings = get_settings()
    if not auth0_device_login_enabled(settings):
        raise ProviderConfigurationError("Auth0 CLI device login is not enabled")
    payload = {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": device_code,
        "client_id": settings.auth_auth0_cli_client_id,
    }
    if settings.auth_auth0_cli_client_secret:
        payload["client_secret"] = settings.auth_auth0_cli_client_secret
    response = await request_with_retry(
        "POST",
        f"{_auth0_base_url()}/oauth/token",
        data=payload,
        retry_statuses=[],
    )
    if response.status_code >= 400:
        data = response.json()
        error = str(data.get("error") or "").strip().lower() if isinstance(data, dict) else ""
        if error in {"authorization_pending", "slow_down"}:
            raise DeviceAuthorizationPending("Waiting for Auth0 approval")
        if error in {"expired_token", "access_denied"}:
            raise DeviceAuthorizationExpired("The Auth0 device login expired or was denied")
        raise InvalidCredentialsError("Auth0 device authorization failed")
    data = response.json()
    access_token = str(data.get("access_token") or "").strip()
    if not access_token:
        raise InvalidCredentialsError("Auth0 response did not include an access token")
    profile = await _auth0_fetch_userinfo(access_token)
    return _auth0_identity_from_profile(profile)


async def authenticate_azure_ad_device_code(device_code: str) -> AuthIdentity:
    """Poll an Azure AD device code until the user authorizes it."""
    settings = get_settings()
    if not azure_ad_device_login_enabled(settings):
        raise ProviderConfigurationError("Azure AD CLI device login is not enabled")
    discovery = await _azure_ad_discovery_document()
    response = await request_with_retry(
        "POST",
        str(discovery["token_endpoint"]),
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code,
            "client_id": settings.auth_azure_ad_cli_client_id,
        },
        retries=0,
    )
    if response.status_code >= 400:
        data = response.json()
        error = str(data.get("error") or "").strip().lower() if isinstance(data, dict) else ""
        if error in {"authorization_pending", "slow_down"}:
            raise DeviceAuthorizationPending("Waiting for Azure AD approval")
        if error in {"expired_token", "authorization_declined", "bad_verification_code"}:
            raise DeviceAuthorizationExpired("The Azure AD device login expired or was denied")
        raise InvalidCredentialsError("Azure AD device authorization failed")
    data = response.json()
    id_token = str(data.get("id_token") or "").strip() if isinstance(data, dict) else ""
    if not id_token:
        raise InvalidCredentialsError("Azure AD response did not include an id_token")
    return await _decode_azure_ad_id_token(
        id_token,
        client_id=settings.auth_azure_ad_cli_client_id,
    )


async def authenticate_device_code(provider: str, device_code: str) -> AuthIdentity:
    """Poll a provider-specific device code until the user authorizes it."""
    if provider == "auth0":
        return await authenticate_auth0_device_code(device_code)
    if provider == "azure_ad":
        return await authenticate_azure_ad_device_code(device_code)
    raise ProviderConfigurationError(f"Provider '{provider}' does not support device login")


async def upsert_principal(db: AsyncSession, identity: AuthIdentity) -> AuthPrincipal:
    """Create or refresh the stored principal record for an external user."""
    statement = select(AuthPrincipal).where(
        AuthPrincipal.provider == identity.provider,
        AuthPrincipal.subject_id == identity.subject_id,
    )
    principal = (await db.execute(statement)).scalar_one_or_none()
    if principal is None:
        principal = AuthPrincipal(
            provider=identity.provider,
            subject_id=identity.subject_id,
            username=identity.username,
            display_name=identity.display_name,
            principal_type=identity.principal_type,
            groups_json=identity.normalized_groups(),
            last_seen_at=utc_now(),
        )
        db.add(principal)
        await db.flush()
        return principal

    principal.username = identity.username
    principal.display_name = identity.display_name
    principal.principal_type = identity.principal_type
    principal.groups_json = identity.normalized_groups()
    principal.last_seen_at = utc_now()
    await db.flush()
    return principal


async def get_principal_by_id(db: AsyncSession, principal_id: int) -> AuthPrincipal | None:
    """Look up a principal by database id."""
    return await db.get(AuthPrincipal, principal_id)


async def list_principals(
    db: AsyncSession,
    *,
    provider: str | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[AuthPrincipal]:
    """List observed principals for access management."""
    statement = select(AuthPrincipal).order_by(AuthPrincipal.username.asc())
    if provider:
        statement = statement.where(AuthPrincipal.provider == provider)
    if search:
        pattern = f"%{search.strip()}%"
        statement = statement.where(
            or_(
                AuthPrincipal.username.ilike(pattern),
                AuthPrincipal.display_name.ilike(pattern),
            )
        )
    statement = statement.offset(offset).limit(limit)
    return list((await db.execute(statement)).scalars().all())


async def list_role_bindings(
    db: AsyncSession,
    *,
    provider: str | None = None,
) -> list[AuthRoleBinding]:
    """Return configured RBAC bindings."""
    statement = select(AuthRoleBinding).order_by(
        AuthRoleBinding.provider.asc(),
        AuthRoleBinding.binding_type.asc(),
        AuthRoleBinding.id.asc(),
    )
    statement = statement.options(selectinload(AuthRoleBinding.principal))
    if provider:
        statement = statement.where(AuthRoleBinding.provider == provider)
    return list((await db.execute(statement)).scalars().unique().all())


async def get_role_binding(db: AsyncSession, binding_id: int) -> AuthRoleBinding | None:
    """Load a single RBAC binding."""
    return await db.get(AuthRoleBinding, binding_id)


async def create_role_binding(
    db: AsyncSession,
    *,
    provider: str,
    binding_type: AuthBindingType,
    role: AuthRole,
    principal_id: int | None,
    external_group: str | None,
    created_by: str | None,
) -> AuthRoleBinding:
    """Create a provider-scoped RBAC binding."""
    binding = AuthRoleBinding(
        provider=provider,
        binding_type=binding_type,
        role=role,
        principal_id=principal_id,
        external_group=(None if external_group is None else external_group.strip() or None),
        created_by=(None if created_by is None else created_by.strip() or None),
    )
    db.add(binding)
    await db.flush()
    return binding


async def update_role_binding(
    db: AsyncSession,
    binding: AuthRoleBinding,
    *,
    role: AuthRole | None = None,
    external_group: str | None = None,
) -> AuthRoleBinding:
    """Update an existing RBAC binding."""
    if role is not None:
        binding.role = role
    if binding.binding_type == "group" and external_group is not None:
        binding.external_group = external_group.strip() or None
    await db.flush()
    return binding


async def delete_role_binding(db: AsyncSession, binding: AuthRoleBinding) -> None:
    """Delete an RBAC binding."""
    await db.delete(binding)


async def resolve_role_for_identity(
    db: AsyncSession,
    identity: AuthIdentity,
    principal: AuthPrincipal | None = None,
) -> tuple[AuthRole | None, AuthPrincipal | None]:
    """Resolve the effective role for a user identity."""
    if identity.provider == "local" and identity.is_superuser:
        return ("admin", None)

    principal = principal or await upsert_principal(db, identity)

    explicit_binding = (
        await db.execute(
            select(AuthRoleBinding).where(
                AuthRoleBinding.provider == identity.provider,
                AuthRoleBinding.binding_type == "user",
                AuthRoleBinding.principal_id == principal.id,
            )
        )
    ).scalar_one_or_none()
    if explicit_binding is not None:
        return (str(explicit_binding.role), principal)  # type: ignore[return-value]

    groups = identity.normalized_groups()
    if not groups:
        return (None, principal)

    group_bindings = (
        (
            await db.execute(
                select(AuthRoleBinding).where(
                    AuthRoleBinding.provider == identity.provider,
                    AuthRoleBinding.binding_type == "group",
                    AuthRoleBinding.external_group.in_(groups),
                )
            )
        )
        .scalars()
        .all()
    )
    matched_role = highest_role([binding.role for binding in group_bindings])
    return (matched_role, principal)


def build_auth_context(
    identity: AuthIdentity,
    role: AuthRole,
    *,
    principal: AuthPrincipal | None = None,
) -> AuthContext:
    """Build the resolved auth context returned to routes and clients."""
    return AuthContext(
        provider=identity.provider,
        subject_id=identity.subject_id,
        username=identity.username,
        display_name=identity.display_name or identity.username,
        groups=identity.normalized_groups(),
        role=role,
        principal_type=identity.principal_type,
        is_superuser=identity.is_superuser,
        permissions=permissions_for_role(role, is_superuser=identity.is_superuser),
        principal_id=None if principal is None else principal.id,
    )


async def build_login_context(db: AsyncSession, identity: AuthIdentity) -> AuthContext:
    """Resolve a newly authenticated identity into a session-ready context."""
    role, principal = await resolve_role_for_identity(db, identity)
    if role is None:
        raise AccessDeniedError("No PoundCake role binding matches this user")
    return build_auth_context(identity, role, principal=principal)


def service_token_context(
    *,
    service_plugin_id: int | None = None,
    service_type: str | None = None,
    plugin_type: str | None = None,
    credential_scope: str | None = None,
) -> AuthContext:
    """Return the static service principal context."""
    normalized_service_type = (service_type or "").strip().lower() or None
    return AuthContext(
        provider="service",
        subject_id=normalized_service_type or "service-token",
        username=normalized_service_type or "service",
        display_name=(
            f"Internal Service ({normalized_service_type})"
            if normalized_service_type
            else "Internal Service"
        ),
        groups=[],
        role="service",
        principal_type="service",
        is_superuser=False,
        permissions=permissions_for_role("service"),
        principal_id=None,
        service_plugin_id=service_plugin_id,
        service_type=normalized_service_type,
        plugin_type=(plugin_type or "").strip().lower() or None,
        credential_scope=(credential_scope or "").strip() or None,
    )


async def rehydrate_session_context(
    db: AsyncSession,
    session_id: str | None,
) -> tuple[AuthContext | None, str | None]:
    """Load session state and refresh RBAC from current bindings."""
    store = get_session_store()
    stored = await store.get_session(session_id)
    if stored is None:
        return (None, None)

    if stored.role == "service" or stored.principal_type == "service":
        return (stored, None)

    if stored.is_superuser and stored.provider == "local":
        stored.role = "admin"
        stored.permissions = permissions_for_role("admin", is_superuser=True)
        return (stored, None)

    max_session = get_settings().auth_absolute_max_session
    created_at_str = (
        stored.to_session_payload().get("_created_at")
        if hasattr(stored, "to_session_payload")
        else None
    )
    if not created_at_str:
        created_at_str = stored.expires_at  # legacy sessions: use expires_at as worst proxy
    try:
        created_at = datetime.fromisoformat(str(created_at_str))
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        elapsed = (utc_now() - created_at).total_seconds()
        if elapsed >= max_session:
            _MEMORY_STATE.pop(store._state_key("session", session_id), None)
            return (None, "Session has expired")
    except (ValueError, TypeError):
        pass

    identity = AuthIdentity(
        provider=stored.provider,
        subject_id=stored.subject_id,
        username=stored.username,
        display_name=stored.display_name,
        groups=stored.groups,
        principal_type=stored.principal_type,
        is_superuser=stored.is_superuser,
    )
    principal = None
    if stored.principal_id is not None:
        principal = await get_principal_by_id(db, stored.principal_id)
    role, principal = await resolve_role_for_identity(db, identity, principal=principal)
    if role is None:
        return (None, "No PoundCake role binding matches this session")

    refreshed = build_auth_context(identity, role, principal=principal)
    refreshed.session_id = stored.session_id
    refreshed.expires_at = stored.expires_at

    ttl = get_settings().auth_session_timeout
    refresh_at = utc_now() + timedelta(seconds=ttl)
    refreshed.expires_at = refresh_at.isoformat()
    stored_dict = stored.to_session_payload() if hasattr(stored, "to_session_payload") else {}
    stored_dict["expires_at"] = refresh_at.isoformat()
    await store.set_value("session", session_id, stored_dict, ttl)

    return (refreshed, None)


def _is_public_path(path: str, method: str) -> bool:
    if method == "OPTIONS":
        return True
    public_paths = {
        "/",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/livez",
        "/readyz",
        "/api/v1/auth/login",
        "/api/v1/auth/providers",
        "/api/v1/auth/oidc/login",
        "/api/v1/auth/oidc/callback",
        "/api/v1/auth/device/start",
        "/api/v1/auth/device/poll",
        "/api/v1/webhook",
    }
    return path in public_paths or path.startswith("/static/")


def is_request_public(path: str, method: str) -> bool:
    """Determine whether an incoming request bypasses auth."""
    return _is_public_path(path, method.upper())


def _path_is(path: str, prefix: str) -> bool:
    """Return True for an exact route or a descendant route under the prefix."""
    return path == prefix or path.startswith(f"{prefix}/")


def _service_only_path(path: str, method: str) -> bool:
    if _path_is(path, "/api/v1/internal"):
        return True
    if _path_is(path, "/api/v1/orders") and method != "GET":
        return True
    if _path_is(path, "/api/v1/dishes") and method != "GET":
        return True
    if path in {"/api/v1/orders", "/api/v1/dishes"} and method == "GET":
        return True
    if (
        path.startswith("/api/v1/orders/")
        and method == "GET"
        and not _reader_reporting_status_path(path, method)
        and not path.endswith("/execution-history")
    ):
        return True
    if path.startswith("/api/v1/cook/") and method != "GET":
        return True
    return False


def _timer_runtime_path(path: str, method: str) -> bool:
    if (
        path
        in {
            "/api/v1/dish-ingredients/in-flight",
            "/api/v1/dish-ingredients/cancel-requested",
            "/api/v1/dish-ingredients/advance-ready",
        }
        and method == "GET"
    ):
        return True
    if path.startswith("/api/v1/dish-ingredients/") and method == "POST":
        return (
            path.endswith("/poll-claim")
            or path.endswith("/poll-release")
            or path.endswith("/reconcile")
        )
    if path.startswith("/api/v1/dishes/") and path.endswith("/ingredients") and method == "GET":
        return True
    if path.startswith("/api/v1/cook/dishes/") and path.endswith("/advance") and method == "POST":
        return True
    if path.startswith("/api/v1/expediter/status/") and method == "GET":
        return True
    if path.startswith("/api/v1/expediter/cancel/") and method == "POST":
        return True
    return False


def _expediter_runner_runtime_path(path: str, method: str) -> bool:
    if path == "/api/v1/dish-ingredients/execution-pending" and method == "GET":
        return True
    if path.startswith("/api/v1/dish-ingredients/") and method == "POST":
        return (
            path.endswith("/execution-claim")
            or path.endswith("/execution-release")
            or path.endswith("/reconcile")
        )
    if path.startswith("/api/v1/expediter/execute/") and method == "POST":
        return True
    if path.startswith("/api/v1/cook/dishes/") and path.endswith("/advance") and method == "POST":
        return True
    return False


def _service_allowed_path(service_type: str | None, path: str, method: str) -> bool:
    """Return True when an internal service identity may call this control-plane route."""
    normalized = (service_type or "").strip().lower()
    method = method.upper()
    if normalized == "prep-chef":
        return (
            (path == "/api/v1/orders" and method == "GET")
            or (path.startswith("/api/v1/cook/orders/") and method == "POST")
            or (path == "/api/v1/plugins/prep-chef" and method == "GET")
        )
    if normalized == "timer":
        return _timer_runtime_path(path, method) or (
            path == "/api/v1/plugins/timer" and method == "GET"
        )
    if normalized == "expediter-runner":
        return _expediter_runner_runtime_path(path, method) or (
            path == "/api/v1/plugins/expediter-runner" and method == "GET"
        )
    if normalized == "dishwasher":
        if path in {"/api/v1/plugins", "/api/v1/plugins/dishwasher"} and method == "GET":
            return True
        if path == "/api/v1/internal/service-registry/ingredients/bulk" and method == "POST":
            return True
        if _path_is(path, "/api/v1/service-registry") and method == "GET":
            return True
        if _path_is(path, "/api/v1/recipes") and method in {"GET", "POST", "PUT", "PATCH"}:
            return True
        if path == "/api/v1/communications/policy" and method in {"GET", "PUT"}:
            return True
        if path.startswith("/api/v1/scheduled-tasks/") and path.endswith("/run-now"):
            return False
        if _path_is(path, "/api/v1/scheduled-tasks") and method in {
            "GET",
            "POST",
            "PATCH",
        }:
            return True
        if path == "/api/v1/orders" and method == "POST":
            return True
        return False
    if normalized == "credential-manager":
        if path in {"/api/v1/plugins", "/api/v1/plugins/credential-manager"} and method == "GET":
            return True
        return False
    return False


def _operator_plugin_runtime_path(path: str, method: str) -> bool:
    """Return True for operator-owned plugin runtime and non-secret connection tuning."""
    if not path.startswith("/api/v1/plugins/"):
        return False
    if method == "PATCH":
        return True
    if path.endswith("/configuration") and method in {"GET", "PUT"}:
        return True
    if path.endswith("/test-connection") and method == "POST":
        return True
    return False


def _admin_plugin_configuration_path(path: str, method: str) -> bool:
    """Return True for admin-owned plugin adapter secret actions."""
    if not path.startswith("/api/v1/plugins/"):
        return False
    if method == "PUT" and path.endswith("/credentials"):
        return True
    return False


def _reader_reporting_status_path(path: str, method: str) -> bool:
    """Return True for redacted reporting/status GET routes."""
    if method != "GET":
        return False
    if path in {
        "/api/v1/health/status",
        "/api/v1/orders/status",
        "/api/v1/recipes/status",
        "/api/v1/dishes/status",
        "/api/v1/service-registry/ingredients/status",
        "/api/v1/scheduled-tasks/status",
        "/api/v1/suppressions/status",
        "/api/v1/observability/overview",
        "/api/v1/observability/activity/status",
        "/api/v1/communications/activity/status",
    }:
        return True
    if path.startswith("/api/v1/orders/") and (
        path.endswith("/status") or path.endswith("/timeline")
    ):
        return True
    if path.startswith("/api/v1/recipes/") and (
        path.endswith("/status") or path.endswith("/ingredient-status")
    ):
        return True
    if path.startswith("/api/v1/dishes/") and path.endswith("/ingredient-status"):
        return True
    return False


def _admin_execution_history_path(path: str, method: str) -> bool:
    """Return True for admin-readable runtime evidence without mutation authority."""
    if method != "GET":
        return False
    if path.startswith("/api/v1/dishes/") and (
        path.endswith("/ingredients") or path.endswith("/ingredient-history")
    ):
        return True
    if path.startswith("/api/v1/orders/") and path.endswith("/execution-history"):
        return True
    return False


def _operator_desired_state_read_path(path: str, method: str) -> bool:
    """Return True for operator-readable recipe and ingredient definition detail."""
    if method != "GET" or _reader_reporting_status_path(path, method):
        return False
    return _path_is(path, "/api/v1/recipes") or _path_is(path, "/api/v1/service-registry")


def _admin_detail_read_path(path: str, method: str) -> bool:
    """Return True for desired-state detail reads used by admin editor/configuration surfaces."""
    if method != "GET" or _reader_reporting_status_path(path, method):
        return False
    if path in {
        "/api/v1/scheduled-tasks",
        "/api/v1/scheduled-tasks/due",
    }:
        return True
    return path.startswith("/api/v1/scheduled-tasks/")


def request_role_requirement(path: str, method: str) -> AuthRole | None:
    """Map API routes to the minimum role required."""
    normalized_method = method.upper()

    if is_request_public(path, normalized_method):
        return None

    if _service_only_path(path, normalized_method):
        return "service"

    if _path_is(path, "/api/v1/auth/bindings") or _path_is(path, "/api/v1/auth/principals"):
        return "admin"

    if path in {"/api/v1/auth/me", "/api/v1/auth/logout"}:
        return "reader"

    if path == "/api/v1/communications/policy" and normalized_method == "PUT":
        return "admin"

    if _operator_plugin_runtime_path(path, normalized_method):
        return "operator"

    if _admin_plugin_configuration_path(path, normalized_method):
        return "admin"

    if _reader_reporting_status_path(path, normalized_method):
        return "reader"

    if _admin_execution_history_path(path, normalized_method):
        return "admin"

    if _timer_runtime_path(path, normalized_method):
        return "service"

    if _expediter_runner_runtime_path(path, normalized_method):
        return "service"

    if _operator_desired_state_read_path(path, normalized_method):
        return "operator"

    if _admin_detail_read_path(path, normalized_method):
        if path == "/api/v1/scheduled-tasks/due":
            return "service"
        return "admin"

    if (
        path.startswith("/api/v1/scheduled-tasks/")
        and path.endswith("/run-now")
        and normalized_method == "POST"
    ):
        return "operator"

    if path.startswith("/api/v1/scheduled-tasks/") and normalized_method == "PATCH":
        return "operator"

    if _path_is(path, "/api/v1/plugins") and normalized_method != "GET":
        return "admin"

    if _path_is(path, "/api/v1/service-registry") and normalized_method != "GET":
        return "admin"

    if _path_is(path, "/api/v1/recipes") and normalized_method != "GET":
        return "operator"

    if _path_is(path, "/api/v1/suppressions") and normalized_method != "GET":
        return "operator"

    if normalized_method == "GET":
        return "reader"

    return "admin"


def is_authorized_for_role(context: AuthContext, required_role: AuthRole) -> bool:
    """Check whether a resolved context satisfies a request requirement."""
    if required_role == "service":
        return context.role == "service"

    if context.role == "service":
        return required_role == "reader"

    if context.is_superuser:
        return True

    if required_role == "reader":
        return context.role in HUMAN_ROLES
    if required_role == "operator":
        return context.role in {"operator", "admin"}
    if required_role == "admin":
        return context.role == "admin"
    return False


def _require_service_credential_scope(context: AuthContext, expected_scope: str) -> None:
    """Raise when a service principal does not carry the expected credential scope."""
    actual_scope = (context.credential_scope or "").strip()
    if actual_scope != expected_scope:
        label = context.service_type or context.username or "service"
        raise AccessDeniedError(f"service credential scope for {label} must be {expected_scope!r}")


def ensure_request_authorized(context: AuthContext, path: str, method: str) -> None:
    """Raise when the principal cannot perform the request."""
    required_role = request_role_requirement(path, method)
    if required_role is None:
        return
    if context.role == "service":
        normalized_method = method.upper()
        if context.plugin_type == "internal_plugin":
            _require_service_credential_scope(context, "poundcake_control_plane")
        if (
            context.plugin_type == "internal_plugin"
            and context.service_type
            and _service_allowed_path(
                context.service_type,
                path,
                normalized_method,
            )
        ):
            return
        raise AccessDeniedError(
            f"service role"
            f"{f' ({context.service_type})' if context.service_type else ''}"
            f" cannot access {normalized_method} {path}"
        )
    if not is_authorized_for_role(context, required_role):
        raise AccessDeniedError(f"{context.role} role cannot access {method.upper()} {path}")


def build_auth_callback_url(base_url: str, provider: str = "auth0") -> str:
    """Return the external auth callback URL for this deployment."""
    settings = get_settings()
    if provider == "auth0":
        if settings.auth_auth0_ui_callback_url:
            return settings.auth_auth0_ui_callback_url
        return f"{base_url.rstrip('/')}/api/v1/auth/oidc/callback"
    if provider == "azure_ad":
        if settings.auth_azure_ad_ui_callback_url:
            return settings.auth_azure_ad_ui_callback_url
        return f"{base_url.rstrip('/')}/api/v1/auth/oidc/callback"
    return f"{base_url.rstrip('/')}/api/v1/auth/oidc/callback"
