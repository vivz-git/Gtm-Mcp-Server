"""Server lifespan: resources created once at startup and shared by every call.

The SDK yields whatever this context manager produces to every handler via
``ctx.request_context.lifespan_context``. Putting the engine, the session
factory and the audit sink here means a tool call costs no connection setup, and
means tests can build an ``AppContext`` with fakes and drive the server without
any real infrastructure.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import TYPE_CHECKING

from gtm_mcp.audit.postgres import PostgresAuditSink
from gtm_mcp.audit.sinks import LoggingAuditSink
from gtm_mcp.context import AppContext
from gtm_mcp.db.engine import build_engine, build_session_factory, check_connection
from gtm_mcp.errors import RepositoryError
from gtm_mcp.logging_setup import configure_logging, get_logger
from gtm_mcp.settings import Settings

if TYPE_CHECKING:
    from mcp.server import MCPServer

_log = get_logger(__name__)


def make_lifespan(
    settings: Settings,
) -> Callable[[MCPServer[AppContext]], AbstractAsyncContextManager[AppContext]]:
    """Build the lifespan context manager bound to a specific configuration.

    A factory rather than a bare context manager because the SDK hands the
    lifespan only the server, with no way to pass configuration through. Closing
    over ``settings`` here is what lets a test run the server against its own
    configuration instead of the process-wide singleton.

    Args:
        settings: Configuration the server should run with.

    Returns:
        A lifespan callable suitable for ``MCPServer(lifespan=...)``.
    """

    @asynccontextmanager
    async def _lifespan(_server: MCPServer[AppContext]) -> AsyncIterator[AppContext]:
        """Build and tear down the server's shared resources.

        Database connectivity is probed but **not** required to start. No tool
        depends on the CRM yet, and refusing to start would make the server
        unusable for MCP Inspector and client-integration work whenever Postgres
        happens to be down. The probe result is recorded on the context so that
        ``server_info`` can report it and CRM tools can fail with a clear message.

        Args:
            _server: The server being started. Unused; required by the SDK signature.

        Yields:
            The application context shared by every handler.
        """
        # Configured here, not only in __main__, so that every entry point -
        # console script, python -m, an embedding test - gets stderr-only logging
        # before the first byte of JSON-RPC is written to stdout.
        configure_logging(level=settings.log_level, log_format=settings.log_format)

        context = AppContext(settings=settings, audit_sink=LoggingAuditSink())

        engine = build_engine(settings)
        try:
            await check_connection(engine, timeout_seconds=settings.db_connect_timeout_seconds)
        except RepositoryError as exc:
            _log.warning("starting_without_database", reason=exc.message)
            await engine.dispose()
        else:
            context.engine = engine
            session_factory = build_session_factory(engine)
            context.session_factory = session_factory
            context.audit_sink = PostgresAuditSink(session_factory)
            context.database_available = True

        _log.info(
            "server_started",
            server_name=settings.server_name,
            environment=settings.environment,
            transport=settings.transport,
            database_available=context.database_available,
            write_tools_enabled=settings.enable_write_tools,
        )

        try:
            yield context
        finally:
            if context.engine is not None:
                await context.engine.dispose()
            _log.info("server_stopped")

    return _lifespan
