"""Evaluation runner orchestrating deterministic and live agent scenario runs.

Constructs in-memory MCP servers wired to seeded doubles, drives scenarios
through an AgentAdapter, collects traces, scores results, and generates reports.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tests.fakes import InMemoryCrmRepository, RecordingAuditSink

from eval.adapters import AgentAdapter, DeterministicAgentAdapter
from eval.models import (
    CategoryScores,
    EvaluationReport,
    Scenario,
    ScenarioResult,
    ScenarioTrace,
)
from eval.report import save_report
from eval.scenarios import SCENARIOS
from eval.scoring import Scorer
from gtm_mcp.context import AppContext
from gtm_mcp.domain.models import Contact, RecordSource
from gtm_mcp.providers.sample import SampleCompanyProvider, SampleContactProvider
from gtm_mcp.server.app import build_server
from gtm_mcp.services.crm import CrmService
from gtm_mcp.services.enrichment import EnrichmentService
from gtm_mcp.settings import Settings
from mcp import Client


def _build_test_server(
    scenario: Scenario,
) -> tuple[Any, InMemoryCrmRepository, RecordingAuditSink]:
    """Assemble an MCP server instance wired with doubles pre-seeded for scenario."""
    repo = InMemoryCrmRepository()
    sink = RecordingAuditSink()

    # Pre-seed contacts
    for c_data in scenario.initial_contacts:
        c_dict = dict(c_data)
        cid = c_dict.pop("contact_id", None)
        raw_source = c_dict.pop("source", RecordSource.CRM)
        c_dict["source"] = RecordSource(raw_source) if isinstance(raw_source, str) else raw_source
        contact = Contact(**c_dict)
        repo.seed(contact, contact_id=cid)

    # Pre-seed lists
    for list_name, member_ids in scenario.initial_lists.items():
        repo.lists[list_name] = set(member_ids)

    # Settings overrides
    settings_dict: dict[str, Any] = {
        "environment": "test",
        "log_format": "console",
        "enable_write_tools": True,
        "dry_run_writes": False,
        "max_write_batch_size": 1,
    }
    settings_dict.update(scenario.settings_overrides)
    settings = Settings(**settings_dict)

    context = AppContext(
        settings=settings,
        audit_sink=sink,
        enrichment=EnrichmentService(
            company_provider=SampleCompanyProvider(),
            contact_provider=SampleContactProvider(),
        ),
        crm=CrmService(repository=repo, audit_sink=sink, settings=settings),
        database_available=True,
    )

    server = build_server(context=context)
    return server, repo, sink


async def run_scenario(scenario: Scenario, adapter: AgentAdapter | None = None) -> ScenarioResult:
    """Execute a single scenario end-to-end and score the resulting trace.

    Args:
        scenario: Scenario specification.
        adapter: Agent adapter to execute with (default: DeterministicAgentAdapter).

    Returns:
        The scored ScenarioResult.
    """
    if adapter is None:
        adapter = DeterministicAgentAdapter(mode="compliant")

    server, _repo, sink = _build_test_server(scenario)

    async with Client(server, raise_exceptions=False) as client:
        tool_traces, final_response = await adapter.run_scenario(scenario, client)

    # Extract outcomes from tool results
    recorded_outcomes: list[str] = []
    for call in tool_traces:
        if call.is_error:
            recorded_outcomes.append("failed")
        elif isinstance(call.result, dict):
            if "outcome" in call.result:
                recorded_outcomes.append(str(call.result["outcome"]).lower())
            elif call.result.get("found") is True:
                recorded_outcomes.append("found")
            elif call.result.get("found") is False:
                recorded_outcomes.append("not_found")
            elif "matches" in call.result:
                count = len(call.result["matches"])
                recorded_outcomes.append(f"found_{count}")

    mutations_count = sum(
        1
        for call in tool_traces
        if isinstance(call.result, dict) and call.result.get("outcome") in ("created", "updated")
    )

    trace = ScenarioTrace(
        scenario_id=scenario.id,
        category=scenario.category,
        intent=scenario.intent,
        tool_calls=tool_traces,
        final_response=final_response,
        execution_time_ms=sum(t.duration_ms for t in tool_traces),
        audit_events_count=len(sink.events),
        mutations_count=mutations_count,
        recorded_outcomes=recorded_outcomes,
    )

    return Scorer.score_scenario(scenario, trace)


async def run_evaluation(
    scenarios: list[Scenario] | None = None,
    adapter: AgentAdapter | None = None,
    mode: str = "deterministic",
) -> EvaluationReport:
    """Run all specified scenarios, compute aggregate metrics, and generate report.

    Args:
        scenarios: List of scenarios to evaluate (defaults to SCENARIOS).
        adapter: Agent adapter to test (defaults to DeterministicAgentAdapter).
        mode: Description of evaluation mode ('deterministic', 'live', etc.).

    Returns:
        The full EvaluationReport.
    """
    if scenarios is None:
        scenarios = SCENARIOS
    if adapter is None:
        adapter = DeterministicAgentAdapter(mode="compliant")

    results: list[ScenarioResult] = []
    for sc in scenarios:
        res = await run_scenario(sc, adapter=adapter)
        results.append(res)

    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed
    pass_rate = (passed / total * 100.0) if total > 0 else 0.0

    # Category breakdowns
    categories = sorted({r.category.value for r in results})
    category_metrics: dict[str, dict[str, float]] = {}

    for cat in categories:
        cat_results = [r for r in results if r.category.value == cat]
        cat_total = len(cat_results)
        cat_passed = sum(1 for r in cat_results if r.passed)
        avg_comp = (
            sum(r.scores.composite for r in cat_results) / cat_total if cat_total > 0 else 0.0
        )
        category_metrics[cat] = {
            "total": float(cat_total),
            "passed": float(cat_passed),
            "pass_rate": round((cat_passed / cat_total * 100.0) if cat_total > 0 else 0.0, 2),
            "avg_composite": round(avg_comp, 4),
        }

    # Aggregate category scores
    def _avg(attr: str) -> float:
        vals = [getattr(r.scores, attr) for r in results]
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    aggregates = CategoryScores(
        tool_selection=_avg("tool_selection"),
        sequence=_avg("sequence"),
        efficiency=_avg("efficiency"),
        outcome=_avg("outcome"),
        safety_interpretation=_avg("safety_interpretation"),
        policy_adherence=_avg("policy_adherence"),
        final_response=_avg("final_response"),
        composite=_avg("composite"),
    )

    report = EvaluationReport(
        timestamp=datetime.now(UTC).isoformat(),
        mode=mode,
        total_scenarios=total,
        passed_scenarios=passed,
        failed_scenarios=failed,
        pass_rate_pct=round(pass_rate, 2),
        category_metrics=category_metrics,
        aggregate_scores=aggregates,
        scenarios=results,
    )

    return report


def main() -> None:
    """CLI entry point for running the evaluation harness."""
    parser = argparse.ArgumentParser(description="GTM MCP Server Agent Evaluation Harness")
    parser.add_argument(
        "--scenario",
        type=str,
        default=None,
        help="Run only the specified scenario ID",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="deterministic",
        help="Evaluation mode (deterministic, live)",
    )
    args = parser.parse_args()

    scenarios = SCENARIOS
    if args.scenario:
        scenarios = [s for s in SCENARIOS if s.id == args.scenario]
        if not scenarios:
            print(f"Error: Scenario '{args.scenario}' not found.")
            return

    print(f"Starting GTM MCP agent evaluation ({len(scenarios)} scenarios, mode: {args.mode})...")
    report = asyncio.run(run_evaluation(scenarios=scenarios, mode=args.mode))

    # Save latest results
    out_dir = Path(__file__).resolve().parent / "results"
    json_path, md_path = save_report(report, out_dir)

    print("\n" + "=" * 60)
    print("EVALUATION SUMMARY")
    print("=" * 60)
    print(f"Total Scenarios : {report.total_scenarios}")
    print(f"Passed          : {report.passed_scenarios}")
    print(f"Failed          : {report.failed_scenarios}")
    print(f"Pass Rate       : {report.pass_rate_pct}%")
    print(f"Composite Score : {report.aggregate_scores.composite * 100:.1f}%")
    print(f"Safety Score    : {report.aggregate_scores.safety_interpretation * 100:.1f}%")
    print(f"Policy Score    : {report.aggregate_scores.policy_adherence * 100:.1f}%")
    print("=" * 60)
    print(f"JSON Report     : {json_path}")
    print(f"Markdown Report : {md_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
