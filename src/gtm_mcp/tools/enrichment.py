"""External enrichment tools: ``search_company`` and ``search_contact``.

Both are read-only. They call an external provider, translate the result into
this server's canonical models and hand it back — they never touch the CRM.
That separation is the point: the agent presents what enrichment found, and a
human decides whether it is written anywhere, which is a Phase 4 write tool with
its own guardrails and audit trail.

The tools stay thin. Normalisation and provider orchestration are the service's
job; the only work done here is translating a ``GTMError`` into the right MCP
failure channel, once, through ``to_mcp_exception`` (D-007).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from gtm_mcp.context import AppContext
from gtm_mcp.domain.enrichment import CompanyLookup, ContactLookup
from gtm_mcp.errors import GTMError, to_mcp_exception

if TYPE_CHECKING:
    from mcp.server import MCPServer

#: Applied to both tools. ``open_world_hint`` is the honest one here: unlike
#: ``server_info``, these reach a third-party API whose contents this server does
#: not control and cannot enumerate.
_READ_ONLY_EXTERNAL = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)


def register_enrichment_tools(mcp: MCPServer[AppContext]) -> None:
    """Register the external enrichment tools on the server.

    Args:
        mcp: The server to register tools on.
    """

    @mcp.tool(title="Enrich a company", annotations=_READ_ONLY_EXTERNAL)
    async def search_company(
        ctx: Context[AppContext],
        domain_or_name: Annotated[
            str,
            Field(
                min_length=1,
                max_length=253,
                description="The company's web domain (strongly preferred: 'stripe.com', "
                "'https://www.stripe.com/pricing' and 'STRIPE.COM' are all accepted and "
                "resolve to the same company), or failing that its name ('Stripe'). A "
                "domain is an exact identifier; a name is a guess, and some providers "
                "cannot resolve one at all.",
            ),
        ],
    ) -> CompanyLookup:
        """Look up firmographic data about a company from an external provider.

        Call this when you need facts about a company that you do not already
        have — industry, headcount, headquarters location, description or
        LinkedIn page — for example when qualifying an account or preparing
        outreach. Prefer the company's web domain: it is this server's join key
        across enrichment and the CRM, and it identifies a company exactly where
        a name only approximates one.

        Do not call this to find out what your CRM already knows about an
        account; that is a separate CRM tool and it costs nothing. Do not call
        it repeatedly with variations of the same identifier: each call may
        consume a paid provider credit, and a `found: false` result is a
        definitive answer, not an invitation to rephrase.

        This tool only reads. It never creates or updates a CRM record, so
        anything worth keeping must be written deliberately with a write tool.

        Returns a result whose `found` flag separates "the provider has no such
        company" from a failure, the canonical company record when there is one,
        and a `provenance` block naming the provider and whether the data came
        from a live API or this server's offline sample dataset.
        """
        app = ctx.request_context.lifespan_context
        try:
            return await app.enrichment.search_company(domain_or_name)
        except GTMError as exc:
            raise to_mcp_exception(exc) from exc

    @mcp.tool(title="Enrich a contact", annotations=_READ_ONLY_EXTERNAL)
    async def search_contact(
        ctx: Context[AppContext],
        name: Annotated[
            str,
            Field(
                min_length=1,
                max_length=200,
                description="The person's full name as it would appear professionally, "
                "e.g. 'Elena Rostova'. Not an email address and not a job title.",
            ),
        ],
        company: Annotated[
            str,
            Field(
                min_length=1,
                max_length=253,
                description="The employer, as a web domain ('cloudscale.io', preferred and "
                "far more precise) or a company name ('CloudScale Systems'). Required: a "
                "name on its own cannot identify a person, because the same name recurs "
                "across companies.",
            ),
        ],
    ) -> ContactLookup:
        """Look up a specific person at a specific company from an external provider.

        Call this when you know who you are looking for and where they work, and
        you need their work email, job title or LinkedIn profile — for example
        after `search_company` has told you an account is worth pursuing. Supply
        the employer's domain when you have one; matching a person by company
        name is materially less reliable.

        Do not call this to browse for people at a company: it resolves one
        named person per call, does not list employees, and each call may
        consume a paid provider credit. Do not retry with the same name and
        company after a `found: false` result — vary the input (a corrected
        spelling, or the domain instead of the name) or report that no record
        exists.

        This tool only reads. The person is not added to the CRM or to any list;
        use a write tool if that is what the user asked for.

        Returns a result whose `found` flag separates "no such person on record"
        from a failure, the canonical contact record when there is one including
        a `confidence` score where the provider reports it, and a `provenance`
        block naming the provider and whether the data is live or from this
        server's offline sample dataset.
        """
        app = ctx.request_context.lifespan_context
        try:
            return await app.enrichment.search_contact(name, company)
        except GTMError as exc:
            raise to_mcp_exception(exc) from exc
