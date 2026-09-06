"""The offline sample enrichment dataset.

Synthetic B2B records used by :mod:`gtm_mcp.providers.sample`. Three of the
domains deliberately overlap the mock CRM seed (``gtm_mcp.crm.seed``) and two do
not, so an enrich-then-compare-with-CRM workflow can be demonstrated end to end
without a provider credential: two of the companies here are already "in the
CRM", and two are net-new prospects.

None of these organisations or people are real.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field


class SampleCompany(BaseModel):
    """One firmographic record in the offline dataset."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    domain: str = Field(description="Canonical web domain, the dataset's key.")
    name: str = Field(description="Company name.")
    description: str = Field(description="Short description of the business.")
    industry: str = Field(description="Primary industry.")
    employee_count: int = Field(ge=0, description="Approximate headcount.")
    city: str = Field(description="Headquarters city.")
    state: str | None = Field(default=None, description="Headquarters state or region.")
    country: str = Field(description="ISO 3166-1 alpha-2 country code.")
    website: str = Field(description="Canonical website URL.")
    linkedin_url: str = Field(description="LinkedIn company page URL.")


class SampleContact(BaseModel):
    """One person record in the offline dataset."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    full_name: str = Field(description="Display name.")
    first_name: str = Field(description="Given name.")
    last_name: str = Field(description="Family name.")
    title: str = Field(description="Current job title.")
    email: str = Field(description="Work email address.")
    phone: str | None = Field(default=None, description="Work phone number.")
    company_domain: str = Field(description="Employer domain; joins to SampleCompany.domain.")
    company_name: str = Field(description="Employer name.")
    city: str = Field(description="City the person is based in.")
    country: str = Field(description="ISO 3166-1 alpha-2 country code.")
    linkedin_url: str = Field(description="LinkedIn profile URL.")
    confidence: float = Field(ge=0.0, le=1.0, description="Match confidence for this record.")


SAMPLE_COMPANIES: Final[tuple[SampleCompany, ...]] = (
    SampleCompany(
        domain="cloudscale.io",
        name="CloudScale Systems",
        description="Cloud infrastructure management and developer platform.",
        industry="Cloud Infrastructure",
        employee_count=450,
        city="San Francisco",
        state="CA",
        country="US",
        website="https://cloudscale.io",
        linkedin_url="https://www.linkedin.com/company/cloudscale-systems",
    ),
    SampleCompany(
        domain="apexfintech.com",
        name="Apex FinTech Labs",
        description="Modern core banking and payments APIs for global enterprises.",
        industry="Financial Services",
        employee_count=850,
        city="New York",
        state="NY",
        country="US",
        website="https://apexfintech.com",
        linkedin_url="https://www.linkedin.com/company/apex-fintech-labs",
    ),
    SampleCompany(
        domain="synthetixrobotics.de",
        name="Synthetix Robotics",
        description="Autonomous warehouse robotics and vision perception systems.",
        industry="Robotics & Automation",
        employee_count=95,
        city="Berlin",
        state=None,
        country="DE",
        website="https://synthetixrobotics.de",
        linkedin_url="https://www.linkedin.com/company/synthetix-robotics",
    ),
    # Not in the CRM seed: the "new prospect" half of the demonstration.
    SampleCompany(
        domain="northwindlogistics.com",
        name="Northwind Logistics",
        description="Freight visibility and multi-carrier shipment orchestration.",
        industry="Logistics & Supply Chain",
        employee_count=1200,
        city="Chicago",
        state="IL",
        country="US",
        website="https://northwindlogistics.com",
        linkedin_url="https://www.linkedin.com/company/northwind-logistics",
    ),
    SampleCompany(
        domain="verdantgrid.co.uk",
        name="Verdant Grid",
        description="Grid-scale battery optimisation and energy trading software.",
        industry="Energy & Utilities",
        employee_count=210,
        city="Manchester",
        state=None,
        country="GB",
        website="https://verdantgrid.co.uk",
        linkedin_url="https://www.linkedin.com/company/verdant-grid",
    ),
)


SAMPLE_CONTACTS: Final[tuple[SampleContact, ...]] = (
    SampleContact(
        full_name="Elena Rostova",
        first_name="Elena",
        last_name="Rostova",
        title="VP of Engineering",
        email="elena.rostova@cloudscale.io",
        phone="+1-415-555-0142",
        company_domain="cloudscale.io",
        company_name="CloudScale Systems",
        city="San Francisco",
        country="US",
        linkedin_url="https://www.linkedin.com/in/elena-rostova",
        confidence=0.97,
    ),
    SampleContact(
        full_name="Marcus Ahearn",
        first_name="Marcus",
        last_name="Ahearn",
        title="Director of Revenue Operations",
        email="marcus.ahearn@cloudscale.io",
        phone=None,
        company_domain="cloudscale.io",
        company_name="CloudScale Systems",
        city="Denver",
        country="US",
        linkedin_url="https://www.linkedin.com/in/marcus-ahearn",
        confidence=0.82,
    ),
    SampleContact(
        full_name="Priya Raghunathan",
        first_name="Priya",
        last_name="Raghunathan",
        title="Chief Technology Officer",
        email="priya.raghunathan@apexfintech.com",
        phone="+1-212-555-0198",
        company_domain="apexfintech.com",
        company_name="Apex FinTech Labs",
        city="New York",
        country="US",
        linkedin_url="https://www.linkedin.com/in/priya-raghunathan",
        confidence=0.94,
    ),
    SampleContact(
        full_name="Jonas Weiss",
        first_name="Jonas",
        last_name="Weiss",
        title="Head of Platform Engineering",
        email="jonas.weiss@synthetixrobotics.de",
        phone=None,
        company_domain="synthetixrobotics.de",
        company_name="Synthetix Robotics",
        city="Berlin",
        country="DE",
        linkedin_url="https://www.linkedin.com/in/jonas-weiss",
        confidence=0.88,
    ),
    SampleContact(
        full_name="Dana Whitfield",
        first_name="Dana",
        last_name="Whitfield",
        title="VP of Sales",
        email="dana.whitfield@northwindlogistics.com",
        phone="+1-312-555-0177",
        company_domain="northwindlogistics.com",
        company_name="Northwind Logistics",
        city="Chicago",
        country="US",
        linkedin_url="https://www.linkedin.com/in/dana-whitfield",
        confidence=0.91,
    ),
    SampleContact(
        full_name="Aoife Brennan",
        first_name="Aoife",
        last_name="Brennan",
        title="Head of Commercial Strategy",
        email="aoife.brennan@verdantgrid.co.uk",
        phone=None,
        company_domain="verdantgrid.co.uk",
        company_name="Verdant Grid",
        city="Manchester",
        country="GB",
        linkedin_url="https://www.linkedin.com/in/aoife-brennan",
        confidence=0.79,
    ),
)
