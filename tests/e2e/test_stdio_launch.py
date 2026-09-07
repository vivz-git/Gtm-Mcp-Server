"""End-to-end proof of the launch contract an MCP host actually uses.

Every other test in this suite connects an in-memory client to a server object
built inside the test process. That covers registration, schema derivation and
handler behaviour, but it cannot fail for any of the reasons a real client
integration fails: a broken console script, a working directory the packaging
cannot resolve, a stray write to stdout corrupting the JSON-RPC stream, or a
process that will not exit when the host closes its stdin.

These tests spawn the server the way `.mcp.json` tells a host to spawn it —
``uv run gtm-mcp-server`` over stdio, as a separate process — and drive it with
the SDK's own stdio client. They are deterministic (the offline sample provider,
no model, no network) so they belong in the normal gate, and they skip cleanly
when ``uv`` is absent rather than failing for it.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from mcp import Client, StdioServerParameters

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: The launch contract, resolved from the repository rather than retyped, so a
#: change to `.mcp.json` that breaks the launch fails here instead of silently
#: in someone's client.
MCP_CONFIG_PATH = PROJECT_ROOT / ".mcp.json"

#: Enough configuration to make the run reproducible and free: the offline
#: sample provider spends no enrichment credit, and writes stay off unless a
#: test turns them on.
BASE_ENV: dict[str, str] = {
    "GTM_ENVIRONMENT": "test",
    "GTM_LOG_LEVEL": "WARNING",
    "GTM_ENRICHMENT_PROVIDER": "sample",
    "GTM_ENABLE_WRITE_TOOLS": "false",
    "GTM_DRY_RUN_WRITES": "false",
}


def _launch_params(**env_overrides: str) -> StdioServerParameters:
    """Build the stdio launch parameters from the committed MCP configuration.

    Args:
        **env_overrides: Extra server environment for a specific test.

    Returns:
        Parameters that spawn the server exactly as a host would.
    """
    config: dict[str, Any] = json.loads(MCP_CONFIG_PATH.read_text(encoding="utf-8"))
    server = config["mcpServers"]["gtm"]
    return StdioServerParameters(
        command=server["command"],
        # `.mcp.json` deliberately carries no absolute path, so the host's
        # working directory is what resolves the project. A test process runs
        # from wherever pytest was invoked, so it supplies the root explicitly.
        args=list(server["args"]),
        cwd=PROJECT_ROOT,
        env={**BASE_ENV, **env_overrides},
    )


@pytest.fixture(scope="module", autouse=True)
def require_uv() -> None:
    """Skip the module when the launcher named in `.mcp.json` is unavailable."""
    if shutil.which("gtm-mcp-server") is None and shutil.which("uv") is None:
        pytest.skip("uv is not installed; cannot exercise the documented launch contract")


async def test_host_can_launch_server_and_discover_tools() -> None:
    """A host spawning the documented command completes initialize and tools/list."""
    async with Client(_launch_params(), raise_exceptions=False) as client:
        assert client.server_info is not None
        assert client.server_info.name == "gtm-mcp-server"
        # An empty version here is the v2 regression D-002 guards against.
        assert client.server_info.version
        assert client.protocol_version

        listing = await client.list_tools()
        discovered = {tool.name for tool in listing.tools}

    assert discovered == {
        "server_info",
        "search_company",
        "search_contact",
        "crm_query",
        "sync_to_crm",
        "save_to_list",
    }


async def test_discovered_schemas_are_usable_by_a_model() -> None:
    """Every tool arrives with an input schema and the annotations a host reads."""
    async with Client(_launch_params(), raise_exceptions=False) as client:
        listing = await client.list_tools()

    for tool in listing.tools:
        assert tool.description, f"{tool.name} has no description"
        assert tool.annotations is not None, f"{tool.name} has no annotations"
        assert tool.input_schema.get("type") == "object"

    by_name = {tool.name: tool for tool in listing.tools}
    for write_tool in ("sync_to_crm", "save_to_list"):
        annotations = by_name[write_tool].annotations
        assert annotations is not None
        assert annotations.read_only_hint is False
        assert annotations.destructive_hint is False
        assert annotations.idempotent_hint is True


async def test_enrichment_round_trip_over_a_real_transport() -> None:
    """A read tool returns a validated structured payload through the subprocess."""
    async with Client(_launch_params(), raise_exceptions=False) as client:
        result = await client.call_tool(
            "search_company", {"domain_or_name": "northwindlogistics.com"}
        )

    assert result.is_error is False
    payload = result.structured_content
    assert payload is not None
    assert payload["found"] is True
    assert payload["company"]["domain"] == "northwindlogistics.com"
    # Sample data must announce itself over the wire, not only in-process.
    assert payload["provenance"]["live"] is False


async def test_disabled_writes_are_refused_and_audited_end_to_end() -> None:
    """A write against a read-only deployment is rejected, and says so."""
    async with Client(_launch_params(), raise_exceptions=False) as client:
        result = await client.call_tool(
            "sync_to_crm",
            {
                "contact": {
                    "full_name": "Dana Whitfield",
                    "email": "dana.whitfield@northwindlogistics.com",
                    "company_domain": "northwindlogistics.com",
                }
            },
        )

    payload = result.structured_content
    assert payload is not None
    assert payload["outcome"] == "rejected"
    assert payload["success"] is False
    assert payload["record_id"] is None
    # Every attempt is audited, including the ones that changed nothing.
    assert payload["audit_id"]
    assert "disabled" in payload["message"].lower()


async def test_invalid_arguments_surface_as_a_protocol_error() -> None:
    """A schema violation fails the call rather than reaching the service."""
    async with Client(_launch_params(), raise_exceptions=False) as client:
        result = await client.call_tool("crm_query", {"country": "United States"})

    assert result.is_error is True


@pytest.mark.skipif(sys.platform == "emscripten", reason="requires a real subprocess")
async def test_server_writes_nothing_but_protocol_to_stdout() -> None:
    """Logs go to stderr; stdout carries JSON-RPC and nothing else.

    The stdio client would fail to parse a corrupted stream, so the assertion is
    that a full session works with logging turned all the way up — the setting
    most likely to produce a stray line.
    """
    async with Client(_launch_params(GTM_LOG_LEVEL="DEBUG"), raise_exceptions=False) as client:
        listing = await client.list_tools()
        result = await client.call_tool("server_info", {})

    assert listing.tools
    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["server_name"] == "gtm-mcp-server"
