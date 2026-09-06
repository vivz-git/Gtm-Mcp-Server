"""Application context: the state shared by every tool invocation.

This lives in its own module, outside ``gtm_mcp.server``, for a concrete reason.
The MCP SDK v2 resolves handler type annotations **at runtime** in order to
derive schemas, so any type named in a tool signature must be importable at
runtime rather than hidden behind ``TYPE_CHECKING``. Tool modules therefore
import ``AppContext`` directly, and if it lived in ``gtm_mcp.server.lifespan``
that import would close the cycle
``server -> app -> tools -> diagnostics -> server``. See DECISIONS.md D-005.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from gtm_mcp.audit.sinks import AuditSink
from gtm_mcp.errors import RepositoryError
from gtm_mcp.settings import Settings


@dataclass(slots=True)
class AppContext:
    """Long-lived state shared across all tool invocations.

    Built once by the server lifespan and reached from a handler via
    ``ctx.request_context.lifespan_context``.
    """

    settings: Settings
    audit_sink: AuditSink
    engine: AsyncEngine | None = None
    session_factory: async_sessionmaker[AsyncSession] | None = None
    database_available: bool = False
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def require_session_factory(self) -> async_sessionmaker[AsyncSession]:
        """Return the session factory, or fail with an actionable message.

        Tools that need the CRM call this instead of dereferencing the optional
        attribute, so an unavailable database produces one clear, fixable error
        rather than an ``AttributeError`` on ``None``.

        Returns:
            The configured session factory.

        Raises:
            RepositoryError: The database was not reachable at startup.
        """
        if self.session_factory is None:
            raise RepositoryError(
                "The CRM database is not available. Start it with "
                "'docker compose up -d db' and restart the server.",
            )
        return self.session_factory
