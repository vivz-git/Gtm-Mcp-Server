"""Error taxonomy for the GTM MCP server.

The MCP Python SDK distinguishes two failure channels, and the distinction
matters for agent behaviour:

* ``ToolError`` -> the ``tools/call`` succeeds at the protocol level but returns
  ``is_error=True`` with our message in the content. The **model sees the text**
  and can correct itself and retry.
* ``MCPError``  -> the JSON-RPC request itself fails. The model sees nothing;
  only the host application receives the error. Correct for problems no amount
  of model cleverness can fix.

The rule we apply: *could a better model choice have avoided this?*
Yes -> ``ToolError``. No -> ``MCPError``.

Every exception below carries a stable ``code`` so that clients, tests and the
audit trail can branch on the failure kind without string matching.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import INTERNAL_ERROR, INVALID_PARAMS

from mcp import MCPError


class GTMError(Exception):
    """Base class for all domain errors raised inside the GTM layers.

    Domain and service code raises these. Only the tool boundary translates them
    into MCP-visible failures, which keeps protocol concerns out of the business
    layers.
    """

    code: str = "gtm_error"
    """Stable, machine-readable identifier for this failure kind."""

    def __init__(self, message: str, /, **context: Any) -> None:
        """Initialise the error.

        Args:
            message: Human- and model-readable description of what went wrong.
            **context: Structured detail attached to logs and the audit record.
        """
        super().__init__(message)
        self.message = message
        self.context = context

    def to_dict(self) -> dict[str, Any]:
        """Render the error as a JSON-serialisable dict for logs and audit rows."""
        return {"code": self.code, "message": self.message, "context": self.context}


# --------------------------------------------------------------------------
# Model-correctable failures -> surfaced to the agent as ToolError
# --------------------------------------------------------------------------


class ValidationError(GTMError):
    """Input passed schema validation but violates a business rule."""

    code = "validation_error"


class NotFoundError(GTMError):
    """The requested record does not exist.

    Model-correctable: the agent may retry with a different identifier.
    """

    code = "not_found"


class ProviderError(GTMError):
    """An external enrichment provider failed or returned an unusable response."""

    code = "provider_error"


class RateLimitError(ProviderError):
    """An external provider rejected the call because a quota was exhausted."""

    code = "rate_limit"


class WriteRejectedError(GTMError):
    """A write was refused by a guardrail before any mutation was attempted.

    Raised when write tools are disabled, a batch exceeds the configured cap, or
    the operation would be destructive. Distinct from ``WriteFailedError``: no
    state was touched.
    """

    code = "write_rejected"


class WriteFailedError(GTMError):
    """A write was attempted and did not complete.

    This exists so that a failed persistence attempt can never be reported to
    the agent as a success.
    """

    code = "write_failed"


# --------------------------------------------------------------------------
# Non-correctable failures -> surfaced to the host as MCPError
# --------------------------------------------------------------------------


class ConfigurationError(GTMError):
    """The server is misconfigured. No model retry can help."""

    code = "configuration_error"


class RepositoryError(GTMError):
    """The datastore is unreachable or returned an unexpected result."""

    code = "repository_error"


#: Domain errors that a smarter model could have avoided, and which therefore
#: belong in the tool result where the agent can read and react to them.
MODEL_CORRECTABLE: tuple[type[GTMError], ...] = (
    ValidationError,
    NotFoundError,
    ProviderError,
    WriteRejectedError,
    WriteFailedError,
)


def to_mcp_exception(error: GTMError) -> ToolError | MCPError:
    """Translate a domain error into the appropriate MCP failure.

    This is the single place where the ``ToolError`` / ``MCPError`` decision is
    made, so the policy is testable and cannot drift between tools.

    Args:
        error: The domain error raised by a service or repository.

    Returns:
        A ``ToolError`` for model-correctable failures, otherwise an
        ``MCPError`` that fails the JSON-RPC request.
    """
    if isinstance(error, MODEL_CORRECTABLE):
        return ToolError(f"[{error.code}] {error.message}")

    code = INVALID_PARAMS if isinstance(error, ConfigurationError) else INTERNAL_ERROR
    return MCPError(code=code, message=f"[{error.code}] {error.message}", data=error.to_dict())
