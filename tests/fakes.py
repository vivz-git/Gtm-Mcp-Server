"""In-memory test doubles for the CRM boundary.

``InMemoryCrmRepository`` mirrors the behaviour ``PostgresCrmRepository`` is
required to have — email as natural identity (D-014), CRM values authoritative
over enrichment and never overwritten with ``None`` (D-015), idempotent list
membership — so that the guardrail, audit and protocol tests can run without a
database.

It is deliberately *not* the only place those rules are tested. The same
scenarios run against real PostgreSQL in ``tests/integration``; if the fake and
the real adapter ever diverge, the integration suite is the one that is right.

Every mutating call is recorded on ``write_calls``. That is what lets a test
assert the property a read tool must have: that it reached no write method at
all, rather than merely that nothing visibly changed.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from gtm_mcp.audit.events import AuditEvent
from gtm_mcp.domain.models import Contact, ContactFilter, RecordSource
from gtm_mcp.domain.results import WriteOutcome, WriteResult

#: Fields the fake merges on an update, in the order the real repository uses.
_MERGEABLE_FIELDS = (
    "first_name",
    "last_name",
    "full_name",
    "title",
    "company_name",
    "phone",
    "city",
    "country",
    "linkedin_url",
    "company_domain",
)


@dataclass
class StoredContact:
    """A mutable CRM row inside the fake."""

    contact_id: str
    contact: Contact

    def replace(self, **changes: object) -> None:
        """Apply field changes, keeping the stored record frozen-but-current.

        Args:
            **changes: Field values to overwrite.
        """
        self.contact = self.contact.model_copy(update=changes)


@dataclass
class InMemoryCrmRepository:
    """A ``CrmRepository`` that keeps everything in dictionaries."""

    contacts: dict[str, StoredContact] = field(default_factory=dict)
    lists: dict[str, set[str]] = field(default_factory=dict)
    write_calls: list[str] = field(default_factory=list)

    #: When set, every write method raises this instead of mutating.
    failure: Exception | None = None

    def seed(self, contact: Contact, *, contact_id: str | None = None) -> str:
        """Insert a record directly, bypassing the write path.

        Used to set up "the CRM already knows this person" without going through
        the guarded path under test.

        Args:
            contact: The record to store.
            contact_id: Identifier to store it under; generated when omitted.

        Returns:
            The identifier of the stored record.
        """
        stored_id = contact_id or str(uuid.uuid4())
        self.contacts[stored_id] = StoredContact(contact_id=stored_id, contact=contact)
        return stored_id

    async def get_contact(self, contact_id: str) -> Contact | None:
        """Fetch one contact by identifier.

        Args:
            contact_id: The identifier to look up.

        Returns:
            The contact, or ``None`` when absent or the identifier is malformed.
        """
        stored = self.contacts.get(contact_id)
        return stored.contact if stored is not None else None

    async def query_contacts(self, criteria: ContactFilter) -> Sequence[Contact]:
        """Return contacts matching the criteria, bounded by the limit.

        Args:
            criteria: The filters to apply.

        Returns:
            Matching contacts, at most ``criteria.limit`` of them.
        """
        matches: list[Contact] = []
        for stored in self.contacts.values():
            contact = stored.contact
            if (
                criteria.company_domain is not None
                and (contact.company_domain or "").lower() != criteria.company_domain.lower()
            ):
                continue
            if (
                criteria.title_contains is not None
                and criteria.title_contains.lower() not in (contact.title or "").lower()
            ):
                continue
            if criteria.country is not None and (contact.country or "") != criteria.country.upper():
                continue
            if criteria.list_name is not None and stored.contact_id not in self.lists.get(
                criteria.list_name, set()
            ):
                continue
            matches.append(contact)
        return matches[: criteria.limit]

    async def upsert_contact(self, contact: Contact) -> WriteResult:
        """Create or update a contact keyed on normalised email.

        Args:
            contact: The record to persist.

        Returns:
            The outcome of the write.

        Raises:
            Exception: The configured ``failure``, when one is set.
        """
        self.write_calls.append("upsert_contact")
        if self.failure is not None:
            raise self.failure

        email = contact.email.strip().lower() if contact.email else None
        existing = self._find_by_email(email) if email else None

        if existing is None:
            stored_id = self.seed(contact)
            return WriteResult(
                outcome=WriteOutcome.CREATED,
                record_id=stored_id,
                message=f"Created contact '{contact.full_name}'",
            )

        changed = self._merge(existing, contact)
        if not changed:
            return WriteResult(
                outcome=WriteOutcome.UNCHANGED,
                record_id=existing.contact_id,
                message=f"Contact '{existing.contact.full_name}' is unchanged",
            )
        return WriteResult(
            outcome=WriteOutcome.UPDATED,
            record_id=existing.contact_id,
            changed_fields=tuple(changed),
            message=f"Updated contact fields: {', '.join(changed)}",
        )

    async def add_contact_to_list(self, contact_id: str, list_name: str) -> WriteResult:
        """Add an existing contact to a list, idempotently.

        Args:
            contact_id: Identifier of the contact.
            list_name: Name of the list, created on first use.

        Returns:
            The outcome of the write.

        Raises:
            Exception: The configured ``failure``, when one is set.
        """
        self.write_calls.append("add_contact_to_list")
        if self.failure is not None:
            raise self.failure

        if contact_id not in self.contacts:
            return WriteResult.failed(f"Contact '{contact_id}' not found", record_id=contact_id)

        members = self.lists.setdefault(list_name.strip(), set())
        if contact_id in members:
            return WriteResult(
                outcome=WriteOutcome.UNCHANGED,
                record_id=contact_id,
                message=f"Contact already a member of list '{list_name}'",
            )
        members.add(contact_id)
        return WriteResult(
            outcome=WriteOutcome.CREATED,
            record_id=contact_id,
            message=f"Added contact to list '{list_name}'",
        )

    def _find_by_email(self, email: str) -> StoredContact | None:
        """Locate a stored contact by normalised email.

        Args:
            email: The normalised address to match.

        Returns:
            The stored contact, or ``None``.
        """
        for stored in self.contacts.values():
            if (stored.contact.email or "").lower() == email:
                return stored
        return None

    @staticmethod
    def _merge(existing: StoredContact, incoming: Contact) -> list[str]:
        """Apply the D-015 merge policy and report what changed.

        Args:
            existing: The stored record.
            incoming: The submitted record.

        Returns:
            Names of the fields whose values changed.
        """
        crm_authoritative = existing.contact.source is RecordSource.CRM
        from_enrichment = incoming.source is RecordSource.ENRICHMENT
        changed: list[str] = []
        for name in _MERGEABLE_FIELDS:
            new_value = getattr(incoming, name)
            if new_value is None:
                continue
            current = getattr(existing.contact, name)
            if crm_authoritative and from_enrichment and current is not None:
                continue
            if current != new_value:
                existing.replace(**{name: new_value})
                changed.append(name)
        return changed


@dataclass
class RecordingAuditSink:
    """An audit sink that keeps every event, and can be made to fail."""

    events: list[AuditEvent] = field(default_factory=list)
    failure: Exception | None = None

    async def record(self, event: AuditEvent) -> None:
        """Record an event, or raise the configured failure.

        Args:
            event: The event to record.

        Raises:
            Exception: The configured ``failure``, when one is set.
        """
        if self.failure is not None:
            raise self.failure
        self.events.append(event)

    @property
    def outcomes(self) -> list[str]:
        """The recorded outcomes, in order."""
        return [event.outcome.value for event in self.events]
