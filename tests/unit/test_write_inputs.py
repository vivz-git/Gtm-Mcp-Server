"""The contract an agent submits to ``sync_to_crm``.

The interesting property here is not that valid input is accepted; it is that
the input model cannot be used to escalate what a write is allowed to do.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from gtm_mcp.domain.models import RecordSource
from gtm_mcp.domain.writes import ContactSyncInput

pytestmark = [pytest.mark.unit]


def test_a_submitted_contact_is_always_marked_as_enrichment_provenance() -> None:
    """An agent must not be able to declare its own values CRM-authoritative.

    Provenance decides who wins a merge conflict (D-015). If ``source`` were an
    input field, an agent could stamp ``crm`` on a guess and overwrite a value a
    human verified.
    """
    assert "source" not in ContactSyncInput.model_fields

    contact = ContactSyncInput(full_name="Elena Rostova").to_contact()

    assert contact.source is RecordSource.ENRICHMENT


def test_unknown_fields_are_rejected_rather_than_silently_dropped() -> None:
    with pytest.raises(PydanticValidationError):
        ContactSyncInput(full_name="Elena Rostova", source="crm")  # type: ignore[call-arg]


def test_a_domain_is_lowercased_on_the_way_into_the_canonical_record() -> None:
    contact = ContactSyncInput(
        full_name="Elena Rostova", company_domain="CloudScale.IO"
    ).to_contact()

    assert contact.company_domain == "cloudscale.io"


@pytest.mark.parametrize("bad_email", ["not-an-email", "@cloudscale.io", "elena@", "elena@io"])
def test_text_that_is_not_an_address_is_refused_as_an_email(bad_email: str) -> None:
    """Email is the natural identity: junk here creates a record nothing can find again."""
    with pytest.raises(PydanticValidationError, match="not an email address"):
        ContactSyncInput(full_name="Elena Rostova", email=bad_email)


def test_an_address_is_normalised_to_lower_case() -> None:
    contact = ContactSyncInput(full_name="Elena", email=" Elena@CloudScale.IO ").to_contact()

    assert contact.email == "elena@cloudscale.io"


def test_an_omitted_email_is_allowed_because_not_every_lead_has_one() -> None:
    assert ContactSyncInput(full_name="Elena Rostova").to_contact().email is None


@pytest.mark.parametrize("bad_country", ["USA", "United States", "U", "12"])
def test_a_country_that_is_not_an_iso_code_is_refused(bad_country: str) -> None:
    """The column is two characters wide; a country name would be truncated silently."""
    with pytest.raises(PydanticValidationError):
        ContactSyncInput(full_name="Elena Rostova", country=bad_country)


def test_a_country_code_is_uppercased() -> None:
    assert ContactSyncInput(full_name="Elena", country="de").to_contact().country == "DE"


def test_a_name_longer_than_the_column_fails_validation_not_the_database() -> None:
    """A model reads a schema error and corrects it; it cannot read a database error."""
    with pytest.raises(PydanticValidationError):
        ContactSyncInput(full_name="x" * 256)


def test_an_empty_name_is_refused() -> None:
    with pytest.raises(PydanticValidationError):
        ContactSyncInput(full_name="   ")


@pytest.mark.parametrize("bad_confidence", [-0.1, 1.1])
def test_confidence_stays_within_zero_to_one(bad_confidence: float) -> None:
    with pytest.raises(PydanticValidationError):
        ContactSyncInput(full_name="Elena Rostova", confidence=bad_confidence)


def test_the_submission_is_frozen() -> None:
    """Nothing between validation and persistence may edit what was submitted."""
    submission = ContactSyncInput(full_name="Elena Rostova")
    with pytest.raises(PydanticValidationError):
        submission.full_name = "Someone Else"  # type: ignore[misc]
