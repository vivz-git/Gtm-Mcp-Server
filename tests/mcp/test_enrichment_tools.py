"""The enrichment tools as an MCP client actually sees them.

Driven through a real in-memory protocol session, so registration, schema
derivation, annotations, lifespan injection and error routing are all covered
together. Calling the Python functions directly would exercise none of that.

The server under test runs on the default offline provider, so the whole file
performs zero outbound requests and spends zero credits.
"""

from __future__ import annotations

from typing import Any

import pytest

from gtm_mcp.errors import ConfigurationError, ProviderError, RateLimitError
from gtm_mcp.providers.factory import EnrichmentProviders
from gtm_mcp.server.app import build_server
from gtm_mcp.settings import Settings
from mcp import Client, MCPError

pytestmark = [pytest.mark.mcp, pytest.mark.anyio]

ENRICHMENT_TOOLS = ("search_company", "search_contact")


class FailingProvider:
    """A provider that always raises, for exercising the tool error boundary."""

    def __init__(self, error: Exception) -> None:
        """Store the error every call should raise."""
        self._error = error

    @property
    def name(self) -> str:
        return "failing"

    @property
    def live(self) -> bool:
        return True

    async def enrich_company(self, query: Any) -> None:
        raise self._error

    async def enrich_contact(self, query: Any) -> None:
        raise self._error


