"""Normalisation of the free-text identifiers an agent passes to a search tool.

An agent will hand us whatever the user wrote: ``https://www.Stripe.com/pricing``,
``stripe.com``, ``STRIPE.COM``, or just ``Stripe``. Providers, on the other hand,
match on precise inputs, and a company domain is this system's join key across
enrichment and the CRM. Normalising here — once, in the domain layer — means every
provider adapter receives the same canonical query and no adapter reimplements
its own string handling.

The deliberate non-goal is guessing. A string that does not parse as a domain is
treated as a *name*, never turned into one by appending ``.com``: a wrong domain
silently returns a confident answer about the wrong company, which is worse than
telling the agent it needs to supply a domain.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

from gtm_mcp.errors import ValidationError

#: Longest legal fully-qualified domain name, per RFC 1035.
MAX_DOMAIN_LENGTH = 253

_SCHEME = re.compile(r"^[a-z][a-z0-9+.\-]*://")
_LABEL = r"[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?"
#: A hostname whose final label is alphabetic. Requiring an alphabetic suffix is
#: what keeps bare IPv4 addresses and version-like strings ("v1.2") out.
_DOMAIN = re.compile(rf"^{_LABEL}(?:\.{_LABEL})*\.[a-z]{{2,}}$")


def normalize_domain(value: str) -> str | None:
    """Reduce a URL, host, email address or bare domain to a canonical domain.

    ``https://www.Example.com/careers?ref=x`` and ``EXAMPLE.COM.`` both reduce to
    ``example.com``, so the same company resolves to the same key however the
    agent phrased it.

    Args:
        value: Raw text that may or may not contain a domain.

    Returns:
        The lowercased, ``www``-stripped domain, or ``None`` if the input does
        not contain a syntactically valid one.
    """
    candidate = value.strip().lower()
    if not candidate:
        return None

    candidate = _SCHEME.sub("", candidate)
    if "@" in candidate:
        # Covers both an email address and a URL's userinfo section.
        candidate = candidate.rsplit("@", 1)[1]
    for separator in ("/", "?", "#"):
        candidate = candidate.split(separator, 1)[0]
    candidate = candidate.split(":", 1)[0].strip().rstrip(".")
    if candidate.startswith("www."):
        candidate = candidate.removeprefix("www.")

    if not candidate or len(candidate) > MAX_DOMAIN_LENGTH:
        return None
    if _DOMAIN.match(candidate) is None:
        return None
    return candidate


def looks_like_domain(value: str) -> bool:
    """Whether a free-text identifier should be read as a domain rather than a name.

    Args:
        value: Raw text supplied by the agent.

    Returns:
        ``True`` if the text parses as a domain. Company names containing a dot
        but no valid suffix (``e.l.f.``) and any text containing whitespace
        (``Acme Inc.``) read as names.
    """
    return normalize_domain(value) is not None


def split_person_name(full_name: str) -> tuple[str | None, str | None]:
    """Split a display name into a given name and a family name.

    Providers match better when given both parts, but a single-token name
    ("Cher") or a multi-part family name ("van der Berg") must not be mangled.
    The first whitespace-separated token is taken as the given name and the
    remainder, unmodified, as the family name.

    Args:
        full_name: The person's name as the agent supplied it.

    Returns:
        ``(first_name, last_name)``, either of which may be ``None`` when the
        name cannot be split with confidence.
    """
    parts = full_name.split()
    if len(parts) < 2:
        return (None, None)
    return (parts[0], " ".join(parts[1:]))


class CompanyQuery(BaseModel):
    """A normalised company identifier, ready to hand to a provider adapter.

    Carries the domain and the name separately rather than one ambiguous string,
    so an adapter can select the right provider parameter — and can refuse
    explicitly when it only supports one of them — instead of guessing.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    raw: str = Field(description="The identifier exactly as the agent supplied it, trimmed.")
    domain: str | None = Field(
        default=None, description="Canonical web domain, when the input contained one."
    )
    name: str | None = Field(
        default=None, description="Company name, when the input was not a domain."
    )

    @model_validator(mode="after")
    def _require_an_identifier(self) -> CompanyQuery:
        """Reject a query that names no company at all.

        Returns:
            The validated query.

        Raises:
            ValueError: Neither a domain nor a name is present.
        """
        if self.domain is None and self.name is None:
            raise ValueError("a company query needs either a domain or a name")
        return self

    @property
    def describe(self) -> str:
        """A short, log-safe rendering of what was searched for."""
        return self.domain or self.name or self.raw


class ContactQuery(BaseModel):
    """A normalised person identifier plus the company they work at."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    full_name: str = Field(min_length=1, description="The person's full name as supplied.")
    first_name: str | None = Field(default=None, description="Given name, when confidently split.")
    last_name: str | None = Field(default=None, description="Family name, when confidently split.")
    company: CompanyQuery = Field(description="The employer, normalised the same way.")


def normalize_company_query(domain_or_name: str) -> CompanyQuery:
    """Build a :class:`CompanyQuery` from a single free-text identifier.

    Args:
        domain_or_name: A domain, a URL, an email address or a company name.

    Returns:
        The normalised query.

    Raises:
        ValidationError: The input is blank once trimmed.
    """
    text = domain_or_name.strip()
    if not text:
        raise ValidationError(
            "A company identifier is required. Pass a web domain such as 'stripe.com' "
            "(preferred, it is the join key across these tools) or a company name.",
        )

    domain = normalize_domain(text)
    if domain is not None:
        return CompanyQuery(raw=text, domain=domain, name=None)
    return CompanyQuery(raw=text, domain=None, name=text)


def normalize_contact_query(name: str, company: str) -> ContactQuery:
    """Build a :class:`ContactQuery` from a person's name and their employer.

    Args:
        name: The person's full name.
        company: The employer's domain or name.

    Returns:
        The normalised query.

    Raises:
        ValidationError: Either argument is blank once trimmed.
    """
    person = name.strip()
    if not person:
        raise ValidationError(
            "A person's full name is required, for example 'Elena Rostova'.",
        )
    if not company.strip():
        raise ValidationError(
            "A company is required to disambiguate the person. Pass the employer's "
            "web domain such as 'cloudscale.io', or its name.",
        )

    first_name, last_name = split_person_name(person)
    return ContactQuery(
        full_name=person,
        first_name=first_name,
        last_name=last_name,
        company=normalize_company_query(company),
    )
