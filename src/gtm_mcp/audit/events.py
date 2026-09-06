"""The audit event record.

Every write tool call produces exactly one ``AuditEvent``, including calls that
were rejected by a guardrail or that failed. Recording refusals and failures is
the point: an audit trail containing only successes cannot answer "did the agent
try to do something it should not have?".
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from gtm_mcp.domain.results import WriteOutcome


class AuditOperation(StrEnum):
    """The kind of mutation an audit event describes.

    There is intentionally no ``DELETE`` member. The architecture forbids
    destructive operations, and omitting the enum value means a delete cannot be
    audited because it cannot be expressed.
    """

    UPSERT = "upsert"
    """Create-or-update of a record, keyed by a natural identifier."""

    LIST_ADD = "list_add"
    """Addition of an existing record to a GTM list. Additive only."""


def _new_audit_id() -> str:
    """Generate a fresh audit identifier."""
    return str(uuid.uuid4())


def _utc_now() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(UTC)


class AuditEvent(BaseModel):
    """An immutable record of one attempted write.

    Written before the caller receives its result, so a crash between the
    mutation and the response still leaves evidence of the attempt.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    audit_id: str = Field(default_factory=_new_audit_id, description="Unique event identifier.")
    occurred_at: datetime = Field(
        default_factory=_utc_now, description="When the write was attempted (UTC)."
    )

    tool_name: str = Field(description="MCP tool that initiated the write, e.g. 'sync_to_crm'.")
    operation: AuditOperation = Field(description="Kind of mutation attempted.")
    outcome: WriteOutcome = Field(description="What actually happened to persistent state.")

    target_type: str = Field(description="Entity type touched, e.g. 'contact' or 'company'.")
    target_id: str | None = Field(
        default=None, description="Identifier of the affected record, when known."
    )
    changed_fields: tuple[str, ...] = Field(
        default=(), description="Field names whose values changed."
    )

    request_id: str | None = Field(
        default=None, description="MCP request identifier, for correlating with server logs."
    )
    error_code: str | None = Field(
        default=None, description="Stable error code when the write was rejected or failed."
    )
    dry_run: bool = Field(
        default=False, description="Whether persistence was intentionally skipped."
    )
