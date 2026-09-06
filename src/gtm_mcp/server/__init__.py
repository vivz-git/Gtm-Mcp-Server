"""MCP server construction and lifecycle."""

from gtm_mcp.context import AppContext
from gtm_mcp.server.app import build_server
from gtm_mcp.server.lifespan import make_lifespan

__all__ = ["AppContext", "build_server", "make_lifespan"]
