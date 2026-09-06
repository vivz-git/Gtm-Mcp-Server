"""Evaluation domain models for the GTM MCP Server evaluation harness.

Defines scenarios, golden expectations, tool traces, category scores,
and evaluation report structures.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ScenarioCategory(StrEnum):
    """Behavioral classes of agent evaluation scenarios."""

    CRM_FIRST = "crm_first"
    ENRICHMENT = "enrichment"
    CRM_QUERY = "crm_query"
    SYNC = "sync"
    LIST_ADD = "list_add"
    SEQUENCING = "sequencing"
    IDEMPOTENCY = "idempotency"
    WRITE_REJECTION = "write_rejection"
    DRY_RUN = "dry_run"
    FAILURE_HANDLING = "failure_handling"
    READ_WRITE_BOUNDARY = "read_write_boundary"


class GoldenExpectations(BaseModel):
    """Explicit behavioral and safety expectations for an evaluation scenario."""

    model_config = ConfigDict(frozen=True)

    allowed_tools: list[str] = Field(
        description="Tools the agent is permitted to call for this scenario."
    )
    required_tools: list[str] = Field(
        default_factory=list,
        description="Tools the agent must invoke to achieve the goal.",
    )
    forbidden_tools: list[str] = Field(
        default_factory=list,
        description="Tools the agent must NOT invoke (e.g. write tools on read intents).",
    )
    preferred_order: list[str] = Field(
        default_factory=list,
        description="Expected order of tool invocations when relative sequencing matters.",
    )
    expected_outcome: str | None = Field(
        default=None,
        description="Expected write outcome (e.g. 'created', 'updated', 'unchanged', 'rejected', 'dry_run', 'failed').",
    )
    mutation_expected: bool = Field(
        default=False,
        description="Whether a persistent database mutation should occur in the CRM.",
    )
    audit_expected: bool = Field(
        default=False,
        description="Whether an audit trail event must be recorded.",
    )
    expect_not_done: bool = Field(
        default=False,
        description="If True, the agent MUST explicitly communicate that mutation was NOT completed (rejected, dry run, or failed).",
    )
    max_tool_calls: int = Field(
        default=4,
        description="Maximum allowed tool calls before efficiency score is penalized.",
    )
    required_response_patterns: list[str] = Field(
        default_factory=list,
        description="Keywords or phrases the final agent response is required to contain.",
    )
    forbidden_response_patterns: list[str] = Field(
        default_factory=list,
        description="Keywords or phrases the final agent response must NOT contain (e.g. claiming success on rejection).",
    )


class Scenario(BaseModel):
    """An executable evaluation scenario."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Unique scenario identifier (e.g. 'crm-first-known').")
    category: ScenarioCategory = Field(description="Behavioral category.")
    title: str = Field(description="Short human-readable title.")
    intent: str = Field(description="User prompt given to the agent.")
    expectations: GoldenExpectations = Field(description="Expected behavior rules.")
    description: str = Field(default="", description="Detailed explanation of the scenario.")
    settings_overrides: dict[str, Any] = Field(
        default_factory=dict,
        description="Server settings overrides (e.g. enable_write_tools=False, dry_run_writes=True).",
    )
    initial_contacts: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Contacts to pre-seed into the in-memory CRM for this scenario.",
    )
    initial_lists: dict[str, list[str]] = Field(
        default_factory=dict,
        description="List memberships to pre-seed (list_name -> contact_ids).",
    )


class ToolCallTrace(BaseModel):
    """Trace of a single MCP tool execution by an agent."""

    model_config = ConfigDict(frozen=True)

    tool_name: str = Field(description="Name of the MCP tool called.")
    arguments: dict[str, Any] = Field(description="Sanitized arguments passed to the tool.")
    result: Any = Field(description="Sanitized result returned by the tool.")
    is_error: bool = Field(default=False, description="Whether the tool execution failed.")
    duration_ms: float = Field(default=0.0, description="Call duration in milliseconds.")


class ScenarioTrace(BaseModel):
    """Complete execution record for one evaluation scenario run."""

    model_config = ConfigDict(frozen=True)

    scenario_id: str = Field(description="Identifier of the scenario.")
    category: ScenarioCategory = Field(description="Category of the scenario.")
    intent: str = Field(description="User prompt evaluated.")
    tool_calls: list[ToolCallTrace] = Field(
        default_factory=list, description="Ordered list of tool calls made by the agent."
    )
    final_response: str = Field(default="", description="Agent's final response to the user.")
    execution_time_ms: float = Field(default=0.0, description="Total execution time in ms.")
    audit_events_count: int = Field(
        default=0, description="Number of audit events logged during execution."
    )
    mutations_count: int = Field(
        default=0, description="Number of persistent CRM mutations recorded."
    )
    recorded_outcomes: list[str] = Field(
        default_factory=list, description="Recorded write outcomes from tool payloads."
    )


class CategoryScores(BaseModel):
    """Scores (0.0 to 1.0) broken down across evaluation categories."""

    model_config = ConfigDict(frozen=True)

    tool_selection: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Score for picking valid and required tools."
    )
    sequence: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Score for proper sequencing order."
    )
    efficiency: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Score for avoiding redundant or runaway calls."
    )
    outcome: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Score for achieving expected GTM outcome."
    )
    safety_interpretation: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Score for correctly understanding rejected, dry_run, failed, unchanged states.",
    )
    policy_adherence: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Score for respecting read/write boundaries."
    )
    final_response: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Score for accurate communication in final response.",
    )
    composite: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Weighted composite score across all categories."
    )


class ScenarioResult(BaseModel):
    """The scored result of a single scenario execution."""

    model_config = ConfigDict(frozen=True)

    scenario_id: str
    category: ScenarioCategory
    title: str
    passed: bool
    scores: CategoryScores
    violations: list[str] = Field(default_factory=list)
    trace: ScenarioTrace


class EvaluationReport(BaseModel):
    """Aggregate evaluation report for a complete evaluation suite run."""

    model_config = ConfigDict(frozen=True)

    timestamp: str
    mode: str
    total_scenarios: int
    passed_scenarios: int
    failed_scenarios: int
    pass_rate_pct: float
    category_metrics: dict[str, dict[str, float]]
    aggregate_scores: CategoryScores
    scenarios: list[ScenarioResult]
