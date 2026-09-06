"""Canonical enrichment results and their provenance.

A provider's own payload never reaches the agent. Adapters translate into the
canonical :class:`~gtm_mcp.domain.models.Company` and
:class:`~gtm_mcp.domain.models.Contact`, and attach an
:class:`EnrichmentProvenance` describing where the values came from and what
they were matched on.

Provenance is modelled rather than logged for two reasons. A GTM operator
merging enriched data into a CRM needs to know which vendor asserted a field
(D-015 makes CRM values authoritative over enriched ones, which requires knowing
which is which). And a model reading the result needs to know whether it is
looking at live vendor data or at this repository's offline sample dataset —
hence ``live``, which is not a detail a demo may quietly omit.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field

from gtm_mcp.domain.identifiers import CompanyQuery, ContactQuery
from gtm_mcp.domain.models import Company, Contact


class MatchBasis(StrEnum):
    """Which identifier the provider actually matched the record on.

    A domain match is an identity match; a name match is a best-effort lookup
    that can return the wrong company. The agent should weigh them differently,
    so the distinction is returned rather than flattened away.
    """

    DOMAIN = "domain"
    """Matched on an exact web domain."""

    COMPANY_NAME = "company_name"
    """Matched on a company name, which is inherently fuzzier than a domain."""

    PERSON_NAME = "person_name"
    """Matched a person by name within a resolved company."""


class EnrichmentProvenance(BaseModel):
    """Where an enriched record came from."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(description="Stable identifier of the provider that returned the record.")
    live: bool = Field(
        description="True when the record came from a live external API. False means it came "
        "from this server's offline sample dataset and must not be treated as real-world data."
    )
    matched_on: MatchBasis = Field(
        description="Which identifier the provider matched on. A domain match is stronger "
        "than a name match."
    )
    retrieved_at: datetime = Field(description="When the record was fetched (UTC).")
    provider_record_id: str | None = Field(
        default=None,
        description="The provider's own identifier for this record, where one is returned. "
        "Retained because it is the secondary natural identity for CRM upserts (D-014).",
    )


class CompanyEnrichment(BaseModel):
    """A company record returned by an enrichment provider, with its provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    company: Company = Field(description="The canonical company record.")
    provenance: EnrichmentProvenance = Field(description="Where the values came from.")


class ContactEnrichment(BaseModel):
    """A person record returned by an enrichment provider, with its provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contact: Contact = Field(description="The canonical contact record.")
    provenance: EnrichmentProvenance = Field(description="Where the values came from.")


class CompanyLookup(BaseModel):
    """The result of a company search, as returned to the calling agent.

    ``found`` is computed from whether a record is present, so the envelope
    cannot claim a match it does not carry — the same structural honesty rule
    that governs ``WriteResult.success`` (D-009).

    ``json_schema_mode_override`` is required, not cosmetic: the SDK derives a
    tool's output schema from the model and then validates the serialised result
    against it. A computed field appears only in the serialisation schema, so
    without this the server would emit ``found`` and then reject its own
    response as an unexpected property (D-018).
    """

    model_config = ConfigDict(
        extra="forbid", frozen=True, json_schema_mode_override="serialization"
    )

    query: CompanyQuery = Field(description="The normalised query that was actually executed.")
    company: Company | None = Field(
        default=None, description="The enriched company, absent when nothing matched."
    )
    provenance: EnrichmentProvenance | None = Field(
        default=None, description="Where the record came from. Absent when nothing matched."
    )
    message: str = Field(
        description="Model-readable summary of the outcome, including what to try next "
        "when nothing matched."
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def found(self) -> bool:
        """Whether a company record was returned."""
        return self.company is not None


class ContactLookup(BaseModel):
    """The result of a contact search, as returned to the calling agent.

    Carries the same computed ``found`` flag and the same schema-mode override
    as :class:`CompanyLookup`, for the same reason (D-018).
    """

    model_config = ConfigDict(
        extra="forbid", frozen=True, json_schema_mode_override="serialization"
    )

    query: ContactQuery = Field(description="The normalised query that was actually executed.")
    contact: Contact | None = Field(
        default=None, description="The enriched contact, absent when nothing matched."
    )
    provenance: EnrichmentProvenance | None = Field(
        default=None, description="Where the record came from. Absent when nothing matched."
    )
    message: str = Field(
        description="Model-readable summary of the outcome, including what to try next "
        "when nothing matched."
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def found(self) -> bool:
        """Whether a contact record was returned."""
        return self.contact is not None
