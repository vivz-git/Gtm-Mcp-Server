"""PostgreSQL implementation of the CRM repository.

Implements the CrmRepository boundary protocol. Provides safe, idempotent upserts,
bounded querying, and list membership operations without exposing SQLAlchemy objects
beyond the repository boundary.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from gtm_mcp.db.models import CompanyModel, ContactModel, ListMemberModel, ListModel
from gtm_mcp.domain.models import Company, Contact, ContactFilter, RecordSource
from gtm_mcp.domain.results import WriteOutcome, WriteResult
from gtm_mcp.logging_setup import get_logger

_log = get_logger(__name__)


def _to_domain_contact(row: ContactModel) -> Contact:
    """Map a SQLAlchemy ContactModel row to the canonical domain Contact."""
    company_domain = row.company_domain
    company_name = row.company_name
    if row.company is not None:
        company_domain = company_domain or row.company.domain
        company_name = company_name or row.company.name

    return Contact(
        full_name=row.full_name,
        first_name=row.first_name,
        last_name=row.last_name,
        title=row.title,
        company_domain=company_domain,
        company_name=company_name,
        email=row.email,
        linkedin_url=row.linkedin_url,
        source=RecordSource(row.source) if row.source in RecordSource else RecordSource.CRM,
        confidence=row.confidence,
        retrieved_at=row.updated_at,
    )


def _to_domain_company(row: CompanyModel) -> Company:
    """Map a SQLAlchemy CompanyModel row to the canonical domain Company."""
    return Company(
        domain=row.domain,
        name=row.name,
        description=row.description,
        industry=row.industry,
        employee_count=row.employee_count,
        country=row.country,
        website=row.website,
        linkedin_url=row.linkedin_url,
        source=RecordSource(row.source) if row.source in RecordSource else RecordSource.CRM,
        confidence=row.confidence,
        retrieved_at=row.updated_at,
    )


class PostgresCrmRepository:
    """PostgreSQL adapter implementing the CrmRepository port.

    Encapsulates all SQL execution, session lifecycle, and mapping between
    domain models and database tables.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Initialise with a session factory.

        Args:
            session_factory: Async session factory bound to the engine.
        """
        self._session_factory = session_factory

    async def get_contact(self, contact_id: str) -> Contact | None:
        """Fetch a single contact by identifier.

        Args:
            contact_id: UUID string of the contact.

        Returns:
            The canonical Contact, or None if not found or ID is invalid.
        """
        try:
            contact_uuid = uuid.UUID(contact_id)
        except (ValueError, AttributeError):
            return None

        async with self._session_factory() as session:
            stmt = (
                select(ContactModel)
                .options(selectinload(ContactModel.company))
                .where(ContactModel.id == contact_uuid)
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return _to_domain_contact(row)

    async def get_company_by_domain(self, domain: str) -> Company | None:
        """Fetch a company by normalized web domain.

        Args:
            domain: The company's web domain.

        Returns:
            The canonical Company, or None if not found.
        """
        normalized_domain = domain.strip().lower()
        async with self._session_factory() as session:
            stmt = select(CompanyModel).where(CompanyModel.domain == normalized_domain)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return _to_domain_company(row)

    async def query_contacts(self, criteria: ContactFilter) -> Sequence[Contact]:
        """Return contacts matching the given criteria.

        Args:
            criteria: Validated filter criteria, including bounded limit.

        Returns:
            Sequence of canonical Contact objects matching filters.
        """
        async with self._session_factory() as session:
            stmt = select(ContactModel).options(selectinload(ContactModel.company))

            if criteria.company_domain is not None:
                normalized_domain = criteria.company_domain.strip().lower()
                stmt = stmt.where(ContactModel.company_domain == normalized_domain)

            if criteria.title_contains is not None:
                stmt = stmt.where(ContactModel.title.ilike(f"%{criteria.title_contains.strip()}%"))

            if criteria.country is not None:
                stmt = stmt.where(ContactModel.country == criteria.country.strip().upper())

            if criteria.list_name is not None:
                clean_list_name = criteria.list_name.strip()
                stmt = (
                    stmt.join(ContactModel.list_memberships)
                    .join(ListMemberModel.list)
                    .where(ListModel.name == clean_list_name)
                )

            stmt = stmt.order_by(ContactModel.created_at.desc(), ContactModel.id.desc())
            stmt = stmt.limit(min(criteria.limit, 100))

            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [_to_domain_contact(row) for row in rows]

    async def upsert_contact(self, contact: Contact) -> WriteResult:
        """Create or update a contact, keyed by its natural identifier.

        Identity resolution (DECISIONS.md D-014):
        1. Normalized email is the strongest natural identity when available.
        2. External provider identity is used when email is unavailable.
        3. Contacts with neither are not coalesced by name + company.

        Conflict resolution (DECISIONS.md D-015):
        1. CRM-curated data is authoritative over enrichment.
        2. Populated fields are never overwritten with None.

        Args:
            contact: The canonical contact to persist.

        Returns:
            WriteResult indicating CREATED, UPDATED, or UNCHANGED.
        """
        normalized_email: str | None = None
        if contact.email is not None and contact.email.strip():
            normalized_email = contact.email.strip().lower()

        async with self._session_factory() as session:
            existing: ContactModel | None = None

            if normalized_email is not None:
                stmt = select(ContactModel).where(ContactModel.email == normalized_email)
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

            # Rule 3: if neither email nor provider ID exists, we never falsely coalesce
            if existing is None:
                # Find matching company if domain provided
                company_id: uuid.UUID | None = None
                normalized_domain: str | None = None
                if contact.company_domain is not None and contact.company_domain.strip():
                    normalized_domain = contact.company_domain.strip().lower()
                    comp_stmt = select(CompanyModel).where(CompanyModel.domain == normalized_domain)
                    comp_result = await session.execute(comp_stmt)
                    comp = comp_result.scalar_one_or_none()
                    if comp is not None:
                        company_id = comp.id

                new_row = ContactModel(
                    company_id=company_id,
                    company_domain=normalized_domain,
                    company_name=contact.company_name,
                    first_name=contact.first_name,
                    last_name=contact.last_name,
                    full_name=contact.full_name,
                    email=normalized_email,
                    title=contact.title,
                    linkedin_url=contact.linkedin_url,
                    source=contact.source.value,
                    confidence=contact.confidence,
                )
                session.add(new_row)
                await session.commit()
                return WriteResult(
                    outcome=WriteOutcome.CREATED,
                    record_id=str(new_row.id),
                    message=f"Created contact '{contact.full_name}'",
                )

            # Record exists; evaluate update with conflict policy
            changed_fields: list[str] = []
            is_crm_authoritative = existing.source == RecordSource.CRM.value
            is_incoming_enrichment = contact.source == RecordSource.ENRICHMENT

            # Field-by-field merge evaluation
            fields_to_check = [
                ("first_name", contact.first_name),
                ("last_name", contact.last_name),
                ("full_name", contact.full_name),
                ("title", contact.title),
                ("company_name", contact.company_name),
                ("linkedin_url", contact.linkedin_url),
            ]

            if contact.company_domain is not None and contact.company_domain.strip().lower() != (
                existing.company_domain or ""
            ):
                normalized_domain = contact.company_domain.strip().lower()
                # If CRM is authoritative and domain is already set, enrichment cannot overwrite
                if not (
                    is_crm_authoritative and is_incoming_enrichment and existing.company_domain
                ):
                    existing.company_domain = normalized_domain
                    # Link to company if available
                    comp_stmt = select(CompanyModel).where(CompanyModel.domain == normalized_domain)
                    comp_result = await session.execute(comp_stmt)
                    comp = comp_result.scalar_one_or_none()
                    if comp is not None:
                        existing.company_id = comp.id
                    changed_fields.append("company_domain")

            for field_name, incoming_val in fields_to_check:
                if incoming_val is None:
                    # Rule: never overwrite populated field with None
                    continue

                curr_val = getattr(existing, field_name)
                # Rule: CRM curated values are authoritative over enrichment
                if is_crm_authoritative and is_incoming_enrichment and curr_val is not None:
                    continue

                if curr_val != incoming_val:
                    setattr(existing, field_name, incoming_val)
                    changed_fields.append(field_name)

            if changed_fields:
                existing.updated_at = datetime.now(UTC)
                await session.commit()
                return WriteResult(
                    outcome=WriteOutcome.UPDATED,
                    record_id=str(existing.id),
                    changed_fields=tuple(changed_fields),
                    message=(
                        f"Updated contact '{existing.full_name}' fields: "
                        f"{', '.join(changed_fields)}"
                    ),
                )

            return WriteResult(
                outcome=WriteOutcome.UNCHANGED,
                record_id=str(existing.id),
                message=f"Contact '{existing.full_name}' is unchanged",
            )

    async def add_contact_to_list(self, contact_id: str, list_name: str) -> WriteResult:
        """Add an existing contact to a named GTM list.

        Additive and idempotent: re-adding a member reports UNCHANGED.
        Creates the list on first use if it does not yet exist.

        Args:
            contact_id: Identifier of an existing contact.
            list_name: Name of the list.

        Returns:
            WriteResult indicating CREATED, UNCHANGED, or FAILED.
        """
        try:
            contact_uuid = uuid.UUID(contact_id)
        except (ValueError, AttributeError):
            return WriteResult.failed(f"Invalid contact ID '{contact_id}'", record_id=contact_id)

        clean_list_name = list_name.strip()
        if not clean_list_name:
            return WriteResult.failed("List name cannot be empty", record_id=contact_id)

        async with self._session_factory() as session:
            # Verify contact exists
            contact = await session.get(ContactModel, contact_uuid)
            if contact is None:
                return WriteResult.failed(f"Contact '{contact_id}' not found", record_id=contact_id)

            # Find or create list
            list_stmt = select(ListModel).where(ListModel.name == clean_list_name)
            list_result = await session.execute(list_stmt)
            target_list = list_result.scalar_one_or_none()
            if target_list is None:
                target_list = ListModel(name=clean_list_name)
                session.add(target_list)
                await session.flush()

            # Check existing membership
            member_stmt = select(ListMemberModel).where(
                ListMemberModel.list_id == target_list.id,
                ListMemberModel.contact_id == contact.id,
            )
            member_result = await session.execute(member_stmt)
            existing_member = member_result.scalar_one_or_none()

            if existing_member is not None:
                return WriteResult(
                    outcome=WriteOutcome.UNCHANGED,
                    record_id=str(contact.id),
                    message=f"Contact already a member of list '{clean_list_name}'",
                )

            new_member = ListMemberModel(list_id=target_list.id, contact_id=contact.id)
            session.add(new_member)
            await session.commit()
            return WriteResult(
                outcome=WriteOutcome.CREATED,
                record_id=str(contact.id),
                message=f"Added contact '{contact.full_name}' to list '{clean_list_name}'",
            )
