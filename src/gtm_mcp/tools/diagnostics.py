"""Diagnostic tools.

``server_info`` is the only tool implemented in the foundation phase. It exists
for two reasons beyond its own usefulness: it makes the MCP wiring verifiable
end to end (registration, schema derivation, annotations, lifespan injection),
and it gives an agent a way to discover whether write capability and the CRM
database are actually available before it plans a workflow.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from gtm_mcp import __version__
from gtm_mcp.context import AppContext

if TYPE_CHECKING:
    from mcp.server import MCPServer


class ServerInfo(BaseModel):
    """Operational status of the GTM MCP server."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    server_name: str = Field(description="Configured name of this server.")
    version: str = Field(description="Server package version.")
    environment: str = Field(description="Environment the server is running in.")
    database_available: bool = Field(
        description="Whether the CRM database is reachable. CRM tools fail when false."
    )
    write_tools_enabled: bool = Field(
        description="Whether write tools may modify data. When false they refuse every call."
    )
    dry_run_writes: bool = Field(
        description="When true, write tools validate and audit but never persist changes. "
        "A 'dry_run' outcome means nothing was written."
    )
    max_write_batch_size: int = Field(
        description="Maximum records a single write tool call may modify. The current write "
        "tools handle one record per call regardless."
    )
    company_enrichment_provider: str = Field(
        description="Identifier of the provider backing search_company."
    )
    contact_enrichment_provider: str = Field(
        description="Identifier of the provider backing search_contact."
    )
    enrichment_is_live: bool = Field(
        description="True when enrichment calls a real external API. False means the "
        "search tools return this server's offline synthetic dataset, which must not be "
        "presented to a user as real-world data."
    )
    implemented_capabilities: tuple[str, ...] = Field(
        description="GTM capabilities that are implemented and callable right now."
    )
    planned_capabilities: tuple[str, ...] = Field(
        description="GTM capabilities that are designed but not yet callable. "
        "Do not attempt to call these."
    )


# Capability lists are declared here, next to the tool that reports them, so
# that enabling a tool and announcing it are a single edit. Each entry moves
# from planned to implemented as its tool is registered.
_IMPLEMENTED: tuple[str, ...] = (
    "server_info",
    "search_company",
    "search_contact",
    "crm_query",
    "sync_to_crm",
    "save_to_list",
)

#: Nothing is planned-but-unregistered at present. The field stays in the
#: response because an empty list is itself the honest answer, and because the
#: contract test asserts that whatever appears here is genuinely not callable.
_PLANNED: tuple[str, ...] = ()


def register_diagnostics_tools(mcp: MCPServer[AppContext]) -> None:
    """Register diagnostic tools on the server.

    Args:
        mcp: The server to register tools on.
    """

    @mcp.tool(
        title="GTM server status",
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    def server_info(ctx: Context[AppContext]) -> ServerInfo:
        """Report which GTM capabilities this server can currently perform.

        Call this first when you are unsure whether a GTM capability is
        available, or when a data-modifying call has failed and you need to know
        whether writes are disabled. It takes no arguments, reads no customer
        data, and changes nothing.

        Returns which tools are implemented versus merely planned, whether the
        CRM database is reachable, and whether write tools are permitted to
        modify data. Do not call tools listed under planned capabilities; they
        are not registered and the call will fail.
        """
        app = ctx.request_context.lifespan_context
        settings = app.settings
        return ServerInfo(
            server_name=settings.server_name,
            version=__version__,
            environment=settings.environment,
            database_available=app.database_available,
            write_tools_enabled=settings.enable_write_tools,
            dry_run_writes=settings.dry_run_writes,
            max_write_batch_size=settings.max_write_batch_size,
            company_enrichment_provider=app.enrichment.company_provider_name,
            contact_enrichment_provider=app.enrichment.contact_provider_name,
            enrichment_is_live=app.enrichment.live,
            implemented_capabilities=_IMPLEMENTED,
            planned_capabilities=_PLANNED,
        )
