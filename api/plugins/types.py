"""Strict execution orchestration contracts."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from api.types import (
    CanonicalExecutionStatus,
    JSONObject,
    PluginBootstrapStatus,
    PluginHealthStatus,
)


class _StrictPluginModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ExecutionContext(_StrictPluginModel):
    """Validated request contract from Expediter into a service adapter."""

    service_type: str = Field(..., min_length=1, max_length=50)
    service_exec: str = Field(..., min_length=1, max_length=255)
    req_id: str = Field(..., min_length=1, max_length=100)
    service_payload: JSONObject | None = None
    service_exec_parameters: JSONObject | None = None
    retry_count: int = Field(default=0, ge=0)
    retry_delay: int = Field(default=0, ge=0)
    service_exec_timeout: int = Field(default=300, gt=0)
    context: JSONObject = Field(default_factory=dict)

    @field_validator("service_type")
    @classmethod
    def _normalize_service_type(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("service_exec", "req_id")
    @classmethod
    def _strip_required_strings(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be empty")
        return stripped


class ExecutionResult(_StrictPluginModel):
    """Validated response contract from a service adapter back to Expediter."""

    service_type: str = Field(..., min_length=1, max_length=50)
    status: CanonicalExecutionStatus
    service_exec_id: str | None = Field(default=None, max_length=255)
    service_exec_error: str | None = None
    result: JSONObject | None = None
    raw: JSONObject | None = None
    retryable: bool = False
    attempts: int = Field(default=1, ge=1)
    context_updates: JSONObject = Field(default_factory=dict)

    @field_validator("service_type")
    @classmethod
    def _normalize_service_type(cls, value: str) -> str:
        return value.strip().lower()


class PluginHealthResult(_StrictPluginModel):
    """Validated plugin health contract exposed by adapters."""

    service_type: str = Field(..., min_length=1, max_length=50)
    status: PluginHealthStatus
    message: str | None = None
    error_code: str | None = Field(default=None, max_length=100)
    latency_ms: int | None = Field(default=None, ge=0)
    details: JSONObject | None = None

    @field_validator("service_type")
    @classmethod
    def _normalize_service_type(cls, value: str) -> str:
        return value.strip().lower()


class PluginBootstrapResult(_StrictPluginModel):
    """Validated runtime plugin bootstrap contract owned by adapters."""

    service_type: str = Field(..., min_length=1, max_length=50)
    status: PluginBootstrapStatus
    message: str | None = None
    error_code: str | None = Field(default=None, max_length=100)
    details: JSONObject | None = None

    @field_validator("service_type")
    @classmethod
    def _normalize_service_type(cls, value: str) -> str:
        return value.strip().lower()
