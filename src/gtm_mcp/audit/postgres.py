"""PostgreSQL durable audit sink.

Persists each AuditEvent into the audit_log table. Escalates failures so that
un-audited mutations cannot appear to succeed.
"""

from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gtm_mcp.audit.events import AuditEvent
from gtm_mcp.db.models import AuditLogModel
from gtm_mcp.errors import RepositoryError
from gtm_mcp.logging_setup import get_logger

_log = get_logger(__name__)


class PostgresAuditSink:
    """Durably records audit events into PostgreSQL."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Initialise with an active session factory.

        Args:
            session_factory: Session maker bound to the PostgreSQL engine.
        """
        self._session_factory = session_factory

    async def record(self, event: AuditEvent) -> None:
        """Durably record a single audit event in the database.

        Args:
            event: The event to persist.

        Raises:
            RepositoryError: The database write failed; escalated to prevent silent failures.
        """
        try:
            async with self._session_factory() as session, session.begin():
                row = AuditLogModel(
                    audit_id=event.audit_id,
                    occurred_at=event.occurred_at,
                    tool_name=event.tool_name,
                    operation=event.operation.value,
                    outcome=event.outcome.value,
                    target_type=event.target_type,
                    target_id=event.target_id,
                    changed_fields=list(event.changed_fields),
                    request_id=event.request_id,
                    error_code=event.error_code,
                    dry_run=event.dry_run,
                )
                session.add(row)
        except SQLAlchemyError as exc:
            _log.error(
                "audit_persistence_failed",
                audit_id=event.audit_id,
                operation=event.operation.value,
                error=str(exc),
            )
            raise RepositoryError(
                f"Failed to durably persist audit event '{event.audit_id}': {exc}"
            ) from exc
