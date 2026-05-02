"""Dummy bootstrap hook for shared helper contract validation."""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from api.plugins.dummy.helper import DummyPluginHelper
from api.types import JSONObject


async def bootstrap_dummy_helper_validation(
    _db: AsyncSession,
    helpers: Mapping[str, object],
) -> JSONObject:
    """Exercise helper lookup during bootstrap without mutating application state."""
    helper = helpers.get("dummy")
    if not isinstance(helper, DummyPluginHelper):
        raise RuntimeError("dummy bootstrap requires enabled dummy helper")
    result = helper.echo({"phase": "bootstrap"})
    return {
        "processed": 1,
        "errors": 0,
        "helper": result,
    }
