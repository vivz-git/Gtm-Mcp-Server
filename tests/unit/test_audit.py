"""Audit trail behaviour."""

from __future__ import annotations

import pytest

from gtm_mcp.audit.events import (
    MAX_DETAIL_ENTRIES,
    MAX_DETAIL_VALUE_LENGTH,
    AuditEvent,
    AuditOperation,
)
from gtm_mcp.audit.sinks import AuditSink, InMemoryAuditSink, LoggingAuditSink, PostgresAuditSink
from gtm_mcp.domain.results import WriteOutcome


@pytest.mark.unit
def test_delete_is_not_an_expressible_operation() -> None:
    """Destructive operations are excluded at the type level, not by review."""
    assert "delete" not in {op.value for op in AuditOperation}
    with pytest.raises(ValueError, match="delete"):
        AuditOperation("delete")


@pytest.mark.unit
def test_events_are_immutable() -> None:
    """An audit record that can be edited after the fact is not an audit record."""
    event = AuditEvent(
        tool_name="sync_to_crm",
        operation=AuditOperation.UPSERT,
        outcome=WriteOutcome.CREATED,
        target_type="contact",
    )
    with pytest.raises(ValueError, match="frozen"):
        event.outcome = WriteOutcome.FAILED  # type: ignore[misc]


@pytest.mark.unit
def test_each_event_gets_a_distinct_id_and_utc_timestamp() -> None:
    first = AuditEvent(
        tool_name="save_to_list",
        operation=AuditOperation.LIST_ADD,
        outcome=WriteOutcome.CREATED,
        target_type="contact",
    )
    second = AuditEvent(
        tool_name="save_to_list",
        operation=AuditOperation.LIST_ADD,
        outcome=WriteOutcome.CREATED,
        target_type="contact",
    )
    assert first.audit_id != second.audit_id
    assert first.occurred_at.tzinfo is not None


@pytest.mark.unit
@pytest.mark.anyio
async def test_failed_and_rejected_writes_are_still_audited() -> None:
    """A trail of successes only cannot answer what the agent tried to do."""
    sink = InMemoryAuditSink()
    for outcome in (WriteOutcome.REJECTED, WriteOutcome.FAILED):
        await sink.record(
            AuditEvent(
                tool_name="sync_to_crm",
                operation=AuditOperation.UPSERT,
                outcome=outcome,
                target_type="contact",
                error_code="write_rejected",
            )
        )
    assert [e.outcome for e in sink.events] == [WriteOutcome.REJECTED, WriteOutcome.FAILED]


@pytest.mark.unit
def test_sinks_satisfy_the_protocol_structurally() -> None:
    assert isinstance(InMemoryAuditSink(), AuditSink)
    assert isinstance(LoggingAuditSink(), AuditSink)
    assert issubclass(PostgresAuditSink, AuditSink)


@pytest.mark.unit
def test_detail_values_for_sensitive_keys_are_redacted_before_the_event_exists() -> None:
    """Audit rows reach log aggregators; a credential must not ride along.

    Enforced on the event rather than at each call site, so the guarantee holds
    for callers that do not exist yet.
    """
    event = AuditEvent(
        tool_name="sync_to_crm",
        operation=AuditOperation.UPSERT,
        outcome=WriteOutcome.CREATED,
        target_type="contact",
        details={
            "email": "elena@cloudscale.io",
            "phone": "+1-555-0100",
            "api_key": "sk-live-not-a-real-key",
            "list_name": "Q4 Pipeline",
        },
    )

    assert event.details["email"] == "[redacted]"
    assert event.details["phone"] == "[redacted]"
    assert event.details["api_key"] == "[redacted]"
    assert event.details["list_name"] == "Q4 Pipeline"
    assert "elena@cloudscale.io" not in str(event.model_dump())


@pytest.mark.unit
def test_a_long_detail_value_is_truncated_rather_than_stored_whole() -> None:
    """An audit row is evidence, not a place to park a provider payload."""
    event = AuditEvent(
        tool_name="sync_to_crm",
        operation=AuditOperation.UPSERT,
        outcome=WriteOutcome.CREATED,
        target_type="contact",
        details={"note": "x" * 5_000},
    )

    assert len(event.details["note"]) == MAX_DETAIL_VALUE_LENGTH


@pytest.mark.unit
def test_an_event_cannot_carry_an_unbounded_number_of_details() -> None:
    with pytest.raises(ValueError, match="at most"):
        AuditEvent(
            tool_name="sync_to_crm",
            operation=AuditOperation.UPSERT,
            outcome=WriteOutcome.CREATED,
            target_type="contact",
            details={f"key_{index}": "value" for index in range(MAX_DETAIL_ENTRIES + 1)},
        )
