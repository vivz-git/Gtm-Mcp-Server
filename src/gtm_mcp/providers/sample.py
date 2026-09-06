"""Offline enrichment adapters backed by a fixed synthetic dataset.

This is the default provider, and it exists for three reasons.

1. Anyone who clones this repository can run the server, connect a real MCP
   client and exercise ``search_company`` and ``search_contact`` without signing
   up for a vendor or spending a credit.
2. The protocol-level tests need a provider whose answers are deterministic and
   whose call count is zero.
3. Having two adapters behind each port is what actually proves the boundary
   holds; a single implementation always looks like a clean abstraction.

Every record it returns is marked ``live=False`` in its provenance, and
``server_info`` reports which provider is configured, so an agent is never in a
position to mistake this dataset for real-world data. Switch to the live
provider with ``GTM_ENRICHMENT_PROVIDER=hunter`` and an API key.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from gtm_mcp.domain.enrichment import (
    CompanyEnrichment,
    ContactEnrichment,
    EnrichmentProvenance,
    MatchBasis,
)
from gtm_mcp.domain.identifiers import CompanyQuery, ContactQuery
from gtm_mcp.domain.models import Company, Contact, RecordSource
from gtm_mcp.providers.sample_data import (
    SAMPLE_COMPANIES,
    SAMPLE_CONTACTS,
    SampleCompany,
    SampleContact,
)

#: Stable provider identifier, stored on every record these adapters return.
PROVIDER_NAME: Final = "sample"


def _normalize_name(value: str) -> str:
    """Reduce a company name to a comparable form.

    Args:
        value: A company name.

    Returns:
        The name lowercased with punctuation and common suffixes removed.
    """
    lowered = "".join(
        character for character in value.lower() if character.isalnum() or character == " "
    )
    words = [word for word in lowered.split() if word not in {"inc", "ltd", "llc", "gmbh", "plc"}]
    return " ".join(words)


class SampleCompanyProvider:
    """Resolves companies against the offline dataset by domain or by name."""

    @property
    def name(self) -> str:
        """Stable provider identifier."""
        return PROVIDER_NAME

    @property
    def live(self) -> bool:
        """This adapter performs no network I/O."""
        return False

    async def enrich_company(self, query: CompanyQuery) -> CompanyEnrichment | None:
        """Look up firmographic data for a company.

        Unlike the live provider, this one can resolve a company name, because
        the dataset is small and closed. It still never invents a domain for a
        name it does not hold.

        Args:
            query: The normalised company identifier.

        Returns:
            The matched company, or ``None`` when the dataset holds no record.
        """
        if query.domain is not None:
            record = self._by_domain(query.domain)
            matched_on = MatchBasis.DOMAIN
        else:
            record = self._by_name(query.name or query.raw)
            matched_on = MatchBasis.COMPANY_NAME

        if record is None:
            return None

        retrieved_at = datetime.now(UTC)
        return CompanyEnrichment(
            company=Company(
                domain=record.domain,
                name=record.name,
                description=record.description,
                industry=record.industry,
                employee_count=record.employee_count,
                city=record.city,
                state=record.state,
                country=record.country,
                website=record.website,
                linkedin_url=record.linkedin_url,
                source=RecordSource.ENRICHMENT,
                retrieved_at=retrieved_at,
            ),
            provenance=EnrichmentProvenance(
                provider=self.name,
                live=self.live,
                matched_on=matched_on,
                retrieved_at=retrieved_at,
                provider_record_id=record.domain,
            ),
        )

    @staticmethod
    def _by_domain(domain: str) -> SampleCompany | None:
        """Find a dataset company by exact domain.

        Args:
            domain: A normalised domain.

        Returns:
            The matching record, or ``None``.
        """
        return next((c for c in SAMPLE_COMPANIES if c.domain == domain), None)

    @staticmethod
    def _by_name(name: str) -> SampleCompany | None:
        """Find a dataset company by normalised name.

        Args:
            name: A company name.

        Returns:
            The matching record, or ``None``.
        """
        wanted = _normalize_name(name)
        if not wanted:
            return None
        return next((c for c in SAMPLE_COMPANIES if _normalize_name(c.name) == wanted), None)


class SampleContactProvider:
    """Resolves people against the offline dataset by name plus employer."""

    @property
    def name(self) -> str:
        """Stable provider identifier."""
        return PROVIDER_NAME

    @property
    def live(self) -> bool:
        """This adapter performs no network I/O."""
        return False

    async def enrich_contact(self, query: ContactQuery) -> ContactEnrichment | None:
        """Look up a person at a company.

        A name alone is never enough: the employer must also match, so two
        people who share a name at different companies cannot be conflated
        (the same rule the CRM applies to contact identity, D-014).

        Args:
            query: The normalised person and employer identifiers.

        Returns:
            The matched contact, or ``None`` when the dataset holds no record.
        """
        wanted_name = query.full_name.strip().casefold()
        candidates = [c for c in SAMPLE_CONTACTS if c.full_name.casefold() == wanted_name]
        if not candidates:
            return None

        if query.company.domain is not None:
            record = next(
                (c for c in candidates if c.company_domain == query.company.domain),
                None,
            )
            matched_on = MatchBasis.PERSON_NAME
        else:
            wanted_company = _normalize_name(query.company.name or query.company.raw)
            record = next(
                (c for c in candidates if _normalize_name(c.company_name) == wanted_company),
                None,
            )
            matched_on = MatchBasis.COMPANY_NAME

        if record is None:
            return None

        retrieved_at = datetime.now(UTC)
        return ContactEnrichment(
            contact=self._to_contact(record, retrieved_at),
            provenance=EnrichmentProvenance(
                provider=self.name,
                live=self.live,
                matched_on=matched_on,
                retrieved_at=retrieved_at,
                provider_record_id=record.email,
            ),
        )

    @staticmethod
    def _to_contact(record: SampleContact, retrieved_at: datetime) -> Contact:
        """Translate a dataset record into the canonical contact model.

        Args:
            record: The dataset record.
            retrieved_at: Timestamp to stamp on the record.

        Returns:
            The canonical contact.
        """
        return Contact(
            full_name=record.full_name,
            first_name=record.first_name,
            last_name=record.last_name,
            title=record.title,
            company_domain=record.company_domain,
            company_name=record.company_name,
            email=record.email,
            phone=record.phone,
            city=record.city,
            country=record.country,
            linkedin_url=record.linkedin_url,
            source=RecordSource.ENRICHMENT,
            confidence=record.confidence,
            retrieved_at=retrieved_at,
        )
