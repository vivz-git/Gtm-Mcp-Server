"""Canonical GTM records.

These models are the *lingua franca* of the system. Enrichment providers and CRM
backends each have their own field names and shapes; adapters translate into and
out of these types so that the tool layer, the service layer and the audit trail
never depend on a vendor's schema.

The field set is deliberately conservative: it covers what every serious B2B
enrichment provider returns, which keeps the canonical model stable when a
provider is added or swapped (DECISIONS.md D-007).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class RecordSource(StrEnum):
    """Where a record's field values came from.

    Provenance is a first-class field, not metadata. Merge logic and the audit
    trail both need to know whether a value was enriched, already in the CRM, or
    supplied by the calling agent.
    """

    CRM = "crm"
    ENRICHMENT = "enrichment"
    AGENT = "agent"
    SEED = "seed"


class GTMRecord(BaseModel):
    """Base class for canonical records."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    source: RecordSource = Field(description="Provenance of this record's field values.")
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Provider-reported or computed confidence in this record, 0.0-1.0.",
    )
    retrieved_at: datetime | None = Field(
        default=None, description="When these values were fetched from their source (UTC)."
    )


class Company(GTMRecord):
    """A canonical company / account record."""

    domain: str = Field(
        description="Primary web domain, normalised and lowercased, e.g. 'stripe.com'. "
        "This is the join key across providers and the CRM."
    )
    name: str | None = Field(default=None, description="Legal or commonly used company name.")
    description: str | None = Field(default=None, description="Short description of the business.")
    industry: str | None = Field(default=None, description="Primary industry or sector.")
    employee_count: int | None = Field(
        default=None, ge=0, description="Approximate headcount, if reported."
    )
    country: str | None = Field(default=None, description="ISO 3166-1 alpha-2 country code.")
    website: str | None = Field(default=None, description="Canonical website URL.")
    linkedin_url: str | None = Field(default=None, description="LinkedIn company page URL.")


class Contact(GTMRecord):
    """A canonical person / contact record."""

    full_name: str = Field(description="The person's full name as displayed.")
    first_name: str | None = Field(default=None, description="Given name.")
    last_name: str | None = Field(default=None, description="Family name.")
    title: str | None = Field(default=None, description="Current job title.")
    company_domain: str | None = Field(
        default=None, description="Domain of the employing company; joins to Company.domain."
    )
    company_name: str | None = Field(default=None, description="Employer name as reported.")
    email: str | None = Field(default=None, description="Work email address, if available.")
    linkedin_url: str | None = Field(default=None, description="LinkedIn profile URL.")


class ContactFilter(BaseModel):
    """Filter criteria for querying contacts in the CRM.

    An explicit model rather than a free-form dict: the agent gets a typed
    schema describing exactly what it may filter on, and the repository cannot
    be handed an unbounded query. ``limit`` is capped so a single tool call can
    never drag the whole database into the model's context window.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    company_domain: str | None = Field(
        default=None, description="Exact match on the employer's web domain, e.g. 'stripe.com'."
    )
    title_contains: str | None = Field(
        default=None, description="Case-insensitive substring match against job title."
    )
    country: str | None = Field(
        default=None, description="Exact match on ISO 3166-1 alpha-2 country code."
    )
    list_name: str | None = Field(
        default=None, description="Restrict results to members of this GTM list."
    )
    limit: int = Field(default=25, ge=1, le=100, description="Maximum number of records to return.")
