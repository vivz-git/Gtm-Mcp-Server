"""Hunter.io enrichment adapters.

The selected live provider for both capabilities (DECISIONS.md D-017). Two
adapters, one per port, sharing an authenticated HTTP client:

* ``HunterCompanyProvider``  → ``GET /v2/companies/find``  (0.2 credits, domain only)
* ``HunterContactProvider``  → ``GET /v2/email-finder``    (1 credit, name + employer)

Everything Hunter-specific is confined to this module: its parameter names, its
status-code semantics (a ``403`` is a rate limit and a ``429`` is a monthly
quota, which is the reverse of most APIs), its ``errors[]`` body shape, and the
fact that it reports a LinkedIn *handle* rather than a URL.

Responses are treated as untrusted input: the payload is validated into private
Pydantic models before a single value is read, so a provider that changes shape
produces a clean ``ProviderError`` rather than a ``KeyError`` halfway through a
translation.

Fields Hunter does not return are left unset. Nothing here derives a value the
provider did not assert — no ``https://{domain}`` stand-in for a website it did
not report, and no invented confidence.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from gtm_mcp.domain.enrichment import (
    CompanyEnrichment,
    ContactEnrichment,
    EnrichmentProvenance,
    MatchBasis,
)
from gtm_mcp.domain.identifiers import CompanyQuery, ContactQuery
from gtm_mcp.domain.models import Company, Contact, RecordSource
from gtm_mcp.errors import ConfigurationError, ProviderError, RateLimitError, ValidationError
from gtm_mcp.providers.http import EnrichmentHttpClient, ProviderResponse

#: Public API root. Overridable per adapter so tests can point at a stub host.
HUNTER_BASE_URL: Final = "https://api.hunter.io/v2"

#: Stable provider identifier, stored on every record this adapter returns.
PROVIDER_NAME: Final = "hunter"

#: How long Hunter may spend searching before giving up, in seconds (3-20).
#: Kept below ``enrichment_timeout_seconds`` so the provider returns a real
#: answer rather than our client abandoning a request Hunter has already billed.
MAX_SEARCH_DURATION_SECONDS: Final = 10

#: Upper bound on how much provider error text is repeated back to the agent.
_MAX_DETAIL_CHARS: Final = 200


# ---------------------------------------------------------------------------
# Provider payload models. Private: nothing outside this module sees these.
# ---------------------------------------------------------------------------


class _PayloadModel(BaseModel):
    """Base for provider payloads: tolerate new vendor fields, never adopt them."""

    model_config = ConfigDict(extra="ignore", populate_by_name=False)


class _HunterCategory(_PayloadModel):
    """The ``data.category`` object of a company response."""

    industry: str | None = None
    sector: str | None = None


class _HunterGeo(_PayloadModel):
    """The ``data.geo`` object of a company response."""

    city: str | None = None
    state: str | None = None
    country_code: str | None = Field(default=None, alias="countryCode")


class _HunterMetrics(_PayloadModel):
    """The ``data.metrics`` object of a company response."""

    employees_count: int | None = Field(default=None, alias="employeesCount")


class _HunterLinkedIn(_PayloadModel):
    """The ``data.linkedin`` object; Hunter reports a handle, not a URL."""

    handle: str | None = None


class _HunterCompanyData(_PayloadModel):
    """The ``data`` object of ``GET /v2/companies/find``."""

    id: str | None = None
    name: str | None = None
    legal_name: str | None = Field(default=None, alias="legalName")
    domain: str | None = None
    description: str | None = None
    category: _HunterCategory = Field(default_factory=_HunterCategory)
    geo: _HunterGeo = Field(default_factory=_HunterGeo)
    metrics: _HunterMetrics = Field(default_factory=_HunterMetrics)
    linkedin: _HunterLinkedIn = Field(default_factory=_HunterLinkedIn)


class _HunterVerification(_PayloadModel):
    """The ``data.verification`` object of an email-finder response."""

    status: str | None = None


class _HunterEmailData(_PayloadModel):
    """The ``data`` object of ``GET /v2/email-finder``."""

    email: str | None = None
    score: int | None = None
    domain: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    position: str | None = None
    company: str | None = None
    linkedin_url: str | None = None
    phone_number: str | None = None
    verification: _HunterVerification = Field(default_factory=_HunterVerification)


# ---------------------------------------------------------------------------
# Shared translation helpers
# ---------------------------------------------------------------------------


def _error_detail(payload: Any) -> str | None:
    """Extract Hunter's human-readable error text, bounded in length.

    Args:
        payload: The decoded error body.

    Returns:
        The provider's explanation, truncated, or ``None`` if the body did not
        carry one in the documented shape.
    """
    if not isinstance(payload, dict):
        return None
    errors = payload.get("errors")
    if not isinstance(errors, list) or not errors:
        return None
    first = errors[0]
    if not isinstance(first, dict):
        return None
    details = first.get("details")
    if not isinstance(details, str) or not details.strip():
        return None
    return details.strip()[:_MAX_DETAIL_CHARS]


def _raise_for_status(response: ProviderResponse, *, capability: str) -> None:
    """Translate a Hunter error status into the appropriate domain error.

    ``404`` is deliberately not handled here: for the company endpoint it means
    "no such company", which is a result, not a failure.

    Args:
        response: The decoded provider response.
        capability: What was being looked up, for the message.

    Raises:
        ValidationError: Hunter rejected the query parameters (400).
        ConfigurationError: Hunter rejected the API key (401). Not surfaced to
            the model: no retry or rephrasing can fix a server credential.
        RateLimitError: A rate limit (403) or the monthly quota (429).
        ProviderError: A legal block (451) or any other unexpected status.
    """
    status = response.status_code
    if status < 400 or status == 404:
        return

    detail = _error_detail(response.payload)
    suffix = f" Provider said: {detail}" if detail else ""

    if status == 400:
        raise ValidationError(
            f"The enrichment provider rejected the {capability} query as invalid.{suffix}",
            provider=PROVIDER_NAME,
        )
    if status == 401:
        raise ConfigurationError(
            "The enrichment provider rejected this server's API key. Set a valid "
            "GTM_ENRICHMENT_API_KEY and restart the server.",
            provider=PROVIDER_NAME,
        )
    if status == 403:
        raise RateLimitError(
            "The enrichment provider is rate limiting this server. Wait a few seconds "
            "before trying again; the lookup was not performed and no credits were used.",
            provider=PROVIDER_NAME,
        )
    if status == 429:
        raise RateLimitError(
            "The enrichment provider's monthly credit allowance is exhausted. Further "
            "enrichment will keep failing until the allowance resets or the plan is upgraded.",
            provider=PROVIDER_NAME,
        )
    if status == 451:
        raise ProviderError(
            f"The enrichment provider refused this {capability} lookup for legal reasons "
            f"(such as a data-protection request). Do not retry it.{suffix}",
            provider=PROVIDER_NAME,
        )
    raise ProviderError(
        f"The enrichment provider returned an unexpected status {status} for the "
        f"{capability} lookup. No data was retrieved.{suffix}",
        provider=PROVIDER_NAME,
        status_code=status,
    )


def _data_object(response: ProviderResponse) -> dict[str, Any] | None:
    """Return the ``data`` object of a successful response, if present.

    Args:
        response: The decoded provider response.

    Returns:
        The ``data`` mapping, or ``None`` when the provider reported no match.

    Raises:
        ProviderError: ``data`` was present but was not an object.
    """
    data = response.payload.get("data")
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ProviderError(
            "The enrichment provider returned a response this server could not interpret. "
            "No data was retrieved.",
            provider=PROVIDER_NAME,
        )
    return data


def _validate[M: BaseModel](model: type[M], data: dict[str, Any]) -> M:
    """Validate a provider payload into a private model.

    Args:
        model: The payload model to validate against.
        data: The raw ``data`` mapping.

    Returns:
        The validated payload model.

    Raises:
        ProviderError: The payload did not match the documented shape.
    """
    try:
        return model.model_validate(data)
    except PydanticValidationError as exc:
        raise ProviderError(
            "The enrichment provider returned a record in an unexpected shape, so it was "
            "discarded rather than partially trusted. No data was retrieved.",
            provider=PROVIDER_NAME,
        ) from exc


def _country_code(value: str | None) -> str | None:
    """Normalise a country code to ISO 3166-1 alpha-2, or drop it.

    Args:
        value: The provider's country code.

    Returns:
        The two-letter uppercase code, or ``None`` if it is not one.
    """
    if value is None:
        return None
    code = value.strip().upper()
    return code if len(code) == 2 and code.isalpha() else None


def _linkedin_url(handle: str | None) -> str | None:
    """Expand Hunter's LinkedIn handle (``company/hunterio``) into a URL.

    Args:
        handle: The provider's handle.

    Returns:
        The canonical LinkedIn URL, or ``None`` when no handle was reported.
    """
    if handle is None or not handle.strip():
        return None
    return f"https://www.linkedin.com/{handle.strip().strip('/')}"


def _blank_to_none(value: str | None) -> str | None:
    """Collapse a provider's empty string to ``None``.

    Args:
        value: A provider-supplied string.

    Returns:
        The trimmed value, or ``None`` when it carries nothing.
    """
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


class _HunterAdapter:
    """Shared construction for the two Hunter adapters."""

    def __init__(
        self,
        http: EnrichmentHttpClient,
        *,
        api_key: str,
        base_url: str = HUNTER_BASE_URL,
    ) -> None:
        """Initialise the adapter.

        Args:
            http: The retry-bounded HTTP client.
            api_key: The Hunter API key. Sent as a header, never as a query
                parameter, so it cannot appear in a URL, a log or a proxy trace.
            base_url: API root, overridable for testing.
        """
        self._http = http
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    @property
    def name(self) -> str:
        """Stable provider identifier."""
        return PROVIDER_NAME

    @property
    def live(self) -> bool:
        """This adapter calls the real Hunter API."""
        return True

    @property
    def _headers(self) -> dict[str, str]:
        """Request headers, including authentication.

        Built per request and never logged.
        """
        return {"X-API-KEY": self._api_key, "Accept": "application/json"}


class HunterCompanyProvider(_HunterAdapter):
    """Firmographic enrichment via Hunter's company endpoint.

    Hunter resolves companies by domain only. A name-only query is therefore
    refused with an actionable ``ValidationError`` rather than guessed at: a
    fabricated ``{name}.com`` would return confident data about a different
    company, which is a worse failure than asking the agent for a domain.
    """

    async def enrich_company(self, query: CompanyQuery) -> CompanyEnrichment | None:
        """Look up firmographic data for a company.

        Args:
            query: The normalised company identifier.

        Returns:
            The enriched company, or ``None`` when Hunter has no record.

        Raises:
            ValidationError: The query carries no domain.
            RateLimitError: Rate limit or monthly quota reached.
            ProviderError: The provider failed or returned an unusable response.
            ConfigurationError: The API key was rejected.
        """
        if query.domain is None:
            raise ValidationError(
                f"This server's company enrichment provider resolves companies by web "
                f"domain, and '{query.raw}' is not one. Call again with the company's "
                f"domain, for example 'stripe.com'.",
                provider=PROVIDER_NAME,
            )

        response = await self._http.get_json(
            f"{self._base_url}/companies/find",
            params={"domain": query.domain},
            headers=self._headers,
        )
        _raise_for_status(response, capability="company")
        if response.status_code == 404:
            return None

        data = _data_object(response)
        if data is None:
            return None
        payload: _HunterCompanyData = _validate(_HunterCompanyData, data)

        company = Company(
            domain=_blank_to_none(payload.domain) or query.domain,
            name=_blank_to_none(payload.name) or _blank_to_none(payload.legal_name),
            description=_blank_to_none(payload.description),
            industry=_blank_to_none(payload.category.industry)
            or _blank_to_none(payload.category.sector),
            employee_count=payload.metrics.employees_count,
            city=_blank_to_none(payload.geo.city),
            state=_blank_to_none(payload.geo.state),
            country=_country_code(payload.geo.country_code),
            # Hunter's company payload carries no website URL, so none is
            # reported. A synthesised https://{domain} would be our assertion
            # dressed up as the provider's.
            website=None,
            linkedin_url=_linkedin_url(payload.linkedin.handle),
            source=RecordSource.ENRICHMENT,
            retrieved_at=datetime.now(UTC),
        )
        return CompanyEnrichment(
            company=company,
            provenance=EnrichmentProvenance(
                provider=self.name,
                live=self.live,
                matched_on=MatchBasis.DOMAIN,
                retrieved_at=company.retrieved_at or datetime.now(UTC),
                provider_record_id=_blank_to_none(payload.id),
            ),
        )


class HunterContactProvider(_HunterAdapter):
    """People enrichment via Hunter's email-finder endpoint.

    Accepts either an employer domain or an employer name, which is what makes
    ``search_contact(name, company)`` work without forcing the agent to resolve
    a domain first. A domain is passed when we have one because it matches far
    more precisely than a name.
    """

    async def enrich_contact(self, query: ContactQuery) -> ContactEnrichment | None:
        """Look up a person at a company.

        Args:
            query: The normalised person and employer identifiers.

        Returns:
            The enriched contact, or ``None`` when Hunter finds no address.

        Raises:
            RateLimitError: Rate limit or monthly quota reached.
            ProviderError: The provider failed or returned an unusable response.
            ConfigurationError: The API key was rejected.
        """
        params: dict[str, str] = {
            "full_name": query.full_name,
            "max_duration": str(MAX_SEARCH_DURATION_SECONDS),
        }
        if query.company.domain is not None:
            params["domain"] = query.company.domain
            matched_on = MatchBasis.PERSON_NAME
        else:
            params["company"] = query.company.name or query.company.raw
            matched_on = MatchBasis.COMPANY_NAME

        response = await self._http.get_json(
            f"{self._base_url}/email-finder",
            params=params,
            headers=self._headers,
        )
        _raise_for_status(response, capability="contact")
        if response.status_code == 404:
            return None

        data = _data_object(response)
        if data is None:
            return None
        payload: _HunterEmailData = _validate(_HunterEmailData, data)

        email = _blank_to_none(payload.email)
        if email is None:
            # A 200 with a null email is Hunter's "no match", not a failure.
            return None

        score = payload.score
        first_name = _blank_to_none(payload.first_name) or query.first_name
        last_name = _blank_to_none(payload.last_name) or query.last_name
        contact = Contact(
            # The provider's own name parts win over the query's when it returns
            # both. Verified against the live endpoint: a response can describe
            # a different person from the one asked about, and a record whose
            # full_name disagrees with its own first and last name is worse than
            # one that simply reports what the provider actually said.
            full_name=f"{first_name} {last_name}"
            if payload.first_name and payload.last_name
            else query.full_name,
            first_name=first_name,
            last_name=last_name,
            title=_blank_to_none(payload.position),
            company_domain=_blank_to_none(payload.domain) or query.company.domain,
            company_name=_blank_to_none(payload.company) or query.company.name,
            email=email.lower(),
            phone=_blank_to_none(payload.phone_number),
            linkedin_url=_blank_to_none(payload.linkedin_url),
            source=RecordSource.ENRICHMENT,
            confidence=min(max(score, 0), 100) / 100 if score is not None else 1.0,
            retrieved_at=datetime.now(UTC),
        )
        return ContactEnrichment(
            contact=contact,
            provenance=EnrichmentProvenance(
                provider=self.name,
                live=self.live,
                matched_on=matched_on,
                retrieved_at=contact.retrieved_at or datetime.now(UTC),
            ),
        )
