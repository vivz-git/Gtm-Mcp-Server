"""The guarded write path against real PostgreSQL.

``tests/unit/test_crm_service.py`` proves the guardrails against an in-memory
repository. This file proves the same properties where they finally matter: that
a dry run leaves no row behind, that a repeated sync leaves exactly one contact,
that a repeated list add leaves exactly one membership, that the D-015 merge
policy survives the full tool-to-database path, and that every attempt lands in
the ``audit_log`` table.

Everything created here is namespaced with a random suffix and left in place;
the schema has no delete path, which is the point.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gtm_mcp.audit.events import AuditOperation
from gtm_mcp.audit.postgres import PostgresAuditSink
from gtm_mcp.crm.repository import PostgresCrmRepository
from gtm_mcp.db.models import AuditLogModel, ContactModel, ListMemberModel, ListModel
from gtm_mcp.domain.models import Contact, ContactFilter, RecordSource
from gtm_mcp.domain.results import WriteOutcome
from gtm_mcp.domain.writes import ContactSyncInput
from gtm_mcp.services.crm import CrmService
from gtm_mcp.settings import Settings

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def _settings(**overrides: object) -> Settings:
    """Build settings with writes enabled unless a test says otherwise.

    Args:
        **overrides: Settings fields to override.

    Returns:
        The settings instance.
    """
    values: dict[str, object] = {
        "environment": "test",
        "log_format": "console",
        "enable_write_tools": True,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _service(session_factory: async_sessionmaker[AsyncSession], **overrides: object) -> CrmService:
    """Assemble the service over the real repository and audit sink.

    Args:
        session_factory: Session factory bound to PostgreSQL.
        **overrides: Settings fields to override.

    Returns:
        The service under test.
    """
    return CrmService(
        repository=PostgresCrmRepository(session_factory),
        audit_sink=PostgresAuditSink(session_factory),
        settings=_settings(**overrides),
    )


@pytest.fixture
def unique_email() -> str:
    """A per-test email so runs never collide on the natural identity index."""
    return f"phase4.{uuid.uuid4().hex[:10]}@writetest.example"


async def _audit_rows(
    session_factory: async_sessionmaker[AsyncSession], request_id: str
) -> list[AuditLogModel]:
    """Fetch the audit rows written for one request, oldest first.

    Args:
        session_factory: Session factory bound to PostgreSQL.
        request_id: The request identifier to filter on.

    Returns:
        The matching audit rows.
    """
    async with session_factory() as session:
        result = await session.execute(
            select(AuditLogModel)
            .where(AuditLogModel.request_id == request_id)
            .order_by(AuditLogModel.occurred_at)
        )
        return list(result.scalars().all())


async def _contact_count(session_factory: async_sessionmaker[AsyncSession], email: str) -> int:
    """Count contact rows carrying an email.

    Args:
        session_factory: Session factory bound to PostgreSQL.
        email: The address to count.

    Returns:
        How many rows exist.
    """
    async with session_factory() as session:
        result = await session.execute(
            select(func.count()).select_from(ContactModel).where(ContactModel.email == email)
        )
        return int(result.scalar_one())


# --------------------------------------------------------------------------
# Guardrails, against a database that would happily have taken the write
# --------------------------------------------------------------------------


async def test_a_disabled_server_writes_no_row_and_still_audits(
    session_factory: async_sessionmaker[AsyncSession], unique_email: str
) -> None:
    request_id = f"req-{uuid.uuid4().hex[:8]}"
    service = _service(session_factory, enable_write_tools=False)

    result = await service.sync_contact(
        ContactSyncInput(full_name="Blocked Write", email=unique_email), request_id=request_id
    )

    assert result.outcome is WriteOutcome.REJECTED
    assert await _contact_count(session_factory, unique_email) == 0
    rows = await _audit_rows(session_factory, request_id)
    assert [row.outcome for row in rows] == ["rejected"]
    assert rows[0].error_code == "write_rejected"
    assert rows[0].dry_run is False
    # Details survive the round trip into JSONB, and carry no personal data.
    assert rows[0].details == {"identified_by": "email", "company_domain": ""}


async def test_a_dry_run_writes_no_row_and_records_a_dry_run(
    session_factory: async_sessionmaker[AsyncSession], unique_email: str
) -> None:
    """The failure mode that would matter most: a 'simulation' that persisted."""
    request_id = f"req-{uuid.uuid4().hex[:8]}"
    service = _service(session_factory, dry_run_writes=True)

    result = await service.sync_contact(
        ContactSyncInput(full_name="Simulated Write", email=unique_email), request_id=request_id
    )

    assert result.outcome is WriteOutcome.DRY_RUN
    assert result.success is False
    assert await _contact_count(session_factory, unique_email) == 0
    rows = await _audit_rows(session_factory, request_id)
    assert [row.outcome for row in rows] == ["dry_run"]
    assert rows[0].dry_run is True


async def test_a_batch_above_the_limit_is_rejected_before_the_database(
    session_factory: async_sessionmaker[AsyncSession], unique_email: str
) -> None:
    request_id = f"req-{uuid.uuid4().hex[:8]}"
    service = _service(session_factory, max_write_batch_size=1)
    repository = PostgresCrmRepository(session_factory)
    contact = ContactSyncInput(full_name="Oversized Batch", email=unique_email).to_contact()

    result = await service._execute_write(
        tool_name="sync_to_crm",
        operation=AuditOperation.UPSERT,
        target_type="contact",
        target_id=None,
        record_count=2,
        details={},
        dry_run_summary="would upsert",
        perform=lambda: repository.upsert_contact(contact),
        request_id=request_id,
    )

    assert result.outcome is WriteOutcome.REJECTED
    assert await _contact_count(session_factory, unique_email) == 0
    assert [row.outcome for row in await _audit_rows(session_factory, request_id)] == ["rejected"]


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------


async def test_syncing_twice_creates_one_row_and_then_reports_unchanged(
    session_factory: async_sessionmaker[AsyncSession], unique_email: str
) -> None:
    request_id = f"req-{uuid.uuid4().hex[:8]}"
    service = _service(session_factory)
    submission = ContactSyncInput(
        full_name="Idempotent Iris",
        email=unique_email,
        title="Director of Demand Generation",
        company_domain="writetest.example",
    )

    first = await service.sync_contact(submission, request_id=request_id)
    second = await service.sync_contact(submission, request_id=request_id)

    assert first.outcome is WriteOutcome.CREATED
    assert second.outcome is WriteOutcome.UNCHANGED
    assert second.record_id == first.record_id
    assert await _contact_count(session_factory, unique_email) == 1
    assert [row.outcome for row in await _audit_rows(session_factory, request_id)] == [
        "created",
        "unchanged",
    ]


async def test_adding_to_a_list_twice_leaves_exactly_one_membership(
    session_factory: async_sessionmaker[AsyncSession], unique_email: str
) -> None:
    request_id = f"req-{uuid.uuid4().hex[:8]}"
    list_name = f"Phase 4 Verification {uuid.uuid4().hex[:6]}"
    service = _service(session_factory)

    created = await service.sync_contact(
        ContactSyncInput(full_name="Listed Lena", email=unique_email), request_id=request_id
    )
    assert created.record_id is not None

    first = await service.save_contact_to_list(created.record_id, list_name, request_id=request_id)
    second = await service.save_contact_to_list(created.record_id, list_name, request_id=request_id)

    assert first.outcome is WriteOutcome.CREATED
    assert second.outcome is WriteOutcome.UNCHANGED

    async with session_factory() as session:
        membership_count = await session.execute(
            select(func.count())
            .select_from(ListMemberModel)
            .join(ListModel, ListMemberModel.list_id == ListModel.id)
            .where(
                ListModel.name == list_name,
                ListMemberModel.contact_id == uuid.UUID(created.record_id),
            )
        )
        assert int(membership_count.scalar_one()) == 1


async def test_a_missing_contact_never_creates_a_list_as_a_side_effect(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    request_id = f"req-{uuid.uuid4().hex[:8]}"
    list_name = f"Never Created {uuid.uuid4().hex[:6]}"
    service = _service(session_factory)

    result = await service.save_contact_to_list(str(uuid.uuid4()), list_name, request_id=request_id)

    assert result.outcome is WriteOutcome.FAILED
    async with session_factory() as session:
        found = await session.execute(select(ListModel).where(ListModel.name == list_name))
        assert found.scalar_one_or_none() is None
    assert [row.outcome for row in await _audit_rows(session_factory, request_id)] == ["failed"]


# --------------------------------------------------------------------------
# Conflict policy (D-015) end to end
# --------------------------------------------------------------------------


async def test_a_synced_value_cannot_overwrite_a_crm_curated_one(
    session_factory: async_sessionmaker[AsyncSession], unique_email: str
) -> None:
    """A verified title must survive a stale enrichment sync."""
    request_id = f"req-{uuid.uuid4().hex[:8]}"
    repository = PostgresCrmRepository(session_factory)
    service = _service(session_factory)

    curated = await repository.upsert_contact(
        Contact(
            full_name="Curated Clara",
            email=unique_email,
            title="VP Sales",
            source=RecordSource.CRM,
        )
    )
    assert curated.outcome is WriteOutcome.CREATED
    assert curated.record_id is not None

    result = await service.sync_contact(
        ContactSyncInput(full_name="Curated Clara", email=unique_email, title="Sales Director"),
        request_id=request_id,
    )

    stored = await repository.get_contact(curated.record_id)
    assert stored is not None
    assert stored.title == "VP Sales"
    assert "title" not in result.changed_fields


async def test_a_field_omitted_from_a_sync_is_left_alone(
    session_factory: async_sessionmaker[AsyncSession], unique_email: str
) -> None:
    request_id = f"req-{uuid.uuid4().hex[:8]}"
    repository = PostgresCrmRepository(session_factory)
    service = _service(session_factory)

    curated = await repository.upsert_contact(
        Contact(
            full_name="Partial Pat",
            email=unique_email,
            title="VP Sales",
            phone="+1-555-0100",
            source=RecordSource.CRM,
        )
    )
    assert curated.record_id is not None

    await service.sync_contact(
        ContactSyncInput(full_name="Partial Pat", email=unique_email), request_id=request_id
    )

    stored = await repository.get_contact(curated.record_id)
    assert stored is not None
    assert stored.phone == "+1-555-0100"
    assert stored.title == "VP Sales"


async def test_a_sync_may_fill_a_field_the_crm_left_empty(
    session_factory: async_sessionmaker[AsyncSession], unique_email: str
) -> None:
    """The policy protects curated values without freezing the record."""
    request_id = f"req-{uuid.uuid4().hex[:8]}"
    repository = PostgresCrmRepository(session_factory)
    service = _service(session_factory)

    curated = await repository.upsert_contact(
        Contact(
            full_name="Sparse Sam", email=unique_email, title="VP Sales", source=RecordSource.CRM
        )
    )
    assert curated.record_id is not None

    result = await service.sync_contact(
        ContactSyncInput(
            full_name="Sparse Sam",
            email=unique_email,
            linkedin_url="https://linkedin.com/in/sparse-sam",
        ),
        request_id=request_id,
    )

    stored = await repository.get_contact(curated.record_id)
    assert stored is not None
    assert stored.linkedin_url == "https://linkedin.com/in/sparse-sam"
    assert stored.title == "VP Sales"
    assert result.outcome is WriteOutcome.UPDATED


# --------------------------------------------------------------------------
# Read / write separation
# --------------------------------------------------------------------------


async def test_a_query_against_the_real_database_writes_no_audit_row(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = _service(session_factory)

    async with session_factory() as session:
        before = int(
            (await session.execute(select(func.count()).select_from(AuditLogModel))).scalar_one()
        )

    await service.query_contacts(ContactFilter(limit=5))
    await service.query_contacts(ContactFilter(title_contains="vp", limit=5))

    async with session_factory() as session:
        after = int(
            (await session.execute(select(func.count()).select_from(AuditLogModel))).scalar_one()
        )

    assert after == before


async def test_a_query_result_carries_no_database_internals(
    session_factory: async_sessionmaker[AsyncSession], unique_email: str
) -> None:
    request_id = f"req-{uuid.uuid4().hex[:8]}"
    service = _service(session_factory)
    await service.sync_contact(
        ContactSyncInput(
            full_name="Queryable Quinn",
            email=unique_email,
            company_domain="writetest.example",
        ),
        request_id=request_id,
    )

    result = await service.query_contacts(ContactFilter(company_domain="writetest.example"))

    assert result.count >= 1
    serialised = result.model_dump()
    for record in serialised["contacts"]:
        assert set(record) <= set(Contact.model_fields)
