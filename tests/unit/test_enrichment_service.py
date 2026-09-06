"""The enrichment service: orchestration, cost discipline, and no vendor leakage.

The service is exercised through hand-written fake providers rather than real
adapters, because what is under test here is the layer's own behaviour: that it
normalises before calling, calls once, turns "no record" into a usable result
instead of an error, and lets provider failures through unchanged for the tool
boundary to route.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from gtm_mcp.domain.enrichment import (
    CompanyEnrichment,
    ContactEnrichment,
    EnrichmentProvenance,
    MatchBasis,
)
from gtm_mcp.domain.identifiers import CompanyQuery, ContactQuery
from gtm_mcp.domain.models import Company, Contact, RecordSource
from gtm_mcp.errors import ProviderError, RateLimitError, ValidationError
from gtm_mcp.ports import CompanyEnrichmentProvider, ContactEnrichmentProvider
from gtm_mcp.providers.factory import build_enrichment_providers
from gtm_mcp.services.enrichment import EnrichmentService
from gtm_mcp.settings import Settings

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


def _provenance(live: bool = True) -> EnrichmentProvenance:
    """Build a provenance block for a fake provider result.

    Args:
        live: Whether to mark the record as live provider data.

    Returns:
        The provenance block.
    """
    return EnrichmentProvenance(
        provider="fake",
        live=live,
        matched_on=MatchBasis.DOMAIN,
        retrieved_at=datetime.now(UTC),
    )


class FakeCompanyProvider:
    """A company provider that records its calls and replays a scripted answer."""

    def __init__(
        self,
        result: CompanyEnrichment | None = None,
        error: Exception | None = None,
        *,
        live: bool = True,
    ) -> None:
        """Script the answer this provider gives to every call."""
        self._result = result
        self._error = error
        self._live = live
        self.queries: list[CompanyQuery] = []

    @property
    def name(self) -> str:
        return "fake"

    @property
    def live(self) -> bool:
        return self._live

    async def enrich_company(self, query: CompanyQuery) -> CompanyEnrichment | None:
        self.queries.append(query)
        if self._error is not None:
            raise self._error
        return self._result


class FakeContactProvider:
    """A contact provider that records its calls and replays a scripted answer."""

    def __init__(
        self,
        result: ContactEnrichment | None = None,
        error: Exception | None = None,
        *,
        live: bool = True,
    ) -> None:
        """Script the answer this provider gives to every call."""
        self._result = result
        self._error = error
        self._live = live
        self.queries: list[ContactQuery] = []

    @property
    def name(self) -> str:
        return "fake"

    @property
    def live(self) -> bool:
        return self._live

    async def enrich_contact(self, query: ContactQuery) -> ContactEnrichment | None:
        self.queries.append(query)
        if self._error is not None:
            raise self._error
        return self._result


def _service(
    company: CompanyEnrichmentProvider | None = None,
    contact: ContactEnrichmentProvider | None = None,
) -> EnrichmentService:
    """Build a service over the given fakes.

    Args:
        company: Company provider, defaulting to one that finds nothing.
        contact: Contact provider, defaulting to one that finds nothing.

    Returns:
        The service under test.
    """
    return EnrichmentService(
        company_provider=company or FakeCompanyProvider(),
        contact_provider=contact or FakeContactProvider(),
    )


COMPANY = CompanyEnrichment(
    company=Company(domain="stripe.com", name="Stripe", source=RecordSource.ENRICHMENT),
    provenance=_provenance(),
)
CONTACT = ContactEnrichment(
    contact=Contact(
        full_name="Elena Rostova",
        email="elena@stripe.com",
        company_domain="stripe.com",
        source=RecordSource.ENRICHMENT,
    ),
    provenance=_provenance(),
)


def test_the_fakes_satisfy_the_ports_structurally() -> None:
    """If a fake drifts from the port, these tests stop meaning anything."""
    assert isinstance(FakeCompanyProvider(), CompanyEnrichmentProvider)
    assert isinstance(FakeContactProvider(), ContactEnrichmentProvider)


async def test_the_provider_receives_a_normalised_query_not_the_raw_string() -> None:
    provider = FakeCompanyProvider(COMPANY)
    await _service(company=provider).search_company("HTTPS://WWW.Stripe.com/pricing")

    assert provider.queries[0].domain == "stripe.com"
    assert provider.queries[0].raw == "HTTPS://WWW.Stripe.com/pricing"


async def test_a_match_is_returned_with_its_record_and_provenance() -> None:
    result = await _service(company=FakeCompanyProvider(COMPANY)).search_company("stripe.com")

    assert result.found is True
    assert result.company is not None
    assert result.company.domain == "stripe.com"
    assert result.provenance is not None
    assert result.provenance.provider == "fake"


async def test_one_search_makes_exactly_one_provider_call() -> None:
    """No pre-flight lookup, no second attempt by name: each call may be billed."""
    provider = FakeCompanyProvider(COMPANY)
    await _service(company=provider).search_company("stripe.com")
    assert len(provider.queries) == 1


async def test_a_name_search_is_not_retried_as_a_domain_behind_the_agents_back() -> None:
    provider = FakeCompanyProvider(None)
    result = await _service(company=provider).search_company("Some Unknown Company")

    assert len(provider.queries) == 1
    assert result.found is False


async def test_nothing_found_is_a_result_the_agent_can_read_not_an_exception() -> None:
    result = await _service(company=FakeCompanyProvider(None)).search_company("nowhere.example")

    assert result.found is False
    assert result.company is None
    assert result.provenance is None
    assert "not found" in result.message
    assert "do not retry" in result.message.lower()


async def test_the_result_names_the_query_that_was_actually_executed() -> None:
    result = await _service(company=FakeCompanyProvider(None)).search_company("WWW.Nowhere.example")
    assert result.query.domain == "nowhere.example"


async def test_blank_input_is_refused_before_any_provider_call() -> None:
    provider = FakeCompanyProvider(COMPANY)
    with pytest.raises(ValidationError):
        await _service(company=provider).search_company("   ")
    assert provider.queries == []


async def test_a_provider_failure_propagates_for_the_tool_boundary_to_route() -> None:
    provider = FakeCompanyProvider(error=ProviderError("upstream is down"))
    with pytest.raises(ProviderError):
        await _service(company=provider).search_company("stripe.com")


async def test_a_rate_limit_is_not_converted_into_a_not_found_result() -> None:
    """A quota failure must not be reported as 'no such company'.

    Collapsing the two would teach the agent something false about the world.
    """
    provider = FakeCompanyProvider(error=RateLimitError("quota exhausted"))
    with pytest.raises(RateLimitError):
        await _service(company=provider).search_company("stripe.com")


async def test_offline_results_say_plainly_that_they_are_not_real_data() -> None:
    provider = FakeCompanyProvider(
        CompanyEnrichment(company=COMPANY.company, provenance=_provenance(live=False)),
        live=False,
    )
    result = await _service(company=provider).search_company("stripe.com")

    assert result.found is True
    assert "NOT real-world data" in result.message


async def test_live_results_do_not_carry_the_synthetic_data_warning() -> None:
    result = await _service(company=FakeCompanyProvider(COMPANY)).search_company("stripe.com")
    assert "NOT real-world data" not in result.message
    assert "read-only" in result.message


async def test_a_contact_search_passes_both_the_person_and_the_employer() -> None:
    provider = FakeContactProvider(CONTACT)
    await _service(contact=provider).search_contact("Elena Rostova", "https://stripe.com")

    query = provider.queries[0]
    assert query.full_name == "Elena Rostova"
    assert query.company.domain == "stripe.com"


async def test_a_contact_match_is_returned_with_its_record() -> None:
    result = await _service(contact=FakeContactProvider(CONTACT)).search_contact(
        "Elena Rostova", "stripe.com"
    )
    assert result.found is True
    assert result.contact is not None
    assert result.contact.email == "elena@stripe.com"


async def test_a_missing_contact_explains_what_to_try_instead() -> None:
    result = await _service(contact=FakeContactProvider(None)).search_contact(
        "Nobody Here", "stripe.com"
    )
    assert result.found is False
    assert result.contact is None
    assert "domain" in result.message


async def test_a_contact_search_without_an_employer_is_refused() -> None:
    provider = FakeContactProvider(CONTACT)
    with pytest.raises(ValidationError):
        await _service(contact=provider).search_contact("Elena Rostova", "")
    assert provider.queries == []


async def test_the_service_reports_which_providers_it_is_running_on() -> None:
    service = _service(FakeCompanyProvider(live=True), FakeContactProvider(live=False))
    assert service.company_provider_name == "fake"
    assert service.contact_provider_name == "fake"
    assert service.live is False, "live is only true when every provider is live"


def test_the_default_configuration_needs_no_credential() -> None:
    """A fresh clone must be able to run both search tools."""
    providers = build_enrichment_providers(
        Settings(environment="test", enrichment_provider="sample")
    )
    assert providers.company.live is False
    assert providers.company.name == "sample"


def test_selecting_a_live_provider_without_a_key_fails_loudly() -> None:
    """A live provider without a credential is a startup failure, not a fallback.

    The graceful-degradation alternative would serve sample data under a real
    provider's name, which is the dishonesty this project argues against.
    """
    from gtm_mcp.errors import ConfigurationError

    with pytest.raises(ConfigurationError, match="GTM_ENRICHMENT_API_KEY"):
        build_enrichment_providers(
            Settings(environment="test", enrichment_provider="hunter", enrichment_api_key=None)
        )
