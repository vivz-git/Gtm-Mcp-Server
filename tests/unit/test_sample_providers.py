"""The offline sample adapters.

They back the default configuration, so their behaviour is what a fresh clone
demonstrates. Two properties matter beyond "it returns a record": every result
must be marked as non-live so it cannot pass for real data, and a person must
never be matched on name alone.
"""

from __future__ import annotations

import pytest

from gtm_mcp.domain.enrichment import MatchBasis
from gtm_mcp.domain.identifiers import normalize_company_query, normalize_contact_query
from gtm_mcp.domain.models import RecordSource
from gtm_mcp.providers.sample import SampleCompanyProvider, SampleContactProvider

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


async def test_a_domain_resolves_to_the_canonical_company_record() -> None:
    result = await SampleCompanyProvider().enrich_company(
        normalize_company_query("https://WWW.CloudScale.io/pricing")
    )

    assert result is not None
    assert result.company.domain == "cloudscale.io"
    assert result.company.name == "CloudScale Systems"
    assert result.company.employee_count == 450
    assert result.company.country == "US"
    assert result.company.source is RecordSource.ENRICHMENT
    assert result.provenance.matched_on is MatchBasis.DOMAIN


async def test_a_company_name_resolves_despite_punctuation_and_a_legal_suffix() -> None:
    result = await SampleCompanyProvider().enrich_company(
        normalize_company_query("cloudscale systems inc")
    )
    assert result is not None
    assert result.company.domain == "cloudscale.io"
    assert result.provenance.matched_on is MatchBasis.COMPANY_NAME


async def test_an_unknown_company_returns_no_match_rather_than_an_error() -> None:
    assert (
        await SampleCompanyProvider().enrich_company(normalize_company_query("unknown.example"))
        is None
    )


async def test_every_sample_record_is_flagged_as_not_live() -> None:
    """The whole safety story of shipping a demo dataset rests on this flag."""
    company = await SampleCompanyProvider().enrich_company(normalize_company_query("cloudscale.io"))
    contact = await SampleContactProvider().enrich_contact(
        normalize_contact_query("Elena Rostova", "cloudscale.io")
    )

    assert company is not None
    assert contact is not None
    assert company.provenance.live is False
    assert contact.provenance.live is False
    assert SampleCompanyProvider().live is False
    assert SampleContactProvider().live is False


async def test_a_person_resolves_from_a_name_and_an_employer_domain() -> None:
    result = await SampleContactProvider().enrich_contact(
        normalize_contact_query("elena rostova", "CloudScale.io")
    )

    assert result is not None
    assert result.contact.email == "elena.rostova@cloudscale.io"
    assert result.contact.title == "VP of Engineering"
    assert result.contact.phone == "+1-415-555-0142"
    assert result.contact.city == "San Francisco"
    assert result.contact.confidence == pytest.approx(0.97)


async def test_a_person_resolves_from_a_name_and_an_employer_name() -> None:
    result = await SampleContactProvider().enrich_contact(
        normalize_contact_query("Elena Rostova", "CloudScale Systems")
    )
    assert result is not None
    assert result.provenance.matched_on is MatchBasis.COMPANY_NAME


async def test_a_person_is_never_matched_on_name_alone() -> None:
    """The same name at the wrong company is a different human being (D-014)."""
    result = await SampleContactProvider().enrich_contact(
        normalize_contact_query("Elena Rostova", "apexfintech.com")
    )
    assert result is None


async def test_an_unknown_person_returns_no_match() -> None:
    assert (
        await SampleContactProvider().enrich_contact(
            normalize_contact_query("Nobody Here", "cloudscale.io")
        )
        is None
    )
