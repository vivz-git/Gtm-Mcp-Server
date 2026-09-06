"""Audit sinks: where audit events are durably recorded.

``AuditSink`` is a Protocol rather than a base class so that the write path
depends only on the shape of a sink. The initial implementation writes to the
structured log; a Postgres-backed sink lands with the database phase
(DECISIONS.md D-006) and requires no change to any calling code.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from gtm_mcp.audit.events import AuditEvent
from gtm_mcp.logging_setup import get_logger

_log = get_logger(__name__)


@runtime_checkable
class AuditSink(Protocol):
    """A destination for audit events."""

    async def record(self, event: AuditEvent) -> None:
        """Durably record a single audit event.

        Implementations must not raise for ordinary conditions; a failure to
        audit is escalated by the caller, never swallowed silently.

        Args:
            event: The event to record.
        """
        ...


class LoggingAuditSink:
    """Writes audit events to the structured log on stderr.

    Adequate for local development and for deployments that ship stderr to a log
    aggregator. Not adequate on its own for a compliance story, which is why the
    database-backed sink is a tracked follow-up rather than an optional extra.
    """

    async def record(self, event: AuditEvent) -> None:
        """Emit the event as a single structured log line.

        Args:
            event: The event to record.
        """
        _log.info("audit", **event.model_dump(mode="json"))


class InMemoryAuditSink:
    """Collects audit events in a list. For tests only.

    Lets guardrail tests assert that a rejected or failed write still produced an
    audit record, which is the behaviour that actually matters.
    """

    def __init__(self) -> None:
        """Initialise an empty event buffer."""
        self.events: list[AuditEvent] = []

    async def record(self, event: AuditEvent) -> None:
        """Append the event to the in-memory buffer.

        Args:
            event: The event to record.
        """
        self.events.append(event)
