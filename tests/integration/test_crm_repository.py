"""Integration tests for PostgresCrmRepository."""

from __future__ import annotations

import uuid

import pytest

from gtm_mcp.crm.repository import PostgresCrmRepository
from gtm_mcp.domain.models import Contact, ContactFilter, RecordSource
from gtm_mcp.domain.results import WriteOutcome
from gtm_mcp.ports import CrmRepository

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def test_repository_satisfies_crm_repository_protocol(
    crm_repo: PostgresCrmRepository,
) -> None:
    """PostgresCrmRepository must structurally satisfy CrmRepository Protocol."""
    assert isinstance(crm_repo, CrmRepository)


async def test_get_contact_lifecycle(crm_repo: PostgresCrmRepository) -> None:
    """Retrieving contact by ID handles existing, non-existent, and invalid IDs."""
    # Invalid ID
    assert await crm_repo.get_contact("not-a-uuid") is None

    # Non-existent UUID
    assert await crm_repo.get_contact(str(uuid.uuid4())) is None

    unique_email = f"grace.hopper.{uuid.uuid4().hex[:6]}@navy.mil"
    contact = Contact(
        full_name="Grace Hopper",
        first_name="Grace",
        last_name="Hopper",
        title="Rear Admiral & Systems Architect",
        email=unique_email,
        company_domain="navy.mil",
        company_name="US Navy",
        source=RecordSource.CRM,
    )
    result = await crm_repo.upsert_contact(contact)
    assert result.outcome is WriteOutcome.CREATED
    assert result.record_id is not None

    # Fetch created contact
    fetched = await crm_repo.get_contact(result.record_id)
    assert fetched is not None
    assert fetched.full_name == "Grace Hopper"
    assert fetched.email == unique_email
    assert fetched.company_domain == "navy.mil"


async def test_upsert_contact_idempotency_and_update(crm_repo: PostgresCrmRepository) -> None:
    """Upserting the same contact twice is idempotent; updates track changed fields."""
    unique_email = f"ada.lovelace.{uuid.uuid4().hex[:6]}@analytics.org"
    contact = Contact(
        full_name="Ada Lovelace",
        first_name="Ada",
        last_name="Lovelace",
        title="Computational Analyst",
        email=unique_email,
        source=RecordSource.CRM,
    )

    # First upsert -> CREATED
    res1 = await crm_repo.upsert_contact(contact)
    assert res1.outcome is WriteOutcome.CREATED
    assert res1.record_id is not None

    # Second upsert identical -> UNCHANGED
    res2 = await crm_repo.upsert_contact(contact)
    assert res2.outcome is WriteOutcome.UNCHANGED
    assert res2.record_id == res1.record_id

    # Third upsert with changed title -> UPDATED
    updated_contact = Contact(
        full_name="Ada Lovelace",
        first_name="Ada",
        last_name="Lovelace",
        title="Chief Mathematician",
        email=unique_email,
        source=RecordSource.CRM,
    )
    res3 = await crm_repo.upsert_contact(updated_contact)
    assert res3.outcome is WriteOutcome.UPDATED
    assert "title" in res3.changed_fields


async def test_conflict_policy_crm_is_authoritative_over_enrichment(
    crm_repo: PostgresCrmRepository,
) -> None:
    """CRM-curated values must not be silently overwritten by enrichment data."""
    unique_email = f"alan.turing.{uuid.uuid4().hex[:6]}@bletchley.ac.uk"
    curated_contact = Contact(
        full_name="Alan Turing",
        first_name="Alan",
        last_name="Turing",
        title="Lead Cryptanalyst",
        email=unique_email,
        source=RecordSource.CRM,
    )
    res = await crm_repo.upsert_contact(curated_contact)
    assert res.outcome is WriteOutcome.CREATED

    # Enrichment attempts to overwrite curated title
    enrichment_attempt = Contact(
        full_name="Alan M. Turing",
        first_name="Alan",
        last_name="Turing",
        title="Mathematician Grade 1",  # Different title
        email=unique_email,
        linkedin_url="https://linkedin.com/in/alan-turing",  # Missing field, should be added
        source=RecordSource.ENRICHMENT,
    )
    update_res = await crm_repo.upsert_contact(enrichment_attempt)
    assert update_res.outcome is WriteOutcome.UPDATED
    # linkedin_url was missing so it should be updated, but title must not have changed
    assert "linkedin_url" in update_res.changed_fields
    assert "title" not in update_res.changed_fields

    # Verify persistent state reflects CRM authority
    assert res.record_id is not None
    persisted = await crm_repo.get_contact(res.record_id)
    assert persisted is not None
    assert persisted.title == "Lead Cryptanalyst"
    assert persisted.linkedin_url == "https://linkedin.com/in/alan-turing"


async def test_never_overwrite_populated_field_with_none(
    crm_repo: PostgresCrmRepository,
) -> None:
    """Incoming None values must never overwrite an existing populated field."""
    unique_email = f"margaret.hamilton.{uuid.uuid4().hex[:6]}@mit.edu"
    initial_contact = Contact(
        full_name="Margaret Hamilton",
        title="Director of Software Engineering",
        email=unique_email,
        linkedin_url="https://linkedin.com/in/margaret-hamilton",
        source=RecordSource.CRM,
    )
    res1 = await crm_repo.upsert_contact(initial_contact)
    assert res1.outcome is WriteOutcome.CREATED

    # Attempt upsert with linkedin_url=None
    update_with_none = Contact(
        full_name="Margaret Hamilton",
        title="Director of Software Engineering",
        email=unique_email,
        linkedin_url=None,  # None must not clear existing URL
        source=RecordSource.CRM,
    )
    res2 = await crm_repo.upsert_contact(update_with_none)
    assert res2.outcome is WriteOutcome.UNCHANGED

    assert res1.record_id is not None
    persisted = await crm_repo.get_contact(res1.record_id)
    assert persisted is not None
    assert persisted.linkedin_url == "https://linkedin.com/in/margaret-hamilton"


