"""The write-result envelope must make dishonest success reports impossible."""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from gtm_mcp.domain.results import WriteOutcome, WriteResult


@pytest.mark.unit
@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (WriteOutcome.CREATED, True),
        (WriteOutcome.UPDATED, True),
        (WriteOutcome.UNCHANGED, True),
        (WriteOutcome.DRY_RUN, False),
        (WriteOutcome.REJECTED, False),
        (WriteOutcome.FAILED, False),
    ],
)
def test_success_is_derived_from_outcome(outcome: WriteOutcome, expected: bool) -> None:
    assert WriteResult(outcome=outcome, message="x").success is expected


@pytest.mark.unit
def test_dry_run_is_not_success() -> None:
    """A dry run persisted nothing, so an agent must not treat it as done."""
    result = WriteResult(outcome=WriteOutcome.DRY_RUN, message="persistence skipped")
    assert result.success is False


@pytest.mark.unit
def test_success_cannot_be_set_directly() -> None:
    """The whole point of the computed field: no call site can forge success."""
    with pytest.raises(PydanticValidationError):
        WriteResult(outcome=WriteOutcome.FAILED, message="db down", success=True)  # type: ignore[call-arg]


@pytest.mark.unit
def test_result_is_immutable() -> None:
    result = WriteResult(outcome=WriteOutcome.FAILED, message="db down")
    with pytest.raises(PydanticValidationError):
        result.outcome = WriteOutcome.CREATED  # type: ignore[misc]


@pytest.mark.unit
def test_failed_constructor_reports_failure_and_reason() -> None:
    result = WriteResult.failed("connection reset", record_id="c-1")
    assert result.success is False
    assert result.outcome is WriteOutcome.FAILED
    assert result.record_id == "c-1"
    assert "connection reset" in result.message


@pytest.mark.unit
def test_rejected_constructor_records_no_attempt() -> None:
    result = WriteResult.rejected("write tools are disabled")
    assert result.success is False
    assert result.changed_fields == ()
    assert result.record_id is None
