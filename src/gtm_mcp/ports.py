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

from gtm_mcp.domain.models import Company, Contact, ContactFilter
from gtm_mcp.domain.results import WriteResult


@runtime_checkable
class CompanyEnrichmentProvider(Protocol):
    """An external source of firmographic data."""

    @property
    def name(self) -> str:
        """Stable provider identifier, recorded on every enriched record."""
        ...

    async def enrich_company(self, domain_or_name: str) -> Company | None:
        """Look up firmographic data for a company.

        Args:
            domain_or_name: A web domain or company name to resolve.

        Returns:
            The enriched company, or ``None`` if the provider has no match.

        Raises:
            ProviderError: The provider was reachable but failed.
            RateLimitError: The provider's quota is exhausted.
        """
        ...


@runtime_checkable
class ContactEnrichmentProvider(Protocol):
    """An external source of people data."""

    @property
    def name(self) -> str:
        """Stable provider identifier, recorded on every enriched record."""
        ...

    async def enrich_contact(self, full_name: str, company: str) -> Contact | None:
        """Look up a person at a company.

        Args:
            full_name: The person's name.
            company: Employer name or web domain.

        Returns:
            The enriched contact, or ``None`` if the provider has no match.

        Raises:
            ProviderError: The provider was reachable but failed.
            RateLimitError: The provider's quota is exhausted.
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
