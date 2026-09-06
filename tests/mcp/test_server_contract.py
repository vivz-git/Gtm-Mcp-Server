"""The MCP surface this server presents to a client.

These run a real protocol session over the SDK's in-memory transport, so they
cover registration, schema derivation, annotations and lifespan injection
together rather than asserting on Python objects that never crossed the wire.
"""

from __future__ import annotations

import pytest

from mcp import Client

pytestmark = [pytest.mark.mcp, pytest.mark.anyio]

#: Substrings that must never appear in a tool name. The architecture forbids
#: destructive operations, and this is the test that keeps a future tool from
#: quietly introducing one.
FORBIDDEN_NAME_FRAGMENTS = ("delete", "remove", "destroy", "purge", "drop", "truncate", "wipe")


async def test_server_advertises_an_explicit_version(client: Client) -> None:
    """SDK v2 reports an empty version unless one is passed to MCPServer."""
    info = client.server_info
    assert info is not None
    assert info.name == "gtm-mcp-server"
    assert info.version not in ("", None)


async def test_instructions_guide_tool_selection(client: Client) -> None:
    """Instructions are the model's only orientation before it picks a tool."""
    instructions = client.instructions
    assert instructions is not None
    assert "write" in instructions.lower()


async def test_server_info_tool_is_registered(client: Client) -> None:
    names = {tool.name for tool in (await client.list_tools()).tools}
    assert "server_info" in names


async def test_no_tool_name_implies_a_destructive_operation(client: Client) -> None:
    for tool in (await client.list_tools()).tools:
        lowered = tool.name.lower()
        offenders = [f for f in FORBIDDEN_NAME_FRAGMENTS if f in lowered]
        assert not offenders, f"tool {tool.name!r} suggests destruction: {offenders}"


async def test_every_tool_declares_behavioural_annotations(client: Client) -> None:
    """Annotations drive host confirmation prompts; an unannotated tool is a gap."""
    for tool in (await client.list_tools()).tools:
        assert tool.annotations is not None, f"{tool.name} has no annotations"
        assert tool.annotations.read_only_hint is not None, f"{tool.name} lacks read_only_hint"


async def test_read_only_tools_are_annotated_as_non_destructive(client: Client) -> None:
    for tool in (await client.list_tools()).tools:
        annotations = tool.annotations
        assert annotations is not None
        if annotations.read_only_hint:
            assert annotations.destructive_hint is not True, (
                f"{tool.name} is marked read-only and destructive"
            )


async def test_every_tool_has_a_description_written_for_an_agent(client: Client) -> None:
    """A one-line description is not enough for a model to choose correctly."""
    for tool in (await client.list_tools()).tools:
        assert tool.description, f"{tool.name} has no description"
        assert len(tool.description) > 80, f"{tool.name} description is too thin to choose on"


async def test_schemas_are_derived_from_type_hints(client: Client) -> None:
    """No hand-written JSON Schema: the SDK derives both directions."""
    tool = next(t for t in (await client.list_tools()).tools if t.name == "server_info")
    assert tool.input_schema["type"] == "object"
    assert tool.output_schema is not None
    assert "database_available" in tool.output_schema["properties"]


async def test_the_injected_context_is_not_exposed_as_a_model_parameter(client: Client) -> None:
    """`ctx` is server-side injection; a model must never be asked to supply it."""
    tool = next(t for t in (await client.list_tools()).tools if t.name == "server_info")
    assert tool.input_schema.get("properties", {}) == {}


async def test_server_info_returns_structured_status(client: Client) -> None:
    result = await client.call_tool("server_info", {})

    assert result.is_error is False
    assert result.structured_content is not None
    payload = result.structured_content
    assert payload["server_name"] == "gtm-mcp-server"
    assert payload["environment"] == "test"
    assert isinstance(payload["database_available"], bool)


async def test_server_info_reports_the_database_as_unavailable_in_tests(client: Client) -> None:
    """The test DSN points at a closed port: startup must degrade, not crash."""
    result = await client.call_tool("server_info", {})
    assert result.structured_content is not None
    assert result.structured_content["database_available"] is False


async def test_planned_capabilities_are_declared_but_not_callable(client: Client) -> None:
    """Honest self-report: what is listed as planned must not be registered.

    The planned list is empty now that the CRM tools have shipped. The assertion
    is kept general rather than deleted, because its job is to catch the next
    capability that is announced before it exists.
    """
    result = await client.call_tool("server_info", {})
    assert result.structured_content is not None
    planned = set(result.structured_content["planned_capabilities"])

    registered = {tool.name for tool in (await client.list_tools()).tools}
    assert planned.isdisjoint(registered)


async def test_the_capabilities_shipped_in_this_phase_are_reported_as_implemented(
    client: Client,
) -> None:
    """The three Phase 4 tools moved from planned to implemented, in both places."""
    result = await client.call_tool("server_info", {})
    assert result.structured_content is not None
    implemented = set(result.structured_content["implemented_capabilities"])
    planned = set(result.structured_content["planned_capabilities"])

    phase_four = {"crm_query", "sync_to_crm", "save_to_list"}
    assert phase_four <= implemented
    assert phase_four.isdisjoint(planned)


async def test_the_write_guardrails_are_reported_so_an_agent_can_plan(client: Client) -> None:
    """An agent that cannot see writes are off will keep trying to write."""
    result = await client.call_tool("server_info", {})
    assert result.structured_content is not None
    payload = result.structured_content

    assert payload["write_tools_enabled"] is False
    assert payload["dry_run_writes"] is False
    assert payload["max_write_batch_size"] >= 1


async def test_implemented_capabilities_are_all_actually_registered(client: Client) -> None:
    """Nothing is announced as implemented that cannot actually be called.

    The other half of the honesty claim, and it also catches a capability listed
    as both implemented and planned.
    """
    result = await client.call_tool("server_info", {})
    assert result.structured_content is not None
    implemented = set(result.structured_content["implemented_capabilities"])
    planned = set(result.structured_content["planned_capabilities"])
    registered = {tool.name for tool in (await client.list_tools()).tools}

    assert implemented == registered
    assert implemented.isdisjoint(planned)
