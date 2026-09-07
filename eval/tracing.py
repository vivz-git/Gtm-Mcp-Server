"""Shared assembly of a scored-ready :class:`ScenarioTrace` from raw tool calls.

Both evaluation modes end up in the same place: a list of
:class:`~eval.models.ToolCallTrace` plus a final response, from which the
scoring engine's inputs (``recorded_outcomes``, ``mutations_count``) are
derived. That derivation is the contract between an adapter and
:class:`~eval.scoring.Scorer`, so it lives here once rather than being
re-implemented per mode — a deterministic run and a live run must summarise an
identical tool result identically, or the two reports are not comparable.
"""

from __future__ import annotations

from typing import Any

from eval.models import Scenario, ScenarioTrace, ToolCallTrace

#: Write outcomes that mean the CRM actually changed. ``unchanged`` is a
#: successful no-op and ``dry_run`` / ``rejected`` / ``failed`` wrote nothing,
#: so none of them count as a mutation (D-023).
_MUTATING_OUTCOMES = frozenset({"created", "updated"})


def summarize_outcomes(tool_calls: list[ToolCallTrace]) -> list[str]:
    """Reduce each tool result to the single token the scorer compares against.

    Args:
        tool_calls: Ordered tool calls recorded by an adapter.

    Returns:
        One outcome token per call, in call order.
    """
    outcomes: list[str] = []
    for call in tool_calls:
        if call.is_error:
            outcomes.append("failed")
        elif isinstance(call.result, dict):
            result: dict[str, Any] = call.result
            if "outcome" in result:
                outcomes.append(str(result["outcome"]).lower())
            elif result.get("found") is True:
                outcomes.append("found")
            elif result.get("found") is False:
                outcomes.append("not_found")
            elif "matches" in result:
                outcomes.append(f"found_{len(result['matches'])}")
    return outcomes


def count_mutations(tool_calls: list[ToolCallTrace]) -> int:
    """Count tool results that report a persisted CRM change.

    Args:
        tool_calls: Ordered tool calls recorded by an adapter.

    Returns:
        The number of calls whose outcome was ``created`` or ``updated``.
    """
    return sum(
        1
        for call in tool_calls
        if isinstance(call.result, dict) and call.result.get("outcome") in _MUTATING_OUTCOMES
    )


def build_trace(
    scenario: Scenario,
    tool_calls: list[ToolCallTrace],
    final_response: str,
    *,
    audit_events_count: int = 0,
    execution_time_ms: float | None = None,
    agent_metadata: dict[str, Any] | None = None,
) -> ScenarioTrace:
    """Assemble the trace the scoring engine consumes.

    Args:
        scenario: The scenario that was executed.
        tool_calls: Ordered, already-redacted tool call records.
        final_response: The agent's closing message to the user.
        audit_events_count: Audit events observed for this run. Only the
            deterministic runner can observe these directly; a live run drives
            a separate server process and reports 0 rather than guessing.
        execution_time_ms: Wall-clock duration of the run. Defaults to the sum
            of the individual call durations, which is all the deterministic
            runner can attribute.
        agent_metadata: Provenance of the agent that produced the trace
            (model, session identifier, cost). Empty for deterministic runs.

    Returns:
        A populated :class:`ScenarioTrace`.
    """
    return ScenarioTrace(
        scenario_id=scenario.id,
        category=scenario.category,
        intent=scenario.intent,
        tool_calls=tool_calls,
        final_response=final_response,
        execution_time_ms=(
            sum(call.duration_ms for call in tool_calls)
            if execution_time_ms is None
            else round(execution_time_ms, 2)
        ),
        audit_events_count=audit_events_count,
        mutations_count=count_mutations(tool_calls),
        recorded_outcomes=summarize_outcomes(tool_calls),
        agent_metadata=agent_metadata or {},
    )