def _install_failing_provider(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    """Make the server's lifespan build a provider that always fails this way.

    Patched at the factory the lifespan actually calls, so the server is wired
    exactly as in production and only the vendor adapter is substituted.

    Args:
        monkeypatch: The pytest patcher.
        error: The domain error every enrichment call should raise.
    """
    provider = FailingProvider(error)
    monkeypatch.setattr(
        "gtm_mcp.server.lifespan.build_enrichment_providers",
        lambda settings, http_client=None: EnrichmentProviders(company=provider, contact=provider),
    )


async def _call(client: Client, name: str, arguments: dict[str, Any]) -> Any:
    """Call a tool and return the raw result.

    Args:
        client: The connected client.
        name: Tool name.
        arguments: Tool arguments.

    Returns:
        The tool result.
    """
    return await client.call_tool(name, arguments)


# ---------------------------------------------------------------------------
# Registration and contract
# ---------------------------------------------------------------------------


async def test_both_enrichment_tools_are_registered(client: Client) -> None:
    names = {tool.name for tool in (await client.list_tools()).tools}
    assert set(ENRICHMENT_TOOLS) <= names


async def test_the_enrichment_tools_declare_read_only_open_world_annotations(
    client: Client,
) -> None:
    """They read from a third-party API: read-only, but not a closed world."""
    tools = {t.name: t for t in (await client.list_tools()).tools if t.name in ENRICHMENT_TOOLS}
    for tool in tools.values():
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.idempotent_hint is True
        assert tool.annotations.open_world_hint is True


async def test_the_enrichment_tools_describe_when_not_to_call_them(client: Client) -> None:
    """A model choosing between five GTM tools needs the negative cases too."""
    tools = {t.name: t for t in (await client.list_tools()).tools if t.name in ENRICHMENT_TOOLS}
    for tool in tools.values():
        description = (tool.description or "").lower()
        assert "do not call" in description
        assert "credit" in description, "cost is a decision input for the model"


async def test_input_schemas_carry_per_parameter_descriptions_and_bounds(
    client: Client,
) -> None:
    tools = {t.name: t for t in (await client.list_tools()).tools}

    company = tools["search_company"].input_schema["properties"]["domain_or_name"]
    assert company["description"]
    assert company["maxLength"] == 253
    assert company["minLength"] == 1

    contact = tools["search_contact"].input_schema
    assert set(contact["required"]) == {"name", "company"}
    for field in ("name", "company"):
        assert contact["properties"][field]["description"]


async def test_output_schemas_are_derived_and_advertise_the_found_flag(client: Client) -> None:
    """The flag is a computed field; it must appear in the schema the model reads."""
    tools = {t.name: t for t in (await client.list_tools()).tools}
    for name in ENRICHMENT_TOOLS:
        schema = tools[name].output_schema
        assert schema is not None
        assert "found" in schema["properties"]
        assert "provenance" in schema["properties"]


async def test_the_injected_context_is_not_a_model_parameter(client: Client) -> None:
    tools = {t.name: t for t in (await client.list_tools()).tools}
    for name in ENRICHMENT_TOOLS:
        assert "ctx" not in tools[name].input_schema.get("properties", {})


# ---------------------------------------------------------------------------
# Successful invocation
# ---------------------------------------------------------------------------


async def test_search_company_returns_a_canonical_structured_record(client: Client) -> None:
    result = await _call(client, "search_company", {"domain_or_name": "cloudscale.io"})

    assert result.is_error is False
    payload = result.structured_content
    assert payload is not None
    assert payload["found"] is True
    assert payload["company"]["domain"] == "cloudscale.io"
    assert payload["company"]["name"] == "CloudScale Systems"
    assert payload["company"]["employee_count"] == 450
    assert payload["company"]["source"] == "enrichment"


async def test_search_company_normalises_a_messy_identifier(client: Client) -> None:
    result = await _call(
        client, "search_company", {"domain_or_name": "HTTPS://WWW.CloudScale.io/pricing?x=1"}
    )
    payload = result.structured_content
    assert payload is not None
    assert payload["query"]["domain"] == "cloudscale.io"
    assert payload["found"] is True


async def test_search_company_resolves_a_company_name_when_the_provider_can(
    client: Client,
) -> None:
    result = await _call(client, "search_company", {"domain_or_name": "Apex FinTech Labs"})
    payload = result.structured_content
    assert payload is not None
    assert payload["found"] is True
    assert payload["company"]["domain"] == "apexfintech.com"


async def test_search_contact_returns_a_canonical_structured_record(client: Client) -> None:
    result = await _call(
        client, "search_contact", {"name": "Elena Rostova", "company": "cloudscale.io"}
    )

    assert result.is_error is False
    payload = result.structured_content
    assert payload is not None
    assert payload["found"] is True
    assert payload["contact"]["email"] == "elena.rostova@cloudscale.io"
    assert payload["contact"]["title"] == "VP of Engineering"
    assert payload["contact"]["company_domain"] == "cloudscale.io"


async def test_results_declare_their_provenance_including_whether_data_is_live(
    client: Client,
) -> None:
    """A demo dataset that cannot be told apart from real data is a liability."""
    result = await _call(client, "search_company", {"domain_or_name": "cloudscale.io"})
    payload = result.structured_content
    assert payload is not None

    assert payload["provenance"]["provider"] == "sample"
    assert payload["provenance"]["live"] is False
    assert payload["provenance"]["matched_on"] == "domain"
    assert "NOT real-world data" in payload["message"]


async def test_server_info_agrees_with_the_registered_tool_list(client: Client) -> None:
    info = await _call(client, "server_info", {})
    assert info.structured_content is not None
    implemented = set(info.structured_content["implemented_capabilities"])
    registered = {tool.name for tool in (await client.list_tools()).tools}

    assert set(ENRICHMENT_TOOLS) <= implemented
    assert implemented <= registered, "server_info must not claim a tool that is not registered"
    assert info.structured_content["enrichment_is_live"] is False
    assert info.structured_content["company_enrichment_provider"] == "sample"


# ---------------------------------------------------------------------------
# Not found, validation and failure behaviour
# ---------------------------------------------------------------------------


async def test_an_unknown_company_is_a_successful_call_reporting_no_match(
    client: Client,
) -> None:
    """`found: false` is a fact about the world; an error would invite retries."""
    result = await _call(client, "search_company", {"domain_or_name": "nosuchcompany.example"})

    assert result.is_error is False
    payload = result.structured_content
    assert payload is not None
    assert payload["found"] is False
    assert payload["company"] is None
    assert payload["provenance"] is None
    assert "not found" in payload["message"]


async def test_an_unknown_contact_is_a_successful_call_reporting_no_match(
    client: Client,
) -> None:
    result = await _call(
        client, "search_contact", {"name": "Nobody Here", "company": "cloudscale.io"}
    )
    assert result.is_error is False
    payload = result.structured_content
    assert payload is not None
    assert payload["found"] is False
    assert payload["contact"] is None


async def test_a_person_at_the_wrong_company_is_not_matched(client: Client) -> None:
    result = await _call(
        client, "search_contact", {"name": "Elena Rostova", "company": "apexfintech.com"}
    )
    payload = result.structured_content
    assert payload is not None
    assert payload["found"] is False


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("search_company", {"domain_or_name": ""}),
        ("search_contact", {"name": "Elena Rostova", "company": ""}),
        ("search_contact", {"name": "", "company": "cloudscale.io"}),
    ],
)
async def test_empty_input_is_rejected_by_the_derived_schema(
    client: Client, tool: str, arguments: dict[str, str]
) -> None:
    """Bad input never reaches a provider, and the model is told what to fix.

    The parameter's min_length rejects the call at the schema boundary, which
    costs nothing.
    """
    result = await _call(client, tool, arguments)

    assert result.is_error is True
    assert "at least 1 character" in str(result.content)


