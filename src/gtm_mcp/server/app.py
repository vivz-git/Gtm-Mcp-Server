"""Construction of the MCP server instance.

``build_server`` is a factory rather than a module-level singleton. Tests build
a fresh server per test and connect an in-memory client to it, which is only
possible if construction is a callable rather than an import side effect.
"""

from __future__ import annotations

from mcp.server import MCPServer

from gtm_mcp import __version__
from gtm_mcp.context import AppContext
from gtm_mcp.server.lifespan import make_lifespan
from gtm_mcp.settings import Settings, get_settings
from gtm_mcp.tools import register_all

#: Guidance shown to the client during initialization. Written for the model
#: that will be choosing among these tools, not for a human reading docs.
SERVER_INSTRUCTIONS = """\
This server provides go-to-market (GTM) data capabilities: enriching companies
and people from external sources, querying a CRM, and writing enriched records
back to it.

Choosing a tool:
- To learn about a company, start from its web domain when you have one; domains
  are the join key across every tool here.
- Read tools never modify data and are safe to call speculatively.
- Write tools modify CRM state. Call them only when the user has asked for a
  change, and use the enriched record you just retrieved rather than values you
  inferred.

Interpreting write results: every write tool returns an explicit outcome. Treat
`created`, `updated` and `unchanged` as done. Treat `dry_run` as NOT done, the
server is configured to skip persistence. Treat `rejected` and `failed` as not
done and report the reason to the user rather than retrying blindly.

Call `server_info` if you need to know which capabilities are currently
available before planning a multi-step workflow.
"""


def build_server(settings: Settings | None = None) -> MCPServer[AppContext]:
    """Build a fully configured GTM MCP server.

    Args:
        settings: Configuration to use. Defaults to the process settings.

    Returns:
        A server with all tools registered and the lifespan attached, ready to
        be run over a transport or driven by an in-memory client.
    """
    resolved = settings or get_settings()

    mcp: MCPServer[AppContext] = MCPServer(
        name=resolved.server_name,
        title="GTM MCP Server",
        # Passing an explicit version matters on the v2 SDK: a server built
        # without one reports an empty string in serverInfo rather than
        # falling back to the SDK's own version as v1 did.
        version=__version__,
        instructions=SERVER_INSTRUCTIONS,
        lifespan=make_lifespan(resolved),
        # Registering the same tool name twice is a bug, not a warning.
        warn_on_duplicate_tools=True,
    )
    register_all(mcp)
    return mcp
