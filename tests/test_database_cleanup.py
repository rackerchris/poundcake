from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker

from api.core import database


class _FakeEngine:
    def __init__(self) -> None:
        self.dispose_calls = 0

    async def dispose(self) -> None:
        self.dispose_calls += 1


async def test_dispose_async_engines_disposes_cached_engines_and_clears_caches(
    monkeypatch,
) -> None:
    default_engine = _FakeEngine()
    credential_engine = _FakeEngine()
    auth_engine = _FakeEngine()
    plugin_engine = _FakeEngine()
    worker_engine = _FakeEngine()

    monkeypatch.setattr(database, "engine", default_engine)
    monkeypatch.setattr(database, "_credential_manager_engine", credential_engine)
    monkeypatch.setattr(database, "_CredentialManagerSessionLocal", async_sessionmaker())
    monkeypatch.setattr(database, "_auth_verifier_engine", auth_engine)
    monkeypatch.setattr(database, "_AuthVerifierSessionLocal", async_sessionmaker())
    monkeypatch.setattr(database, "_plugin_op_engine", plugin_engine)
    monkeypatch.setattr(database, "_PluginOpSessionLocal", async_sessionmaker())
    monkeypatch.setattr(database, "_worker_reader_engines", {"dishwasher": worker_engine})
    monkeypatch.setattr(
        database,
        "_WorkerReaderSessionLocal",
        {"dishwasher": async_sessionmaker()},
    )

    await database.dispose_async_engines()

    assert default_engine.dispose_calls == 1
    assert credential_engine.dispose_calls == 1
    assert auth_engine.dispose_calls == 1
    assert plugin_engine.dispose_calls == 1
    assert worker_engine.dispose_calls == 1
    assert database._credential_manager_engine is None
    assert database._CredentialManagerSessionLocal is None
    assert database._auth_verifier_engine is None
    assert database._AuthVerifierSessionLocal is None
    assert database._plugin_op_engine is None
    assert database._PluginOpSessionLocal is None
    assert database._worker_reader_engines == {}
    assert database._WorkerReaderSessionLocal == {}


async def test_dispose_async_engines_deduplicates_shared_engine_references(monkeypatch) -> None:
    default_engine = _FakeEngine()

    monkeypatch.setattr(database, "engine", default_engine)
    monkeypatch.setattr(database, "_credential_manager_engine", default_engine)
    monkeypatch.setattr(database, "_CredentialManagerSessionLocal", async_sessionmaker())
    monkeypatch.setattr(database, "_auth_verifier_engine", None)
    monkeypatch.setattr(database, "_AuthVerifierSessionLocal", None)
    monkeypatch.setattr(database, "_plugin_op_engine", None)
    monkeypatch.setattr(database, "_PluginOpSessionLocal", None)
    monkeypatch.setattr(database, "_worker_reader_engines", {"dishwasher": default_engine})
    monkeypatch.setattr(database, "_WorkerReaderSessionLocal", {})

    await database.dispose_async_engines()

    assert default_engine.dispose_calls == 1