async def test_a_missing_required_argument_is_rejected(client: Client) -> None:
    result = await _call(client, "search_contact", {"name": "Elena Rostova"})

    assert result.is_error is True
    assert "company" in str(result.content)


async def test_a_provider_failure_reaches_the_model_as_a_readable_tool_error(
    test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provider failure stays in the tool result where the model can read it.

    The agent can then tell the user what happened instead of hanging on a
    dependency that is down.
    """
    _install_failing_provider(monkeypatch, ProviderError("upstream exploded"))
    async with Client(build_server(test_settings)) as client:
        result = await _call(client, "search_company", {"domain_or_name": "cloudscale.io"})

    assert result.is_error is True
    text = str(result.content)
    assert "provider_error" in text
    assert "Traceback" not in text


async def test_a_rate_limit_reaches_the_model_with_its_own_error_code(
    test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_failing_provider(monkeypatch, RateLimitError("quota exhausted"))
    async with Client(build_server(test_settings)) as client:
        result = await _call(client, "search_company", {"domain_or_name": "cloudscale.io"})

    assert result.is_error is True
    assert "rate_limit" in str(result.content)


async def test_a_server_misconfiguration_fails_the_request_instead_of_teaching_the_model(
    test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rejected API key fails the request rather than teaching the model.

    D-007: no better model choice fixes a server credential, so showing it to
    the model would only invite retries against a call that cannot succeed.
    """
    _install_failing_provider(monkeypatch, ConfigurationError("bad api key"))
    async with Client(build_server(test_settings)) as client:
        with pytest.raises(MCPError) as excinfo:
            await _call(client, "search_company", {"domain_or_name": "cloudscale.io"})

    assert "configuration_error" in str(excinfo.value)


async def test_the_read_tools_perform_no_database_work(client: Client) -> None:
    """Enrichment does not touch the CRM.

    The test server has no reachable database, so a read tool that quietly
    queried or wrote to it would fail here rather than return a record.
    """
    company = await _call(client, "search_company", {"domain_or_name": "cloudscale.io"})
    contact = await _call(
        client, "search_contact", {"name": "Elena Rostova", "company": "cloudscale.io"}
    )

    info = await _call(client, "server_info", {})
    assert info.structured_content is not None
    assert info.structured_content["database_available"] is False
    assert company.is_error is False
    assert contact.is_error is False
