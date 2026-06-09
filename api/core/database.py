#  ___                        _  ____      _
# |  _ \ ___  _   _ _ __   __| |/ ___|__ _| | _____
# | |_) / _ \| | | | '_ \ / _` | |   / _` | |/ / _ \
# |  __/ (_) | |_| | | | | (_| | |__| (_| |   <  __/
# |_|   \___/ \__,_|_| |_|\__,_|\____\__,_|_|\_\___|
#
"""Database configuration and session management."""
from contextlib import asynccontextmanager
from typing import Any
from typing import AsyncGenerator

from api.core.logging import get_logger
from api.core.config import settings
from sqlalchemy import event
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

logger = get_logger(__name__)

def get_async_database_url() -> str:
    """Return an async DB URL for the runtime engine."""
    url = settings.database_url
    if "+pymysql" in url:
        return url.replace("+pymysql", "+aiomysql")
    return url


def get_credential_manager_async_database_url() -> str:
    """Return the async DB URL for credential-manager owned writes."""
    url = settings.credential_manager_database_url.strip()
    if not url:
        raise RuntimeError(
            "POUNDCAKE_CREDENTIAL_MANAGER_DATABASE_URL is required for credential writes"
        )
    if "+pymysql" in url:
        return url.replace("+pymysql", "+aiomysql")
    return url


def get_auth_verifier_async_database_url() -> str:
    """Return the async DB URL for internal HMAC auth verification."""
    url = settings.auth_verifier_database_url.strip()
    if not url:
        raise RuntimeError("POUNDCAKE_AUTH_VERIFIER_DATABASE_URL is required for service auth")
    if "+pymysql" in url:
        return url.replace("+pymysql", "+aiomysql")
    return url


def get_worker_reader_async_database_url(service_type: str) -> str:
    """Return the async DB URL for one worker's service-identity view reads."""
    normalized = service_type.strip().lower()
    url_by_service = {
        "prep-chef": settings.prep_chef_reader_database_url.strip(),
        "timer": settings.timer_reader_database_url.strip(),
        "expediter-runner": settings.expediter_runner_reader_database_url.strip(),
        "dishwasher": settings.dishwasher_reader_database_url.strip(),
    }
    url = url_by_service.get(normalized, "")
    if not url:
        url = settings.database_url.strip()
    if not url:
        raise RuntimeError(f"worker reader database URL is not configured for {service_type}")
    if "+pymysql" in url:
        return url.replace("+pymysql", "+aiomysql")
    return url


def get_plugin_operation_async_database_url() -> str:
    """Return the async DB URL for the plugin-operation database identity."""
    url = settings.plugin_operation_database_url.strip()
    if not url:
        raise RuntimeError(
            "POUNDCAKE_PLUGIN_OPERATION_DB_URL is required for plugin database operations"
        )
    if "+pymysql" in url:
        return url.replace("+pymysql", "+aiomysql")
    return url


# Create async engine
engine = create_async_engine(
    get_async_database_url(),
    echo=settings.database_echo,
    pool_pre_ping=True,  # Validates connections before using them
    pool_size=10,
    max_overflow=20,
)


@event.listens_for(engine.sync_engine, "connect")
def _set_connection_utc(dbapi_connection, _connection_record) -> None:
    """Force MySQL/MariaDB sessions to UTC."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("SET time_zone = '+00:00'")
    finally:
        cursor.close()


SessionLocal = async_sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    expire_on_commit=False,
)


_credential_manager_engine: Any | None = None
_CredentialManagerSessionLocal = None
_auth_verifier_engine: Any | None = None
_AuthVerifierSessionLocal = None
_plugin_op_engine: Any | None = None
_PluginOpSessionLocal = None
_worker_reader_engines: dict[str, Any] = {}
_WorkerReaderSessionLocal: dict[str, async_sessionmaker[AsyncSession]] = {}


async def dispose_async_engines() -> None:
    """Dispose all async SQLAlchemy engines owned by this module.

    This is intended for short-lived scripts and tests that may touch multiple
    database identities in one interpreter and want a clean async shutdown
    before the event loop closes.
    """

    global _credential_manager_engine, _CredentialManagerSessionLocal
    global _auth_verifier_engine, _AuthVerifierSessionLocal
    global _plugin_op_engine, _PluginOpSessionLocal

    engines: list[Any] = [engine]
    for candidate in (
        _credential_manager_engine,
        _auth_verifier_engine,
        _plugin_op_engine,
    ):
        if candidate is not None:
            engines.append(candidate)
    engines.extend(_worker_reader_engines.values())

    seen: set[int] = set()
    for candidate in engines:
        if candidate is None:
            continue
        candidate_id = id(candidate)
        if candidate_id in seen:
            continue
        seen.add(candidate_id)
        await candidate.dispose()

    _credential_manager_engine = None
    _CredentialManagerSessionLocal = None
    _auth_verifier_engine = None
    _AuthVerifierSessionLocal = None
    _plugin_op_engine = None
    _PluginOpSessionLocal = None
    _worker_reader_engines.clear()
    _WorkerReaderSessionLocal.clear()


def _credential_manager_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Create the credential-manager sessionmaker lazily from its own DB URL."""
    global _credential_manager_engine, _CredentialManagerSessionLocal
    if _CredentialManagerSessionLocal is None:
        _credential_manager_engine = create_async_engine(
            get_credential_manager_async_database_url(),
            echo=settings.database_echo,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )

        @event.listens_for(_credential_manager_engine.sync_engine, "connect")
        def _set_credential_manager_connection_utc(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("SET time_zone = '+00:00'")
            finally:
                cursor.close()

        _CredentialManagerSessionLocal = async_sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=_credential_manager_engine,
            expire_on_commit=False,
        )
    return _CredentialManagerSessionLocal


