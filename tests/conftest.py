"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from mcp.server import MCPServer

from gtm_mcp.context import AppContext
from gtm_mcp.server.app import build_server
from gtm_mcp.settings import Settings
from mcp import Client


@pytest.fixture
def anyio_backend() -> str:
    """Run async tests on asyncio only; the server has no trio-specific code."""
    return "asyncio"


@pytest.fixture
def test_settings() -> Settings:
    """Settings for tests.

    The DSN points at a closed port on purpose: the startup probe must fail
    fast and the server must still come up, which is exactly the degraded-mode
    behaviour we want covered by default.
    """
    return Settings(
        environment="test",
        log_format="console",
        database_url="postgresql+asyncpg://nobody:nobody@127.0.0.1:1/gtm_test",
        db_connect_timeout_seconds=0.25,
    )


@pytest.fixture
def server(test_settings: Settings) -> MCPServer[AppContext]:
    """A freshly built server instance, not yet started."""
    return build_server(test_settings)


@pytest.fixture
async def client(server: MCPServer[AppContext]) -> AsyncIterator[Client]:
    """An in-memory MCP client connected to the server.

    Exercises the real protocol path (initialize, tools/list, tools/call)
    without a subprocess or a socket. ``raise_exceptions=True`` surfaces genuine
    handler bugs instead of the sanitized message a real client would see.
    """
    async with Client(server, raise_exceptions=True) as connected:
        yield connected
