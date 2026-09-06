"""The write-control sequence, at the layer that owns it.

These tests are about the guardrails themselves: that each one *blocks*
something, that a blocked attempt is still audited, and that nothing can reach
the repository without passing the same checkpoint. Behaviour of the tools that
call this service is covered through a real protocol session in ``tests/mcp``;
behaviour of the repository underneath it is covered against real PostgreSQL in
``tests/integration``.
"""

from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError as PydanticValidationError

from gtm_mcp.audit.events import AuditOperation
from gtm_mcp.domain.models import Contact, ContactFilter, RecordSource
from gtm_mcp.domain.results import WriteOutcome
from gtm_mcp.domain.writes import ContactSyncInput
from gtm_mcp.errors import RepositoryError
from gtm_mcp.services.crm import CrmService
from gtm_mcp.settings import Settings
from tests.fakes import InMemoryCrmRepository, RecordingAuditSink

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

REQUEST_ID = "req-42"


def _settings(**overrides: object) -> Settings:
    """Build test settings with writes enabled unless told otherwise.

    Args:
        **overrides: Settings fields to override.

    Returns:
        A frozen settings instance.
    """
    values: dict[str, object] = {
        "environment": "test",
        "log_format": "console",
        "enable_write_tools": True,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _service(
    repo: InMemoryCrmRepository,
    sink: RecordingAuditSink,
    **setting_overrides: object,
) -> CrmService:
    """Assemble a service over the given doubles.

    Args:
        repo: The repository double.
        sink: The audit sink double.
        **setting_overrides: Settings fields to override.

    Returns:
        The service under test.
    """
    return CrmService(repository=repo, audit_sink=sink, settings=_settings(**setting_overrides))


def _submission(**overrides: object) -> ContactSyncInput:
    """Build a valid contact submission.

    Args:
        **overrides: Fields to override.

    Returns:
        The submission.
    """
    values: dict[str, object] = {
        "full_name": "Elena Rostova",
        "email": "elena@cloudscale.io",
        "title": "Head of Revenue Operations",
        "company_domain": "cloudscale.io",
    }
    values.update(overrides)
    return ContactSyncInput(**values)


# --------------------------------------------------------------------------
# enable_write_tools
# --------------------------------------------------------------------------


async def test_a_disabled_server_refuses_to_upsert_and_touches_no_repository_method() -> None:
    """The master switch must stop the call before the repository sees it."""
    repo, sink = InMemoryCrmRepository(), RecordingAuditSink()
    service = _service(repo, sink, enable_write_tools=False)

    result = await service.sync_contact(_submission(), request_id=REQUEST_ID)

    assert result.outcome is WriteOutcome.REJECTED
    assert result.success is False
    assert repo.write_calls == []
    assert repo.contacts == {}


async def test_a_disabled_server_refuses_list_membership_too() -> None:
    """Every write tool is behind the same switch, not just the interesting one."""
    repo, sink = InMemoryCrmRepository(), RecordingAuditSink()
    contact_id = repo.seed(Contact(full_name="Priya Raman", source=RecordSource.CRM))
    service = _service(repo, sink, enable_write_tools=False)

    result = await service.save_contact_to_list(contact_id, "Q4 Pipeline", request_id=REQUEST_ID)

    assert result.outcome is WriteOutcome.REJECTED
    assert repo.write_calls == []
    assert repo.lists == {}


async def test_a_rejection_is_audited_with_a_code_and_no_dry_run_flag() -> None:
    """A refusal the audit trail cannot see is a refusal nobody can review."""
    repo, sink = InMemoryCrmRepository(), RecordingAuditSink()
    service = _service(repo, sink, enable_write_tools=False)

    result = await service.sync_contact(_submission(), request_id=REQUEST_ID)

    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.outcome is WriteOutcome.REJECTED
    assert event.error_code == "write_rejected"
    assert event.dry_run is False
    assert event.tool_name == "sync_to_crm"
    assert event.request_id == REQUEST_ID
    assert result.audit_id == event.audit_id


async def test_the_refusal_tells_the_model_that_retrying_will_not_help() -> None:
    """An agent that reads 'rejected' and retries has been failed by the message."""
    repo, sink = InMemoryCrmRepository(), RecordingAuditSink()
    service = _service(repo, sink, enable_write_tools=False)

    message = (await service.sync_contact(_submission(), request_id=REQUEST_ID)).message.lower()

    assert "enable_write_tools" in message
    assert "nothing was written" in message
    assert "retrying will fail" in message


async def test_enabling_writes_lets_a_valid_upsert_through() -> None:
    """The switch has to permit as well as forbid, or it proves nothing."""
    repo, sink = InMemoryCrmRepository(), RecordingAuditSink()
    service = _service(repo, sink, enable_write_tools=True)

    result = await service.sync_contact(_submission(), request_id=REQUEST_ID)

    assert result.outcome is WriteOutcome.CREATED
    assert result.success is True
    assert len(repo.contacts) == 1
    assert sink.outcomes == ["created"]


# --------------------------------------------------------------------------
# dry_run_writes
# --------------------------------------------------------------------------


async def test_a_dry_run_validates_but_never_reaches_a_repository_write() -> None:
    repo, sink = InMemoryCrmRepository(), RecordingAuditSink()
    service = _service(repo, sink, dry_run_writes=True)

    result = await service.sync_contact(_submission(), request_id=REQUEST_ID)

    assert result.outcome is WriteOutcome.DRY_RUN
    assert result.success is False
    assert repo.write_calls == []
    assert repo.contacts == {}


async def test_a_dry_run_result_says_plainly_that_nothing_was_written() -> None:
    """`dry_run` is the outcome most likely to be misread as done."""
    repo, sink = InMemoryCrmRepository(), RecordingAuditSink()
    service = _service(repo, sink, dry_run_writes=True)

    message = (await service.sync_contact(_submission(), request_id=REQUEST_ID)).message

    assert "NOTHING WAS WRITTEN" in message
    assert "Do not report this change as done" in message


async def test_a_dry_run_is_audited_and_flagged_as_one() -> None:
    repo, sink = InMemoryCrmRepository(), RecordingAuditSink()
    service = _service(repo, sink, dry_run_writes=True)

    await service.sync_contact(_submission(), request_id=REQUEST_ID)

    assert len(sink.events) == 1
    assert sink.events[0].outcome is WriteOutcome.DRY_RUN
    assert sink.events[0].dry_run is True


async def test_a_dry_run_still_enforces_preconditions() -> None:
    """Simulating a write that could never succeed would be a false reassurance."""
    repo, sink = InMemoryCrmRepository(), RecordingAuditSink()
    service = _service(repo, sink, dry_run_writes=True)

    result = await service.save_contact_to_list(
        "3f1a5c9e-0000-4000-8000-000000000000", "Q4 Pipeline", request_id=REQUEST_ID
    )

    assert result.outcome is WriteOutcome.FAILED
    assert "No CRM contact has identifier" in result.message
    assert sink.events[0].error_code == "not_found"


async def test_turning_dry_run_off_persists_normally() -> None:
    repo, sink = InMemoryCrmRepository(), RecordingAuditSink()
    service = _service(repo, sink, dry_run_writes=False)

    result = await service.sync_contact(_submission(), request_id=REQUEST_ID)

    assert result.outcome is WriteOutcome.CREATED
    assert repo.write_calls == ["upsert_contact"]


async def test_a_disabled_server_rejects_before_it_dry_runs() -> None:
    """Both switches on must report the stronger, more honest refusal."""
    repo, sink = InMemoryCrmRepository(), RecordingAuditSink()
    service = _service(repo, sink, enable_write_tools=False, dry_run_writes=True)

    result = await service.sync_contact(_submission(), request_id=REQUEST_ID)

    assert result.outcome is WriteOutcome.REJECTED
    assert sink.events[0].dry_run is False


# --------------------------------------------------------------------------
# max_write_batch_size
# --------------------------------------------------------------------------


async def test_a_call_at_exactly_the_batch_limit_is_allowed() -> None:
    repo, sink = InMemoryCrmRepository(), RecordingAuditSink()
    service = _service(repo, sink, max_write_batch_size=1)

    result = await service._execute_write(
        tool_name="sync_to_crm",
        operation=AuditOperation.UPSERT,
        target_type="contact",
        target_id=None,
        record_count=1,
        details={},
        dry_run_summary="would upsert",
        perform=lambda: repo.upsert_contact(_submission().to_contact()),
    )

    assert result.outcome is WriteOutcome.CREATED


@pytest.mark.parametrize(("limit", "requested"), [(1, 2), (5, 6), (1, 25)])
async def test_a_call_above_the_batch_limit_is_rejected_and_audited(
    limit: int, requested: int
) -> None:
    repo, sink = InMemoryCrmRepository(), RecordingAuditSink()
    service = _service(repo, sink, max_write_batch_size=limit)

    result = await service._execute_write(
        tool_name="sync_to_crm",
        operation=AuditOperation.UPSERT,
        target_type="contact",
        target_id=None,
        record_count=requested,
        details={},
        dry_run_summary="would upsert",
        perform=lambda: repo.upsert_contact(_submission().to_contact()),
    )

    assert result.outcome is WriteOutcome.REJECTED
    assert str(limit) in result.message
    assert repo.write_calls == []
    assert sink.events[0].outcome is WriteOutcome.REJECTED


async def test_an_empty_batch_is_rejected_rather_than_reported_as_done() -> None:
    repo, sink = InMemoryCrmRepository(), RecordingAuditSink()
    service = _service(repo, sink)

    result = await service._execute_write(
        tool_name="sync_to_crm",
        operation=AuditOperation.UPSERT,
        target_type="contact",
        target_id=None,
        record_count=0,
        details={},
        dry_run_summary="would upsert",
        perform=lambda: repo.upsert_contact(_submission().to_contact()),
    )

    assert result.outcome is WriteOutcome.REJECTED
    assert repo.write_calls == []


@pytest.mark.parametrize("bad_size", [0, -1, 26])
def test_an_impossible_batch_limit_is_refused_at_configuration_time(bad_size: int) -> None:
    """A guardrail set to zero or to a wild number is an operator mistake."""
    with pytest.raises(PydanticValidationError):
        _settings(max_write_batch_size=bad_size)


def test_every_write_method_goes_through_the_single_guarded_path() -> None:
    """The batch limit is only unbypassable if there is one way through.

    A new write method that calls the repository directly would slip past every
    guardrail, and would look perfectly reasonable in review. This asserts the
    property structurally instead of trusting that nobody does it.
    """
    write_methods = [
        name
        for name, member in inspect.getmembers(CrmService, inspect.isfunction)
        if not name.startswith("_") and name not in {"query_contacts"}
    ]
    assert set(write_methods) == {"sync_contact", "save_contact_to_list"}

    for name in write_methods:
        source = inspect.getsource(getattr(CrmService, name))
        assert "self._execute_write(" in source, f"{name} bypasses the guarded write path"

    guarded = inspect.getsource(CrmService._execute_write)
    assert "_guardrail_rejection" in guarded


# --------------------------------------------------------------------------
# Audit coverage of every outcome
# --------------------------------------------------------------------------


async def test_created_updated_and_unchanged_are_each_audited() -> None:
    repo, sink = InMemoryCrmRepository(), RecordingAuditSink()
    service = _service(repo, sink)

    await service.sync_contact(_submission(), request_id=REQUEST_ID)
    await service.sync_contact(_submission(title="VP Revenue Operations"), request_id=REQUEST_ID)
    await service.sync_contact(_submission(title="VP Revenue Operations"), request_id=REQUEST_ID)

    assert sink.outcomes == ["created", "updated", "unchanged"]
    assert sink.events[1].changed_fields == ("title",)


async def test_a_repository_failure_is_audited_and_never_reported_as_success() -> None:
    repo, sink = InMemoryCrmRepository(), RecordingAuditSink()
    repo.failure = RepositoryError("connection reset by peer")
    service = _service(repo, sink)

    with pytest.raises(RepositoryError):
        await service.sync_contact(_submission(), request_id=REQUEST_ID)

    assert sink.outcomes == ["failed"]
    assert sink.events[0].error_code == "repository_error"


async def test_audit_details_are_recorded_but_never_carry_personal_data() -> None:
    """Audit rows are shipped to log aggregators; an email must not ride along."""
    repo, sink = InMemoryCrmRepository(), RecordingAuditSink()
    contact_id = repo.seed(Contact(full_name="Priya Raman", source=RecordSource.CRM))
    service = _service(repo, sink)

    await service.sync_contact(_submission(), request_id=REQUEST_ID)
    await service.save_contact_to_list(contact_id, "Q4 Pipeline", request_id=REQUEST_ID)

    upsert_event, list_event = sink.events
    assert upsert_event.details["company_domain"] == "cloudscale.io"
    assert list_event.details["list_name"] == "Q4 Pipeline"
    serialised = str([event.model_dump() for event in sink.events])
    assert "elena@cloudscale.io" not in serialised


async def test_an_unauditable_write_is_escalated_rather_than_reported_as_done() -> None:
    """Silence about a failed audit would make the whole trail untrustworthy."""
    repo, sink = InMemoryCrmRepository(), RecordingAuditSink()
    sink.failure = RuntimeError("audit table is gone")
    service = _service(repo, sink)

    with pytest.raises(RepositoryError, match="WAS applied but could not be audited"):
        await service.sync_contact(_submission(), request_id=REQUEST_ID)


async def test_an_unauditable_rejection_says_no_change_was_applied() -> None:
    """The escalation must not imply a mutation that never happened."""
    repo, sink = InMemoryCrmRepository(), RecordingAuditSink()
    sink.failure = RuntimeError("audit table is gone")
    service = _service(repo, sink, enable_write_tools=False)

    with pytest.raises(RepositoryError, match="No CRM change was applied"):
        await service.sync_contact(_submission(), request_id=REQUEST_ID)


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------


async def test_a_query_emits_no_audit_event_and_calls_no_write_method() -> None:
    """A read that writes an audit row would pollute the trail of real writes."""
    repo, sink = InMemoryCrmRepository(), RecordingAuditSink()
    repo.seed(
        Contact(full_name="Priya Raman", company_domain="cloudscale.io", source=RecordSource.CRM)
    )
    service = _service(repo, sink)

    result = await service.query_contacts(ContactFilter(company_domain="cloudscale.io"))

    assert result.count == 1
    assert sink.events == []
    assert repo.write_calls == []


async def test_an_empty_query_result_is_an_answer_not_a_failure() -> None:
    repo, sink = InMemoryCrmRepository(), RecordingAuditSink()
    service = _service(repo, sink)

    result = await service.query_contacts(ContactFilter(company_domain="nobody.example"))

    assert result.count == 0
    assert result.contacts == ()
    assert "not a failure" in result.message


async def test_a_full_page_of_results_reports_that_more_may_exist() -> None:
    repo, sink = InMemoryCrmRepository(), RecordingAuditSink()
    for index in range(5):
        repo.seed(Contact(full_name=f"Person {index}", source=RecordSource.CRM))
    service = _service(repo, sink)

    result = await service.query_contacts(ContactFilter(limit=3))

    assert result.count == 3
    assert result.limit_reached is True
    assert "narrow the filters" in result.message
