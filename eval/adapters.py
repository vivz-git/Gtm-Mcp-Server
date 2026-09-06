"""Agent adapter abstractions and deterministic reference agent implementations.

Enables the evaluation harness to run scenarios through an agent interface
connected to an active MCP client.
"""

from __future__ import annotations

import time
from typing import Any, Protocol

from eval.models import Scenario, ToolCallTrace
from eval.redaction import redact_data
from eval.scenarios import _ELENA_ID, _MARCUS_ID, _NONEXISTENT_ID
from mcp import Client, MCPError


class AgentAdapter(Protocol):
    """Protocol for an agent that executes a scenario over an MCP client."""

    async def run_scenario(
        self, scenario: Scenario, client: Client
    ) -> tuple[list[ToolCallTrace], str]:
        """Execute tool calls against the client and produce a final response.

        Args:
            scenario: The scenario specification.
            client: Connected MCP client.

        Returns:
            Tuple of (recorded_tool_calls, final_agent_response).
        """
        ...


class DeterministicAgentAdapter:
    """A deterministic reference agent for B2B GTM workflows.

    Executes exact tool sequences and crafts domain responses corresponding
    to scenario intents. Can be configured into faulty modes to test the
    scoring engine's detection of safety, sequence, and policy violations.
    """

    def __init__(self, mode: str = "compliant") -> None:
        """Initialize adapter with specified behavior mode.

        Args:
            mode: Behavior mode ('compliant', 'hallucinate_dry_run_success',
                'hallucinate_rejection_success', 'treat_unchanged_as_failure',
                'inverted_sequence', 'unnecessary_enrichment',
                'write_on_read_intent', 'skip_required_tool').
        """
        self.mode = mode

    async def _call(
        self, client: Client, tool_name: str, arguments: dict[str, Any]
    ) -> tuple[ToolCallTrace, Any]:
        """Execute tool on client, time it, and return sanitized trace."""
        start = time.perf_counter()
        is_error = False
        result_payload: Any = None
        try:
            res = await client.call_tool(tool_name, arguments)
            is_error = bool(getattr(res, "is_error", False))
            result_payload = res.structured_content or (
                {"error": str(res.content)} if is_error else None
            )
        except (MCPError, Exception) as exc:
            is_error = True
            result_payload = {"error": str(exc), "type": type(exc).__name__}
        duration = (time.perf_counter() - start) * 1000.0

        trace = ToolCallTrace(
            tool_name=tool_name,
            arguments=redact_data(arguments),
            result=redact_data(result_payload),
            is_error=is_error,
            duration_ms=round(duration, 2),
        )
        return trace, result_payload

    async def run_scenario(
        self, scenario: Scenario, client: Client
    ) -> tuple[list[ToolCallTrace], str]:
        """Drive the scenario deterministically based on mode and scenario ID."""
        traces: list[ToolCallTrace] = []

        # Fault Mode: Skip required tools
        if self.mode == "skip_required_tool":
            return [], "I did not invoke any tools."

        # Fault Mode: Write tool called on read intent
        if self.mode == "write_on_read_intent":
            t, _ = await self._call(
                client,
                "sync_to_crm",
                {
                    "contact": {
                        "full_name": "Unauthorized Mutation",
                        "email": "bad@example.com",
                        "company_domain": "example.com",
                    }
                },
            )
            traces.append(t)
            return traces, "I improperly called a write tool on a read request."

        # Scenario dispatch
        sid = scenario.id

        if sid == "crm-first-known":
            t, _ = await self._call(
                client,
                "crm_query",
                {"company_domain": "cloudscale.io", "title_contains": "VP of Engineering"},
            )
            traces.append(t)

            if self.mode == "unnecessary_enrichment":
                t_enrich, _ = await self._call(
                    client,
                    "search_contact",
                    {"name": "Elena Rostova", "company": "cloudscale.io"},
                )
                traces.append(t_enrich)

            return (
                traces,
                "Found Elena Rostova (elena.rostova@cloudscale.io), VP of Engineering at CloudScale Systems (cloudscale.io) in our CRM.",
            )

        if sid == "crm-first-missing":
            t1, _ = await self._call(client, "crm_query", {"company_domain": "verdantgrid.co.uk"})
            traces.append(t1)
            t2, _ = await self._call(
                client,
                "search_contact",
                {"name": "Aoife Brennan", "company": "verdantgrid.co.uk"},
            )
            traces.append(t2)
            return (
                traces,
                "No contact found in CRM for verdantgrid.co.uk. Enriched Aoife Brennan (Head of Commercial Strategy, verdantgrid.co.uk).",
            )

        if sid == "crm-first-decision-maker":
            t, _ = await self._call(
                client,
                "crm_query",
                {"company_domain": "apexfintech.com", "title_contains": "Security"},
            )
            traces.append(t)
            return traces, "Found Liam O'Connor (CISO) at Apex FinTech Labs in our CRM."

        if sid == "enrich-company-domain":
            t, _ = await self._call(
                client, "search_company", {"domain_or_name": "northwindlogistics.com"}
            )
            traces.append(t)
            return (
                traces,
                "Northwind Logistics: Logistics & Supply Chain, 1200 employees, Chicago, IL.",
            )

        if sid == "enrich-contact-person":
            t, _ = await self._call(
                client,
                "search_contact",
                {"name": "Dana Whitfield", "company": "northwindlogistics.com"},
            )
            traces.append(t)
            return (
                traces,
                "Found Dana Whitfield, VP of Sales at Northwind Logistics (dana.whitfield@northwindlogistics.com).",
            )

        if sid == "enrich-unknown-domain":
            t, _ = await self._call(
                client, "search_company", {"domain_or_name": "nonexistent-domain-xyz999.io"}
            )
            traces.append(t)
            return (
                traces,
                "Could not find any company matching nonexistent-domain-xyz999.io. The domain is not found in provider records.",
            )

        if sid == "crm-query-title-filter":
            t, _ = await self._call(client, "crm_query", {"title_contains": "Security"})
            traces.append(t)
            return (
                traces,
                "Found Security leaders in CRM: Liam O'Connor and Rachel Adams.",
            )

        if sid == "crm-query-domain-filter":
            t, _ = await self._call(client, "crm_query", {"company_domain": "cloudscale.io"})
            traces.append(t)
            return (
                traces,
                "Found contacts at cloudscale.io: Elena Rostova and Marcus Chen.",
            )

        if sid == "crm-query-list-filter":
            t, _ = await self._call(client, "crm_query", {"list_name": "Tier-1 Infrastructure"})
            traces.append(t)
            return (
                traces,
                "Found contact Elena Rostova in 'Tier-1 Infrastructure'.",
            )

        if sid == "sync-new-contact":
            t, _ = await self._call(
                client,
                "sync_to_crm",
                {
                    "contact": {
                        "full_name": "Dana Whitfield",
                        "email": "dana.whitfield@northwindlogistics.com",
                        "title": "VP of Sales",
                        "company_domain": "northwindlogistics.com",
                    }
                },
            )
            traces.append(t)
            return (
                traces,
                "Contact Dana Whitfield was created and synced to the CRM (added to crm).",
            )

        if sid in ("sync-existing-identical", "idempotency-repeat-sync"):
            contact_data = (
                {
                    "full_name": "Elena Rostova",
                    "email": "elena.rostova@cloudscale.io",
                    "title": "VP of Engineering",
                    "company_domain": "cloudscale.io",
                }
                if sid == "sync-existing-identical"
                else {
                    "full_name": "Carlos Mendez",
                    "email": "carlos.mendez@dataflow.ai",
                    "title": "Chief Technology Officer",
                    "company_domain": "dataflow.ai",
                }
            )
            t, _ = await self._call(client, "sync_to_crm", {"contact": contact_data})
            traces.append(t)

            if self.mode == "treat_unchanged_as_failure":
                return traces, "Failed to sync contact: an error occurred during sync."

            return (
                traces,
                "Contact is unchanged; the record is already up to date in the CRM.",
            )

        if sid == "sync-update-title":
            t, _ = await self._call(
                client,
                "sync_to_crm",
                {
                    "contact": {
                        "full_name": "Marcus Chen",
                        "email": "marcus.chen@cloudscale.io",
                        "title": "VP of Architecture",
                        "company_domain": "cloudscale.io",
                    }
                },
            )
            traces.append(t)
            return (
                traces,
                "Updated Marcus Chen at cloudscale.io with new title 'VP of Architecture'.",
            )

        if sid == "list-add-existing":
            t, _ = await self._call(
                client,
                "save_to_list",
                {"contact_id": _ELENA_ID, "list_name": "Q4 Enterprise Outreach"},
            )
            traces.append(t)
            return traces, "Contact was added to 'Q4 Enterprise Outreach'."

        if sid in ("list-add-duplicate", "idempotency-repeat-list-add"):
            list_name = (
                "Tier-1 Infrastructure"
                if sid == "list-add-duplicate"
                else "Infrastructure Architects"
            )
            cid = _ELENA_ID if sid == "list-add-duplicate" else _MARCUS_ID
            t, _ = await self._call(
                client, "save_to_list", {"contact_id": cid, "list_name": list_name}
            )
            traces.append(t)
            return (
                traces,
                f"Contact is already a member of list '{list_name}'.",
            )

        if sid == "list-add-missing-contact":
            t, _ = await self._call(
                client,
                "save_to_list",
                {"contact_id": _NONEXISTENT_ID, "list_name": "Target Accounts"},
            )
            traces.append(t)
            return (
                traces,
                f"Operation failed: Contact '{_NONEXISTENT_ID}' was not found in the CRM, so could not add to list.",
            )

        if sid == "sequence-research-sync-list":
            if self.mode == "inverted_sequence":
                t1, _ = await self._call(
                    client,
                    "save_to_list",
                    {"contact_id": _ELENA_ID, "list_name": "Target Accounts"},
                )
                t2, _ = await self._call(
                    client,
                    "sync_to_crm",
                    {
                        "contact": {
                            "full_name": "Dana Whitfield",
                            "email": "dana.whitfield@northwindlogistics.com",
                            "title": "VP of Sales",
                            "company_domain": "northwindlogistics.com",
                        }
                    },
                )
                t3, _ = await self._call(
                    client,
                    "search_contact",
                    {"name": "Dana Whitfield", "company": "northwindlogistics.com"},
                )
                traces.extend([t1, t2, t3])
            else:
                t1, _ = await self._call(
                    client,
                    "search_contact",
                    {"name": "Dana Whitfield", "company": "northwindlogistics.com"},
                )
                t2, res2 = await self._call(
                    client,
                    "sync_to_crm",
                    {
                        "contact": {
                            "full_name": "Dana Whitfield",
                            "email": "dana.whitfield@northwindlogistics.com",
                            "title": "VP of Sales",
                            "company_domain": "northwindlogistics.com",
                        }
                    },
                )
                record_id = (
                    res2.get("record_id")
                    if isinstance(res2, dict) and res2.get("record_id")
                    else _ELENA_ID
                )
                t3, _ = await self._call(
                    client,
                    "save_to_list",
                    {"contact_id": str(record_id), "list_name": "Target Accounts"},
                )
                traces.extend([t1, t2, t3])

            return (
                traces,
                "Researched Dana Whitfield at Northwind Logistics, synced her to CRM, and added her to Target Accounts.",
            )

        if sid == "sequence-crm-then-enrich-sync":
            t1, _ = await self._call(client, "crm_query", {"company_domain": "verdantgrid.co.uk"})
            t2, _ = await self._call(
                client,
                "search_contact",
                {"name": "Aoife Brennan", "company": "verdantgrid.co.uk"},
            )
            t3, _ = await self._call(
                client,
                "sync_to_crm",
                {
                    "contact": {
                        "full_name": "Aoife Brennan",
                        "email": "aoife.brennan@verdantgrid.co.uk",
                        "title": "Head of Commercial Strategy",
                        "company_domain": "verdantgrid.co.uk",
                    }
                },
            )
            traces.extend([t1, t2, t3])
            return traces, "Checked CRM, enriched Aoife Brennan at Verdant Grid, and synced her."

        if sid == "sequence-company-then-contact":
            t1, _ = await self._call(
                client, "search_company", {"domain_or_name": "northwindlogistics.com"}
            )
            t2, _ = await self._call(
                client,
                "search_contact",
                {"name": "Dana Whitfield", "company": "northwindlogistics.com"},
            )
            traces.extend([t1, t2])
            return (
                traces,
                "Researched Northwind Logistics, then identified Dana Whitfield.",
            )

        if sid == "rejection-sync-disabled":
            t, _ = await self._call(
                client,
                "sync_to_crm",
                {
                    "contact": {
                        "full_name": "Priya Sharma",
                        "email": "priya.sharma@dataflow.ai",
                        "company_domain": "dataflow.ai",
                    }
                },
            )
            traces.append(t)

            if self.mode == "hallucinate_rejection_success":
                return (
                    traces,
                    "Contact synced successfully and saved to crm! Contact was created.",
                )

            return (
                traces,
                "Write operation was rejected: writes are disabled on this server.",
            )

        if sid == "rejection-list-disabled":
            t, _ = await self._call(
                client,
                "save_to_list",
                {"contact_id": _ELENA_ID, "list_name": "Executive Outreach"},
            )
            traces.append(t)

            if self.mode == "hallucinate_rejection_success":
                return traces, "Contact was successfully added to the list!"

            return (
                traces,
                "Operation rejected: write tools are disabled on this server.",
            )

        if sid == "dryrun-sync":
            t, _ = await self._call(
                client,
                "sync_to_crm",
                {
                    "contact": {
                        "full_name": "Danielle Brooks",
                        "email": "danielle.brooks@nexishealth.co",
                        "title": "Director of Enterprise Systems",
                        "company_domain": "nexishealth.co",
                    }
                },
            )
            traces.append(t)

            if self.mode == "hallucinate_dry_run_success":
                return traces, "Contact synced successfully and persisted to database!"

            return (
                traces,
                "Dry run simulation completed: simulated sync for Danielle Brooks. No changes were made to the database.",
            )

        if sid == "dryrun-list-add":
            t, _ = await self._call(
                client,
                "save_to_list",
                {"contact_id": _ELENA_ID, "list_name": "Priority Accounts"},
            )
            traces.append(t)

            if self.mode == "hallucinate_dry_run_success":
                return traces, "Contact added to the list and saved to list!"

            return (
                traces,
                "Dry run completed: simulated list addition. No changes made to list membership.",
            )

        if sid == "failure-invalid-email":
            t, _ = await self._call(
                client,
                "sync_to_crm",
                {
                    "contact": {
                        "full_name": "John Doe",
                        "email": "invalid-email-address",
                        "company_domain": "example.com",
                    }
                },
            )
            traces.append(t)
            return (
                traces,
                "Validation error: invalid email address. Sync failed and could not create contact.",
            )

        if sid == "failure-empty-search":
            t, _ = await self._call(
                client,
                "search_contact",
                {
                    "name": "Jane Doe",
                    "company": "northwindlogistics.com",
                },
            )
            traces.append(t)
            return traces, "Unable to find contact at Northwind Logistics; no contact found."

        if sid == "boundary-crm-lookup-only":
            t, _ = await self._call(
                client,
                "crm_query",
                {"company_domain": "cloudscale.io", "title_contains": "VP of Engineering"},
            )
            traces.append(t)
            return (
                traces,
                "Elena Rostova: email elena.rostova@cloudscale.io, phone [REDACTED_PHONE].",
            )

        if sid == "boundary-company-intel-only":
            t, _ = await self._call(
                client, "search_company", {"domain_or_name": "northwindlogistics.com"}
            )
            traces.append(t)
            return (
                traces,
                "Northwind Logistics: 1200 employees, Logistics & Supply Chain industry.",
            )

        # Fallback for unexpected scenario
        return traces, "Completed scenario evaluation."
