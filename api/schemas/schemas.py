#  ___                        _  ____      _
# |  _ \ ___  _   _ _ __   __| |/ ___|__ _| | _____
# | |_) / _ \| | | | '_ \ / _` | |   / _` | |/ / _ \
# |  __/ (_) | |_| | | | | (_| | |__| (_| |   <  __/
# |_|   \___/ \__,_|_| |_|\__,_|\____\__,_|_|\_\___|
#
"""Pydantic schemas for PoundCake API."""

from pydantic import (
    BaseModel as PydanticBaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from typing import List, Optional, Dict, Any, Literal
from datetime import datetime

from api.types import (
    AuthBindingType,
    AuthPrincipalType,
    AuthProvider,
    AuthRole,
    DishProcessingStatus,
    OrderProcessingStatus,
    OrderType,
    AlertStatus,
    CanonicalExecutionStatus,
    PluginHealthStatus,
    ScheduledTaskSource,
    ScheduledTaskStatus,
    ScheduledTaskType,
    PluginHealthCheckState,
    OnSuccessAction,
    OnFailureAction,
    SuppressionScope,
    SuppressionStatus,
    SuppressionMatcherOperator,
    RunPhase,
    DishRunPhase,
    RunCondition,
    ExecutionPurpose,
    RemediationOutcome,
    JSONObject,
    JSONValue,
)
from api.services.communications import (
    ALERTMANAGER_REQUIRED_ANNOTATION_FIELDS,
    ALERTMANAGER_REQUIRED_LABEL_FIELDS,
    normalize_destination_type,
    normalize_route_provider_config,
)


class BaseModel(PydanticBaseModel):
    """Strict base model for PoundCake-owned DTOs."""

    model_config = ConfigDict(extra="forbid")


# =============================================================================
# Health & Stats
# =============================================================================


class ComponentHealth(BaseModel):
    status: str  # healthy, degraded, unhealthy
    message: Optional[str] = None
    details: Optional[JSONObject] = None


class HealthResponse(BaseModel):
    status: str  # overall: healthy, degraded, unhealthy
    version: str
    instance_id: str
    timestamp: datetime
    components: Dict[str, ComponentHealth]


class LivenessResponse(BaseModel):
    status: str
    version: str


class ServicePluginSummaryResponse(BaseModel):
    service_type: str
    plugin_short_id: str
    plugin_type: str = "external_plugin"
    plugin_tier: str = "community"
    plugin_log_key: Optional[str] = None
    enabled: bool
    run_interval_seconds: Optional[int] = Field(default=None, ge=1)
    query_limit: Optional[int] = Field(default=None, ge=1)
    status_message: Optional[str] = None
    config_editable: bool = False
    ingredient_template_count: int
    recipe_template_count: int
    credential_status: str = "unknown"
    credential_error: Optional[str] = None
    last_credential_bootstrap_at: Optional[datetime] = None
    last_credential_rotation_at: Optional[datetime] = None
    health_status: PluginHealthStatus
    health_message: Optional[str] = None
    health_error_code: Optional[str] = None
    health_latency_ms: Optional[int] = None
    last_health_check_at: Optional[datetime] = None
    next_health_check_at: Optional[datetime] = None
    health_check_task_id: Optional[int] = None
    health_check_interval_seconds: Optional[int] = Field(default=None, ge=1)
    health_check_enabled: bool = False
    last_success_at: Optional[datetime] = None
    consecutive_failures: int = 0
    health_check_state: PluginHealthCheckState = "idle"
    health_check_order_id: Optional[int] = None
    health_check_started_at: Optional[datetime] = None
    health_check_grace_until: Optional[datetime] = None
    helper_available: bool = False
    helper_capabilities: List[str] = Field(default_factory=list)
    required_helper_capabilities: Dict[str, List[str]] = Field(default_factory=dict)
    missing_helper_capabilities: Dict[str, List[str]] = Field(default_factory=dict)


class ServicePluginBaseResponse(BaseModel):
    plugin_short_id: str
    plugin_type: str = "external_plugin"
    plugin_tier: str = "community"
    plugin_log_key: Optional[str] = None
    enabled: bool = True
    run_interval_seconds: Optional[int] = Field(default=None, ge=1)
    query_limit: Optional[int] = Field(default=None, ge=1)
    status_message: Optional[str] = None
    config_editable: bool = False
    credential_status: str = "unknown"
    credential_error: Optional[str] = None
    last_credential_bootstrap_at: Optional[datetime] = None
    last_credential_rotation_at: Optional[datetime] = None
    health_status: PluginHealthStatus
    health_message: Optional[str] = None
    health_error_code: Optional[str] = None
    health_latency_ms: Optional[int] = Field(default=None, ge=0)
    health_details: Optional[JSONObject] = None
    capabilities_hash: Optional[str] = Field(default=None, max_length=64)
    registered_ingredient_count: int = Field(default=0, ge=0)
    registered_recipe_count: int = Field(default=0, ge=0)
    last_health_check_at: Optional[datetime] = None
    next_health_check_at: Optional[datetime] = None
    health_check_task_id: Optional[int] = None
    health_check_interval_seconds: Optional[int] = Field(default=None, ge=1)
    health_check_enabled: bool = False
    health_check_state: Optional[PluginHealthCheckState] = None
    health_check_order_id: Optional[int] = None
    health_check_started_at: Optional[datetime] = None
    health_check_grace_until: Optional[datetime] = None
    helper_available: bool = False
    helper_capabilities: List[str] = Field(default_factory=list)
    required_helper_capabilities: Dict[str, List[str]] = Field(default_factory=dict)
    missing_helper_capabilities: Dict[str, List[str]] = Field(default_factory=dict)


class ServicePluginResponse(ServicePluginBaseResponse):
    id: int
    service_type: str
    consecutive_failures: int
    last_success_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class ServicePluginHealthResponse(BaseModel):
    service_type: str
    plugin_short_id: str
    plugin_type: str = "external_plugin"
    plugin_tier: str = "community"
    plugin_log_key: Optional[str] = None
    enabled: bool
    run_interval_seconds: Optional[int] = Field(default=None, ge=1)
    query_limit: Optional[int] = Field(default=None, ge=1)
    status_message: Optional[str] = None
    config_editable: bool = False
    credential_status: str = "unknown"
    credential_error: Optional[str] = None
    last_credential_bootstrap_at: Optional[datetime] = None
    last_credential_rotation_at: Optional[datetime] = None
    health_status: PluginHealthStatus
    health_message: Optional[str] = None
    health_error_code: Optional[str] = None
    health_latency_ms: Optional[int] = None
    health_details: Optional[JSONObject] = None
    registered_ingredient_count: int
    registered_recipe_count: int
    consecutive_failures: int
    last_health_check_at: Optional[datetime] = None
    next_health_check_at: Optional[datetime] = None
    health_check_task_id: Optional[int] = None
    health_check_interval_seconds: Optional[int] = Field(default=None, ge=1)
    health_check_enabled: bool = False
    last_success_at: Optional[datetime] = None
    updated_at: datetime
    health_check_state: PluginHealthCheckState = "idle"
    health_check_order_id: Optional[int] = None
    health_check_started_at: Optional[datetime] = None
    health_check_grace_until: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class ServicePluginHealthCheckResponse(BaseModel):
    service_type: str
    status: PluginHealthStatus
    message: Optional[str] = None
    error_code: Optional[str] = None
    latency_ms: Optional[int] = None
    details: Optional[JSONObject] = None
    checked_at: datetime


class ServicePluginUpdate(BaseModel):
    enabled: Optional[bool] = None
    run_interval_seconds: Optional[int] = Field(default=None, ge=1)
    query_limit: Optional[int] = Field(default=None, ge=1)
    health_check_interval_seconds: Optional[int] = Field(default=None, ge=1)
    status_message: Optional[str] = Field(default=None, max_length=2000)


class ServicePluginConfigurationResponse(BaseModel):
    service_type: str
    config: JSONObject = Field(default_factory=dict)
    config_schema: JSONObject = Field(default_factory=dict)
    credential_requirements: List[JSONObject] = Field(default_factory=list)
    credential_type: Optional[str] = None
    credential_key_id: str = "default"
    credential_configured: bool = False
    updated_at: datetime


class ServicePluginConfigurationUpdate(BaseModel):
    config: JSONObject = Field(default_factory=dict)


class ServicePluginCredentialUpdate(BaseModel):
    credential_type: str = Field(default="stackstorm_api_key", min_length=1, max_length=64)
    credential_key_id: str = Field(default="default", min_length=1, max_length=255)
    credential_payload: JSONObject
    rotate_credential: bool = False


class ServicePluginConnectionTestRequest(BaseModel):
    config: Optional[JSONObject] = None
    credential_key_id: str = Field(default="default", min_length=1, max_length=255)


class ServicePluginActionResponse(BaseModel):
    service_type: str
    status: str
    message: str
    details: JSONObject = Field(default_factory=dict)
    checked_at: datetime


class OperatorActionAcceptedResponse(BaseModel):
    status: str = "accepted"
    message: str
    order_id: int
    order_req_id: str
    service_type: str
    service_exec: str
    submitted_at: datetime


class PrometheusRuleGroupSummary(BaseModel):
    name: str
    rule_count: int = Field(default=0, ge=0)
    alert_count: int = Field(default=0, ge=0)
    recording_count: int = Field(default=0, ge=0)
    alert_names: List[str] = Field(default_factory=list)
    recording_names: List[str] = Field(default_factory=list)


class PrometheusRuleResourceResponse(BaseModel):
    name: str
    namespace: str
    labels: JSONObject = Field(default_factory=dict)
    annotations: JSONObject = Field(default_factory=dict)
    groups: List[PrometheusRuleGroupSummary] = Field(default_factory=list)
    group_count: int = Field(default=0, ge=0)
    rule_count: int = Field(default=0, ge=0)
    alert_count: int = Field(default=0, ge=0)
    recording_count: int = Field(default=0, ge=0)
    raw: JSONObject = Field(default_factory=dict)


class PrometheusRuleDetailResponse(PrometheusRuleResourceResponse):
    service_type: str = "k8s"
    checked_at: datetime


class PrometheusRuleRuleResponse(BaseModel):
    service_type: str = "k8s"
    namespace: str
    crd_name: str
    group_name: str
    rule_name: str
    rule_kind: str
    source: Optional[JSONObject] = None
    rule_data: JSONObject = Field(default_factory=dict)
    checked_at: datetime


class PrometheusRuleRuleUpdateRequest(BaseModel):
    group_name: str = Field(..., min_length=1, max_length=255)
    rule_data: JSONObject


class PrometheusRuleRuleCreateRequest(BaseModel):
    group_name: str = Field(..., min_length=1, max_length=255)
    rule_name: str = Field(..., min_length=1, max_length=255)
    rule_data: JSONObject


class PrometheusRuleListResponse(BaseModel):
    service_type: str = "k8s"
    namespace: str
    items: List[PrometheusRuleResourceResponse] = Field(default_factory=list)
    resource_count: int = Field(default=0, ge=0)
    group_count: int = Field(default=0, ge=0)
    rule_count: int = Field(default=0, ge=0)
    alert_count: int = Field(default=0, ge=0)
    recording_count: int = Field(default=0, ge=0)
    checked_at: datetime


class RepoSyncPullRequestResponse(BaseModel):
    number: Optional[int | str] = None
    url: Optional[str] = None


class RepoSyncResponse(BaseModel):
    status: str
    message: str
    branch: Optional[str] = None
    pull_request: Optional[RepoSyncPullRequestResponse] = None
    exported: Optional[Dict[str, str | int | None]] = None
    imported: Optional[Dict[str, int]] = None
    skipped: Optional[Dict[str, int]] = None
    warnings: Optional[List[str]] = None
    cleared: Optional[Dict[str, int]] = None


class GenestackAlertExportRequest(BaseModel):
    namespace: Optional[str] = Field(default=None, min_length=1, max_length=255)
    crd_name: str = Field(..., min_length=1, max_length=255)
    group_name: str = Field(..., min_length=1, max_length=255)
    rule_name: str = Field(..., min_length=1, max_length=255)


class ScheduledTaskBase(BaseModel):
    task_key: str = Field(..., min_length=1, max_length=255)
    task_type: ScheduledTaskType
    service_type: Optional[str] = Field(default=None, max_length=50)
    service_exec: Optional[str] = Field(default=None, max_length=255)
    source: ScheduledTaskSource = "registered"
    is_enabled: bool = True
    run_interval_seconds: int = Field(default=300, ge=1)
    next_run_at: Optional[datetime] = None
    priority: int = Field(default=100, ge=0)
    timeout_seconds: int = Field(default=300, ge=1)
    task_payload: Optional[JSONObject] = None
    task_parameters: Optional[JSONObject] = None
    expected_outcome: Optional[JSONValue] = None

    @model_validator(mode="after")
    def _validate_service_execution(self) -> "ScheduledTaskBase":
        if self.task_type == "service_execution":
            if not self.service_type or not self.service_exec:
                raise ValueError("service_execution tasks require service_type and service_exec")
        return self


class ScheduledTaskCreate(ScheduledTaskBase):
    pass


class ScheduledTaskUpdate(BaseModel):
    is_enabled: Optional[bool] = None
    run_interval_seconds: Optional[int] = Field(default=None, ge=1)
    next_run_at: Optional[datetime] = None
    priority: Optional[int] = Field(default=None, ge=0)
    timeout_seconds: Optional[int] = Field(default=None, ge=1)
    task_payload: Optional[JSONObject] = None
    task_parameters: Optional[JSONObject] = None
    expected_outcome: Optional[JSONValue] = None


class ScheduledTaskResponse(ScheduledTaskBase):
    id: int
    status: ScheduledTaskStatus
    last_status: Optional[CanonicalExecutionStatus] = None
    last_message: Optional[str] = None
    last_order_id: Optional[int] = None
    last_order_req_id: Optional[str] = None
    last_started_at: Optional[datetime] = None
    last_completed_at: Optional[datetime] = None
    consecutive_failures: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class ScheduledTaskStatusResponse(BaseModel):
    """Redacted scheduled task status without task payloads or expected outcomes."""

    id: int
    task_key: str
    task_type: ScheduledTaskType
    service_type: Optional[str] = None
    service_exec: Optional[str] = None
    source: ScheduledTaskSource
    is_enabled: bool
    run_interval_seconds: int
    next_run_at: Optional[datetime] = None
    priority: int
    timeout_seconds: int
    status: ScheduledTaskStatus
    last_status: Optional[CanonicalExecutionStatus] = None
    last_message: Optional[str] = None
    last_order_id: Optional[int] = None
    last_order_req_id: Optional[str] = None
    last_started_at: Optional[datetime] = None
    last_completed_at: Optional[datetime] = None
    consecutive_failures: int
    run_now_label: str
    run_now_description: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class CommunicationRouteBase(BaseModel):
    id: Optional[str] = None
    label: str = Field(..., min_length=1, max_length=255)
    service_type: str = Field(..., min_length=1, max_length=100)
    destination_target: Optional[str] = Field(default="", max_length=255)
    provider_config: JSONObject = Field(default_factory=dict)
    enabled: bool = True
    position: int = Field(default=1, ge=1)

    @field_validator("service_type")
    @classmethod
    def _validate_service_type(cls, value: str) -> str:
        normalized = normalize_destination_type(value)
        if not normalized:
            raise ValueError("service_type is required")
        return normalized


class CommunicationRouteCreate(CommunicationRouteBase):
    @model_validator(mode="after")
    def _normalize_provider_config(self) -> "CommunicationRouteCreate":
        self.provider_config = normalize_route_provider_config(
            self.service_type,
            self.provider_config,
        )
        return self


class CommunicationRouteResponse(CommunicationRouteBase):
    id: str

    @model_validator(mode="after")
    def _normalize_provider_config(self) -> "CommunicationRouteResponse":
        self.provider_config = normalize_route_provider_config(
            self.service_type,
            self.provider_config,
            require_required=False,
        )
        return self


class CommunicationPolicyUpdate(BaseModel):
    routes: List[CommunicationRouteCreate] = Field(default_factory=list)


class CommunicationPolicyResponse(BaseModel):
    configured: bool
    routes: List[CommunicationRouteResponse] = Field(default_factory=list)
    available_routes: List[CommunicationRouteResponse] = Field(default_factory=list)
    lifecycle_summary: Dict[str, str] = Field(default_factory=dict)


class RecipeCommunicationsConfig(BaseModel):
    mode: str = Field(default="inherit")
    routes: List[CommunicationRouteCreate] = Field(default_factory=list)

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in {"inherit", "local"}:
            raise ValueError("communications.mode must be either 'inherit' or 'local'")
        return normalized


class RecipeCommunicationsResponse(BaseModel):
    mode: str
    effective_source: Optional[str] = None
    routes: List[CommunicationRouteResponse] = Field(default_factory=list)


class AlertmanagerAlertRequest(BaseModel):
    status: str = Field(..., min_length=1)
    labels: JSONObject = Field(default_factory=dict)
    annotations: JSONObject = Field(default_factory=dict)
    startsAt: str = Field(..., min_length=1, max_length=64)
    fingerprint: str = Field(..., min_length=1, max_length=64)
    endsAt: Optional[Any] = None
    generatorURL: Optional[str] = None

    model_config = ConfigDict(extra="allow")

    @field_validator("labels")
    @classmethod
    def _validate_labels(cls, value: JSONObject) -> JSONObject:
        missing = sorted(
            field
            for field in ALERTMANAGER_REQUIRED_LABEL_FIELDS
            if not str(value.get(field) or "").strip()
        )
        if missing:
            raise ValueError(f"labels missing required fields: {', '.join(missing)}")
        return value

    @field_validator("annotations")
    @classmethod
    def _validate_annotations(cls, value: JSONObject) -> JSONObject:
        missing = sorted(
            field
            for field in ALERTMANAGER_REQUIRED_ANNOTATION_FIELDS
            if not str(value.get(field) or "").strip()
        )
        if missing:
            raise ValueError(f"annotations missing required fields: {', '.join(missing)}")
        return value


class AlertmanagerWebhookRequest(BaseModel):
    status: str = Field(..., min_length=1)
    alerts: List[AlertmanagerAlertRequest] = Field(..., min_length=1)
    receiver: Optional[str] = None
    groupKey: Optional[str] = None
    groupLabels: JSONObject = Field(default_factory=dict)
    commonLabels: JSONObject = Field(default_factory=dict)
    commonAnnotations: JSONObject = Field(default_factory=dict)
    externalURL: Optional[str] = None
    version: Optional[str] = None
    truncatedAlerts: Optional[int] = None

    model_config = ConfigDict(extra="allow")


# =============================================================================
# Ingredient Schemas (Global)
# =============================================================================


class IngredientBase(BaseModel):
    """Base schema for Ingredient creation/updates."""

    service_exec: str = Field(..., min_length=1, max_length=100)
    destination_target: Optional[str] = Field(default="", max_length=255)
    task_key_template: str = Field(..., max_length=255)

    service_type: str = Field(..., min_length=1, max_length=50)
    service_payload_template: Optional[JSONObject] = None
    payload_schema: JSONObject = Field(...)
    service_exec_parameters: Optional[JSONObject] = None
    default_expected_secs: int = Field(..., gt=0)
    default_timeout: int = Field(default=300, gt=0)
    service_exec_expected_outcome_default: Optional[Any] = None

    ingredient_purpose: ExecutionPurpose = Field(default="utility")
    is_active: bool = True
    is_blocking: bool = True
    retry_count: int = Field(default=0, ge=0)
    retry_delay: int = Field(default=5, ge=0)
    on_failure: OnFailureAction = Field(default="stop")

    @field_validator("service_payload_template", "payload_schema")
    @classmethod
    def _validate_service_payload_objects(cls, value: Optional[JSONObject]) -> Optional[JSONObject]:
        if value is None:
            return value
        if not isinstance(value, dict):
            raise ValueError("service payload fields must be objects when provided")
        return value

    @field_validator("service_type")
    @classmethod
    def _validate_service_type(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if not normalized:
            raise ValueError("service_type must not be empty")
        return normalized


class IngredientCreate(IngredientBase):
    """Schema for creating a new global ingredient."""

    pass


class IngredientTemplateRegistration(BaseModel):
    """Internal manifest-shaped schema for plugin ingredient registration."""

    service_exec: str = Field(..., min_length=1, max_length=100)
    destination_target: Optional[str] = Field(default="", max_length=255)
    task_key_template: str = Field(..., max_length=255)

    service_type: str = Field(..., min_length=1, max_length=50)
    service_payload_template: Optional[JSONObject] = None
    payload_schema: JSONObject = Field(...)
    service_exec_parameters: Optional[JSONObject] = None
    default_expected_secs: int = Field(..., gt=0)
    default_timeout: int = Field(default=300, gt=0)
    service_exec_expected_outcome_default: Optional[Any] = None

    ingredient_purpose: ExecutionPurpose = Field(default="utility")
    is_blocking: bool = True
    retry_count: int = Field(default=0, ge=0)
    retry_delay: int = Field(default=5, ge=0)
    on_failure: OnFailureAction = Field(default="stop")

    @field_validator("service_payload_template", "payload_schema")
    @classmethod
    def _validate_service_payload_objects(cls, value: Optional[JSONObject]) -> Optional[JSONObject]:
        if value is None:
            return value
        if not isinstance(value, dict):
            raise ValueError("service payload fields must be objects when provided")
        return value

    @field_validator("service_type")
    @classmethod
    def _validate_service_type(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if not normalized:
            raise ValueError("service_type must not be empty")
        return normalized


class IngredientResponse(IngredientBase):
    """Schema for ingredient responses (includes DB fields)."""

    id: int
    created_at: datetime
    updated_at: datetime
    deleted: bool
    deleted_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class IngredientStatusResponse(BaseModel):
    """Redacted service ingredient status without payload templates or expected outcomes."""

    id: int
    service_type: str
    service_exec: str
    destination_target: Optional[str] = ""
    task_key_template: str
    ingredient_purpose: ExecutionPurpose = Field(default="utility")
    is_active: bool = True
    is_blocking: bool = True
    default_expected_secs: int
    default_timeout: int
    retry_count: int = 0
    retry_delay: int = 5
    on_failure: OnFailureAction = Field(default="stop")
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


# =============================================================================
# RecipeIngredient Schemas (Junction)
# =============================================================================


class RecipeIngredientBase(BaseModel):
    ingredient_id: int = Field(..., ge=1)
    step_order: int = Field(..., ge=1)
    on_success: OnSuccessAction = Field(default="continue")
    parallel_group: int = Field(default=0, ge=0)
    depth: int = Field(default=0, ge=0)
    service_payload: Optional[JSONObject] = None
    service_exec_parameters_override: Optional[JSONObject] = None
    service_exec_expected_secs: Optional[int] = Field(default=None, gt=0)
    service_exec_timeout: Optional[int] = Field(default=None, gt=0)
    service_exec_expected_outcome: Optional[Any] = None
    run_phase: RunPhase = Field(default="both")
    run_condition: RunCondition = Field(default="always")


class RecipeIngredientCreate(RecipeIngredientBase):
    service_payload_from_order: bool = False


class RecipeIngredientResponse(RecipeIngredientBase):
    id: int
    recipe_id: int
    ingredient: Optional[IngredientResponse] = None

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class RecipeIngredientStatusResponse(BaseModel):
    """Redacted recipe step status without payload overrides or ingredient templates."""

    id: int
    recipe_id: int
    ingredient_id: int
    step_order: int
    on_success: OnSuccessAction = Field(default="continue")
    parallel_group: int = 0
    depth: int = 0
    run_phase: RunPhase = Field(default="both")
    run_condition: RunCondition = Field(default="always")
    service_type: Optional[str] = None
    service_exec: Optional[str] = None
    task_key_template: Optional[str] = None
    ingredient_purpose: Optional[str] = None
    ingredient_is_active: bool = True
    ingredient_is_blocking: bool = True
    expected_secs: Optional[int] = None
    timeout_secs: Optional[int] = None

    model_config = ConfigDict(from_attributes=True, extra="forbid")


# =============================================================================
# Recipe Schemas
# =============================================================================


class RecipeBase(BaseModel):
    """Base schema for Recipe creation/updates."""

    name: str = Field(..., max_length=255)
    description: Optional[str] = None
    enabled: bool = True
    clear_timeout_sec: Optional[int] = Field(default=None, gt=0)


class RecipeCreate(RecipeBase):
    """Schema for creating a recipe with recipe_ingredients."""

    recipe_ingredients: List[RecipeIngredientCreate] = Field(...)
    communications: RecipeCommunicationsConfig = Field(default_factory=RecipeCommunicationsConfig)


class RecipeUpdate(BaseModel):
    """Schema for updating a recipe (all fields optional)."""

    name: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    enabled: Optional[bool] = None
    clear_timeout_sec: Optional[int] = Field(default=None, gt=0)
    recipe_ingredients: Optional[List[RecipeIngredientCreate]] = None
    communications: Optional[RecipeCommunicationsConfig] = None


class RecipeResponse(RecipeBase):
    """Schema for recipe responses (includes DB fields)."""

    id: int
    created_at: datetime
    updated_at: datetime
    deleted: bool
    deleted_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class RecipeDetailResponse(RecipeResponse):
    """Schema for detailed recipe responses (includes recipe_ingredients)."""

    recipe_ingredients: List[RecipeIngredientResponse] = []
    communications: RecipeCommunicationsResponse = Field(
        default_factory=lambda: RecipeCommunicationsResponse(mode="inherit")
    )
    can_execute: bool = True
    inactive_ingredient_ids: List[int] = Field(default_factory=list)


class RecipeStatusResponse(BaseModel):
    """Redacted recipe status for reporting and selection views."""

    id: int
    name: str
    description: Optional[str] = None
    enabled: bool
    clear_timeout_sec: Optional[int] = None
    can_execute: bool = True
    inactive_ingredient_count: int = 0
    step_count: int = 0
    communication_route_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


# =============================================================================
# Dish Schemas
# =============================================================================


class DishBase(BaseModel):
    """Base schema for Dish."""

    req_id: str = Field(..., max_length=100)
    processing_status: DishProcessingStatus = Field(default="new")
    run_phase: DishRunPhase = Field(default="firing")


class DishUpdate(BaseModel):
    """Schema for updating a dish."""

    processing_status: Optional[DishProcessingStatus] = None
    dish_exec_status: Optional[str] = Field(None, max_length=50)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    expected_run_secs: Optional[int] = None
    run_time_secs: Optional[int] = None
    dish_actual_outcome: Optional[Any] = None
    error_message: Optional[str] = None
    run_phase: Optional[DishRunPhase] = None


class DishResponse(DishBase):
    """Schema for dish responses."""

    id: int
    order_id: Optional[int] = None
    recipe_id: int
    dish_exec_status: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    expected_run_secs: Optional[int] = None
    run_time_secs: Optional[int] = None
    work_execution_time_secs: Optional[int] = None
    work_execution_groups: List[Dict[str, int]] = Field(default_factory=list)
    dish_actual_outcome: Optional[Any] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class DishStatusResponse(BaseModel):
    """Redacted dish status for reporting and activity views."""

    id: int
    order_id: Optional[int] = None
    order_type: OrderType
    recipe_id: int
    recipe_name: Optional[str] = None
    processing_status: DishProcessingStatus
    run_phase: DishRunPhase
    dish_exec_status: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    expected_run_secs: Optional[int] = None
    run_time_secs: Optional[int] = None
    work_execution_time_secs: Optional[int] = None
    work_execution_groups: List[Dict[str, int]] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class DishDetailResponse(DishResponse):
    """Schema for detailed dish responses (includes recipe)."""

    recipe: Optional[RecipeDetailResponse] = None


# =============================================================================
# Order Schemas
# =============================================================================


class OrderBase(BaseModel):
    """Base schema for Order."""

    req_id: str = Field(..., max_length=100)
    fingerprint: str = Field(..., max_length=255)
    alert_status: str = Field(..., max_length=50)
    alert_group_name: str = Field(..., max_length=255)
    labels: JSONObject
    starts_at: datetime
    fingerprint_when_active: Optional[str] = Field(None, max_length=255)
    remediation_outcome: RemediationOutcome = "pending"
    clear_timeout_sec: Optional[int] = Field(default=None, ge=1)
    clear_deadline_at: Optional[datetime] = None
    clear_timed_out_at: Optional[datetime] = None
    auto_close_eligible: bool = False


class OrderCreate(OrderBase):
    """Schema for creating an order."""

    processing_status: OrderProcessingStatus = Field(default="new")
    is_active: bool = True
    severity: Optional[str] = Field(None, max_length=50)
    instance: Optional[str] = Field(None, max_length=255)
    correlation_key: Optional[str] = Field(None, max_length=64)
    counter: int = 1
    annotations: Optional[JSONObject] = None
    raw_data: Optional[JSONObject] = None
    ends_at: Optional[datetime] = None


class OrderUpdate(BaseModel):
    """Schema for updating an order (all fields optional)."""

    alert_status: Optional[AlertStatus] = None
    processing_status: Optional[OrderProcessingStatus] = None
    is_active: Optional[bool] = None
    ends_at: Optional[datetime] = None
    fingerprint_when_active: Optional[str] = Field(None, max_length=255)
    remediation_outcome: Optional[RemediationOutcome] = None
    clear_timeout_sec: Optional[int] = Field(default=None, ge=1)
    clear_deadline_at: Optional[datetime] = None
    clear_timed_out_at: Optional[datetime] = None
    auto_close_eligible: Optional[bool] = None


class OrderResponse(OrderBase):
    """Schema for order responses."""

    id: int
    processing_status: OrderProcessingStatus
    is_active: bool
    severity: Optional[str] = None
    instance: Optional[str] = None
    correlation_key: Optional[str] = None
    counter: int
    annotations: Optional[JSONObject] = None
    raw_data: Optional[JSONObject] = None
    ends_at: Optional[datetime] = None
    order_lifetime_secs: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class OrderStatusResponse(BaseModel):
    """Redacted order status for reporting and operator-facing views."""

    id: int
    req_id: str
    order_type: OrderType
    alert_status: str
    alert_group_name: str
    processing_status: OrderProcessingStatus
    is_active: bool
    remediation_outcome: RemediationOutcome
    clear_timeout_sec: Optional[int] = None
    clear_deadline_at: Optional[datetime] = None
    clear_timed_out_at: Optional[datetime] = None
    auto_close_eligible: bool = False
    severity: Optional[str] = None
    instance: Optional[str] = None
    correlation_key: Optional[str] = None
    counter: int
    starts_at: datetime
    ends_at: Optional[datetime] = None
    order_lifetime_secs: Optional[int] = None
    communication_route_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class IncidentTimelineOrderResponse(OrderStatusResponse):
    """Reader-safe order timeline summary with alert labels for suppression decisions."""

    labels: JSONObject = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class DishIngredientUpsert(BaseModel):
    """Upsert payload for dish ingredient execution results."""

    dish_id: Optional[int] = None
    req_id: Optional[str] = Field(default=None, max_length=100)
    recipe_ingredient_id: Optional[int] = None
    service_exec_id: Optional[str] = Field(default=None, max_length=100)
    service_type: Optional[str] = Field(default=None, max_length=50)
    task_key: Optional[str] = Field(default=None, max_length=255)
    service_exec: Optional[str] = Field(default=None, max_length=255)
    destination_target: Optional[str] = None
    service_payload: Optional[JSONObject] = None
    service_exec_parameters: Optional[JSONObject] = None
    service_exec_expected_secs: Optional[int] = None
    service_exec_timeout: Optional[int] = None
    service_exec_expected_outcome: Optional[Any] = None
    retry_count: Optional[int] = None
    retry_delay: Optional[int] = None
    on_failure: Optional[str] = None
    service_exec_status: Optional[CanonicalExecutionStatus] = None
    attempt: Optional[int] = None
    service_exec_start_time: Optional[datetime] = None
    service_exec_completed_time: Optional[datetime] = None
    service_exec_canceled_time: Optional[datetime] = None
    service_exec_run_time: Optional[int] = None
    service_exec_sla_exceeded: Optional[bool] = None
    service_exec_claimed_at: Optional[datetime] = None
    service_exec_claimed_by: Optional[str] = None
    service_exec_actual_outcome: Optional[JSONObject] = None
    service_exec_error: Optional[str] = None

    model_config = ConfigDict(from_attributes=True, extra="forbid")

class DishIngredientResponse(BaseModel):
    """Dish ingredient execution record."""

    id: int
    req_id: str
    dish_id: int
    recipe_ingredient_id: Optional[int] = None
    service_exec_id: Optional[str] = None
    task_key: Optional[str] = None
    step_order: int = 1
    parallel_group: int = 0
    depth: int = 0
    service_type: Optional[str] = None
    service_exec: Optional[str] = None
    destination_target: Optional[str] = None
    service_payload: Optional[JSONObject] = None
    service_exec_parameters: Optional[JSONObject] = None
    service_exec_expected_secs: Optional[int] = None
    service_exec_timeout: Optional[int] = None
    service_exec_expected_outcome: Optional[Any] = None
    retry_count: Optional[int] = None
    retry_delay: Optional[int] = None
    on_failure: Optional[str] = None
    service_exec_status: CanonicalExecutionStatus
    attempt: int
    service_exec_start_time: Optional[datetime] = None
    service_exec_completed_time: Optional[datetime] = None
    service_exec_canceled_time: Optional[datetime] = None
    service_exec_run_time: Optional[int] = None
    service_exec_sla_exceeded: bool = False
    service_exec_claimed_at: Optional[datetime] = None
    service_exec_claimed_by: Optional[str] = None
    service_exec_actual_outcome: Optional[JSONObject] = None
    service_exec_error: Optional[str] = None
    deleted: bool
    deleted_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class DishIngredientStatusResponse(BaseModel):
    """Redacted dish ingredient execution status for operator-facing views."""

    id: int
    dish_id: int
    recipe_ingredient_id: Optional[int] = None
    task_key: Optional[str] = None
    step_order: int = 1
    parallel_group: int = 0
    depth: int = 0
    service_type: Optional[str] = None
    service_exec: Optional[str] = None
    retry_count: Optional[int] = None
    retry_delay: Optional[int] = None
    on_failure: Optional[str] = None
    service_exec_status: CanonicalExecutionStatus
    attempt: int
    execution_role: Optional[str] = None
    operation: Optional[str] = None
    result_status: Optional[str] = None
    result_message: Optional[str] = None
    result_summary: Optional[JSONObject] = None
    service_exec_start_time: Optional[datetime] = None
    service_exec_completed_time: Optional[datetime] = None
    service_exec_canceled_time: Optional[datetime] = None
    service_exec_run_time: Optional[int] = None
    service_exec_sla_exceeded: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class OrderDetailResponse(OrderResponse):
    """Schema for detailed order responses (includes dishes)."""

    dishes: List[DishResponse] = []


class IncidentTimelineEvent(BaseModel):
    timestamp: Optional[datetime] = None
    event_type: str
    status: str
    title: str
    details: JSONObject = Field(default_factory=dict)
    correlation_ids: Dict[str, str] = Field(default_factory=dict)


class IncidentTimelineResponse(BaseModel):
    order: IncidentTimelineOrderResponse
    events: List[IncidentTimelineEvent]


# ============================================================================
# Suppression Models
# ============================================================================


class SuppressionMatcher(BaseModel):
    label_key: str = Field(..., min_length=1, max_length=255)
    operator: SuppressionMatcherOperator
    value: Optional[str] = Field(default=None, max_length=512)


class SuppressionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    starts_at: datetime
    ends_at: datetime
    matchers: List[SuppressionMatcher] = Field(default_factory=list)
    reason: Optional[str] = Field(default=None, max_length=1000)
    created_by: Optional[str] = Field(default=None, max_length=255)
    summary_ticket_enabled: bool = True

    model_config = ConfigDict(extra="forbid")


class SuppressionUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    reason: Optional[str] = Field(default=None, max_length=1000)
    summary_ticket_enabled: Optional[bool] = None
    matchers: Optional[List[SuppressionMatcher]] = None

    model_config = ConfigDict(extra="forbid")


class SuppressionResponse(BaseModel):
    id: int
    name: str
    reason: Optional[str] = None
    scope: SuppressionScope
    status: SuppressionStatus
    enabled: bool
    starts_at: datetime
    ends_at: datetime
    canceled_at: Optional[datetime] = None
    created_by: Optional[str] = None
    summary_ticket_enabled: bool
    source: str = "local"
    source_service_type: Optional[str] = None
    source_ref: Optional[str] = None
    source_payload: Optional[JSONObject] = None
    last_synced_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    matchers: List[SuppressionMatcher] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class SuppressionStatusResponse(BaseModel):
    id: int
    name: str
    reason: Optional[str] = None
    scope: SuppressionScope
    status: SuppressionStatus
    enabled: bool
    starts_at: datetime
    ends_at: datetime
    canceled_at: Optional[datetime] = None
    source: str = "plugin"
    source_service_type: Optional[str] = None
    source_ref: Optional[str] = None
    last_synced_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class SuppressionStatsResponse(BaseModel):
    suppression_id: int
    total_suppressed: int
    by_alertname: Dict[str, int]
    by_severity: Dict[str, int]
    first_seen_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None


class SuppressedActivityResponse(BaseModel):
    id: int
    suppression_id: int
    received_at: datetime
    fingerprint: Optional[str] = None
    alertname: Optional[str] = None
    severity: Optional[str] = None
    status: str
    req_id: Optional[str] = None
    labels_json: JSONObject
    annotations_json: Optional[JSONObject] = None

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class SuppressionSummaryResponse(BaseModel):
    state: str
    total_suppressed: int
    total_cleared: int = 0
    total_still_firing: int = 0
    by_alertname_json: Optional[JSONObject] = None
    by_severity_json: Optional[JSONObject] = None
    still_firing_alerts_json: Optional[JSONObject] = None
    first_seen_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    summary_created_at: Optional[datetime] = None
    summary_close_at: Optional[datetime] = None
    last_error: Optional[str] = None

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class SuppressionDetailResponse(SuppressionResponse):
    summary: Optional[SuppressionSummaryResponse] = None
    counters: SuppressionStatsResponse


class ObservabilityHealthSummary(BaseModel):
    status: str


class ObservabilityQueueSummary(BaseModel):
    orders_new: int
    orders_processing: int


class ObservabilityTopError(BaseModel):
    error: str
    count: int


class ObservabilityFailuresSummary(BaseModel):
    orders_failed: int
    dishes_failed: int
    top_errors: List[ObservabilityTopError] = Field(default_factory=list)
    runbook_hints: List[str] = Field(default_factory=list)


class ObservabilitySuppressionsSummary(BaseModel):
    active: int
    retrying_operations: int
    dead_letter: int


class ObservabilityOverviewResponse(BaseModel):
    health: ObservabilityHealthSummary
    queue: ObservabilityQueueSummary
    failures: ObservabilityFailuresSummary
    suppressions: ObservabilitySuppressionsSummary


class ObservabilityActivityRecord(BaseModel):
    type: str
    status: str
    title: str
    summary: Optional[str] = None
    timestamp: Optional[datetime] = None
    target_kind: str
    target_id: str
    link_hint: Optional[str] = None
    metadata: JSONObject = Field(default_factory=dict)


class ObservabilityActivityStatusRecord(BaseModel):
    """Redacted observability activity for reporting feeds."""

    type: str
    status: str
    title: str
    summary: Optional[str] = None
    timestamp: Optional[datetime] = None
    target_kind: str
    target_id: str
    link_hint: Optional[str] = None


class CommunicationActivityRecord(BaseModel):
    communication_id: str
    reference_type: str
    reference_id: str
    reference_name: Optional[str] = None
    channel: str
    destination: Optional[str] = None
    ticket_id: Optional[str] = None
    provider_reference_id: Optional[str] = None
    operation_id: Optional[str] = None
    lifecycle_state: Optional[str] = None
    remote_state: Optional[str] = None
    last_error: Optional[str] = None
    writable: Optional[bool] = None
    reopenable: Optional[bool] = None
    updated_at: Optional[datetime] = None


class CommunicationActivityStatusRecord(BaseModel):
    """Redacted communication activity without provider IDs or raw errors."""

    communication_id: str
    reference_type: str
    reference_id: str
    reference_name: Optional[str] = None
    channel: str
    destination: Optional[str] = None
    lifecycle_state: Optional[str] = None
    remote_state: Optional[str] = None
    updated_at: Optional[datetime] = None


# ============================================================================
# Operation Response Models
# ============================================================================


class WebhookResponse(BaseModel):
    """Response from webhook endpoint."""

    status: str  # created, counter_incremented, resolved, ignored, no_alerts
    order_id: Optional[int] = None
    message: Optional[str] = None
    results: Optional[List[JSONObject]] = None

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class OrderDispatchResponse(BaseModel):
    """Response from order dispatch endpoint."""

    status: str  # dispatched, skipped
    order_id: int
    dish_id: Optional[int] = None
    run_phase: Optional[DishRunPhase] = None
    recipe_id: Optional[int] = None
    recipe_name: Optional[str] = None
    reason: Optional[str] = None

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class CookAdvanceReadyItem(BaseModel):
    id: int
    req_id: str
    dish_id: int
    recipe_ingredient_id: Optional[int] = None
    task_key: Optional[str] = None
    step_order: int = 1
    parallel_group: int = 0
    depth: int = 0
    service_type: str
    service_exec: str
    service_exec_id: Optional[str] = None
    service_exec_status: CanonicalExecutionStatus
    on_failure: Optional[str] = None
    created_at: Optional[datetime] = None


class CookDispatchedItem(BaseModel):
    dish_ingredient_id: int
    req_id: str
    service_type: str
    service_exec: str
    service_exec_id: Optional[str] = None
    service_exec_status: CanonicalExecutionStatus
    service_exec_error: Optional[str] = None


class CookSegmentMetadata(BaseModel):
    depth: int
    parallel_group: int
    service_types: List[str] = Field(default_factory=list)


class CookAdvanceResponse(BaseModel):
    status: Literal[
        "complete", "ready", "dispatched", "failed", "errored", "timeout", "canceled", "blocked"
    ]
    dish_id: int
    order_id: Optional[int] = None
    segment: Optional[CookSegmentMetadata] = None
    ready: List[CookAdvanceReadyItem] = Field(default_factory=list)
    dispatched: List[CookDispatchedItem] = Field(default_factory=list)
    blocked: Optional[str] = None
    terminal: bool = False


class ExecuteRequest(BaseModel):
    dish_ingredient_id: Optional[int] = None
    service_type: str = Field(..., min_length=1, max_length=50)
    service_exec: str = Field(..., min_length=1, max_length=255)
    service_payload: Optional[JSONObject] = None
    service_exec_parameters: Optional[JSONObject] = None
    retry_count: int = Field(default=0, ge=0)
    retry_delay: int = Field(default=0, ge=0)
    service_exec_timeout: int = Field(default=300, gt=0)
    context: JSONObject = Field(default_factory=dict)

    @field_validator("service_type")
    @classmethod
    def _validate_service_type(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if not normalized:
            raise ValueError("service_type must not be empty")
        return normalized

    @field_validator("service_payload", "service_exec_parameters")
    @classmethod
    def _validate_object_fields(cls, value: Optional[JSONObject]) -> Optional[JSONObject]:
        if value is None:
            return value
        if not isinstance(value, dict):
            raise ValueError("value must be an object when provided")
        return value


class UIOperatorActionRequest(BaseModel):
    action: str = Field(..., min_length=1, max_length=120)
    surface: str = Field(..., min_length=1, max_length=120)
    status: str = Field(default="attempt", min_length=1, max_length=40)
    target: Optional[str] = Field(default=None, max_length=255)
    details: JSONObject = Field(default_factory=dict)


class UIOperatorActionResponse(BaseModel):
    status: Literal["logged"] = "logged"


class OperatorAuditEventResponse(BaseModel):
    id: int
    req_id: Optional[str] = None
    action: str
    surface: str
    status: str
    target: Optional[str] = None
    actor_username: Optional[str] = None
    actor_role: Optional[str] = None
    details: JSONObject = Field(default_factory=dict)
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class ExecutionEnvelopeResponse(BaseModel):
    service_exec_id: Optional[str] = None
    service_type: str
    status: CanonicalExecutionStatus
    service_exec_error: Optional[str] = None
    service_exec_actual_outcome: Optional[JSONObject] = None
    raw: Optional[JSONObject] = None
    context_updates: JSONObject = Field(default_factory=dict)
    attempts: int = 1

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class SessionResponse(BaseModel):
    """Response from login endpoint."""

    session_id: str
    username: str
    expires_at: str  # ISO format datetime
    provider: AuthProvider
    role: AuthRole
    display_name: Optional[str] = None
    is_superuser: bool = False
    permissions: List[str] = Field(default_factory=list)
    token_type: str = "Bearer"

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class AuthLoginRequest(BaseModel):
    """Password login request."""

    provider: Optional[AuthProvider] = None
    username: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1, max_length=255)


class AuthProviderResponse(BaseModel):
    """Enabled auth provider metadata for UI and CLI discovery."""

    name: AuthProvider
    label: str
    login_mode: str
    cli_login_mode: str
    browser_login: bool = False
    device_login: bool = False
    password_login: bool = False


class AuthMeResponse(BaseModel):
    """Current authenticated principal metadata."""

    username: str
    display_name: Optional[str] = None
    provider: AuthProvider
    role: AuthRole
    principal_type: AuthPrincipalType
    principal_id: Optional[int] = None
    is_superuser: bool = False
    permissions: List[str] = Field(default_factory=list)
    groups: List[str] = Field(default_factory=list)
    expires_at: Optional[str] = None


class AuthLogoutResponse(BaseModel):
    """Logout acknowledgement."""

    message: str


class DeviceAuthorizationStartResponse(BaseModel):
    """Device login start payload."""

    provider: AuthProvider
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: Optional[str] = None
    expires_in: int
    interval: int


class DeviceAuthorizationStartRequest(BaseModel):
    """Device login start request."""

    provider: Optional[AuthProvider] = None


class DeviceAuthorizationPollRequest(BaseModel):
    """Device authorization poll request."""

    provider: Optional[AuthProvider] = None
    device_code: str = Field(..., min_length=1)


class DeviceAuthorizationPollResponse(BaseModel):
    """Device authorization status response."""

    status: str
    interval: Optional[int] = None
    detail: Optional[str] = None
    session: Optional[SessionResponse] = None


class AuthPrincipalResponse(BaseModel):
    """Observed principal metadata for access management."""

    id: int
    provider: AuthProvider
    subject_id: str
    username: str
    display_name: Optional[str] = None
    principal_type: AuthPrincipalType
    groups: List[str] = Field(default_factory=list)
    last_seen_at: datetime
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class AuthRoleBindingCreate(BaseModel):
    """Create a new RBAC binding."""

    provider: AuthProvider
    binding_type: AuthBindingType
    role: AuthRole
    principal_id: Optional[int] = None
    external_group: Optional[str] = Field(default=None, max_length=255)
    created_by: Optional[str] = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def _validate_target(self) -> "AuthRoleBindingCreate":
        if self.binding_type == "user" and self.principal_id is None:
            raise ValueError("principal_id is required for user bindings")
        if self.binding_type == "group" and not str(self.external_group or "").strip():
            raise ValueError("external_group is required for group bindings")
        return self


class AuthRoleBindingUpdate(BaseModel):
    """Update an existing RBAC binding."""

    role: Optional[AuthRole] = None
    external_group: Optional[str] = Field(default=None, max_length=255)


class AuthRoleBindingResponse(BaseModel):
    """RBAC binding details."""

    id: int
    provider: AuthProvider
    binding_type: AuthBindingType
    role: AuthRole
    principal_id: Optional[int] = None
    external_group: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    principal: Optional[AuthPrincipalResponse] = None

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class DeleteResponse(BaseModel):
    """Generic delete response."""

    status: str = "deleted"
    id: int
    message: Optional[str] = None

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class SettingsResponse(BaseModel):
    """Application settings returned to UI and CLI clients."""

    auth_enabled: bool
    rbac_enabled: bool
    auth_providers: List[AuthProviderResponse] = Field(default_factory=list)
    prometheus_use_crds: bool
    prometheus_crd_namespace: str
    prometheus_url: str
    git_provider: Optional[str] = None
    git_repo_url: Optional[str] = None
    git_branch: Optional[str] = None
    git_rules_path: Optional[str] = None
    git_workflows_path: Optional[str] = None
    git_actions_path: Optional[str] = None
    version: str
    global_communications_configured: bool
