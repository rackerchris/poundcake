"""Dummy shared helper used to prove plugin helper registration."""

from __future__ import annotations

from api.types import JSONObject


class DummyPluginHelper:
    """Small deterministic helper for local bootstrap and registry tests."""

    service_type = "dummy"

    def echo(self, payload: JSONObject | None = None) -> JSONObject:
        return {"success": True, "service_type": self.service_type, "payload": payload or {}}


def get_dummy_helper() -> DummyPluginHelper:
    return DummyPluginHelper()
