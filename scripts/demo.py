"""The canonical end-to-end demonstration: one request, a real agent, real tools.

Runs a single natural-language GTM request through a real MCP host connected to
this server, and prints what the agent actually did — every tool call, every
argument, every outcome, and the answer it gave. Nothing here is scripted: the
tool sequence and the closing summary are whatever the model chose, which is the
only version of this demo worth showing.

Three safety modes, so the guardrails are visible rather than asserted:

* ``safe-read``  — writes disabled. Research works; every mutation is refused.
* ``dry-run``    — writes enabled but not persisted. The agent can prepare and
                   validate a write and must report that nothing was written.
* ``live-write`` — writes persist to the seeded demo CRM.

Enrichment always runs against the offline sample dataset, so a demo can be
re-run as often as you like without spending a provider credit.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Final

from eval.agents import AgentRequest, AgentRun, ClaudeCodeAgentProvider
from eval.live import ALL_TOOLS, SERVER_ALIAS

PROJECT_DIR: Final = Path(__file__).resolve().parent.parent

#: The demo request. One sentence, four capabilities, and a genuine dependency
#: chain: the list add needs an identifier only the sync can produce.
#:
#: The person is named because `search_contact` resolves one named person per
#: call and cannot browse a company for a title — see :data:`UNANSWERABLE_PROMPT`.
DEFAULT_PROMPT: Final = (
    "Research Northwind Logistics (northwindlogistics.com), look up their VP of Sales "
    "Dana Whitfield, sync that contact to the CRM, and add her to my 'Q4 Outreach' list."
)

#: The same request with the person's name withheld. This server has no tool
#: that finds a person by title, so a correct agent says so instead of inventing
#: a name to feed `search_contact`. Worth running: refusing to guess is the
#: behaviour the tool descriptions are written to produce.
UNANSWERABLE_PROMPT: Final = (
    "Research Northwind Logistics (northwindlogistics.com), find their VP of Sales, "
    "sync that contact to the CRM, and add them to my 'Q4 Outreach' list."
)

#: Same framing the live evaluation uses: a role, and no tool guidance.
SYSTEM_PROMPT: Final = (
    "You are a go-to-market assistant for a B2B revenue team. The user's request "
    "concerns company and contact data. Use the tools available to you to answer "
    "it, and report accurately what you found and what you changed."
)

#: The three demo configurations, as the environment the server runs with.
MODES: Final[dict[str, dict[str, str]]] = {
    "safe-read": {"GTM_ENABLE_WRITE_TOOLS": "false", "GTM_DRY_RUN_WRITES": "false"},
    "dry-run": {"GTM_ENABLE_WRITE_TOOLS": "true", "GTM_DRY_RUN_WRITES": "true"},
    "live-write": {"GTM_ENABLE_WRITE_TOOLS": "true", "GTM_DRY_RUN_WRITES": "false"},
}

_BASE_ENV: Final[dict[str, str]] = {
    "GTM_ENVIRONMENT": "local",
    "GTM_LOG_LEVEL": "WARNING",
    # Never the live provider: a demo must not be able to spend metered credits.
    "GTM_ENRICHMENT_PROVIDER": "sample",
    "GTM_MAX_WRITE_BATCH_SIZE": "1",
}


def build_mcp_config(mode: str, database_url: str | None) -> dict[str, object]:
    """Build the MCP configuration the host launches the server from.

    Args:
        mode: One of the keys of :data:`MODES`.
        database_url: DSN override, or ``None`` to use the server's default.

    Returns:
        A configuration in the host's ``mcpServers`` format.
    """
    env = {**_BASE_ENV, **MODES[mode]}
    if database_url:
        env["GTM_DATABASE_URL"] = database_url
    return {
        "mcpServers": {
            SERVER_ALIAS: {
                "type": "stdio",
                "command": "uv",
                "args": ["--directory", str(PROJECT_DIR), "run", "gtm-mcp-server"],
                "env": env,
            }
        }
    }


def render(run: AgentRun, mode: str) -> str:
    """Format an agent run for a terminal.

    Args:
        run: What the agent did.
        mode: The safety mode it ran under.

    Returns:
        The printable transcript.
    """
    rule = "=" * 78
    lines = [
        rule,
        f"GTM MCP DEMO - mode: {mode}",
        rule,
        f"MCP servers connected : {', '.join(run.connected_servers) or 'none'}",
        f"Tool calls            : {len(run.tool_calls)}",
        f"Wall clock            : {run.duration_ms / 1000.0:.1f}s",
        "",
        "TOOL CALLS (as the agent chose them, not scripted)",
        "-" * 78,
    ]

    for index, call in enumerate(run.tool_calls, start=1):
        arguments = json.dumps(call.arguments, ensure_ascii=False)
        lines.append(f"{index}. {call.tool_name}({arguments[:160]})")
        if isinstance(call.result, dict) and "outcome" in call.result:
            lines.append(
                f"   -> outcome={call.result['outcome']}  success={call.result.get('success')}"
            )
            lines.append(f"      {str(call.result.get('message', ''))[:220]}")
        elif isinstance(call.result, dict) and "found" in call.result:
            lines.append(f"   -> found={call.result['found']}")
        elif isinstance(call.result, dict) and "count" in call.result:
            lines.append(f"   -> count={call.result['count']}")
        else:
            lines.append(f"   -> {str(call.result)[:200]}")

    lines += ["", "AGENT'S FINAL ANSWER", "-" * 78, run.final_response or "(none)", ""]
    if run.error:
        lines += [f"RUN ERROR: {run.error}", ""]
    lines.append(rule)
    return "\n".join(lines)


async def _run(args: argparse.Namespace) -> int:
    """Execute the demo.

    Args:
        args: Parsed command-line arguments.

    Returns:
        Process exit status.
    """
    provider = ClaudeCodeAgentProvider(model=args.model)
    if not provider.is_available():
        print("Claude Code CLI not found on PATH. Install it, or run the MCP Inspector instead.")
        return 2
    if shutil.which("uv") is None:
        print("uv not found on PATH; the host cannot launch the MCP server.")
        return 2

    with tempfile.TemporaryDirectory(prefix="gtm-demo-") as work_dir:
        run = await provider.run(
            AgentRequest(
                prompt=args.prompt,
                mcp_config=build_mcp_config(args.mode, args.database_url),
                allowed_tools=[f"mcp__{SERVER_ALIAS}__{tool}" for tool in ALL_TOOLS],
                system_prompt=SYSTEM_PROMPT,
                working_dir=Path(work_dir),
                timeout_seconds=args.timeout,
            )
        )

    print(render(run, args.mode))
    return 1 if run.error else 0


def main() -> None:
    """CLI entry point for the demonstration."""
    # A model writes em dashes and typographic quotes; a Windows console defaults
    # to cp1252 and would raise on them. Losing a character is acceptable here,
    # crashing after paying for the run is not.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Run the canonical GTM workflow through a real MCP client. "
        "Uses the offline sample enrichment provider, so it spends no API credits."
    )
    parser.add_argument(
        "--mode",
        choices=sorted(MODES),
        default="safe-read",
        help="Write guardrail configuration to demonstrate (default: safe-read).",
    )
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Request to put to the agent.")
    parser.add_argument(
        "--unanswerable",
        action="store_const",
        const=UNANSWERABLE_PROMPT,
        dest="prompt",
        help="Ask for a person by title instead of by name. No tool here can do that, "
        "so this demonstrates the agent reporting the gap rather than guessing.",
    )
    parser.add_argument("--model", default=None, help="Model alias to pin the agent to.")
    parser.add_argument("--database-url", default=None, help="Override the CRM DSN.")
    parser.add_argument("--timeout", type=float, default=300.0, help="Timeout in seconds.")
    sys.exit(asyncio.run(_run(parser.parse_args())))


if __name__ == "__main__":
    main()