async def test_contact_identity_rule_does_not_coalesce_without_email(
    crm_repo: PostgresCrmRepository,
) -> None:
    """Contacts lacking email and provider key must not falsely coalesce on name."""
    contact1 = Contact(
        full_name="Anonymous Lead",
        company_domain="mysterycorp.com",
        source=RecordSource.AGENT,
    )
    contact2 = Contact(
        full_name="Anonymous Lead",
        company_domain="mysterycorp.com",
        source=RecordSource.AGENT,
    )

    res1 = await crm_repo.upsert_contact(contact1)
    res2 = await crm_repo.upsert_contact(contact2)

    # Must both be CREATED with distinct record IDs rather than falsely claimed UNCHANGED
    assert res1.outcome is WriteOutcome.CREATED
    assert res2.outcome is WriteOutcome.CREATED
    assert res1.record_id != res2.record_id


async def test_add_contact_to_list_idempotent(crm_repo: PostgresCrmRepository) -> None:
    """Adding a contact to a list creates on first use and reports UNCHANGED on repeat."""
    contact = Contact(
        full_name="Claude Shannon",
        email=f"claude.shannon.{uuid.uuid4().hex[:6]}@bell-labs.com",
        source=RecordSource.CRM,
    )
    created = await crm_repo.upsert_contact(contact)
    contact_id = created.record_id
    assert contact_id is not None

    # First add -> CREATED (list created on first use)
    list_name = f"Information Theorists {uuid.uuid4().hex[:4]}"
    add1 = await crm_repo.add_contact_to_list(contact_id, list_name)
    assert add1.outcome is WriteOutcome.CREATED

    # Second add -> UNCHANGED
    add2 = await crm_repo.add_contact_to_list(contact_id, list_name)
    assert add2.outcome is WriteOutcome.UNCHANGED

    # Adding non-existent contact -> FAILED
    bad_id = str(uuid.uuid4())
    add_bad = await crm_repo.add_contact_to_list(bad_id, list_name)
    assert add_bad.outcome is WriteOutcome.FAILED


async def test_query_contacts_with_filters(crm_repo: PostgresCrmRepository) -> None:
    """Querying contacts supports domain, title substring, list filter, and limit."""
    domain = f"innovate-{uuid.uuid4().hex[:6]}.com"
    list_name = f"Innovators List {uuid.uuid4().hex[:4]}"

    c1 = Contact(
        full_name="Engineer One",
        title="Senior Site Reliability Engineer",
        company_domain=domain,
        email=f"e1.{uuid.uuid4().hex[:4]}@{domain}",
        source=RecordSource.CRM,
    )
    c2 = Contact(
        full_name="Manager One",
        title="Engineering Manager",
        company_domain=domain,
        email=f"m1.{uuid.uuid4().hex[:4]}@{domain}",
        source=RecordSource.CRM,
    )
    r1 = await crm_repo.upsert_contact(c1)
    await crm_repo.upsert_contact(c2)

    assert r1.record_id is not None
    await crm_repo.add_contact_to_list(r1.record_id, list_name)

    # Filter by domain
    domain_matches = await crm_repo.query_contacts(ContactFilter(company_domain=domain))
    assert len(domain_matches) == 2

    # Filter by title contains
    title_matches = await crm_repo.query_contacts(
        ContactFilter(company_domain=domain, title_contains="Reliability")
    )
    assert len(title_matches) == 1
    assert title_matches[0].full_name == "Engineer One"

    # Filter by list name
    list_matches = await crm_repo.query_contacts(ContactFilter(list_name=list_name))
    assert len(list_matches) >= 1
    assert any(c.full_name == "Engineer One" for c in list_matches)


async def test_enrichment_location_fields_survive_a_round_trip(
    crm_repo: PostgresCrmRepository,
) -> None:
    """Phone, city and country reach the database and come back.

    These columns existed before the canonical model carried them, so an
    enriched contact would have had its phone number silently dropped on the way
    in — a data loss no test would have caught.
    """
    unique_email = f"ada.lovelace.{uuid.uuid4().hex[:6]}@analytical.engine"
    enriched = Contact(
        full_name="Ada Lovelace",
        first_name="Ada",
        last_name="Lovelace",
        title="Chief Mathematician",
        email=unique_email,
        phone="+44-20-7946-0000",
        city="London",
        country="GB",
        company_domain="analytical.engine",
        company_name="Analytical Engine Ltd",
        source=RecordSource.ENRICHMENT,
    )

    created = await crm_repo.upsert_contact(enriched)
    assert created.outcome is WriteOutcome.CREATED
    assert created.record_id is not None

    stored = await crm_repo.get_contact(created.record_id)
    assert stored is not None
    assert stored.phone == "+44-20-7946-0000"
    assert stored.city == "London"
    assert stored.country == "GB"

    unchanged = await crm_repo.upsert_contact(enriched)
    assert unchanged.outcome is WriteOutcome.UNCHANGED, "the new fields must not break idempotency"
