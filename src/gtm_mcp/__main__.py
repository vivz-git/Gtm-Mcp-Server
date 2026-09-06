"""Entry point for running the GTM MCP server.

Invoked as ``gtm-mcp-server`` (console script), ``python -m gtm_mcp``, or by an
MCP client that spawns the process over stdio.
"""

from __future__ import annotations

from gtm_mcp.logging_setup import configure_logging, get_logger
from gtm_mcp.server.app import build_server
from gtm_mcp.settings import get_settings


def main() -> None:
    """Configure logging and run the server on the configured transport.

    Logging is configured before the server is built so that startup failures
    are reported through the same structured sink as everything else.
    """
    settings = get_settings()
    configure_logging(level=settings.log_level, log_format=settings.log_format)

    log = get_logger(__name__)
    log.info("starting_server", transport=settings.transport)

    server = build_server(settings)

    if settings.transport == "streamable-http":
        # Transport options moved from the constructor to run() in SDK v2.
        server.run(
            transport="streamable-http",
            host=settings.http_host,
            port=settings.http_port,
        )
    else:
        server.run(transport="stdio")


if __name__ == "__main__":
    main()
