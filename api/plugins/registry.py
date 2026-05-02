"""Registry for service plugin adapters."""

from __future__ import annotations

from api.plugins.base import ExecutionAdapter


class ExecutionAdapterRegistry:
    """Resolve adapters by normalized service type."""

    def __init__(self) -> None:
        self._adapters: dict[str, ExecutionAdapter] = {}

    def register(self, adapter: ExecutionAdapter) -> None:
        self._adapters[adapter.service_type.lower()] = adapter

    def get(self, service_type: str) -> ExecutionAdapter | None:
        return self._adapters.get((service_type or "").strip().lower())
