"""Enrichment orchestration.

Sits between the tool layer and the provider ports and owns the logic neither of
them should: normalising the agent's free text into a canonical query, calling
the provider **exactly once**, and turning "the provider has no record" into a
result the model can act on rather than an error it will retry.

Cost discipline is a property of this layer, and it is deliberately dull:

* One tool call performs at most one provider request. No pre-flight lookup, no
  "try the domain then try the name", no enriching the company as a side effect
  of enriching a contact.
* There is no automatic fallback to a second vendor. A configured provider that
  fails, fails — silently paying a second vendor to answer the same question is
  a decision for an operator, not a default.
* Retries live in the HTTP client, are bounded, and never cover a rejection.

This module contains no vendor names and no ``if provider ==`` branch; that is
the boundary it exists to hold.
"""

from __future__ import annotations

from gtm_mcp.domain.enrichment import CompanyLookup, ContactLookup
from gtm_mcp.domain.identifiers import normalize_company_query, normalize_contact_query
from gtm_mcp.logging_setup import get_logger
from gtm_mcp.ports import CompanyEnrichmentProvider, ContactEnrichmentProvider

_log = get_logger(__name__)


class EnrichmentService:
    """Coordinates company and contact enrichment behind the provider ports."""

    def __init__(
        self,
        *,
        company_provider: CompanyEnrichmentProvider,
        contact_provider: ContactEnrichmentProvider,
    ) -> None:
        """Initialise the service.

        Args:
            company_provider: Adapter used for firmographic lookups.
            contact_provider: Adapter used for people lookups.
        """
        self._company_provider = company_provider
        self._contact_provider = contact_provider

    @property
    def company_provider_name(self) -> str:
        """Identifier of the configured company provider."""
        return self._company_provider.name

    @property
    def contact_provider_name(self) -> str:
        """Identifier of the configured contact provider."""
        return self._contact_provider.name

    @property
    def live(self) -> bool:
        """Whether both configured providers query real external services."""
        return self._company_provider.live and self._contact_provider.live

    async def search_company(self, domain_or_name: str) -> CompanyLookup:
        """Enrich a company from a domain, URL or name.

        Args:
            domain_or_name: The identifier as the agent supplied it.

        Returns:
            The lookup result, whose ``found`` flag distinguishes a match from
            a provider that simply has no record.

        Raises:
            ValidationError: The input is blank, or names a company in a way the
                configured provider cannot resolve.
            RateLimitError: The provider's quota or rate limit is exhausted.
            ProviderError: The provider failed or returned an unusable response.
            ConfigurationError: The provider rejected this server's credential.
        """
        query = normalize_company_query(domain_or_name)
        _log.info(
            "company_enrichment_requested",
            provider=self._company_provider.name,
            matched_by="domain" if query.domain else "name",
        )

        enrichment = await self._company_provider.enrich_company(query)
        if enrichment is None:
            return CompanyLookup(
                query=query,
                message=(
                    f"The {self._company_provider.name} enrichment provider has no record for "
                    f"'{query.describe}'. This is a definitive 'not found', not a failure: "
                    f"do not retry the same identifier. If you searched by name, retry with "
                    f"the company's web domain, which matches far more reliably."
                ),
            )

        return CompanyLookup(
            query=query,
            company=enrichment.company,
            provenance=enrichment.provenance,
            message=self._matched_message(
                f"company '{enrichment.company.name or enrichment.company.domain}'",
                live=enrichment.provenance.live,
                provider=enrichment.provenance.provider,
            ),
        )

    async def search_contact(self, name: str, company: str) -> ContactLookup:
        """Enrich a person from their name and employer.

        Args:
            name: The person's full name.
            company: The employer's domain or name.

        Returns:
            The lookup result, whose ``found`` flag distinguishes a match from
            a provider that simply has no record.

        Raises:
            ValidationError: Either input is blank, or the employer is named in
                a way the configured provider cannot resolve.
            RateLimitError: The provider's quota or rate limit is exhausted.
            ProviderError: The provider failed or returned an unusable response.
            ConfigurationError: The provider rejected this server's credential.
        """
        query = normalize_contact_query(name, company)
        _log.info(
            "contact_enrichment_requested",
            provider=self._contact_provider.name,
            matched_by="domain" if query.company.domain else "name",
        )

        enrichment = await self._contact_provider.enrich_contact(query)
        if enrichment is None:
            return ContactLookup(
                query=query,
                message=(
                    f"The {self._contact_provider.name} enrichment provider has no record for "
                    f"a person by that name at '{query.company.describe}'. This is a definitive "
                    f"'not found', not a failure: do not retry the same pair. Check the "
                    f"spelling of the name, or supply the employer's web domain instead of "
                    f"its name."
                ),
            )

        return ContactLookup(
            query=query,
            contact=enrichment.contact,
            provenance=enrichment.provenance,
            message=self._matched_message(
                f"contact '{enrichment.contact.full_name}'",
                live=enrichment.provenance.live,
                provider=enrichment.provenance.provider,
            ),
        )

    @staticmethod
    def _matched_message(subject: str, *, live: bool, provider: str) -> str:
        """Compose the success message, stating plainly when data is not live.

        Args:
            subject: What was matched, already phrased.
            live: Whether the provider queried a real external service.
            provider: The provider identifier.

        Returns:
            A model-readable summary.
        """
        if live:
            return (
                f"Matched {subject} via the {provider} enrichment provider. "
                f"Nothing was written to the CRM; this is a read-only lookup."
            )
        return (
            f"Matched {subject} in this server's offline '{provider}' dataset. "
            f"The data is synthetic demonstration data, NOT real-world data, and must not be "
            f"presented to the user as fact. Nothing was written to the CRM."
        )
