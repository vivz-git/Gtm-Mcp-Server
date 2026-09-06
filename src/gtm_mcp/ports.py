"""Boundary interfaces between the GTM service layer and the outside world.

These Protocols are the seams that keep the tool layer independent of any
particular vendor or datastore. Structural typing (``Protocol``) is used rather
than inheritance so that an adapter never has to import from this module to
satisfy it, and so tests can substitute plain fakes.

Two rules are encoded here rather than merely documented:

* ``CrmRepository`` exposes **no delete operation**. The capability does not
  exist at the boundary, so no tool can reach for it.
* Write methods return :class:`~gtm_mcp.domain.results.WriteResult`, so the
  outcome of a mutation is always explicit at the point it crosses the boundary.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from gtm_mcp.domain.enrichment import CompanyEnrichment, ContactEnrichment
from gtm_mcp.domain.identifiers import CompanyQuery, ContactQuery
from gtm_mcp.domain.models import Contact, ContactFilter
from gtm_mcp.domain.results import WriteResult


@runtime_checkable
class CompanyEnrichmentProvider(Protocol):
    """An external source of firmographic data.

    Implementations are the only place that may know a vendor's field names,
    parameter names or error codes. Everything above this seam sees canonical
    records and ``GTMError`` subclasses.
    """

    @property
    def name(self) -> str:
        """Stable provider identifier, recorded on every enriched record."""
        ...

    @property
    def live(self) -> bool:
        """Whether this adapter calls a real external API.

        ``False`` for the offline sample adapter. Surfaced to the agent through
        ``EnrichmentProvenance.live`` so demonstration data is never mistaken
        for real-world data.
        """
        ...

    async def enrich_company(self, query: CompanyQuery) -> CompanyEnrichment | None:
        """Look up firmographic data for a company.

        Exactly one outbound request per call. Implementations must not fall
        back to another vendor, and must not retry a call the provider rejected
        on validation or authentication grounds, because both multiply cost.

        Args:
            query: The normalised company identifier.

        Returns:
            The enriched company with its provenance, or ``None`` if the
            provider has no match for an input it was able to resolve.

        Raises:
            ValidationError: The query lacks an identifier this provider can
                resolve — distinct from "no match", and actionable by the agent.
            RateLimitError: The provider's quota or rate limit is exhausted.
            ProviderError: The provider failed or returned an unusable response.
            ConfigurationError: The provider rejected this server's credential.
        """
        ...


@runtime_checkable
class ContactEnrichmentProvider(Protocol):
    """An external source of people data."""

    @property
    def name(self) -> str:
        """Stable provider identifier, recorded on every enriched record."""
        ...

    @property
    def live(self) -> bool:
        """Whether this adapter calls a real external API."""
        ...

    async def enrich_contact(self, query: ContactQuery) -> ContactEnrichment | None:
        """Look up a person at a company.

        The same single-request, no-fallback, no-retry-on-4xx rules as
        :meth:`CompanyEnrichmentProvider.enrich_company` apply.

        Args:
            query: The normalised person and employer identifiers.

        Returns:
            The enriched contact with its provenance, or ``None`` if the
            provider has no match.

        Raises:
            ValidationError: The query lacks an identifier this provider can
                resolve.
            RateLimitError: The provider's quota or rate limit is exhausted.
            ProviderError: The provider failed or returned an unusable response.
            ConfigurationError: The provider rejected this server's credential.
        """
        ...


@runtime_checkable
class CrmRepository(Protocol):
    """Persistence boundary for CRM records.

    Deliberately offers no delete and no bulk mutation. Both writes are
    upserts keyed by a natural identifier, which makes retries safe.
    """

    async def get_contact(self, contact_id: str) -> Contact | None:
        """Fetch a single contact by identifier.

        Args:
            contact_id: The CRM identifier of the contact.

        Returns:
            The contact, or ``None`` if no such record exists.
        """
        ...

    async def query_contacts(self, criteria: ContactFilter) -> Sequence[Contact]:
        """Return contacts matching the given criteria.

        Args:
            criteria: Validated filter criteria, including a bounded limit.

        Returns:
            Matching contacts, at most ``criteria.limit`` of them.
        """
        ...

    async def upsert_contact(self, contact: Contact) -> WriteResult:
        """Create or update a contact, keyed by its natural identifier.

        Must be idempotent: applying the same contact twice leaves the record in
        the same state and reports ``UNCHANGED`` the second time. Must never
        overwrite a populated field with ``None``.

        Args:
            contact: The canonical contact to persist.

        Returns:
            The outcome of the write.
        """
        ...

    async def add_contact_to_list(self, contact_id: str, list_name: str) -> WriteResult:
        """Add an existing contact to a named GTM list.

        Additive and idempotent: re-adding a member reports ``UNCHANGED``.

        Args:
            contact_id: Identifier of an existing contact.
            list_name: Name of the list; created on first use.

        Returns:
            The outcome of the write.
        """
        ...
