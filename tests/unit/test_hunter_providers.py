"""The Hunter adapters: request shape, normalisation, and error semantics.

Payloads mirror the shapes in Hunter's published API reference. The point of
these tests is not that the adapter runs but that a vendor response becomes a
correct canonical record, and that each documented failure lands in the right
error channel — a rejected credential must not reach the model as something to
retry, and a quota exhaustion must not look like "no such company".
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from gtm_mcp.domain.enrichment import MatchBasis
from gtm_mcp.domain.identifiers import normalize_company_query, normalize_contact_query
from gtm_mcp.domain.models import RecordSource
from gtm_mcp.errors import ConfigurationError, ProviderError, RateLimitError, ValidationError
from gtm_mcp.providers.hunter import HunterCompanyProvider, HunterContactProvider

from .conftest import TEST_API_KEY, ProviderHarness

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


COMPANY_PAYLOAD: dict[str, Any] = {
    "data": {
        "id": "95ca56a8-a019-5c41-881e-293d9ca4741a",
        "name": "Hunter",
        "legalName": "Hunter SAS",
        "domain": "hunter.io",
        "description": "Email finding and verification for outbound teams.",
        "foundedYear": 2015,
        "category": {
            "sector": "Information Technology",
            "industry": "Internet Software & Services",
        },
        "geo": {
            "city": "Wilmington",
            "state": "Delaware",
            "country": "United States",
            "countryCode": "us",
        },
        "metrics": {"employees": "11-50", "employeesCount": 42},
        "linkedin": {"handle": "company/hunterio"},
    },
    "meta": {"params": {"domain": "hunter.io"}},
}

CONTACT_PAYLOAD: dict[str, Any] = {
    "data": {
        "first_name": "Alexis",
        "last_name": "Ohanian",
        "email": "Alexis@Reddit.com",
        "score": 97,
        "domain": "reddit.com",
        "position": "Cofounder",
        "company": "Reddit",
        "twitter": None,
        "linkedin_url": "https://www.linkedin.com/in/alexisohanian",
        "phone_number": None,
        "verification": {"date": "2021-06-14", "status": "valid"},
        "sources": [{"domain": "redditblog.com"}],
    },
    "meta": {"params": {"full_name": "Alexis Ohanian", "domain": "reddit.com"}},
}


def _company(built: ProviderHarness) -> HunterCompanyProvider:
    """Build the company adapter against a scripted provider.

    Args:
        built: The harness.

    Returns:
        The adapter under test.
    """
    return HunterCompanyProvider(built.http, api_key=TEST_API_KEY, base_url="https://api.test/v2")


def _contact(built: ProviderHarness) -> HunterContactProvider:
    """Build the contact adapter against a scripted provider.

    Args:
        built: The harness.

    Returns:
        The adapter under test.
    """
    return HunterContactProvider(built.http, api_key=TEST_API_KEY, base_url="https://api.test/v2")


# ---------------------------------------------------------------------------
# Company enrichment
# ---------------------------------------------------------------------------


async def test_a_company_response_becomes_a_canonical_record(
    harness: Callable[..., ProviderHarness],
) -> None:
    built = harness([httpx2.Response(200, json=COMPANY_PAYLOAD)])
    result = await _company(built).enrich_company(normalize_company_query("https://hunter.io/"))

    assert result is not None
    company = result.company
    assert company.domain == "hunter.io"
    assert company.name == "Hunter"
    assert company.industry == "Internet Software & Services"
    assert company.employee_count == 42
    assert company.city == "Wilmington"
    assert company.state == "Delaware"
    assert company.country == "US", "a lowercase provider code is normalised to ISO alpha-2"
    assert company.linkedin_url == "https://www.linkedin.com/company/hunterio"
    assert company.source is RecordSource.ENRICHMENT
    assert company.retrieved_at is not None


async def test_no_field_is_invented_for_data_the_provider_did_not_return(
    harness: Callable[..., ProviderHarness],
) -> None:
    """Hunter reports no website URL, so the adapter asserts none.

    A synthesised 'https://{domain}' would be our guess wearing the provider's
    name.
    """
    built = harness([httpx2.Response(200, json=COMPANY_PAYLOAD)])
    result = await _company(built).enrich_company(normalize_company_query("hunter.io"))
    assert result is not None
    assert result.company.website is None


async def test_a_company_request_sends_the_key_as_a_header_and_never_in_the_url(
    harness: Callable[..., ProviderHarness],
) -> None:
    built = harness([httpx2.Response(200, json=COMPANY_PAYLOAD)])
    await _company(built).enrich_company(normalize_company_query("HTTPS://WWW.Hunter.io/pricing"))

    call = built.stub.calls[0]
    assert call.url == "https://api.test/v2/companies/find"
    assert call.params == {"domain": "hunter.io"}, "the normalised domain is what gets sent"
    assert call.headers["x-api-key"] == TEST_API_KEY
    assert TEST_API_KEY not in call.url
    assert "api_key" not in call.params


async def test_company_enrichment_makes_exactly_one_provider_call(
    harness: Callable[..., ProviderHarness],
) -> None:
    built = harness([httpx2.Response(200, json=COMPANY_PAYLOAD)], max_retries=2)
    await _company(built).enrich_company(normalize_company_query("hunter.io"))
    assert built.stub.call_count == 1


async def test_an_unknown_company_is_not_found_rather_than_a_failure(
    harness: Callable[..., ProviderHarness],
) -> None:
    built = harness([httpx2.Response(404, json={"errors": [{"id": "not_found", "code": 404}]})])
    assert await _company(built).enrich_company(normalize_company_query("nope.example")) is None


async def test_a_success_carrying_no_data_object_is_treated_as_no_match(
    harness: Callable[..., ProviderHarness],
) -> None:
    built = harness([httpx2.Response(200, json={"data": None, "meta": {}})])
    assert await _company(built).enrich_company(normalize_company_query("nope.example")) is None


async def test_a_name_only_query_is_refused_instead_of_being_guessed_into_a_domain(
    harness: Callable[..., ProviderHarness],
) -> None:
    """Hunter resolves companies by domain only, and a guess is worse than a refusal.

    Inventing 'acme.com' would return confident data about a different company.
    """
    built = harness([])
    with pytest.raises(ValidationError) as excinfo:
        await _company(built).enrich_company(normalize_company_query("Acme Corporation"))

    assert "domain" in str(excinfo.value)
    assert built.stub.call_count == 0, "an unresolvable query must not spend a credit"


async def test_a_rejected_api_key_is_a_configuration_failure_not_a_model_problem(
    harness: Callable[..., ProviderHarness],
) -> None:
    """A rejected key is a configuration failure, routed to MCPError by D-007.

    No rephrasing by the model fixes a bad credential, so telling the model
    about it only invites retries.
    """
    built = harness(
        [httpx2.Response(401, json={"errors": [{"id": "wrong_auth", "details": "Invalid key"}]})],
        max_retries=2,
    )
    with pytest.raises(ConfigurationError, match="GTM_ENRICHMENT_API_KEY"):
        await _company(built).enrich_company(normalize_company_query("hunter.io"))
    assert built.stub.call_count == 1


async def test_a_rate_limit_is_reported_as_a_rate_limit_and_not_retried(
    harness: Callable[..., ProviderHarness],
) -> None:
    built = harness(
        [httpx2.Response(403, json={"errors": [{"details": "Too fast"}]})], max_retries=3
    )
    with pytest.raises(RateLimitError, match="rate limiting"):
        await _company(built).enrich_company(normalize_company_query("hunter.io"))
    assert built.stub.call_count == 1


async def test_an_exhausted_monthly_quota_says_so_rather_than_looking_transient(
    harness: Callable[..., ProviderHarness],
) -> None:
    built = harness([httpx2.Response(429, json={"errors": [{"details": "Quota"}]})], max_retries=3)
    with pytest.raises(RateLimitError, match="monthly credit allowance"):
        await _company(built).enrich_company(normalize_company_query("hunter.io"))
    assert built.stub.call_count == 1


async def test_a_rejected_query_is_a_validation_error_the_model_can_act_on(
    harness: Callable[..., ProviderHarness],
) -> None:
    built = harness(
        [httpx2.Response(400, json={"errors": [{"details": "domain is invalid"}]})],
    )
    with pytest.raises(ValidationError) as excinfo:
        await _company(built).enrich_company(normalize_company_query("hunter.io"))
    assert "domain is invalid" in str(excinfo.value)


async def test_a_legal_block_is_reported_as_do_not_retry(
    harness: Callable[..., ProviderHarness],
) -> None:
    built = harness([httpx2.Response(451, json={"errors": [{"details": "GDPR request"}]})])
    with pytest.raises(ProviderError, match="Do not retry"):
        await _company(built).enrich_company(normalize_company_query("hunter.io"))


async def test_provider_error_text_is_bounded_not_echoed_wholesale(
    harness: Callable[..., ProviderHarness],
) -> None:
    """Provider error text reaches the model, so it is bounded before it does.

    An unbounded echo burns context and lets a vendor payload inject prose into
    a prompt.
    """
    built = harness([httpx2.Response(400, json={"errors": [{"details": "x" * 5000}]})])
    with pytest.raises(ValidationError) as excinfo:
        await _company(built).enrich_company(normalize_company_query("hunter.io"))
    assert len(str(excinfo.value)) < 400


async def test_a_malformed_record_is_discarded_rather_than_partially_trusted(
    harness: Callable[..., ProviderHarness],
) -> None:
    built = harness([httpx2.Response(200, json={"data": {"metrics": {"employeesCount": "many"}}})])
    with pytest.raises(ProviderError, match="unexpected shape"):
        await _company(built).enrich_company(normalize_company_query("hunter.io"))


async def test_a_data_field_of_the_wrong_type_is_rejected(
    harness: Callable[..., ProviderHarness],
) -> None:
    built = harness([httpx2.Response(200, json={"data": "surprise"})])
    with pytest.raises(ProviderError, match="could not interpret"):
        await _company(built).enrich_company(normalize_company_query("hunter.io"))


async def test_an_unusable_country_code_is_dropped_not_passed_through(
    harness: Callable[..., ProviderHarness],
) -> None:
    payload = {"data": {"domain": "hunter.io", "geo": {"countryCode": "United States"}}}
    built = harness([httpx2.Response(200, json=payload)])
    result = await _company(built).enrich_company(normalize_company_query("hunter.io"))
    assert result is not None
    assert result.company.country is None


# ---------------------------------------------------------------------------
# Contact enrichment
# ---------------------------------------------------------------------------


async def test_a_contact_response_becomes_a_canonical_record(
    harness: Callable[..., ProviderHarness],
) -> None:
    built = harness([httpx2.Response(200, json=CONTACT_PAYLOAD)])
    result = await _contact(built).enrich_contact(
        normalize_contact_query("Alexis Ohanian", "reddit.com")
    )

    assert result is not None
    contact = result.contact
    assert contact.full_name == "Alexis Ohanian"
    assert contact.email == "alexis@reddit.com", "emails are lowercased for CRM identity (D-014)"
    assert contact.title == "Cofounder"
    assert contact.company_domain == "reddit.com"
    assert contact.company_name == "Reddit"
    assert contact.linkedin_url == "https://www.linkedin.com/in/alexisohanian"
    assert contact.phone is None
    assert contact.source is RecordSource.ENRICHMENT


async def test_a_provider_score_becomes_a_zero_to_one_confidence(
    harness: Callable[..., ProviderHarness],
) -> None:
    built = harness([httpx2.Response(200, json=CONTACT_PAYLOAD)])
    result = await _contact(built).enrich_contact(
        normalize_contact_query("Alexis Ohanian", "reddit.com")
    )
    assert result is not None
    assert result.contact.confidence == pytest.approx(0.97)


async def test_a_contact_request_prefers_the_employer_domain_over_its_name(
    harness: Callable[..., ProviderHarness],
) -> None:
    built = harness([httpx2.Response(200, json=CONTACT_PAYLOAD)])
    result = await _contact(built).enrich_contact(
        normalize_contact_query("Alexis Ohanian", "https://www.reddit.com")
    )

    call = built.stub.calls[0]
    assert call.url == "https://api.test/v2/email-finder"
    assert call.params["domain"] == "reddit.com"
    assert "company" not in call.params
    assert call.params["full_name"] == "Alexis Ohanian"
    assert result is not None
    assert result.provenance.matched_on is MatchBasis.PERSON_NAME


async def test_a_contact_request_falls_back_to_the_company_name_when_there_is_no_domain(
    harness: Callable[..., ProviderHarness],
) -> None:
    built = harness([httpx2.Response(200, json=CONTACT_PAYLOAD)])
    result = await _contact(built).enrich_contact(
        normalize_contact_query("Alexis Ohanian", "Reddit Inc")
    )

    call = built.stub.calls[0]
    assert call.params["company"] == "Reddit Inc"
    assert "domain" not in call.params
    assert result is not None
    assert result.provenance.matched_on is MatchBasis.COMPANY_NAME


async def test_a_search_duration_is_bounded_so_the_provider_answers_before_we_hang_up(
    harness: Callable[..., ProviderHarness],
) -> None:
    built = harness([httpx2.Response(200, json=CONTACT_PAYLOAD)])
    await _contact(built).enrich_contact(normalize_contact_query("Alexis Ohanian", "reddit.com"))
    assert int(built.stub.calls[0].params["max_duration"]) <= 20


async def test_a_null_email_is_no_match_rather_than_a_contact_without_an_address(
    harness: Callable[..., ProviderHarness],
) -> None:
    """Hunter answers 200 with a null email when it finds nobody.

    Returning a contact here would hand the agent a person record with no way
    to reach them.
    """
    payload = {"data": {"email": None, "score": 0, "first_name": None, "last_name": None}}
    built = harness([httpx2.Response(200, json=payload)])
    result = await _contact(built).enrich_contact(
        normalize_contact_query("Nobody Here", "reddit.com")
    )
    assert result is None


async def test_contact_enrichment_makes_exactly_one_provider_call(
    harness: Callable[..., ProviderHarness],
) -> None:
    built = harness([httpx2.Response(200, json=CONTACT_PAYLOAD)], max_retries=2)
    await _contact(built).enrich_contact(normalize_contact_query("Alexis Ohanian", "reddit.com"))
    assert built.stub.call_count == 1


async def test_a_contact_rate_limit_is_not_retried(
    harness: Callable[..., ProviderHarness],
) -> None:
    built = harness([httpx2.Response(429, json={"errors": [{"details": "Quota"}]})], max_retries=3)
    with pytest.raises(RateLimitError):
        await _contact(built).enrich_contact(
            normalize_contact_query("Alexis Ohanian", "reddit.com")
        )
    assert built.stub.call_count == 1


async def test_a_contact_authentication_failure_is_a_configuration_error(
    harness: Callable[..., ProviderHarness],
) -> None:
    built = harness([httpx2.Response(401, json={"errors": [{"details": "Invalid key"}]})])
    with pytest.raises(ConfigurationError):
        await _contact(built).enrich_contact(
            normalize_contact_query("Alexis Ohanian", "reddit.com")
        )


async def test_a_transient_contact_failure_is_retried_then_reported(
    harness: Callable[..., ProviderHarness],
) -> None:
    built = harness([httpx2.Response(502, json={})] * 2, max_retries=1)
    with pytest.raises(ProviderError):
        await _contact(built).enrich_contact(
            normalize_contact_query("Alexis Ohanian", "reddit.com")
        )
    assert built.stub.call_count == 2


async def test_a_contact_timeout_is_reported_without_leaking_a_stack_trace(
    harness: Callable[..., ProviderHarness],
) -> None:
    request = httpx2.Request("GET", "https://api.test/v2/email-finder")
    built = harness([httpx2.ReadTimeout("timed out", request=request)], max_retries=0)
    with pytest.raises(ProviderError) as excinfo:
        await _contact(built).enrich_contact(
            normalize_contact_query("Alexis Ohanian", "reddit.com")
        )
    message = str(excinfo.value)
    assert "Traceback" not in message
    assert "api.test" not in message


async def test_a_malformed_contact_record_is_discarded(
    harness: Callable[..., ProviderHarness],
) -> None:
    built = harness([httpx2.Response(200, json={"data": {"email": "a@b.com", "score": "high"}})])
    with pytest.raises(ProviderError, match="unexpected shape"):
        await _contact(built).enrich_contact(
            normalize_contact_query("Alexis Ohanian", "reddit.com")
        )


async def test_the_record_reports_the_person_the_provider_returned(
    harness: Callable[..., ProviderHarness],
) -> None:
    """A returned record describes whoever the provider matched, not who was asked for.

    Found by validating against the live endpoint: the response can name a
    different person, and a contact whose full_name contradicts its own first
    and last name is a worse artefact than an honest mismatch.
    """
    payload = {
        "data": {
            "email": "richard@piedpiper.com",
            "score": 99,
            "first_name": "Richard",
            "last_name": "Hendricks",
            "domain": "piedpiper.com",
            "company": "Pied Piper",
        }
    }
    built = harness([httpx2.Response(200, json=payload)])
    result = await _contact(built).enrich_contact(
        normalize_contact_query("Alexis Ohanian", "piedpiper.com")
    )

    assert result is not None
    assert result.contact.full_name == "Richard Hendricks"
    assert result.contact.first_name == "Richard"
    assert result.contact.last_name == "Hendricks"


async def test_the_query_name_is_kept_when_the_provider_returns_no_name_parts(
    harness: Callable[..., ProviderHarness],
) -> None:
    payload = {"data": {"email": "someone@piedpiper.com", "score": 80}}
    built = harness([httpx2.Response(200, json=payload)])
    result = await _contact(built).enrich_contact(
        normalize_contact_query("Alexis Ohanian", "piedpiper.com")
    )

    assert result is not None
    assert result.contact.full_name == "Alexis Ohanian"
