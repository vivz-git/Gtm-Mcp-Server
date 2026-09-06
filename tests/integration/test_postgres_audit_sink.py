"""Integration tests for PostgresAuditSink."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gtm_mcp.audit.events import AuditEvent, AuditOperation
from gtm_mcp.audit.postgres import PostgresAuditSink
from gtm_mcp.audit.sinks import AuditSink
from gtm_mcp.db.models import AuditLogModel
from gtm_mcp.domain.results import WriteOutcome

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def test_postgres_audit_sink_satisfies_protocol(audit_sink: PostgresAuditSink) -> None:
    """PostgresAuditSink must structurally satisfy AuditSink Protocol."""
    assert isinstance(audit_sink, AuditSink)


async def test_record_persists_successful_and_rejected_events(
    audit_sink: PostgresAuditSink,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Every attempted write (success, rejected, or failed) must be durably recorded."""
    # 1. Successful upsert
    success_id = str(uuid.uuid4())
    success_event = AuditEvent(
        audit_id=success_id,
        occurred_at=datetime.now(UTC),
        tool_name="sync_to_crm",
        operation=AuditOperation.UPSERT,
        outcome=WriteOutcome.CREATED,
        target_type="contact",
        target_id="cont-123",
        changed_fields=("email", "title"),
    )
    await audit_sink.record(success_event)

    # 2. Rejected write
    rejected_id = str(uuid.uuid4())
    rejected_event = AuditEvent(
        audit_id=rejected_id,
        occurred_at=datetime.now(UTC),
        tool_name="save_to_list",
        operation=AuditOperation.LIST_ADD,
        outcome=WriteOutcome.REJECTED,
        target_type="contact",
        target_id="cont-456",
        error_code="write_disabled",
    )
    await audit_sink.record(rejected_event)

    # Verify rows in PostgreSQL
    async with session_factory() as session:
        # Check success row
        stmt = select(AuditLogModel).where(AuditLogModel.audit_id == success_id)
        row = (await session.execute(stmt)).scalar_one_or_none()
        assert row is not None
        assert row.tool_name == "sync_to_crm"
        assert row.operation == "upsert"
        assert row.outcome == "created"
        assert row.target_id == "cont-123"
        assert row.changed_fields == ["email", "title"]

        # Check rejected row
        stmt_rej = select(AuditLogModel).where(AuditLogModel.audit_id == rejected_id)
        rej_row = (await session.execute(stmt_rej)).scalar_one_or_none()
        assert rej_row is not None
        assert rej_row.outcome == "rejected"
        assert rej_row.error_code == "write_disabled"
