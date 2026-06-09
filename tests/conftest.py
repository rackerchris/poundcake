"""Pytest configuration and fixtures."""

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def setup_test_env() -> None:
    """Set up shared defaults without implicitly changing auth behavior."""
    os.environ["POUNDCAKE_ENABLED_PLUGINS"] = "dummy"
