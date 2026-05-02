"""Tests for worker-scoped service identity credential reads."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from api.services.credentials import ServicePluginCredentialError
from api.services.service_identity import _read_internal_hmac_payload


class _Result:
    def __init__(self, value: object | None) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object | None:
        return self.value


class _Db:
    def __init__(self, value: object | None) -> None:
        self.value = value

    async def execute(self, _statement: object, _params: object | None = None) -> _Result:
        return _Result(self.value)


@pytest.mark.asyncio
async def test_read_internal_hmac_payload_uses_worker_reader_session(monkeypatch) -> None:
    captured: dict[str, object] = {}

    @asynccontextmanager
    async def worker_session(service_type: str):
        captured["service_type"] = service_type
        yield _Db("ciphertext")

    monkeypatch.setattr("api.services.service_identity.worker_reader_db_session", worker_session)
    monkeypatch.setattr(
        "api.services.service_identity.decrypt_service_identity_payload",
        lambda payload: {"hmac_secret": f"secret:{payload}"},
    )

    payload = await _read_internal_hmac_payload(
        service_type="timer",
        credential_key_id="poundcake-control-plane:timer",
    )

    assert payload == {"hmac_secret": "secret:ciphertext"}
    assert captured == {"service_type": "timer"}


@pytest.mark.asyncio
async def test_read_internal_hmac_payload_rejects_unknown_worker() -> None:
    with pytest.raises(ServicePluginCredentialError, match="not registered for worker"):
        await _read_internal_hmac_payload(
            service_type="credential-manager",
            credential_key_id="poundcake-control-plane:credential-manager",
        )
