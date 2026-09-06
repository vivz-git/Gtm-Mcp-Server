"""Input normalisation: the same company must resolve to the same key.

An agent passes through whatever the user wrote. If two spellings of one company
produce two different queries, enrichment is charged twice and the CRM join key
splits in half, so these cases are behaviour, not cosmetics.
"""

from __future__ import annotations

import pytest

from gtm_mcp.domain.identifiers import (
    looks_like_domain,
    normalize_company_query,
    normalize_contact_query,
    normalize_domain,
    split_person_name,
)
from gtm_mcp.errors import ValidationError

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "written",
    [
        "example.com",
        "EXAMPLE.COM",
        "www.example.com",
        "WWW.Example.com",
        "https://www.example.com/",
        "http://example.com/careers?ref=hn#apply",
        "https://EXAMPLE.com:8443/pricing",
        "  example.com  ",
        "example.com.",
        "jane.doe@example.com",
    ],
)
def test_every_spelling_of_one_company_resolves_to_one_domain(written: str) -> None:
    assert normalize_domain(written) == "example.com"


def test_subdomains_are_preserved_because_they_can_be_distinct_businesses() -> None:
    """Only 'www' is stripped: 'careers.example.com' is not 'example.com'."""
    assert normalize_domain("https://careers.example.com") == "careers.example.com"


@pytest.mark.parametrize(
    "written",
    [
        "",
        "   ",
        "Acme Inc.",
        "Stripe",
        "e.l.f.",
        "localhost",
        "192.168.0.1",
        "v1.2",
        "not a domain at all",
        "https://",
    ],
)
def test_text_that_is_not_a_domain_is_rejected_rather_than_guessed_at(written: str) -> None:
    assert normalize_domain(written) is None
    assert looks_like_domain(written) is False


def test_an_overlong_hostname_is_rejected() -> None:
    too_long = ".".join(["abcdefghij"] * 30) + ".com"
    assert normalize_domain(too_long) is None


def test_a_domain_input_becomes_a_domain_query() -> None:
    query = normalize_company_query("https://WWW.Stripe.com/pricing")
    assert query.domain == "stripe.com"
    assert query.name is None
    assert query.raw == "https://WWW.Stripe.com/pricing"
    assert query.describe == "stripe.com"


def test_a_name_input_becomes_a_name_query_and_no_domain_is_invented() -> None:
    """A name is kept as a name; no domain is invented from it.

    The dangerous alternative is appending '.com' and then confidently
    enriching a completely different company.
    """
    query = normalize_company_query("Acme Corporation")
    assert query.domain is None
    assert query.name == "Acme Corporation"


def test_a_blank_company_identifier_is_refused_with_actionable_guidance() -> None:
    with pytest.raises(ValidationError) as excinfo:
        normalize_company_query("   ")
    assert "domain" in str(excinfo.value)


@pytest.mark.parametrize(
    ("full_name", "expected"),
    [
        ("Elena Rostova", ("Elena", "Rostova")),
        ("Jean-Luc van der Berg", ("Jean-Luc", "van der Berg")),
        ("  Priya   Raghunathan ", ("Priya", "Raghunathan")),
        ("Cher", (None, None)),
    ],
)
def test_names_are_split_without_being_mangled(
    full_name: str, expected: tuple[str | None, str | None]
) -> None:
    assert split_person_name(full_name.strip()) == expected


def test_a_contact_query_carries_both_the_person_and_the_normalised_employer() -> None:
    query = normalize_contact_query("Elena Rostova", "HTTPS://www.CloudScale.io")
    assert query.full_name == "Elena Rostova"
    assert query.first_name == "Elena"
    assert query.last_name == "Rostova"
    assert query.company.domain == "cloudscale.io"


def test_a_contact_query_accepts_a_company_name_as_well_as_a_domain() -> None:
    query = normalize_contact_query("Elena Rostova", "CloudScale Systems")
    assert query.company.domain is None
    assert query.company.name == "CloudScale Systems"


@pytest.mark.parametrize(
    ("name", "company"),
    [("", "cloudscale.io"), ("   ", "cloudscale.io"), ("Elena Rostova", ""), ("Elena", "   ")],
)
def test_a_contact_query_requires_both_a_person_and_an_employer(name: str, company: str) -> None:
    """A name alone identifies nobody: the same name recurs across companies."""
    with pytest.raises(ValidationError):
        normalize_contact_query(name, company)
