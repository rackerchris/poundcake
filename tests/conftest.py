"""Pytest configuration and fixtures."""

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def setup_test_env() -> None:
    """Set up a dummy-only contract-test environment."""
    os.environ["TESTING"] = "true"
    os.environ["POUNDCAKE_ENABLED_PLUGINS"] = "dummy"
    os.environ["POUNDCAKE_AUTH_ENABLED"] = "false"
