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

from pydantic import BaseModel, ConfigDict, Field, field_validator

from gtm_mcp.domain.results import WriteOutcome
from gtm_mcp.logging_setup import REDACTED_KEYS, REDACTION_PLACEHOLDER

#: Hard cap on a single ``details`` value. Audit rows are operational evidence,
#: not a place to park a provider payload.
MAX_DETAIL_VALUE_LENGTH = 200

#: Hard cap on how many detail entries one event may carry.
MAX_DETAIL_ENTRIES = 10


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
    details: dict[str, str] = Field(
        default_factory=dict,
        description="Small, non-sensitive key/value context about the attempt, e.g. the "
        "list name a contact was added to. Values are truncated and known-sensitive keys "
        "are redacted before the event is constructed.",
    )

    @field_validator("details")
    @classmethod
    def _sanitise_details(cls, value: dict[str, str]) -> dict[str, str]:
        """Redact sensitive keys and bound the size of every detail value.

        Audit rows are read by operators and shipped to log aggregators, so a
        credential or an email address must not be able to reach one through a
        careless call site. Enforcing it here rather than at each caller means
        the guarantee holds for callers that do not yet exist.

        Args:
            value: The raw detail mapping supplied by the write path.

        Returns:
            A mapping with sensitive values replaced and long values truncated.

        Raises:
            ValueError: More than ``MAX_DETAIL_ENTRIES`` entries were supplied.
        """
        if len(value) > MAX_DETAIL_ENTRIES:
            raise ValueError(
                f"an audit event carries at most {MAX_DETAIL_ENTRIES} detail entries, "
                f"got {len(value)}"
            )
        sanitised: dict[str, str] = {}
        for key, raw in value.items():
            if key.lower() in REDACTED_KEYS:
                sanitised[key] = REDACTION_PLACEHOLDER
                continue
            sanitised[key] = raw[:MAX_DETAIL_VALUE_LENGTH]
        return sanitised
