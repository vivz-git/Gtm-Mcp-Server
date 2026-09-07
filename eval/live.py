"""Live-agent evaluation: a real model, a real MCP host, the same scoring engine.

The deterministic harness in :mod:`eval.runner` drives a scripted agent against
an in-memory server. This module drives a *real* agent through a *real* MCP host
against the server running as a stdio subprocess over the seeded PostgreSQL CRM,
and hands the result to the same :class:`~eval.scoring.Scorer` with the same
golden expectations.

Two things are deliberately not shared with the deterministic path:

* **The report file.** Live results are written to ``live-latest.*`` and carry
  their own provenance block. A real model is not reproducible, and averaging
  its score together with a scripted agent's would produce a number that
  describes neither (Phase 6I).
* **The database.** Live scenarios mutate the real CRM, so the run resets and
  reseeds it first. Without that, a second run scores ``unchanged`` where the
  first scored ``created`` and the suite silently stops being comparable.

The subset is intentionally small. Every scenario is a paid model call, so the
26-scenario deterministic suite stays the broad regression net and this measures
the behaviours where a real agent can actually go wrong.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import tempfile
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from eval.agents import AgentProvider, AgentRequest, AgentRun, ClaudeCodeAgentProvider
from eval.models import (
    CategoryScores,
    EvaluationReport,
    Scenario,
    ScenarioResult,
    ToolCallTrace,
)
from eval.redaction import redact_agent_response, redact_data
from eval.report import render_comparison_report, save_report
from eval.scenarios import SCENARIOS
from eval.scoring import Scorer
from eval.tracing import build_trace

#: Repository root and report directory, resolved at import time: touching the
#: filesystem inside the async run would block its event loop.
PROJECT_DIR: Final = Path(__file__).resolve().parent.parent
RESULTS_DIR: Final = Path(__file__).resolve().parent / "results"

#: The MCP server name the host registers this server under. Also the namespace
#: prefix the model sees, as ``mcp__gtm__<tool>``.
SERVER_ALIAS: Final = "gtm"

#: Every tool the server exposes. The host is given exactly this allowance, so a
#: scenario that calls a forbidden tool is a real model decision rather than an
#: artefact of the harness having withheld it.
ALL_TOOLS: Final[tuple[str, ...]] = (
    "server_info",
    "search_company",
    "search_contact",
    "crm_query",
    "sync_to_crm",
    "save_to_list",
)

#: The live subset (Phase 6G). Chosen for coverage of the eleven behaviours
#: worth paying a model call to observe, and restricted to scenarios whose
#: golden expectations hold against the seeded PostgreSQL CRM rather than the
#: in-memory double. Scenarios keyed to hard-coded fake UUIDs are excluded,
#: because in a live run the agent must discover identifiers for itself.
LIVE_SCENARIO_IDS: Final[tuple[str, ...]] = (
    "crm-first-known",  # 1. CRM-first lookup
    "crm-first-missing",  # 2. conditional intent: check, then enrich
    "enrich-company-domain",  # 3. enrichment
    "crm-query-title-filter",  # 4. CRM query
    "sequence-crm-then-enrich-sync",  # 5. enrichment + sync
    "sequence-research-sync-list",  # 6. sync + list add
    "idempotency-repeat-sync",  # 7. idempotent unchanged
    "rejection-sync-disabled",  # 8. write rejection
    "dryrun-sync",  # 9. dry run
    "list-add-missing-contact",  # 10. failure handling
    "boundary-crm-lookup-only",  # 11. read/write boundary
    "failure-empty-search",  # 12. no such record
)

#: Operating context for the agent. It states the role and nothing else: no tool
#: policy, no hint about which tool to reach for, and nothing scenario-specific.
#: Anything more would be the harness answering the question it is scoring. It
#: is reproduced in the report so a reader can judge that for themselves.
SYSTEM_PROMPT: Final = (
    "You are a go-to-market assistant for a B2B revenue team. The user's request "
    "concerns company and contact data. Use the tools available to you to answer "
    "it, and report accurately what you found and what you changed."
)

#: Settings the live server runs with unless a scenario overrides them. This is
#: the same baseline the deterministic runner uses, so the two reports describe
#: agents operating under identical server policy.
_BASE_SERVER_ENV: Final[dict[str, str]] = {
    "GTM_ENVIRONMENT": "test",
    "GTM_LOG_LEVEL": "WARNING",
    "GTM_LOG_FORMAT": "json",
    "GTM_ENRICHMENT_PROVIDER": "sample",
    "GTM_ENABLE_WRITE_TOOLS": "true",
    "GTM_DRY_RUN_WRITES": "false",
    "GTM_MAX_WRITE_BATCH_SIZE": "1",
}

#: Scenario setting overrides, mapped to the environment variables that carry
#: them into a spawned server process.
_SETTING_ENV_NAMES: Final[dict[str, str]] = {
    "enable_write_tools": "GTM_ENABLE_WRITE_TOOLS",
    "dry_run_writes": "GTM_DRY_RUN_WRITES",
    "max_write_batch_size": "GTM_MAX_WRITE_BATCH_SIZE",
    "enrichment_provider": "GTM_ENRICHMENT_PROVIDER",
}


def live_scenarios(scenario_ids: Sequence[str] = LIVE_SCENARIO_IDS) -> list[Scenario]:
    """Resolve scenario identifiers to the shared scenario definitions.

    Args:
        scenario_ids: Identifiers to select, in the order to run them.

    Returns:
        The matching scenarios.

    Raises:
        KeyError: An identifier does not name a scenario, which would otherwise
            silently shrink the live subset.
    """
    by_id = {scenario.id: scenario for scenario in SCENARIOS}
    missing = [sid for sid in scenario_ids if sid not in by_id]
    if missing:
        raise KeyError(f"unknown scenario id(s): {', '.join(missing)}")
    return [by_id[sid] for sid in scenario_ids]


def server_env_for(scenario: Scenario, *, database_url: str) -> dict[str, str]:
    """Build the environment the server subprocess runs with for one scenario.

    Every value is explicit. The host spawns the server with a minimal inherited
    environment and the run happens outside the repository, so nothing is
    resolved from a developer's ``.env``: what a scenario ran under is exactly
    what this function returned.

    Args:
        scenario: The scenario, whose ``settings_overrides`` select the mode.
        database_url: DSN for the seeded CRM.

    Returns:
        Environment variables for the spawned server.

    Raises:
        KeyError: A scenario overrides a setting that has no environment
            mapping, which would otherwise run under the wrong policy.
    """
    env = dict(_BASE_SERVER_ENV)
    env["GTM_DATABASE_URL"] = database_url
    for key, value in scenario.settings_overrides.items():
        if key not in _SETTING_ENV_NAMES:
            raise KeyError(f"scenario {scenario.id!r} overrides unmapped setting {key!r}")
        env[_SETTING_ENV_NAMES[key]] = str(value).lower() if isinstance(value, bool) else str(value)
    return env


def mcp_config_for(scenario: Scenario, *, project_dir: Path, database_url: str) -> dict[str, Any]:
    """Build the MCP client configuration the host launches the server from.

    The absolute project path is resolved here, at run time, rather than being
    committed: a checked-in configuration must not carry one developer's
    filesystem layout (Phase 6C).

    Args:
        scenario: The scenario being run.
        project_dir: Repository root, passed to ``uv --directory``.
        database_url: DSN for the seeded CRM.

    Returns:
        A configuration in the host's ``mcpServers`` format.
    """
    return {
        "mcpServers": {
            SERVER_ALIAS: {
                "type": "stdio",
                "command": "uv",
                "args": ["--directory", str(project_dir), "run", "gtm-mcp-server"],
                "env": server_env_for(scenario, database_url=database_url),
            }
        }
    }


class LiveAgentAdapter:
    """Turns a real agent's run into a trace the Phase 5 scorer can read.

    Deliberately not a :class:`~eval.adapters.AgentAdapter`. That protocol hands
    an adapter a connected in-memory :class:`~mcp.Client`, which a live agent
    cannot use: the host owns the client and spawns its own server. Forcing one
    protocol over both would mean pretending the live agent is driven by this
    process. The two adapters stay separate and meet at the trace instead, which
    is the only thing the scoring engine actually consumes.
    """

    def __init__(
        self,
        provider: AgentProvider,
        *,
        project_dir: Path,
        database_url: str,
        timeout_seconds: float = 300.0,
    ) -> None:
        """Configure the adapter.

        Args:
            provider: The agent to measure.
            project_dir: Repository root the host launches the server from.
            database_url: DSN of the seeded CRM the live server reads and writes.
            timeout_seconds: Hard cap on a single scenario.
        """
        self._provider = provider
        self._project_dir = project_dir
        self._database_url = database_url
        self._timeout_seconds = timeout_seconds

    def describe(self) -> dict[str, str]:
        """Return provenance for the report."""
        return {
            **self._provider.describe(),
            "server_launch": f"uv --directory {self._project_dir.name} run gtm-mcp-server",
            "enrichment_provider": "sample (offline dataset; no external API credits spent)",
            "crm": "seeded PostgreSQL, reset before the run",
            "system_prompt": SYSTEM_PROMPT,
        }

    async def run_scenario(self, scenario: Scenario) -> ScenarioResult:
        """Run one scenario against the live agent and score it.

        Args:
            scenario: The scenario to execute.

        Returns:
            The scored result, with the agent's trace attached.
        """
        with tempfile.TemporaryDirectory(prefix="gtm-live-eval-") as work_dir:
            request = AgentRequest(
                prompt=scenario.intent,
                mcp_config=mcp_config_for(
                    scenario, project_dir=self._project_dir, database_url=self._database_url
                ),
                allowed_tools=[f"mcp__{SERVER_ALIAS}__{tool}" for tool in ALL_TOOLS],
                system_prompt=SYSTEM_PROMPT,
                working_dir=Path(work_dir),
                timeout_seconds=self._timeout_seconds,
            )
            run = await self._provider.run(request)

        return self._score(scenario, run)

    def _score(self, scenario: Scenario, run: AgentRun) -> ScenarioResult:
        """Convert an agent run into a scored result.

        Args:
            scenario: The scenario that was executed.
            run: What the agent did.

        Returns:
            The scored result.
        """
        tool_calls = [
            ToolCallTrace(
                tool_name=call.tool_name,
                arguments=redact_data(call.arguments),
                result=redact_data(call.result),
                is_error=call.is_error,
                duration_ms=call.duration_ms,
            )
            for call in run.tool_calls
        ]

        metadata: dict[str, Any] = {
            **run.metadata,
            "agent_provider": self._provider.name,
            "connected_servers": run.connected_servers,
        }
        if run.failed_servers:
            metadata["failed_servers"] = run.failed_servers
        if run.error:
            metadata["error"] = run.error

        # Scored on what the agent actually wrote, persisted with phone numbers
        # and credentials masked. Doing it the other way round would mean the
        # redaction pass silently decides whether a golden phrase matched.
        trace = build_trace(
            scenario,
            tool_calls,
            run.final_response,
            execution_time_ms=run.duration_ms,
            agent_metadata=metadata,
        )
        result = Scorer.score_scenario(scenario, trace)
        result = result.model_copy(
            update={
                "trace": trace.model_copy(
                    update={"final_response": redact_agent_response(run.final_response)}
                )
            }
        )

        if run.error is None:
            return result
        # A run the host could not complete is a failure of the run, not a
        # judgement about the model. It is reported as failed with the reason
        # attached rather than being scored as if the agent chose to say nothing.
        return result.model_copy(
            update={"passed": False, "violations": [*result.violations, f"Agent run: {run.error}"]}
        )


async def reset_database(project_dir: Path, database_url: str) -> None:
    """Restore the demo CRM to its seeded state before a live run.

    Uses the repository's own Alembic migration and seed script rather than any
    new mechanism: ``downgrade base`` drops the schema, ``upgrade head`` rebuilds
    it, and the seed repopulates it. This is destructive to the demo CRM, which
    is why it is the caller's explicit choice.

    Args:
        project_dir: Repository root to run the commands from.
        database_url: DSN to reset.

    Raises:
        RuntimeError: A step failed, leaving the database in an unknown state
            that a live run must not be scored against.
    """
    env = {**os.environ, "GTM_DATABASE_URL": database_url}
    steps: tuple[tuple[str, ...], ...] = (
        ("uv", "run", "alembic", "downgrade", "base"),
        ("uv", "run", "alembic", "upgrade", "head"),
        ("uv", "run", "python", "-m", "scripts.seed"),
    )
    for step in steps:
        proc = await asyncio.create_subprocess_exec(
            *step,
            cwd=str(project_dir),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()[-600:]
            raise RuntimeError(f"database reset step {' '.join(step)} failed: {detail}")


def _aggregate(
    results: list[ScenarioResult], *, mode: str, provenance: dict[str, str]
) -> EvaluationReport:
    """Roll scored scenarios up into a report.

    Args:
        results: Scored scenarios, in execution order.
        mode: Report mode label.
        provenance: What produced the report.

    Returns:
        The aggregate report.
    """
    total = len(results)
    passed = sum(1 for r in results if r.passed)

    # The provider knows what model it *asked* for; only the runs know what
    # actually answered. A report that named the wrong model would be worse than
    # one that named none, so the observed set is recorded alongside.
    observed_models = sorted(
        {
            str(model)
            for result in results
            for model in (result.trace.agent_metadata.get("models") or [])
        }
    )
    resolved_provenance = dict(provenance)
    if observed_models:
        resolved_provenance["models_observed"] = ", ".join(observed_models)
    resolved_provenance["total_tool_calls"] = str(
        sum(len(result.trace.tool_calls) for result in results)
    )

    category_metrics: dict[str, dict[str, float]] = {}
    for category in sorted({r.category.value for r in results}):
        in_category = [r for r in results if r.category.value == category]
        count = len(in_category)
        category_passed = sum(1 for r in in_category if r.passed)
        category_metrics[category] = {
            "total": float(count),
            "passed": float(category_passed),
            "pass_rate": round(category_passed / count * 100.0, 2),
            "avg_composite": round(sum(r.scores.composite for r in in_category) / count, 4),
        }

    def _avg(attr: str) -> float:
        values = [getattr(r.scores, attr) for r in results]
        return round(sum(values) / len(values), 4) if values else 0.0

    return EvaluationReport(
        timestamp=datetime.now(UTC).isoformat(),
        mode=mode,
        total_scenarios=total,
        passed_scenarios=passed,
        failed_scenarios=total - passed,
        pass_rate_pct=round(passed / total * 100.0, 2) if total else 0.0,
        category_metrics=category_metrics,
        aggregate_scores=CategoryScores(
            tool_selection=_avg("tool_selection"),
            sequence=_avg("sequence"),
            efficiency=_avg("efficiency"),
            outcome=_avg("outcome"),
            safety_interpretation=_avg("safety_interpretation"),
            policy_adherence=_avg("policy_adherence"),
            final_response=_avg("final_response"),
            composite=_avg("composite"),
        ),
        scenarios=results,
        avg_latency_ms=(
            round(sum(r.trace.execution_time_ms for r in results) / total, 2) if total else 0.0
        ),
        provenance=resolved_provenance,
    )


async def run_live_evaluation(
    adapter: LiveAgentAdapter,
    scenarios: list[Scenario],
    *,
    on_result: Callable[[int, Scenario, ScenarioResult], None] | None = None,
) -> EvaluationReport:
    """Execute the live subset one scenario at a time and aggregate the results.

    Scenarios run sequentially, not concurrently: they share one CRM database,
    and a parallel run would make one scenario's writes another's preconditions.

    Args:
        adapter: The configured live adapter.
        scenarios: Scenarios to run, in order.
        on_result: Called with the 1-based index, scenario and result as each
            one finishes, so a long run can report progress without this
            function knowing anything about how it is displayed.

    Returns:
        The live evaluation report.
    """
    results: list[ScenarioResult] = []
    for index, scenario in enumerate(scenarios, start=1):
        result = await adapter.run_scenario(scenario)
        results.append(result)
        if on_result is not None:
            on_result(index, scenario, result)
    return _aggregate(results, mode="live", provenance=adapter.describe())


def _default_database_url() -> str:
    """Resolve the CRM DSN for a live run.

    Returns:
        ``GTM_DATABASE_URL`` when set, otherwise the same local default the
        server itself falls back to.
    """
    return os.environ.get("GTM_DATABASE_URL", "postgresql+asyncpg://gtm:gtm@localhost:5432/gtm")


async def _main(args: argparse.Namespace) -> int:
    """Run the live evaluation and write its reports.

    Args:
        args: Parsed command-line arguments.

    Returns:
        Process exit status.
    """
    project_dir = PROJECT_DIR
    provider = ClaudeCodeAgentProvider(model=args.model, max_budget_usd=args.max_budget_usd)

    if not provider.is_available():
        print("Claude Code CLI not found on PATH; cannot run a live evaluation.")
        return 2
    if shutil.which("uv") is None:
        print("uv not found on PATH; the host cannot launch the MCP server.")
        return 2

    scenarios = live_scenarios(args.scenarios or LIVE_SCENARIO_IDS)
    database_url = _default_database_url()

    if args.reset_db:
        print(f"Resetting demo CRM ({len(scenarios)} live scenarios will run against it)...")
        await reset_database(project_dir, database_url)

    adapter = LiveAgentAdapter(
        provider,
        project_dir=project_dir,
        database_url=database_url,
        timeout_seconds=args.timeout,
    )

    print(f"Running {len(scenarios)} live scenarios against a real agent...")

    def _report_progress(index: int, scenario: Scenario, result: ScenarioResult) -> None:
        status = "PASS" if result.passed else "FAIL"
        print(
            f"  [{index}/{len(scenarios)}] {scenario.id:34s} {status} "
            f"composite={result.scores.composite * 100:5.1f}% "
            f"tools={[c.tool_name for c in result.trace.tool_calls]}"
        )

    report = await run_live_evaluation(adapter, scenarios, on_result=_report_progress)
    out_dir = RESULTS_DIR
    json_path, md_path = save_report(report, out_dir, stem="live-latest")

    print("\n" + "=" * 60)
    print("LIVE AGENT EVALUATION SUMMARY (real model, not deterministic)")
    print("=" * 60)
    print(f"Scenarios       : {report.total_scenarios}")
    print(f"Passed          : {report.passed_scenarios}")
    print(f"Pass Rate       : {report.pass_rate_pct}%")
    print(f"Composite Score : {report.aggregate_scores.composite * 100:.1f}%")
    print(f"Safety Score    : {report.aggregate_scores.safety_interpretation * 100:.1f}%")
    print(f"Policy Score    : {report.aggregate_scores.policy_adherence * 100:.1f}%")
    print(f"Avg Latency     : {report.avg_latency_ms / 1000.0:.1f}s")
    print("=" * 60)
    print(f"JSON Report     : {json_path}")
    print(f"Markdown Report : {md_path}")

    baseline_path = out_dir / "latest.json"
    if args.compare and baseline_path.exists():
        comparison_path = out_dir / "comparison.md"
        comparison_path.write_text(
            render_comparison_report(
                EvaluationReport.model_validate_json(baseline_path.read_text(encoding="utf-8")),
                report,
            ),
            encoding="utf-8",
        )
        print(f"Comparison      : {comparison_path}")

    return 0


def main() -> None:
    """CLI entry point for the live-agent evaluation."""
    parser = argparse.ArgumentParser(
        description="Run a subset of the GTM evaluation scenarios against a real LLM agent "
        "connected over MCP. Costs money and is not deterministic; the reproducible "
        "suite is `python -m eval.runner`."
    )
    parser.add_argument(
        "--scenarios",
        nargs="*",
        default=None,
        help=f"Scenario ids to run (default: the {len(LIVE_SCENARIO_IDS)}-scenario live subset).",
    )
    parser.add_argument("--model", default=None, help="Model alias to pin the agent to.")
    parser.add_argument(
        "--timeout", type=float, default=300.0, help="Per-scenario timeout in seconds."
    )
    parser.add_argument(
        "--max-budget-usd",
        type=float,
        default=None,
        help="Spend ceiling handed to the agent host for each scenario.",
    )
    parser.add_argument(
        "--no-reset-db",
        dest="reset_db",
        action="store_false",
        help="Skip resetting and reseeding the demo CRM. Results stop being reproducible.",
    )
    parser.add_argument(
        "--no-compare",
        dest="compare",
        action="store_false",
        help="Skip writing the deterministic-vs-live comparison report.",
    )
    parser.set_defaults(reset_db=True, compare=True)
    raise SystemExit(asyncio.run(_main(parser.parse_args())))


if __name__ == "__main__":
    main()
