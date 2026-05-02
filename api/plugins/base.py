"""Execution adapter interface for service plugin execution backends."""

from __future__ import annotations

from abc import ABC, abstractmethod

from api.plugins.types import (
    ExecutionContext,
    ExecutionResult,
    PluginBootstrapResult,
    PluginHealthResult,
)
from api.types import JSONObject


class ExecutionAdapter(ABC):
    """Adapter contract implemented by each service plugin."""

    service_type: str

    @abstractmethod
    def validate(self, ctx: ExecutionContext) -> str | None:
        """Validate service-specific context before execution."""

    @abstractmethod
    async def dispatch(self, ctx: ExecutionContext) -> ExecutionResult:
        """Start service work and return the accepted execution receipt or terminal result."""

    @abstractmethod
    async def poll(self, ctx: ExecutionContext, service_exec_id: str) -> ExecutionResult:
        """Read-only observation of service work by receipt id.

        Poll implementations must not start or retry the requested work, mutate
        PoundCake-owned runtime state, perform catalog/bootstrap work, or perform
        provider write operations. Work starts in dispatch; poll only reads
        execution state and returns canonical runtime state.
        """

    @abstractmethod
    def health_check(self) -> PluginHealthResult:
        """Return plugin control-plane health without exposing credentials or internals."""

    def credential_requirements(self) -> list[JSONObject]:
        """Return adapter-owned credential requirements, if any."""
        return []

    def operator_config_schema(self) -> JSONObject:
        """Return non-secret operator-editable configuration schema."""
        return {"type": "object", "properties": {}, "additionalProperties": False}

    def default_operator_config(self) -> JSONObject:
        """Return default non-secret operator configuration."""
        return {}

    def normalize_operator_config(self, config: JSONObject | None) -> JSONObject:
        """Validate and normalize non-secret operator configuration."""
        if not config:
            return self.default_operator_config()
        normalized = dict(self.default_operator_config())
        normalized.update(config)
        return normalized

    def with_operator_config(self, config: JSONObject | None) -> "ExecutionAdapter":
        """Return an adapter instance configured with non-secret operator settings."""
        return self

    def validate_credential_payload(self, credential_type: str, payload: JSONObject) -> str | None:
        """Validate secret-bearing adapter credential payloads without echoing secret values."""
        if not isinstance(payload, dict) or not payload:
            return f"{credential_type} credential payload is required"
        return None

    async def test_connection(self, *, credential_key_id: str = "default") -> PluginHealthResult:
        """Run an adapter-owned control-plane connection check."""
        return self.health_check()

    async def bootstrap_credentials(
        self,
        *,
        force: bool = False,
    ) -> None:
        """Bootstrap or refresh adapter-owned credentials when supported."""
        return None

    async def bootstrap_plugin(
        self,
        ctx: ExecutionContext,
        *,
        force: bool = False,
    ) -> PluginBootstrapResult:
        """Bootstrap runtime plugin state before adapter health or activation checks."""
        return PluginBootstrapResult(
            service_type=(ctx.service_type or self.service_type).strip().lower(),
            status="ready",
            message="Plugin bootstrap is not required",
            details={"bootstrap_status": "ready", "credential_status": "not_required"},
        )

    async def cancel(self, ctx: ExecutionContext, service_exec_id: str) -> ExecutionResult:
        """Cancel service work when supported by the plugin."""
        return ExecutionResult(
            service_type=(ctx.service_type or self.service_type).strip().lower(),
            status="errored",
            service_exec_id=service_exec_id,
            service_exec_error=f"Cancellation is not supported for service_type={self.service_type}",
            retryable=False,
        )
