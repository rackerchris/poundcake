#  ___                        _  ____      _
# |  _ \ ___  _   _ _ __   __| |/ ___|__ _| | _____
# | |_) / _ \| | | | '_ \ / _` | |   / _` | |/ / _ \
# |  __/ (_) | |_| | | | | (_| | |__| (_| |   <  __/
# |_|   \___/ \__,_|_| |_|\__,_|\____\__,_|_|\_\___|
#
"""Application configuration - merged from poundcake and poundcake-api."""

import os
from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from api.version import __version__


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="POUNDCAKE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ==========================================================================
    # Application Settings
    # ==========================================================================
    app_version: str = Field(default=__version__)
    debug: bool = False

    # API Server
    server_host: str = "0.0.0.0"
    server_port: int = 8000

    # ==========================================================================
    # Database Settings
    # ==========================================================================
    # Default to in-cluster MariaDB service (overridden by Helm env var in production).
    database_url: str = "mysql+pymysql://poundcake_api:poundcake@poundcake-mariadb:3306/poundcake"
    credential_manager_database_url: str = ""
    auth_verifier_database_url: str = ""
    prep_chef_reader_database_url: str = ""
    timer_reader_database_url: str = ""
    expediter_runner_reader_database_url: str = ""
    dishwasher_reader_database_url: str = ""
    plugin_operation_database_url: str = Field(
        default="",
        validation_alias="POUNDCAKE_PLUGIN_OPERATION_DB_URL",
    )
    database_echo: bool = False

    lock_timeout_seconds: int = 300

    # ==========================================================================
    # Alertmanager Settings
    # ==========================================================================
    alertmanager_url: str = ""
    alertmanager_verify_ssl: bool = True
    alertmanager_timeout_seconds: float = 10.0
    webhook_bearer_token: str = ""

    # ==========================================================================
    # Prometheus Settings
    # ==========================================================================
    prometheus_url: str = (
        "http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090"
    )
    prometheus_verify_ssl: bool = True
    prometheus_reload_enabled: bool = True
    prometheus_reload_url: str = ""

    # Prometheus Operator CRD settings (Kubernetes)
    prometheus_use_crds: bool = True
    prometheus_crd_namespace: str = "prometheus"
    prometheus_crd_labels: dict[str, str] = Field(default_factory=dict)
    k8s_allow_local_kubeconfig: bool = False

    # ==========================================================================
    # Git Repository Settings
    # ==========================================================================
    git_repo_url: str = ""
    git_branch: str = "main"
    git_rules_path: str = "prometheus/rules"
    git_workflows_path: str = "poundcake/workflows"
    git_actions_path: str = "poundcake/actions"
    git_user_name: str = "PoundCake"
    git_user_email: str = "poundcake@localhost"
    git_provider: str = "github"

    # ==========================================================================
    # Mappings & Logging
    # ==========================================================================
    httpx_timeout_seconds: int = 30
    httpx_connect_timeout_seconds: int = 10
    httpx_read_timeout_seconds: int = 30
    httpx_write_timeout_seconds: int = 30
    httpx_max_connections: int = 100
    httpx_max_keepalive: int = 20
    httpx_retries: int = 2
    httpx_retry_backoff_seconds: float = 0.5
    httpx_retry_statuses: list[int] = Field(default_factory=lambda: [429, 500, 502, 503, 504])
    poller_http_retries: int = 0
    external_http_retries: int = 2

    log_level: str = "INFO"
    log_format: str = "console"  # Change to 'json' for production/Helm

    # ==========================================================================
    # Metrics & CORS
    # ==========================================================================
    metrics_enabled: bool = True
    allowed_origins: list[str] = Field(
        default_factory=lambda: [],
        description=(
            "CORS allowed origins. Must be set explicitly. Wildcard `*` is prohibited "
            "with `allow_credentials=True` per RFC 6454."
        ),
    )

    # ==========================================================================
    # Rate Limiting
    # ==========================================================================
    rate_limit_webhook: str = "1000/minute"
    rate_limit_default: str = "60/minute"
    rate_limit_internal: str = "10000/minute"
    max_request_body_bytes: int = 1_048_576
    max_webhook_body_bytes: int = 262_144

    # ==========================================================================
    # Authentication & Identification
    # ==========================================================================
    auth_enabled: bool = True
    auth_session_timeout: int = 900
    auth_session_refresh_window: int = 300
    auth_absolute_max_session: int = 28800
    auth_oidc_state_ttl: int = 600
    auth_rbac_enabled: bool = True
    internal_hmac_clock_skew_seconds: int = 300
    force_secure_cookie: bool = True

    # Local bootstrap superuser.
    auth_local_enabled: bool = True
    auth_username: str = ""
    auth_password: str = ""

    # Active Directory / LDAP.
    auth_ad_enabled: bool = False
    auth_ad_server_uri: str = ""
    auth_ad_bind_dn: str = ""
    auth_ad_bind_password: str = ""
    auth_ad_user_base_dn: str = ""
    auth_ad_user_filter: str = "(&(objectClass=user)(sAMAccountName={username}))"
    auth_ad_group_attribute: str = "memberOf"
    auth_ad_display_name_attribute: str = "displayName"
    auth_ad_username_attribute: str = "sAMAccountName"
    auth_ad_subject_attribute: str = "distinguishedName"
    auth_ad_use_ssl: bool = True
    auth_ad_validate_tls: bool = True
    auth_ad_ca_certs_file: str = ""
    auth_ad_group_name_regex: str = r"CN=([^,]+)"

    # Auth0.
    auth_auth0_enabled: bool = False
    auth_auth0_domain: str = ""
    auth_auth0_audience: str = ""
    auth_auth0_scope: str = "openid profile email"
    auth_auth0_organization: str = ""
    auth_auth0_connection: str = ""
    auth_auth0_username_claim: str = "email"
    auth_auth0_display_name_claim: str = "name"
    auth_auth0_groups_claim: str = "groups"
    auth_auth0_subject_claim: str = "sub"
    auth_auth0_ui_enabled: bool = Field(
        default_factory=lambda: bool(os.getenv("POUNDCAKE_AUTH_AUTH0_UI_CLIENT_ID"))
    )
    auth_auth0_ui_client_id: str = Field(
        default_factory=lambda: os.getenv("POUNDCAKE_AUTH_AUTH0_UI_CLIENT_ID", "")
    )
    auth_auth0_ui_client_secret: str = Field(
        default_factory=lambda: os.getenv("POUNDCAKE_AUTH_AUTH0_UI_CLIENT_SECRET", "")
    )
    auth_auth0_ui_callback_url: str = Field(
        default_factory=lambda: os.getenv("POUNDCAKE_AUTH_AUTH0_UI_CALLBACK_URL", "")
    )
    auth_auth0_cli_enabled: bool = Field(
        default_factory=lambda: bool(os.getenv("POUNDCAKE_AUTH_AUTH0_CLI_CLIENT_ID"))
    )
    auth_auth0_cli_client_id: str = Field(
        default_factory=lambda: os.getenv("POUNDCAKE_AUTH_AUTH0_CLI_CLIENT_ID", "")
    )
    auth_auth0_cli_client_secret: str = Field(
        default_factory=lambda: os.getenv("POUNDCAKE_AUTH_AUTH0_CLI_CLIENT_SECRET", "")
    )

    # Azure AD / Microsoft Entra ID.
    auth_azure_ad_enabled: bool = False
    auth_azure_ad_tenant: str = ""
    auth_azure_ad_scope: str = "openid profile email"
    auth_azure_ad_username_claim: str = "preferred_username"
    auth_azure_ad_display_name_claim: str = "name"
    auth_azure_ad_groups_claim: str = "groups"
    auth_azure_ad_subject_claim: str = "sub"
    auth_azure_ad_ui_enabled: bool = Field(
        default_factory=lambda: bool(os.getenv("POUNDCAKE_AUTH_AZURE_AD_UI_CLIENT_ID"))
    )
    auth_azure_ad_ui_client_id: str = Field(
        default_factory=lambda: os.getenv("POUNDCAKE_AUTH_AZURE_AD_UI_CLIENT_ID", "")
    )
    auth_azure_ad_ui_client_secret: str = Field(
        default_factory=lambda: os.getenv("POUNDCAKE_AUTH_AZURE_AD_UI_CLIENT_SECRET", "")
    )
    auth_azure_ad_ui_callback_url: str = Field(
        default_factory=lambda: os.getenv("POUNDCAKE_AUTH_AZURE_AD_UI_CALLBACK_URL", "")
    )
    auth_azure_ad_cli_enabled: bool = Field(
        default_factory=lambda: bool(os.getenv("POUNDCAKE_AUTH_AZURE_AD_CLI_CLIENT_ID"))
    )
    auth_azure_ad_cli_client_id: str = Field(
        default_factory=lambda: os.getenv("POUNDCAKE_AUTH_AZURE_AD_CLI_CLIENT_ID", "")
    )

    # ==========================================================================
    # Alert Suppression Settings
    # ==========================================================================
    suppressions_enabled: bool = True
    suppression_lifecycle_enabled: bool = True
    suppression_lifecycle_batch_limit: int = 25

    @model_validator(mode="after")
    def _validate_cors_config(self) -> "Settings":
        """Prohibit wildcard origins with credentials (RFC 6454)."""
        if "*" in self.allowed_origins and len(self.allowed_origins) == 1:
            raise ValueError(
                "POUNDCAKE_ALLOWED_ORIGINS must not be wildcard '*' when allow_credentials=True. "
                "Set explicit origins (e.g. 'https://app.example.com')."
            )
        return self


settings = Settings()


@lru_cache
def get_settings() -> Settings:
    return Settings()
