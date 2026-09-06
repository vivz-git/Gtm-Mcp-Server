"""Async SQLAlchemy engine and session wiring.

The engine is created once per process during the server lifespan and shared by
every tool call. Creating it per request would spend a TCP handshake and a
Postgres authentication round trip on every tool invocation.

Schema definitions and migrations arrive with the CRM phase; this module owns
only connection management, so it stays useful unchanged when tables are added.
"""

from __future__ import annotations

import anyio
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from gtm_mcp.errors import RepositoryError
from gtm_mcp.logging_setup import get_logger
from gtm_mcp.settings import Settings

_log = get_logger(__name__)


def build_engine(settings: Settings) -> AsyncEngine:
    """Create the async database engine.

    Args:
        settings: Runtime configuration supplying the DSN and pool size.

    Returns:
        A configured async engine. Connections are established lazily.
    """
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_size=settings.db_pool_size,
        max_overflow=0,
        # Verify a pooled connection before handing it out. Long-lived MCP
        # sessions sit idle between tool calls, long enough for Postgres or an
        # intermediate proxy to have dropped the connection.
        pool_pre_ping=True,
        echo=False,
        # asyncpg's own connect timeout. Belt: the driver gives up on its own.
        connect_args={"timeout": settings.db_connect_timeout_seconds},
    )


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create the session factory bound to an engine.

    Args:
        engine: The engine sessions should use.

    Returns:
        A session factory. ``expire_on_commit`` is disabled so that domain
        objects stay readable after the transaction that produced them closes.
    """
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


async def check_connection(engine: AsyncEngine, *, timeout_seconds: float = 5.0) -> None:
    """Verify the database is reachable, within a bounded time.

    Called during startup so that a bad DSN surfaces immediately with a clear
    message, rather than as a confusing failure inside the first tool call.

    The explicit timeout is braces to the driver's belt: a host that accepts the
    TCP connection and then never answers would otherwise stall startup for as
    long as the operating system allows.

    Args:
        engine: The engine to test.
        timeout_seconds: Hard ceiling on the whole probe.

    Raises:
        RepositoryError: The database could not be reached in time.
    """
    try:
        with anyio.fail_after(timeout_seconds):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
    except TimeoutError as exc:
        _log.error("database_probe_timed_out", timeout_seconds=timeout_seconds)
        raise RepositoryError(
            f"The CRM database did not respond within {timeout_seconds:g}s. "
            "Check GTM_DATABASE_URL and that PostgreSQL is reachable.",
        ) from exc
    # OSError matters as much as SQLAlchemyError here: a refused TCP connection
    # surfaces as ConnectionRefusedError straight from the asyncpg driver,
    # unwrapped by SQLAlchemy, and would otherwise abort startup.
    except (SQLAlchemyError, OSError) as exc:
        _log.error("database_unreachable", error=str(exc))
        raise RepositoryError(
            "Could not connect to the CRM database. "
            "Check GTM_DATABASE_URL and that PostgreSQL is running "
            "(docker compose up -d db).",
        ) from exc