def _plugin_op_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Create the plugin-operation sessionmaker lazily from a dedicated DB URL.

    Uses a dedicated MariaDB user configured via POUNDCAKE_PLUGIN_OPERATION_DB_URL.
    This user has explicit grants only for the tables that plugin operations need
    (Recipe, Ingredient, RecipeIngredient, Dish, ScheduledTask, service_plugins).
    RBAC enforcement happens in plugin_operations.py, but the database identity
    provides an additional layer of traceability and defense-in-depth.
    """
    global _plugin_op_engine, _PluginOpSessionLocal
    if _PluginOpSessionLocal is None:
        _plugin_op_engine = create_async_engine(
            get_plugin_operation_async_database_url(),
            echo=settings.database_echo,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )

        @event.listens_for(_plugin_op_engine.sync_engine, "connect")
        def _set_plugin_op_connection_utc(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("SET time_zone = '+00:00'")
            finally:
                cursor.close()

        _PluginOpSessionLocal = async_sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=_plugin_op_engine,
            expire_on_commit=False,
        )
    return _PluginOpSessionLocal


def _auth_verifier_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Create the auth-verifier sessionmaker lazily from its own DB URL."""
    global _auth_verifier_engine, _AuthVerifierSessionLocal
    if _AuthVerifierSessionLocal is None:
        _auth_verifier_engine = create_async_engine(
            get_auth_verifier_async_database_url(),
            echo=settings.database_echo,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )

        @event.listens_for(_auth_verifier_engine.sync_engine, "connect")
        def _set_auth_verifier_connection_utc(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("SET time_zone = '+00:00'")
            finally:
                cursor.close()

        _AuthVerifierSessionLocal = async_sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=_auth_verifier_engine,
            expire_on_commit=False,
        )
    return _AuthVerifierSessionLocal


def _worker_reader_sessionmaker(service_type: str) -> async_sessionmaker[AsyncSession]:
    """Create a worker-reader sessionmaker lazily from a service-scoped DB URL."""
    normalized = service_type.strip().lower()
    if normalized not in _WorkerReaderSessionLocal:
        engine = create_async_engine(
            get_worker_reader_async_database_url(normalized),
            echo=settings.database_echo,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )

        @event.listens_for(engine.sync_engine, "connect")
        def _set_worker_reader_connection_utc(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("SET time_zone = '+00:00'")
            finally:
                cursor.close()

        _worker_reader_engines[normalized] = engine
        _WorkerReaderSessionLocal[normalized] = async_sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=engine,
            expire_on_commit=False,
        )
    return _WorkerReaderSessionLocal[normalized]


@asynccontextmanager
async def credential_manager_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a DB session backed by the credential-manager database identity."""
    sessionmaker = _credential_manager_sessionmaker()
    async with sessionmaker() as db:
        yield db


@asynccontextmanager
async def auth_verifier_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a DB session backed by the auth-verifier database identity."""
    sessionmaker = _auth_verifier_sessionmaker()
    async with sessionmaker() as db:
        yield db


@asynccontextmanager
async def plugin_operation_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a DB session backed by the plugin-operation database identity.

    All adapter database writes go through this session, which is backed by
    the dedicated plugin_operation MariaDB user. This enables operator
    audit trails to trace which tables were written and when via MariaDB
    general query log.
    """
    sessionmaker = _plugin_op_sessionmaker()
    async with sessionmaker() as db:
        yield db


@asynccontextmanager
async def worker_reader_db_session(service_type: str) -> AsyncGenerator[AsyncSession, None]:
    """Yield a DB session backed by one worker's reader database identity."""
    sessionmaker = _worker_reader_sessionmaker(service_type)
    async with sessionmaker() as db:
        yield db


class Base(DeclarativeBase):
    """Base class for SQLAlchemy declarative models."""

    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Database session dependency for FastAPI."""
    async with SessionLocal() as db:
        yield db
