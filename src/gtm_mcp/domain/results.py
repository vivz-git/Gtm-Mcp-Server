"""Structured result envelopes returned by write tools.

The architecture requires that a write tool can never report success for an
operation that did not happen. That rule is enforced structurally here rather
than by convention: ``success`` is a *computed* property derived from
``outcome``, so there is no field a careless call site could set to ``True``
after a failure.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field


class WriteOutcome(StrEnum):
    """What actually happened to persistent state during a write tool call."""

    CREATED = "created"
    """A new record was inserted."""

    UPDATED = "updated"
    """An existing record was found and one or more fields changed."""

    UNCHANGED = "unchanged"
    """An existing record was found and already matched the input. Idempotent no-op."""

    DRY_RUN = "dry_run"
    """Input validated and audited, but persistence was skipped by configuration."""

    REJECTED = "rejected"
    """A guardrail refused the operation. Nothing was attempted."""

    FAILED = "failed"
    """Persistence was attempted and did not complete."""


#: Outcomes in which the caller's intent was satisfied. ``DRY_RUN`` is excluded
#: deliberately: nothing was persisted, so an agent must not treat it as done.
SUCCESSFUL_OUTCOMES: frozenset[WriteOutcome] = frozenset(
    {WriteOutcome.CREATED, WriteOutcome.UPDATED, WriteOutcome.UNCHANGED}
)


class WriteResult(BaseModel):
    """The result of a single write operation, as returned to the calling agent.

    Returned by every write tool. The shape is uniform so that an agent can
    branch on ``outcome`` without knowing which tool it called.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: WriteOutcome = Field(description="What happened to persistent state.")
    record_id: str | None = Field(
        default=None, description="Identifier of the affected record, when one exists."
    )
    changed_fields: tuple[str, ...] = Field(
        default=(),
        description="Names of fields whose values changed. Empty for unchanged or failed writes.",
    )
    audit_id: str | None = Field(
        default=None, description="Identifier of the audit record for this operation."
    )
    message: str = Field(
        description="Human- and model-readable summary, including the reason on failure."
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def success(self) -> bool:
        """Whether persistent state now reflects the caller's intent.

        Derived from ``outcome`` and therefore impossible to set independently.
        """
        return self.outcome in SUCCESSFUL_OUTCOMES

    @classmethod
    def rejected(cls, reason: str) -> WriteResult:
        """Build a result for a write refused by a guardrail before any attempt.

        Args:
            reason: Why the guardrail refused, phrased for the calling agent.

        Returns:
            A ``WriteResult`` with outcome ``REJECTED``.
        """
        return cls(outcome=WriteOutcome.REJECTED, message=reason)

    @classmethod
    def failed(cls, reason: str, *, record_id: str | None = None) -> WriteResult:
        """Build a result for a write that was attempted and did not complete.

        Args:
            reason: What failed, phrased for the calling agent.
            record_id: Identifier of the record involved, if known.

        Returns:
            A ``WriteResult`` with outcome ``FAILED``.
        """
        return cls(outcome=WriteOutcome.FAILED, record_id=record_id, message=reason)
