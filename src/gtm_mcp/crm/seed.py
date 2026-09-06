"""Seed script to populate the PostgreSQL mock CRM with realistic B2B data.

Reproducible and idempotent: can be executed repeatedly without generating
duplicate records or violating database constraints.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gtm_mcp.db.models import CompanyModel, ContactModel, ListMemberModel, ListModel
from gtm_mcp.domain.models import RecordSource
from gtm_mcp.logging_setup import get_logger

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SeedStats:
    """Summary metrics of the seed operation."""

    companies_created: int
    companies_updated: int
    contacts_created: int
    contacts_updated: int
    lists_created: int
    memberships_created: int


# Realistic synthetic B2B company records
COMPANIES_SEED: list[dict[str, Any]] = [
    {
        "domain": "cloudscale.io",
        "name": "CloudScale Systems",
        "description": "Cloud infrastructure management and developer platform.",
        "industry": "Cloud Infrastructure",
        "employee_count": 450,
        "city": "San Francisco",
        "state": "CA",
        "country": "US",
        "website": "https://cloudscale.io",
        "linkedin_url": "https://linkedin.com/company/cloudscale-systems",
    },
    {
        "domain": "apexfintech.com",
        "name": "Apex FinTech Labs",
        "description": "Modern core banking and payments APIs for global enterprises.",
        "industry": "Financial Services",
        "employee_count": 850,
        "city": "New York",
        "state": "NY",
        "country": "US",
        "website": "https://apexfintech.com",
        "linkedin_url": "https://linkedin.com/company/apex-fintech-labs",
    },
    {
        "domain": "dataflow.ai",
        "name": "DataFlow Analytics",
        "description": "Real-time stream processing and semantic lakehouse engine.",
        "industry": "Data Infrastructure",
        "employee_count": 120,
        "city": "Seattle",
        "state": "WA",
        "country": "US",
        "website": "https://dataflow.ai",
        "linkedin_url": "https://linkedin.com/company/dataflow-ai",
    },
    {
        "domain": "cybershield.io",
        "name": "CyberShield Defense",
        "description": "Zero-trust network access and identity threat protection.",
        "industry": "Cybersecurity",
        "employee_count": 280,
        "city": "Austin",
        "state": "TX",
        "country": "US",
        "website": "https://cybershield.io",
        "linkedin_url": "https://linkedin.com/company/cybershield-defense",
    },
    {
        "domain": "nexishealth.co",
        "name": "Nexis Health Solutions",
        "description": "HIPAA-compliant healthcare data exchange and telemetry platform.",
        "industry": "Healthcare IT",
        "employee_count": 650,
        "city": "Boston",
        "state": "MA",
        "country": "US",
        "website": "https://nexishealth.co",
        "linkedin_url": "https://linkedin.com/company/nexis-health",
    },
    {
        "domain": "synthetixrobotics.de",
        "name": "Synthetix Robotics",
        "description": "Autonomous warehouse robotics and vision perception systems.",
        "industry": "Robotics & Automation",
        "employee_count": 95,
        "city": "Berlin",
        "state": "BE",
        "country": "DE",
        "website": "https://synthetixrobotics.de",
        "linkedin_url": "https://linkedin.com/company/synthetix-robotics",
    },
]

# Realistic synthetic B2B contact records
CONTACTS_SEED: list[dict[str, Any]] = [
    # CloudScale Systems
    {
        "full_name": "Elena Rostova",
        "first_name": "Elena",
        "last_name": "Rostova",
        "title": "VP of Engineering",
        "email": "elena.rostova@cloudscale.io",
        "phone": "+1-415-555-0142",
        "company_domain": "cloudscale.io",
        "city": "San Francisco",
        "country": "US",
        "linkedin_url": "https://linkedin.com/in/elena-rostova-mock",
    },
    {
        "full_name": "Marcus Chen",
        "first_name": "Marcus",
        "last_name": "Chen",
        "title": "Principal Infrastructure Architect",
        "email": "marcus.chen@cloudscale.io",
        "phone": "+1-415-555-0188",
        "company_domain": "cloudscale.io",
        "city": "San Francisco",
        "country": "US",
        "linkedin_url": "https://linkedin.com/in/marcus-chen-mock",
    },
    {
        "full_name": "Sarah Jenkins",
        "first_name": "Sarah",
        "last_name": "Jenkins",
        "title": "Director of Developer Relations",
        "email": "sarah.jenkins@cloudscale.io",
        "phone": None,
        "company_domain": "cloudscale.io",
        "city": "San Francisco",
        "country": "US",
        "linkedin_url": "https://linkedin.com/in/sarah-jenkins-mock",
    },
    # Apex FinTech Labs
    {
        "full_name": "Liam O'Connor",
        "first_name": "Liam",
        "last_name": "O'Connor",
        "title": "Chief Information Security Officer",
        "email": "liam.oconnor@apexfintech.com",
        "phone": "+1-212-555-0193",
        "company_domain": "apexfintech.com",
        "city": "New York",
        "country": "US",
        "linkedin_url": "https://linkedin.com/in/liam-oconnor-mock",
    },
    {
        "full_name": "Aisha Patel",
        "first_name": "Aisha",
        "last_name": "Patel",
        "title": "Head of Payment Platforms",
        "email": "aisha.patel@apexfintech.com",
        "phone": "+1-212-555-0177",
        "company_domain": "apexfintech.com",
        "city": "New York",
        "country": "US",
        "linkedin_url": "https://linkedin.com/in/aisha-patel-mock",
    },
    {
        "full_name": "David Vance",
        "first_name": "David",
        "last_name": "Vance",
        "title": "Senior Risk Operations Manager",
        "email": "david.vance@apexfintech.com",
        "phone": None,
        "company_domain": "apexfintech.com",
        "city": "New York",
        "country": "US",
        "linkedin_url": "https://linkedin.com/in/david-vance-mock",
    },
    # DataFlow Analytics
    {
        "full_name": "Carlos Mendez",
        "first_name": "Carlos",
        "last_name": "Mendez",
        "title": "Chief Technology Officer",
        "email": "carlos.mendez@dataflow.ai",
        "phone": "+1-206-555-0112",
        "company_domain": "dataflow.ai",
        "city": "Seattle",
        "country": "US",
        "linkedin_url": "https://linkedin.com/in/carlos-mendez-mock",
    },
    {
        "full_name": "Priya Sharma",
        "first_name": "Priya",
        "last_name": "Sharma",
        "title": "Staff AI Research Engineer",
        "email": "priya.sharma@dataflow.ai",
        "phone": None,
        "company_domain": "dataflow.ai",
        "city": "Seattle",
        "country": "US",
        "linkedin_url": "https://linkedin.com/in/priya-sharma-mock",
    },
    # CyberShield Defense
    {
        "full_name": "David Kim",
        "first_name": "David",
        "last_name": "Kim",
        "title": "VP of Product Management",
        "email": "david.kim@cybershield.io",
        "phone": "+1-512-555-0164",
        "company_domain": "cybershield.io",
        "city": "Austin",
        "country": "US",
        "linkedin_url": "https://linkedin.com/in/david-kim-mock",
    },
    {
        "full_name": "Rachel Adams",
        "first_name": "Rachel",
        "last_name": "Adams",
        "title": "Lead Security Operations Engineer",
        "email": "rachel.adams@cybershield.io",
        "phone": "+1-512-555-0129",
        "company_domain": "cybershield.io",
        "city": "Austin",
        "country": "US",
        "linkedin_url": "https://linkedin.com/in/rachel-adams-mock",
    },
    # Nexis Health Solutions
    {
        "full_name": "Dr. Alexander Wright",
        "first_name": "Alexander",
        "last_name": "Wright",
        "title": "Chief Medical Information Officer",
        "email": "alexander.wright@nexishealth.co",
        "phone": "+1-617-555-0135",
        "company_domain": "nexishealth.co",
        "city": "Boston",
        "country": "US",
        "linkedin_url": "https://linkedin.com/in/alexander-wright-mock",
    },
    {
        "full_name": "Danielle Brooks",
        "first_name": "Danielle",
        "last_name": "Brooks",
        "title": "Director of Enterprise Systems",
        "email": "danielle.brooks@nexishealth.co",
        "phone": None,
        "company_domain": "nexishealth.co",
        "city": "Boston",
        "country": "US",
        "linkedin_url": "https://linkedin.com/in/danielle-brooks-mock",
    },
    # Synthetix Robotics
    {
        "full_name": "Lukas Weber",
        "first_name": "Lukas",
        "last_name": "Weber",
        "title": "VP of Hardware Engineering",
        "email": "lukas.weber@synthetixrobotics.de",
        "phone": "+49-30-555-0182",
        "company_domain": "synthetixrobotics.de",
        "city": "Berlin",
        "country": "DE",
        "linkedin_url": "https://linkedin.com/in/lukas-weber-mock",
    },
    # Provider identity prospect with NO email
    {
        "full_name": "Fatima Al-Mansoor",
        "first_name": "Fatima",
        "last_name": "Al-Mansoor",
        "title": "Head of Automation",
        "email": None,
        "phone": None,
        "company_domain": "synthetixrobotics.de",
        "city": "Berlin",
        "country": "DE",
        "linkedin_url": "https://linkedin.com/in/fatima-almansoor-mock",
        "provider_name": "apollo",
        "provider_contact_id": "ap_fatima_m_9821",
    },
]

# Realistic synthetic GTM lists
LISTS_SEED: list[dict[str, str]] = [
    {
        "name": "Tier-1 Enterprise Infrastructure Targets",
        "description": "High-priority infrastructure and platform targets for Q4 outreach.",
    },
    {
        "name": "Security & Compliance Decision Makers",
        "description": "CISOs, SecOps directors, and compliance leaders.",
    },
    {
        "name": "AI & Data Engineering Prospects",
        "description": "Data architects and AI engineers evaluating modern platforms.",
    },
]

# Contact memberships by contact email (or provider ID) -> list names
MEMBERSHIPS_SEED: list[tuple[str, str]] = [
    ("elena.rostova@cloudscale.io", "Tier-1 Enterprise Infrastructure Targets"),
    ("marcus.chen@cloudscale.io", "Tier-1 Enterprise Infrastructure Targets"),
    ("carlos.mendez@dataflow.ai", "Tier-1 Enterprise Infrastructure Targets"),
    ("lukas.weber@synthetixrobotics.de", "Tier-1 Enterprise Infrastructure Targets"),
    ("liam.oconnor@apexfintech.com", "Security & Compliance Decision Makers"),
    ("rachel.adams@cybershield.io", "Security & Compliance Decision Makers"),
    ("danielle.brooks@nexishealth.co", "Security & Compliance Decision Makers"),
    ("carlos.mendez@dataflow.ai", "AI & Data Engineering Prospects"),
    ("priya.sharma@dataflow.ai", "AI & Data Engineering Prospects"),
    ("marcus.chen@cloudscale.io", "AI & Data Engineering Prospects"),
    ("ap_fatima_m_9821", "AI & Data Engineering Prospects"),
]


async def seed_database(session_factory: async_sessionmaker[AsyncSession]) -> SeedStats:
    """Populate database with synthetic B2B CRM data idempotently.

    Args:
        session_factory: Async session factory for the CRM database.

    Returns:
        SeedStats summary with counts of created/updated entities.
    """
    comp_created = 0
    comp_updated = 0
    cont_created = 0
    cont_updated = 0
    lists_created = 0
    memberships_created = 0

    async with session_factory() as session, session.begin():
        # 1. Seed companies
        domain_to_company: dict[str, CompanyModel] = {}
        for comp_data in COMPANIES_SEED:
            stmt = select(CompanyModel).where(CompanyModel.domain == comp_data["domain"])
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if existing is None:
                company = CompanyModel(
                    domain=comp_data["domain"],
                    name=comp_data["name"],
                    description=comp_data["description"],
                    industry=comp_data["industry"],
                    employee_count=comp_data["employee_count"],
                    city=comp_data["city"],
                    state=comp_data["state"],
                    country=comp_data["country"],
                    website=comp_data["website"],
                    linkedin_url=comp_data["linkedin_url"],
                    source=RecordSource.SEED.value,
                    confidence=1.0,
                )
                session.add(company)
                comp_created += 1
                domain_to_company[comp_data["domain"]] = company
            else:
                existing.name = comp_data["name"]
                existing.description = comp_data["description"]
                existing.industry = comp_data["industry"]
                existing.employee_count = comp_data["employee_count"]
                existing.city = comp_data["city"]
                existing.state = comp_data["state"]
                existing.country = comp_data["country"]
                comp_updated += 1
                domain_to_company[comp_data["domain"]] = existing

        await session.flush()

        # 2. Seed contacts
        email_or_provider_to_contact: dict[str, ContactModel] = {}
        for cont_data in CONTACTS_SEED:
            existing_cont: ContactModel | None = None
            key = cont_data["email"] or cont_data.get("provider_contact_id")

            if cont_data["email"]:
                contact_stmt = select(ContactModel).where(ContactModel.email == cont_data["email"])
                existing_cont = (await session.execute(contact_stmt)).scalar_one_or_none()
            elif cont_data.get("provider_name") and cont_data.get("provider_contact_id"):
                provider_stmt = select(ContactModel).where(
                    ContactModel.provider_name == cont_data["provider_name"],
                    ContactModel.provider_contact_id == cont_data["provider_contact_id"],
                )
                existing_cont = (await session.execute(provider_stmt)).scalar_one_or_none()

            comp_id = None
            comp_name = None
            if cont_data.get("company_domain") in domain_to_company:
                comp = domain_to_company[cont_data["company_domain"]]
                comp_id = comp.id
                comp_name = comp.name

            if existing_cont is None:
                new_contact = ContactModel(
                    company_id=comp_id,
                    company_domain=cont_data.get("company_domain"),
                    company_name=comp_name,
                    first_name=cont_data.get("first_name"),
                    last_name=cont_data.get("last_name"),
                    full_name=cont_data["full_name"],
                    email=cont_data.get("email"),
                    phone=cont_data.get("phone"),
                    title=cont_data.get("title"),
                    city=cont_data.get("city"),
                    country=cont_data.get("country"),
                    linkedin_url=cont_data.get("linkedin_url"),
                    provider_name=cont_data.get("provider_name"),
                    provider_contact_id=cont_data.get("provider_contact_id"),
                    source=RecordSource.SEED.value,
                    confidence=1.0,
                )
                session.add(new_contact)
                cont_created += 1
                if key:
                    email_or_provider_to_contact[key] = new_contact
            else:
                existing_cont.first_name = cont_data.get("first_name")
                existing_cont.last_name = cont_data.get("last_name")
                existing_cont.full_name = cont_data["full_name"]
                existing_cont.title = cont_data.get("title")
                existing_cont.phone = cont_data.get("phone")
                existing_cont.city = cont_data.get("city")
                existing_cont.country = cont_data.get("country")
                cont_updated += 1
                if key:
                    email_or_provider_to_contact[key] = existing_cont

        await session.flush()

        # 3. Seed lists
        name_to_list: dict[str, ListModel] = {}
        for list_data in LISTS_SEED:
            list_stmt = select(ListModel).where(ListModel.name == list_data["name"])
            existing_list = (await session.execute(list_stmt)).scalar_one_or_none()
            if existing_list is None:
                lst = ListModel(
                    name=list_data["name"],
                    description=list_data["description"],
                )
                session.add(lst)
                lists_created += 1
                name_to_list[list_data["name"]] = lst
            else:
                existing_list.description = list_data["description"]
                name_to_list[list_data["name"]] = existing_list

        await session.flush()

        # 4. Seed list memberships
        for contact_key, list_name in MEMBERSHIPS_SEED:
            resolved_contact = email_or_provider_to_contact.get(contact_key)
            target_list = name_to_list.get(list_name)
            if resolved_contact is None or target_list is None:
                continue

            member_stmt = select(ListMemberModel).where(
                ListMemberModel.list_id == target_list.id,
                ListMemberModel.contact_id == resolved_contact.id,
            )
            existing_member = (await session.execute(member_stmt)).scalar_one_or_none()
            if existing_member is None:
                membership = ListMemberModel(list_id=target_list.id, contact_id=resolved_contact.id)
                session.add(membership)
                memberships_created += 1

    stats = SeedStats(
        companies_created=comp_created,
        companies_updated=comp_updated,
        contacts_created=cont_created,
        contacts_updated=cont_updated,
        lists_created=lists_created,
        memberships_created=memberships_created,
    )
    _log.info(
        "database_seeded",
        companies_created=comp_created,
        contacts_created=cont_created,
        lists_created=lists_created,
        memberships_created=memberships_created,
    )
    return stats
