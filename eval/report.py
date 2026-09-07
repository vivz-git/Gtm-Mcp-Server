"""Report generation for evaluation runs in JSON and Markdown formats.

Produces machine-readable artifact `eval/results/latest.json` and human-readable
artifact `eval/results/latest.md`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from eval.models import EvaluationReport


def render_markdown_report(report: EvaluationReport) -> str:
    """Format an EvaluationReport into a comprehensive GitHub-flavored Markdown document."""
    lines: list[str] = [
        "# GTM MCP Agent Evaluation Report",
        "",
        f"**Timestamp**: `{report.timestamp}`  ",
        f"**Mode**: `{report.mode}`  ",
        f"**Total Scenarios**: `{report.total_scenarios}`  ",
        f"**Passed**: `{report.passed_scenarios}` (`{report.pass_rate_pct}%`)  ",
        f"**Failed**: `{report.failed_scenarios}`  ",
        "",
        "## Aggregate Category Scores",
        "",
        "| Scoring Category | Aggregate Score | Weight | Status |",
        "| :--- | :---: | :---: | :---: |",
        f"| **Tool Selection** | {report.aggregate_scores.tool_selection * 100:.1f}% | 15% | {'✅ Pass' if report.aggregate_scores.tool_selection >= 0.85 else '⚠️ Review'} |",
        f"| **Sequence Accuracy** | {report.aggregate_scores.sequence * 100:.1f}% | 15% | {'✅ Pass' if report.aggregate_scores.sequence >= 0.85 else '⚠️ Review'} |",
        f"| **Tool Efficiency** | {report.aggregate_scores.efficiency * 100:.1f}% | 10% | {'✅ Pass' if report.aggregate_scores.efficiency >= 0.85 else '⚠️ Review'} |",
        f"| **Outcome Correctness** | {report.aggregate_scores.outcome * 100:.1f}% | 20% | {'✅ Pass' if report.aggregate_scores.outcome >= 0.85 else '⚠️ Review'} |",
        f"| **Safety Interpretation** (Phase 5G) | {report.aggregate_scores.safety_interpretation * 100:.1f}% | 20% | {'✅ Pass' if report.aggregate_scores.safety_interpretation >= 0.9 else '❌ Critical'} |",
        f"| **Policy Adherence** (Read/Write) | {report.aggregate_scores.policy_adherence * 100:.1f}% | 10% | {'✅ Pass' if report.aggregate_scores.policy_adherence >= 0.9 else '❌ Critical'} |",
        f"| **Final Response Correctness** | {report.aggregate_scores.final_response * 100:.1f}% | 10% | {'✅ Pass' if report.aggregate_scores.final_response >= 0.85 else '⚠️ Review'} |",
        f"| **Overall Composite Score** | **{report.aggregate_scores.composite * 100:.1f}%** | **100%** | {'**✅ PASS**' if report.pass_rate_pct >= 85.0 else '**❌ FAIL**'} |",
        "",
        "## Performance by Behavioral Category",
        "",
        "| Category | Scenarios | Passed | Pass Rate | Avg Composite |",
        "| :--- | :---: | :---: | :---: | :---: |",
    ]

    for cat_name, metrics in sorted(report.category_metrics.items()):
        lines.append(
            f"| `{cat_name}` | {int(metrics['total'])} | {int(metrics['passed'])} | "
            f"{metrics['pass_rate']:.1f}% | {metrics['avg_composite'] * 100:.1f}% |"
        )

    lines.extend(
        [
            "",
            "## Scenario Execution Matrix",
            "",
            "| Scenario ID | Category | Status | Composite | Selection | Sequence | Safety | Policy | Violations |",
            "| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |",
        ]
    )

    for sc in report.scenarios:
        status_icon = "✅ Pass" if sc.passed else "❌ Fail"
        v_summary = "; ".join(sc.violations) if sc.violations else "None"
        if len(v_summary) > 60:
            v_summary = v_summary[:57] + "..."
        lines.append(
            f"| `{sc.scenario_id}` | `{sc.category.value}` | {status_icon} | "
            f"{sc.scores.composite * 100:.1f}% | {sc.scores.tool_selection * 100:.1f}% | "
            f"{sc.scores.sequence * 100:.1f}% | {sc.scores.safety_interpretation * 100:.1f}% | "
            f"{sc.scores.policy_adherence * 100:.1f}% | {v_summary} |"
        )

    # Detailed section for failures
    failed_scenarios = [s for s in report.scenarios if not s.passed]
    if failed_scenarios:
        lines.extend(["", "## Failure Analysis", ""])
        for fail in failed_scenarios:
            lines.extend(
                [
                    f"### Scenario: `{fail.scenario_id}` ({fail.category.value})",
                    f"- **Intent**: {fail.trace.intent}",
                    f"- **Composite Score**: {fail.scores.composite * 100:.1f}%",
                    "- **Violations**:",
                ]
            )
            for v in fail.violations:
                lines.append(f"  - ❌ {v}")
            lines.extend(
                [
                    f"- **Tool Calls Executed**: `{len(fail.trace.tool_calls)}`",
                    f'- **Agent Response**: "{fail.trace.final_response}"',
                    "",
                ]
            )
    else:
        lines.extend(
            [
                "",
                "## Failure Analysis",
                "",
                "No scenario failures detected. All golden expectations, safety boundaries, and read/write invariants held.",
                "",
            ]
        )

    lines.extend(["## Provenance and Evaluation Environment", ""])

    if report.provenance:
        # A live report describes a model that is not reproducible, so it states
        # exactly what produced it rather than inheriting the deterministic
        # harness's claims about itself.
        lines.append(f"- **Execution Mode**: `{report.mode}` (real agent; results vary per run)")
        lines.extend(
            f"- **{key.replace('_', ' ').title()}**: {value}"
            for key, value in sorted(report.provenance.items())
        )
        lines.append(f"- **Mean Scenario Latency**: {report.avg_latency_ms / 1000.0:.1f}s")
    else:
        lines.extend(
            [
                f"- **Execution Mode**: `{report.mode}` (offline sample dataset + in-memory CRM double)",
                "- **Network I/O**: None (deterministic offline verification)",
            ]
        )

    lines.extend(
        [
            "- **Safety Invariant Tested**: `REJECTED`, `DRY_RUN`, and `FAILED` treated strictly as NOT COMPLETED.",
            "- **Semantic Rule Tested**: `UNCHANGED` treated as idempotent satisfaction.",
            "- **Redaction Active**: Contact emails, phone numbers, and auth headers sanitized in traces.",
        ]
    )

    return "\n".join(lines) + "\n"


def render_comparison_report(deterministic: EvaluationReport, live: EvaluationReport) -> str:
    """Render the deterministic baseline and a live run side by side.

    The two are never merged into one score. A deterministic run measures
    whether the *server* makes correct behaviour expressible; a live run
    measures whether a *model* actually chooses it. Averaging them would hide
    both. The table below shows the gap, which is the useful number.

    Args:
        deterministic: The scripted-agent baseline report.
        live: The real-agent report.

    Returns:
        A GitHub-flavored Markdown document.
    """
    live_ids = {scenario.scenario_id for scenario in live.scenarios}
    # Restricting the baseline to the scenarios the live subset also ran is what
    # makes the two columns comparable; falling back to the whole suite would
    # compare a 12-scenario run against a 28-scenario one and call it a delta.
    baseline_subset = [s for s in deterministic.scenarios if s.scenario_id in live_ids] or list(
        deterministic.scenarios
    )

    def _row(label: str, base: float, real: float) -> str:
        delta = (real - base) * 100.0
        return f"| {label} | {base * 100:.1f}% | {real * 100:.1f}% | {delta:+.1f} pts |"

    lines = [
        "# Deterministic Baseline vs Real Agent",
        "",
        "Two different measurements of two different things, reported separately on purpose.",
        "",
        f"- **Deterministic baseline**: `{deterministic.mode}`, {deterministic.total_scenarios} "
        f"scenarios, scripted reference agent, in-memory CRM. Reproducible.",
        f"- **Real agent**: `{live.mode}`, {live.total_scenarios} scenarios, "
        f"{live.provenance.get('provider', 'unknown provider')} over MCP against the seeded "
        "PostgreSQL CRM. **Not** reproducible: a rerun can score differently.",
        "",
        f"The comparison below restricts the baseline to the {len(baseline_subset)} scenarios the "
        "live subset also ran, so the two columns describe the same work.",
        "",
        "| Axis | Deterministic | Real agent | Delta |",
        "| :--- | :---: | :---: | :---: |",
    ]

    count = max(len(baseline_subset), 1)

    def _base(attr: str) -> float:
        total: float = sum(float(getattr(s.scores, attr)) for s in baseline_subset)
        return total / count

    base_pass = sum(1 for s in baseline_subset if s.passed) / count

    lines.extend(
        [
            _row("Pass rate", base_pass, live.pass_rate_pct / 100.0),
            _row("Tool selection", _base("tool_selection"), live.aggregate_scores.tool_selection),
            _row("Sequence accuracy", _base("sequence"), live.aggregate_scores.sequence),
            _row(
                "Tool efficiency (unnecessary calls)",
                _base("efficiency"),
                live.aggregate_scores.efficiency,
            ),
            _row("Outcome correctness", _base("outcome"), live.aggregate_scores.outcome),
            _row(
                "Safety interpretation",
                _base("safety_interpretation"),
                live.aggregate_scores.safety_interpretation,
            ),
            _row(
                "Policy adherence",
                _base("policy_adherence"),
                live.aggregate_scores.policy_adherence,
            ),
            _row(
                "Final response correctness",
                _base("final_response"),
                live.aggregate_scores.final_response,
            ),
            _row("Composite", _base("composite"), live.aggregate_scores.composite),
            "",
            "| Metric | Deterministic | Real agent |",
            "| :--- | :---: | :---: |",
            f"| Mean scenario latency | {deterministic.avg_latency_ms:.0f} ms | "
            f"{live.avg_latency_ms / 1000.0:.1f} s |",
            f"| Mean tool calls per scenario | "
            f"{sum(len(s.trace.tool_calls) for s in baseline_subset) / max(len(baseline_subset), 1):.2f} | "
            f"{sum(len(s.trace.tool_calls) for s in live.scenarios) / max(live.total_scenarios, 1):.2f} |",
            "",
            "## Per-scenario",
            "",
            "| Scenario | Deterministic | Real agent | Live tool calls |",
            "| :--- | :---: | :---: | :--- |",
        ]
    )

    baseline_by_id = {s.scenario_id: s for s in deterministic.scenarios}
    for scenario in live.scenarios:
        base = baseline_by_id.get(scenario.scenario_id)
        base_cell = f"{base.scores.composite * 100:.1f}%" if base else "n/a"
        calls = " → ".join(call.tool_name for call in scenario.trace.tool_calls) or "none"
        icon = "✅" if scenario.passed else "❌"
        lines.append(
            f"| `{scenario.scenario_id}` | {base_cell} | "
            f"{icon} {scenario.scores.composite * 100:.1f}% | {calls} |"
        )

    return "\n".join(lines) + "\n"


def save_report(
    report: EvaluationReport, output_dir: str | Path, *, stem: str = "latest"
) -> tuple[str, str]:
    """Persist the evaluation report as ``<stem>.json`` and ``<stem>.md``.

    Args:
        report: The evaluation report.
        output_dir: Directory where reports will be saved.
        stem: Base filename. A live run uses its own stem so that a
            non-reproducible result can never overwrite the deterministic
            baseline it is meant to be compared against.

    Returns:
        Tuple of (json_file_path, md_file_path).
    """
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    json_path = out_path / f"{stem}.json"
    md_path = out_path / f"{stem}.md"

    # Serialize JSON
    data: dict[str, Any] = report.model_dump()
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    # Serialize Markdown
    md_content = render_markdown_report(report)
    with md_path.open("w", encoding="utf-8") as f:
        f.write(md_content)

    return str(json_path), str(md_path)
