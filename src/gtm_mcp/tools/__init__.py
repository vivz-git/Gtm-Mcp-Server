"""MCP tool layer.

Each module registers one cohesive group of tools against an ``MCPServer``.
Tools are thin: they validate input, delegate to the service layer, translate
domain errors into MCP failures, and shape the response. Business logic does not
live here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gtm_mcp.context import AppContext
from gtm_mcp.tools.crm import register_crm_tools
from gtm_mcp.tools.diagnostics import register_diagnostics_tools
from gtm_mcp.tools.enrichment import register_enrichment_tools

if TYPE_CHECKING:
    from mcp.server import MCPServer


def register_all(mcp: MCPServer[AppContext]) -> None:
    """Register every tool group on the server.

    The single registration entry point keeps ``build_server`` free of a growing
    list of imports and makes the server's full tool surface greppable.

    Args:
        mcp: The server to register tools on.
    """
    register_diagnostics_tools(mcp)
    register_enrichment_tools(mcp)
    register_crm_tools(mcp)


__all__ = ["register_all"]
