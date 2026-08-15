"""Bakery plugin-local Pydantic contract models."""

from __future__ import annotations

from api.types import JSONObject

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _BakeryContractModel(BaseModel):
    """Base model for Bakery-owned communication contracts."""

    model_config = ConfigDict(extra="forbid")


class CommunicationOpenRequest(_BakeryContractModel):
    """Open a new logical communication."""

    title: str = Field(..., min_length=1, max_length=512)
    description: str = Field(..., min_length=1)
    message: str | None = Field(default=None, min_length=1)
    severity: str | None = Field(default=None, max_length=50)
    category: str | None = Field(default=None, max_length=100)
    source: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=50)
    context: JSONObject = Field(default_factory=dict)


class CommunicationUpdateRequest(_BakeryContractModel):
    """Update mutable communication fields."""

    title: str | None = Field(default=None, min_length=1, max_length=512)
    description: str | None = Field(default=None, min_length=1)
    severity: str | None = Field(default=None, max_length=50)
    category: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=50)
    context: JSONObject = Field(default_factory=dict)


class CommunicationNotifyRequest(_BakeryContractModel):
    """Send a message/notification to an existing communication."""

    message: str | None = Field(default=None, min_length=1)
    comment: str | None = Field(default=None, min_length=1)
    visibility: str | None = Field(default=None, max_length=50)
    context: JSONObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def _coalesce_message_and_comment(self) -> "CommunicationNotifyRequest":
        if self.message is None and self.comment is not None:
            self.message = self.comment
        if self.comment is None and self.message is not None:
            self.comment = self.message
        if self.message is None:
            raise ValueError("message is required")
        return self


class CommunicationCloseRequest(_BakeryContractModel):
    """Close a logical communication."""

    title: str | None = Field(default=None, min_length=1, max_length=512)
    description: str | None = Field(default=None, min_length=1)
    message: str | None = Field(default=None, min_length=1)
    source: str | None = Field(default=None, max_length=100)
    resolution_code: str | None = Field(default=None, max_length=100)
    resolution_notes: str | None = Field(default=None, max_length=4096)
    state: str | None = Field(default="closed", max_length=50)
    context: JSONObject = Field(default_factory=dict)


class CommunicationAcceptedResponse(_BakeryContractModel):
    """Accepted async communication operation response."""

    communication_id: str
    operation_id: str
    action: str
    status: str
    created_at: datetime


class CommunicationResponse(_BakeryContractModel):
    """Logical communication status."""

    communication_id: str
    provider_type: str
    provider_reference_id: str | None = None
    state: str
    latest_error: str | None = None
    created_at: datetime
    updated_at: datetime
    data_source: str = "local_cache"
    communication_data: JSONObject | None = None
    last_sync_operation_id: str | None = None
    last_sync_at: datetime | None = None


class CommunicationOperationResponse(_BakeryContractModel):
    """Detailed communication operation state."""

    operation_id: str
    communication_id: str
    action: str
    status: str
    attempt_count: int
    max_attempts: int
    next_attempt_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    last_error: str | None = None
    provider_response: JSONObject | None = None
    created_at: datetime
    updated_at: datetime


class MonitorHeartbeatRequest(_BakeryContractModel):
    """Liveness heartbeat sent by PoundCake to Bakery."""

    catalog_hash: str | None = Field(default=None, max_length=64)
    installation_id: str | None = Field(default=None, max_length=255)
    app_version: str | None = Field(default=None, max_length=100)
    environment_label: str | None = Field(default=None, max_length=255)
    region: str | None = Field(default=None, max_length=100)
    cluster_name: str | None = Field(default=None, max_length=255)
    namespace: str | None = Field(default=None, max_length=255)
    release_name: str | None = Field(default=None, max_length=255)
    tags: list[str] = Field(default_factory=list)
    details: JSONObject = Field(default_factory=dict)


class MonitorHeartbeatResponse(_BakeryContractModel):
    """Heartbeat acknowledgement and route sync hint."""

    monitor_uuid: str
    monitor_id: str
    status: str
    route_sync_required: bool
    heartbeat_interval_sec: int
    miss_threshold: int
    recorded_at: datetime
