"""Error translation policy: which failures the model may see and act on."""

from __future__ import annotations

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import INTERNAL_ERROR, INVALID_PARAMS

from gtm_mcp.errors import (
    ConfigurationError,
    GTMError,
    NotFoundError,
    ProviderError,
    RateLimitError,
    RepositoryError,
    ValidationError,
    WriteFailedError,
    WriteRejectedError,
    to_mcp_exception,
)
from mcp import MCPError


@pytest.mark.unit
@pytest.mark.parametrize(
    "error",
    [
        ValidationError("bad domain"),
        NotFoundError("no such contact"),
        ProviderError("provider 500"),
        RateLimitError("quota exhausted"),
        WriteRejectedError("writes disabled"),
        WriteFailedError("insert failed"),
    ],
)
def test_model_correctable_errors_reach_the_agent(error: GTMError) -> None:
    """These become tool results, so the model can read the reason and adapt."""
    translated = to_mcp_exception(error)
    assert isinstance(translated, ToolError)
    assert error.code in str(translated)
    assert error.message in str(translated)


@pytest.mark.unit
def test_rate_limit_inherits_provider_error_handling() -> None:
    """A subclass of a correctable error must stay correctable."""
    assert isinstance(to_mcp_exception(RateLimitError("slow down")), ToolError)


@pytest.mark.unit
def test_repository_failure_is_hidden_from_the_agent() -> None:
    """No prompt change fixes a dead database, so it fails the request instead."""
    translated = to_mcp_exception(RepositoryError("connection refused"))
    assert isinstance(translated, MCPError)
    assert translated.error.code == INTERNAL_ERROR


@pytest.mark.unit
def test_configuration_error_is_reported_as_invalid_params() -> None:
    translated = to_mcp_exception(ConfigurationError("missing DSN"))
    assert isinstance(translated, MCPError)
    assert translated.error.code == INVALID_PARAMS


@pytest.mark.unit
def test_error_context_is_preserved_for_the_host() -> None:
    """Structured context must survive translation so operators can debug."""
    translated = to_mcp_exception(RepositoryError("connection refused", dsn_host="db"))
    assert isinstance(translated, MCPError)
    assert translated.error.data == {
        "code": "repository_error",
        "message": "connection refused",
        "context": {"dsn_host": "db"},
    }


@pytest.mark.unit
def test_codes_are_unique_across_the_taxonomy() -> None:
    """Duplicate codes would make audit rows and client branching ambiguous."""
    types = [
        ValidationError,
        NotFoundError,
        ProviderError,
        RateLimitError,
        WriteRejectedError,
        WriteFailedError,
        ConfigurationError,
        RepositoryError,
    ]
    codes = [t.code for t in types]
    assert len(codes) == len(set(codes))
