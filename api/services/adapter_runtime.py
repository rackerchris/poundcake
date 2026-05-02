"""Service-layer helpers for short-lived adapter/bootstrap runtime cleanup."""

from __future__ import annotations

from api.core.database import dispose_async_engines


async def dispose_adapter_runtime_resources() -> None:
    """Dispose adapter/runtime resources without exposing database internals."""

    await dispose_async_engines()
