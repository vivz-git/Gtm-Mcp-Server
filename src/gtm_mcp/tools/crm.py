"""CRM tools: ``crm_query``, ``sync_to_crm`` and ``save_to_list``.

One read tool and two write tools, all thin. They validate through Pydantic,
delegate to :class:`~gtm_mcp.services.crm.CrmService`, and translate a domain
error into the right MCP failure channel. No guardrail check, no merge rule and
no SQL lives here — putting any of that in a tool would mean a second write tool
could implement it differently, which is exactly the drift the service layer
exists to prevent.

Write tools return a :class:`~gtm_mcp.domain.results.WriteResult` rather than
raising for an outcome the agent can act on: "writes are disabled", "no such
contact" and "already a member" are all results a model should read and respond
to, not protocol errors it never sees. Only a broken server — an unreachable
database, an unwritable audit trail — fails the request itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from gtm_mcp.context import AppContext
from gtm_mcp.domain.models import ContactFilter, ContactQueryResult
from gtm_mcp.domain.results import WriteResult
from gtm_mcp.domain.writes import ContactSyncInput
from gtm_mcp.errors import GTMError, to_mcp_exception

if TYPE_CHECKING:
    from mcp.server import MCPServer

#: The CRM read tool touches this server's own database, whose contents are
#: enumerable, hence ``open_world_hint=False`` — unlike the enrichment tools.
_READ_ONLY_LOCAL = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

#: Both writes are non-destructive upserts. ``destructive_hint=False`` is a claim
#: the architecture actually backs: there is no delete anywhere beneath it, and
#: ``idempotent_hint=True`` because repeating a call reports ``unchanged``.
_GUARDED_WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


def register_crm_tools(mcp: MCPServer[AppContext]) -> None:
    """Register the CRM read and write tools on the server.

    Args:
        mcp: The server to register tools on.
    """

    @mcp.tool(title="Query CRM contacts", annotations=_READ_ONLY_LOCAL)
    async def crm_query(
        ctx: Context[AppContext],
        company_domain: Annotated[
            str | None,
            Field(
                default=None,
                max_length=253,
                description="Exact match on the employer's web domain, e.g. 'stripe.com'. "
                "Lowercased before matching. This is the most selective filter available; "
                "prefer it when you know the account.",
            ),
        ] = None,
        title_contains: Annotated[
            str | None,
            Field(
                default=None,
                min_length=1,
                max_length=100,
                description="Case-insensitive substring of the job title, e.g. 'VP' or "
                "'security'. A substring, not a pattern: do not pass wildcards.",
            ),
        ] = None,
        country: Annotated[
            str | None,
            Field(
                default=None,
                min_length=2,
                max_length=2,
                pattern=r"^[A-Za-z]{2}$",
                description="ISO 3166-1 alpha-2 country code, e.g. 'US'. Two letters "
                "exactly, not a country name.",
            ),
        ] = None,
        list_name: Annotated[
            str | None,
            Field(
                default=None,
                min_length=1,
                max_length=100,
                description="Return only members of this GTM list, matched exactly.",
            ),
        ] = None,
        limit: Annotated[
            int,
            Field(
                default=25,
                ge=1,
                le=100,
                description="Maximum records to return, 1-100. Keep it small: these records "
                "land in your context window.",
            ),
        ] = 25,
    ) -> ContactQueryResult:
        """Search the CRM this server owns for contacts the business already knows.

        Call this before any enrichment tool when the user asks about people or
        accounts that are likely already customers, prospects or campaign
        members — "who do we know at Stripe?", "which VPs are in the Q4 list?",
        "do we have anyone in Germany?". Reading the CRM is free and instant,
        whereas `search_contact` reaches a metered third-party API, so answering
        from the CRM first is both cheaper and more accurate about your own data.

        Combine filters to narrow: they are ANDed together. Omit all of them to
        see the most recently created contacts. Use this tool to obtain a
        contact's identifier, which is what `save_to_list` needs.

        Do not call this to look up a company or person the business has never
        recorded — that is `search_company` / `search_contact`. This tool only
        reads: it creates nothing, updates nothing and writes no audit record,
        so it is safe to call speculatively.

        Returns the matching contacts newest first, the filters as they were
        actually applied, a `count`, and `limit_reached` telling you whether more
        matches exist beyond the ones returned. An empty result with `count: 0`
        means the CRM genuinely holds no such record; it is an answer, not an
        error.
        """
        app = ctx.request_context.lifespan_context
        try:
            crm = app.require_crm()
            return await crm.query_contacts(
                ContactFilter(
                    company_domain=company_domain,
                    title_contains=title_contains,
                    country=country,
                    list_name=list_name,
                    limit=limit,
                )
            )
        except GTMError as exc:
            raise to_mcp_exception(exc) from exc

    @mcp.tool(title="Save a contact to the CRM", annotations=_GUARDED_WRITE)
    async def sync_to_crm(
        ctx: Context[AppContext],
        contact: Annotated[
            ContactSyncInput,
            Field(
                description="The contact to create or update. Copy the values from a "
                "`search_contact` result rather than retyping or inferring them."
            ),
        ],
    ) -> WriteResult:
        """Create or update one contact in the CRM. Modifies data.

        Call this only when the user has asked for a record to be saved or
        updated — after `search_contact` found someone worth keeping, for
        example. One contact per call.

        Supply `email` whenever you have it: it is the contact's identity here,
        and it is what makes a second call update the existing record instead of
        creating a duplicate person. Without an email the record cannot be
        matched to an existing one and a new contact is created, so do not call
        this repeatedly with a nameless-email variant of someone already synced.

        Safety behaviour you should rely on rather than work around:
        - Nothing is ever deleted, and a field you omit is left alone. Omitting a
          field is not a request to clear it, and there is no way to clear one.
        - Values a human curated in the CRM win over the values you supply, so a
          stale enrichment title cannot overwrite a verified one. If a field you
          sent does not appear in `changed_fields`, that is why.
        - Writes may be disabled or in dry-run mode on this server.

        Returns a `WriteResult`. Read its `outcome`: `created` and `updated` mean
        the CRM now holds your values, `unchanged` means the record already
        matched and the call was a safe no-op, `dry_run` means the request was
        valid but NOTHING WAS WRITTEN, and `rejected` or `failed` mean nothing
        was written and the `message` says why. Report `dry_run`, `rejected` and
        `failed` to the user instead of retrying: none of them will change on a
        second identical call. `record_id` carries the CRM identifier, which is
        what `save_to_list` takes.
        """
        app = ctx.request_context.lifespan_context
        try:
            crm = app.require_crm()
            return await crm.sync_contact(contact, request_id=ctx.request_id)
        except GTMError as exc:
            raise to_mcp_exception(exc) from exc

    @mcp.tool(title="Add a contact to a GTM list", annotations=_GUARDED_WRITE)
    async def save_to_list(
        ctx: Context[AppContext],
        contact_id: Annotated[
            str,
            Field(
                min_length=1,
                max_length=36,
                description="CRM identifier of an existing contact, as returned by "
                "`crm_query` or by a successful `sync_to_crm`. A UUID. Do not construct "
                "one from a name or an email address.",
            ),
        ],
        list_name: Annotated[
            str,
            Field(
                min_length=1,
                max_length=100,
                pattern=r"^[A-Za-z0-9][A-Za-z0-9 _.\-&/()]*$",
                description="Name of the GTM list, e.g. 'Q4 Enterprise Pipeline'. Created "
                "if it does not exist yet, so check the spelling: a typo silently makes a "
                "second list rather than adding to the intended one. Letters, digits, "
                "spaces and - _ . & / ( ) only.",
            ),
        ],
    ) -> WriteResult:
        """Add an existing CRM contact to a named GTM list. Modifies data.

        Call this when the user wants someone put on a campaign, segment or
        outreach list — "add her to the Q4 pipeline". The contact must already be
        in the CRM: if you have enrichment data but no CRM record yet, call
        `sync_to_crm` first and pass the `record_id` it returns.

        The list is created on first use, which makes a misspelled name into a
        new one-person list rather than an error. Prefer a list name the user
        actually said, and confirm it with `crm_query`'s `list_name` filter if
        you are unsure which lists exist.

        Membership is additive and idempotent: adding the same contact twice
        reports `unchanged` and creates no duplicate. There is deliberately no
        way to take a contact off a list — this server has no destructive
        capability — so tell the user that rather than looking for one.

        Returns a `WriteResult`. `created` means the membership now exists,
        `unchanged` means it already did, `dry_run` means the request was valid
        but NOTHING WAS WRITTEN, and `rejected` or `failed` mean nothing was
        written — most often because writes are disabled or because no contact
        carries that identifier. Read `message` and tell the user rather than
        retrying an identical call.
        """
        app = ctx.request_context.lifespan_context
        try:
            crm = app.require_crm()
            return await crm.save_contact_to_list(contact_id, list_name, request_id=ctx.request_id)
        except GTMError as exc:
            raise to_mcp_exception(exc) from exc
