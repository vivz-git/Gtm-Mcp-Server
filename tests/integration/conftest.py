"""Shared fixtures for integration tests requiring PostgreSQL."""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator
from urllib.parse import urlparse

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from gtm_mcp.audit.postgres import PostgresAuditSink
from gtm_mcp.crm.repository import PostgresCrmRepository
from gtm_mcp.db.engine import build_engine, build_session_factory
from gtm_mcp.settings import Settings


def _check_socket_reachable(dsn: str) -> bool:
    """Quickly check if the PostgreSQL host and port accept TCP connections."""
    try:
        clean_url = dsn.replace("+asyncpg", "")
        parsed = urlparse(clean_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 5432
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


@pytest.fixture
async def pg_engine() -> AsyncIterator[AsyncEngine]:
    """Provide a real PostgreSQL engine or skip cleanly if unreachable."""
    settings = Settings()
    dsn = settings.database_url.get_secret_value()
    if not _check_socket_reachable(dsn):
        pytest.skip(f"PostgreSQL is not reachable at {dsn}")

    engine = build_engine(settings)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def session_factory(pg_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Provide an active session factory bound to the PostgreSQL engine."""
    return build_session_factory(pg_engine)


@pytest.fixture
def crm_repo(session_factory: async_sessionmaker[AsyncSession]) -> PostgresCrmRepository:
    """Provide a PostgresCrmRepository instance."""
    return PostgresCrmRepository(session_factory)


@pytest.fixture
def audit_sink(session_factory: async_sessionmaker[AsyncSession]) -> PostgresAuditSink:
    """Provide a PostgresAuditSink instance."""
    return PostgresAuditSink(session_factory)
