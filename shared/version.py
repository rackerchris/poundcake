"""Shared version helpers for PoundCake services."""

from __future__ import annotations

import os

__version__ = "1.0.0"


def resolve_version(*env_vars: str) -> str:
    """Return the first configured runtime version, or the repo default."""
    for env_var in env_vars:
        value = os.getenv(env_var, "").strip()
        if value:
            return value
    return __version__
