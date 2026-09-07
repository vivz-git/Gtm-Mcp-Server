"""The real-agent provider boundary.

Phase 5 measured a deterministic reference agent. Phase 6 measures a real
model, and the evaluator must not learn anything about which model that is.
:class:`AgentProvider` is the whole contract: given a prompt and an MCP server
to talk to, return the tool calls that were made, their results, and the final
answer. Nothing about scoring, scenarios or golden expectations appears here,
and nothing vendor-specific appears above this module.

One implementation ships — :class:`ClaudeCodeAgentProvider`, which drives the
Claude Code CLI in headless mode. It is the practical path for this repository
because Claude Code is itself an MCP host: it spawns the server over stdio
exactly as a user's own client would, so the run under measurement is the real
integration rather than a re-implementation of one. A second vendor would be a
second class in this module and no change anywhere else; adding one now, with
no second integration to validate it against, would be speculative.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

#: Claude Code namespaces MCP tools as ``mcp__<server>__<tool>``. The evaluator
#: scores bare tool names, so the prefix is stripped on the way in.
_MCP_TOOL_PREFIX = "mcp__"


class AgentToolCall(BaseModel):
    """One tool invocation observed during a live agent run."""

    model_config = ConfigDict(frozen=True)

    tool_name: str = Field(description="Tool name as the server exposes it, without namespace.")
    namespaced_name: str = Field(description="Name as the host presented it to the model.")
    arguments: dict[str, Any] = Field(
        default_factory=dict, description="Arguments the model supplied."
    )
    result: Any = Field(default=None, description="Result payload the tool returned.")
    is_error: bool = Field(default=False, description="Whether the host flagged the call failed.")
    duration_ms: float = Field(default=0.0, description="Time from request to result, in ms.")


class AgentRun(BaseModel):
    """Everything an evaluator needs to score one real-agent execution."""

    model_config = ConfigDict(frozen=True)

    tool_calls: list[AgentToolCall] = Field(
        default_factory=list, description="Tool calls in the order the agent made them."
    )
    final_response: str = Field(default="", description="The agent's closing message.")
    duration_ms: float = Field(default=0.0, description="Wall-clock duration of the run.")
    connected_servers: list[str] = Field(
        default_factory=list, description="MCP servers the host reported as connected."
    )
    failed_servers: list[str] = Field(
        default_factory=list, description="MCP servers the host could not connect to."
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Provenance of the run: model, session id, cost, turn count.",
    )
    error: str | None = Field(
        default=None, description="Why the run could not be completed, if it could not."
    )


class AgentRequest(BaseModel):
    """A single prompt to put to a real agent, and the tools it may use."""

    model_config = ConfigDict(frozen=True)

    prompt: str = Field(description="The user turn, verbatim from the scenario intent.")
    mcp_config: dict[str, Any] = Field(
        description="MCP client configuration in the host's own `mcpServers` format."
    )
    allowed_tools: list[str] = Field(
        default_factory=list,
        description="Namespaced tool names the agent is permitted to call. Empty means all.",
    )
    system_prompt: str | None = Field(
        default=None,
        description="Operating context for the agent. Never scenario-specific guidance.",
    )
    working_dir: Path | None = Field(
        default=None,
        description="Directory to run the agent in. Kept away from this repository so the "
        "agent cannot read the implementation it is being measured against.",
    )
    timeout_seconds: float = Field(
        default=300.0, gt=0, description="Hard cap on a single scenario run."
    )


class AgentProvider(Protocol):
    """A real agent that can be asked a question with MCP tools attached."""

    @property
    def name(self) -> str:
        """Short identifier for the provider, recorded in the report."""
        ...

    def describe(self) -> dict[str, str]:
        """Return provenance for the report: model, host, connection method."""
        ...

    async def run(self, request: AgentRequest) -> AgentRun:
        """Execute one prompt and return what the agent did.

        Args:
            request: The prompt, the MCP configuration and the tool allowance.

        Returns:
            The observed tool calls, final response and run provenance. A run
            that could not be completed returns an :class:`AgentRun` with
            ``error`` set rather than raising: a single unusable scenario must
            be scored as a failure, not abort the suite.
        """
        ...


def strip_namespace(namespaced: str) -> str:
    """Reduce a host-namespaced MCP tool name to the name the server registered.

    Args:
        namespaced: A name such as ``mcp__gtm__crm_query``.

    Returns:
        ``crm_query`` for a namespaced MCP tool; the input unchanged otherwise,
        so a built-in host tool stays visibly distinct from a GTM tool.
    """
    if not namespaced.startswith(_MCP_TOOL_PREFIX):
        return namespaced
    parts = namespaced.split("__", 2)
    return parts[2] if len(parts) == 3 else namespaced


def _decode_tool_result(content: Any) -> Any:
    """Recover a tool's structured payload from the host's transcript form.

    The host records a tool result as the text blocks the model saw, which for
    these tools is a JSON document. Parsing it back is what lets the scorer read
    an ``outcome`` field; text that is not JSON is returned as-is rather than
    being forced into a shape it does not have.

    Args:
        content: The ``content`` field of a ``tool_result`` block.

    Returns:
        The decoded payload, or the original text when it is not JSON.
    """
    if isinstance(content, list):
        text = "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
        if not text:
            return content
    elif isinstance(content, str):
        text = content
    else:
        return content

    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return text


class ClaudeCodeAgentProvider:
    """Drives the Claude Code CLI headlessly and reads back its event stream.

    The CLI is used rather than a model SDK on purpose. Claude Code is an MCP
    host: it launches the server as a stdio subprocess, performs the
    initialization handshake, discovers tools and enforces the tool allowance.
    Calling a model API directly would mean re-implementing all of that inside
    the evaluator, and would then be measuring the evaluator's MCP client rather
    than a real one.

    Built-in tools are disabled and settings sources are suppressed for every
    run, so the only capability the agent has is the GTM server and the only
    context it has is the prompt. Without that, an agent could answer a CRM
    question by reading this repository.
    """

    def __init__(
        self,
        *,
        executable: str = "claude",
        model: str | None = None,
        max_budget_usd: float | None = None,
    ) -> None:
        """Configure the provider.

        Args:
            executable: The Claude Code CLI to invoke.
            model: Model alias to pin the run to. ``None`` uses the CLI default
                and records whatever it reports.
            max_budget_usd: Optional spend ceiling passed to the CLI, so a
                misbehaving loop cannot run up an unbounded bill.
        """
        self._executable = executable
        self._model = model
        self._max_budget_usd = max_budget_usd

    @property
    def name(self) -> str:
        """Short identifier for the provider."""
        return "claude-code-cli"

    def describe(self) -> dict[str, str]:
        """Return provenance recorded in the live evaluation report."""
        return {
            "provider": self.name,
            "host": "Claude Code CLI (headless, --print)",
            "model": self._model or "cli-default",
            "mcp_connection": "stdio subprocess spawned by the host from --mcp-config",
            "isolation": "built-in tools disabled, settings sources suppressed",
        }

    def is_available(self) -> bool:
        """Whether the CLI can be found on PATH.

        Returns:
            True if the executable resolves, so a caller can skip rather than
            fail when no host is installed.
        """
        return shutil.which(self._executable) is not None

    def _build_argv(self, request: AgentRequest, config_path: Path) -> list[str]:
        """Assemble the CLI invocation for one scenario.

        Args:
            request: The prompt and tool allowance.
            config_path: File holding the MCP configuration for this run.

        Returns:
            The argument vector to execute.
        """
        argv = [
            self._executable,
            "--print",
            request.prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--mcp-config",
            str(config_path),
            # Only the server under test: no user, project or plugin MCP config
            # leaks into a measured run.
            "--strict-mcp-config",
            # No file, shell or web access. The agent's only capability is MCP.
            "--tools",
            "",
            # No CLAUDE.md, no project settings: the agent must not be able to
            # read the implementation it is being evaluated against.
            "--setting-sources",
            "",
        ]
        if request.allowed_tools:
            argv += ["--allowed-tools", " ".join(request.allowed_tools)]
        if request.system_prompt:
            argv += ["--append-system-prompt", request.system_prompt]
        if self._model:
            argv += ["--model", self._model]
        if self._max_budget_usd is not None:
            argv += ["--max-budget-usd", str(self._max_budget_usd)]
        return argv

    async def run(self, request: AgentRequest) -> AgentRun:
        """Execute one prompt through the CLI and parse the resulting stream.

        Args:
            request: The prompt, MCP configuration and tool allowance.

        Returns:
            The observed run. Timeouts, a missing CLI and a non-zero exit are
            all reported through ``AgentRun.error``.
        """
        if not self.is_available():
            return AgentRun(error=f"agent executable {self._executable!r} not found on PATH")

        work_dir = request.working_dir or Path.cwd()
        config_path = work_dir / "mcp-config.json"
        config_path.write_text(json.dumps(request.mcp_config, indent=2), encoding="utf-8")

        argv = self._build_argv(request, config_path)
        started = time.perf_counter()
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(work_dir),
            )
        except OSError as exc:  # pragma: no cover - depends on host OS state
            return AgentRun(error=f"could not start agent process: {exc}")

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=request.timeout_seconds
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return AgentRun(
                duration_ms=round((time.perf_counter() - started) * 1000.0, 2),
                error=f"agent run exceeded {request.timeout_seconds}s",
            )

        duration_ms = round((time.perf_counter() - started) * 1000.0, 2)
        run = parse_claude_stream(stdout.decode("utf-8", errors="replace"))
        run = run.model_copy(update={"duration_ms": duration_ms})

        if proc.returncode != 0 and run.error is None:
            detail = stderr.decode("utf-8", errors="replace").strip()[:400]
            run = run.model_copy(
                update={"error": f"agent exited {proc.returncode}: {detail or 'no stderr'}"}
            )
        return run


def parse_claude_stream(stdout: str) -> AgentRun:
    """Turn a Claude Code ``stream-json`` transcript into an :class:`AgentRun`.

    Tool calls and their results arrive as separate events joined by a tool-use
    identifier, so results are matched back by id rather than by position: an
    agent that issues two calls before either returns must still be recorded in
    the order it made them, with the right result on each.

    Per-call durations stay at zero: the transcript timestamps the turn, not the
    individual call, and inventing a plausible split would put a number in the
    report that nothing measured. Latency is reported per run instead.

    Args:
        stdout: The raw newline-delimited JSON the CLI wrote.

    Returns:
        The reconstructed run. A transcript with no terminating result event
        yields ``error``, because a truncated run must not be scored as if the
        agent simply chose to say nothing.
    """
    calls: list[AgentToolCall] = []
    by_id: dict[str, int] = {}
    final_response = ""
    connected: list[str] = []
    failed: list[str] = []
    metadata: dict[str, Any] = {}
    saw_result = False
    error: str | None = None

    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event: dict[str, Any] = json.loads(line)
        except ValueError:
            continue

        kind = event.get("type")

        if kind == "system" and event.get("subtype") == "init":
            for server in event.get("mcp_servers") or []:
                name = str(server.get("name", ""))
                if server.get("status") == "connected":
                    connected.append(name)
                else:
                    failed.append(f"{name}:{server.get('status')}")

        elif kind == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    namespaced = str(block.get("name", ""))
                    by_id[str(block.get("id"))] = len(calls)
                    calls.append(
                        AgentToolCall(
                            tool_name=strip_namespace(namespaced),
                            namespaced_name=namespaced,
                            arguments=block.get("input") or {},
                        )
                    )

        elif kind == "user":
            content = event.get("message", {}).get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                index = by_id.get(str(block.get("tool_use_id")))
                if index is None:
                    continue
                calls[index] = calls[index].model_copy(
                    update={
                        "result": _decode_tool_result(block.get("content")),
                        "is_error": bool(block.get("is_error")),
                    }
                )

        elif kind == "result":
            saw_result = True
            final_response = str(event.get("result") or "")
            metadata = {
                "session_id": event.get("session_id"),
                "num_turns": event.get("num_turns"),
                "total_cost_usd": event.get("total_cost_usd"),
                "duration_api_ms": event.get("duration_api_ms"),
                "stop_reason": event.get("stop_reason"),
                "models": sorted((event.get("modelUsage") or {}).keys()),
                "permission_denials": len(event.get("permission_denials") or []),
            }
            if event.get("is_error"):
                error = f"agent reported failure: {event.get('subtype')}"

    if not saw_result:
        error = error or "agent transcript ended without a result event"

    return AgentRun(
        tool_calls=calls,
        final_response=final_response,
        connected_servers=connected,
        failed_servers=failed,
        metadata=metadata,
        error=error,
    )
