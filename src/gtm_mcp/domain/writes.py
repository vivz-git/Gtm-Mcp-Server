"""Input contracts for the CRM write tools.

The canonical :class:`~gtm_mcp.domain.models.Contact` is what the system stores.
It is deliberately *not* what an agent submits, for two reasons:

* ``Contact.source`` decides which side wins a merge conflict (D-015). If an
  agent could set it, an agent could declare its own guess CRM-authoritative and
  overwrite a human-curated value. ``ContactSyncInput`` has no ``source`` field:
  :meth:`ContactSyncInput.to_contact` stamps ``RecordSource.ENRICHMENT``, the
  least privileged provenance, on every record a tool submits.
* Every string an agent supplies is bounded here to the width of the column that
  will hold it, so an over-long field fails at schema validation with a message
  the model can act on, rather than as a database error mid-write.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from gtm_mcp.domain.models import Contact, RecordSource

#: Two-letter ISO 3166-1 alpha-2 country code, uppercased on the way in. The
#: column is two characters wide; anything else is a client mistake worth an
#: explicit message rather than a truncated row.
CountryCode = Annotated[
    str,
    Field(
        min_length=2,
        max_length=2,
        pattern=r"^[A-Za-z]{2}$",
        description="ISO 3166-1 alpha-2 country code, e.g. 'US', 'GB', 'DE'. "
        "Two letters exactly; not a country name.",
    ),
]


class ContactSyncInput(BaseModel):
    """A contact as an agent submits it to ``sync_to_crm``.

    Mirrors the fields of an enrichment result so that an agent can pass through
    what ``search_contact`` returned without reshaping it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    full_name: str = Field(
        min_length=1,
        max_length=255,
        description="The person's full name as it should appear in the CRM, "
        "e.g. 'Elena Rostova'. Required: a contact with no name is not useful to a "
        "salesperson reading the record later.",
    )
    email: str | None = Field(
        default=None,
        max_length=255,
        description="Work email address. This is the contact's natural identity: supply it "
        "whenever you have it, because it is what makes a repeated sync update the existing "
        "record instead of creating a second one for the same person.",
    )
    first_name: str | None = Field(default=None, max_length=100, description="Given name.")
    last_name: str | None = Field(default=None, max_length=100, description="Family name.")
    title: str | None = Field(
        default=None, max_length=255, description="Current job title, e.g. 'VP of Sales'."
    )
    company_domain: str | None = Field(
        default=None,
        max_length=255,
        description="Web domain of the employing company, e.g. 'stripe.com'. Lowercased on "
        "write and used to link the contact to an existing account. Do not guess one from a "
        "company name.",
    )
    company_name: str | None = Field(
        default=None, max_length=255, description="Employer name, e.g. 'Stripe'."
    )
    phone: str | None = Field(
        default=None, max_length=50, description="Direct or work phone number."
    )
    city: str | None = Field(
        default=None, max_length=100, description="City the person is based in."
    )
    country: CountryCode | None = Field(default=None, description="ISO 3166-1 alpha-2 code.")
    linkedin_url: str | None = Field(
        default=None, max_length=500, description="LinkedIn profile URL."
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="How confident you are in these values, 0.0-1.0. Pass through the "
        "confidence an enrichment result reported rather than inventing one.",
    )

    @field_validator("email")
    @classmethod
    def _reject_text_that_is_not_an_email(cls, value: str | None) -> str | None:
        """Reject an obvious non-address before it becomes a bad natural identity.

        A malformed value here is worse than a missing one: email is the key the
        upsert matches on, so a junk address silently creates a record that no
        later sync will ever find again.

        Args:
            value: The submitted address, if any.

        Returns:
            The address, lowercased, or ``None``.

        Raises:
            ValueError: The value cannot be an email address.
        """
        if value is None:
            return None
        candidate = value.strip().lower()
        if not candidate:
            return None
        local, separator, domain = candidate.partition("@")
        if not separator or not local or "." not in domain or domain.startswith("."):
            raise ValueError(
                f"'{value}' is not an email address. Supply a work address such as "
                f"'first.last@example.com', or omit the field entirely."
            )
        return candidate

    @field_validator("country")
    @classmethod
    def _uppercase_country(cls, value: str | None) -> str | None:
        """Normalise the country code to upper case.

        Args:
            value: The submitted code, if any.

        Returns:
            The uppercased code, or ``None``.
        """
        return value.upper() if value is not None else None

    def to_contact(self) -> Contact:
        """Translate into the canonical record the CRM layer understands.

        ``source`` is fixed to ``ENRICHMENT`` rather than taken from the input:
        it is the least privileged provenance, so under D-015 a synced value can
        fill an empty CRM field but can never overwrite a curated one.

        Returns:
            The canonical contact to persist.
        """
        return Contact(
            full_name=self.full_name,
            first_name=self.first_name,
            last_name=self.last_name,
            title=self.title,
            company_domain=self.company_domain.lower() if self.company_domain else None,
            company_name=self.company_name,
            email=self.email,
            phone=self.phone,
            city=self.city,
            country=self.country,
            linkedin_url=self.linkedin_url,
            source=RecordSource.ENRICHMENT,
            confidence=self.confidence,
        )
