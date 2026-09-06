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

    lines.extend(
        [
            "## Provenance and Evaluation Environment",
            "",
            f"- **Execution Mode**: `{report.mode}` (offline sample dataset + in-memory CRM double)",
            "- **Network I/O**: None (deterministic offline verification)",
            "- **Safety Invariant Tested**: `REJECTED`, `DRY_RUN`, and `FAILED` treated strictly as NOT COMPLETED.",
            "- **Semantic Rule Tested**: `UNCHANGED` treated as idempotent satisfaction.",
            "- **Redaction Active**: Contact emails, phone numbers, and auth headers sanitized in traces.",
        ]
    )

    return "\n".join(lines) + "\n"


def save_report(report: EvaluationReport, output_dir: str | Path) -> tuple[str, str]:
    """Persist the evaluation report as latest.json and latest.md.

    Args:
        report: The evaluation report.
        output_dir: Directory where reports will be saved.

    Returns:
        Tuple of (json_file_path, md_file_path).
    """
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    json_path = out_path / "latest.json"
    md_path = out_path / "latest.md"

    # Serialize JSON
    data: dict[str, Any] = report.model_dump()
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    # Serialize Markdown
    md_content = render_markdown_report(report)
    with md_path.open("w", encoding="utf-8") as f:
        f.write(md_content)

    return str(json_path), str(md_path)
