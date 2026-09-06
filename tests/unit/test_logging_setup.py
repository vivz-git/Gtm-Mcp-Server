"""Transport safety: nothing may be written to stdout under the stdio transport."""

from __future__ import annotations

import json
import logging

import pytest

from gtm_mcp.logging_setup import REDACTION_PLACEHOLDER, configure_logging, get_logger


@pytest.mark.unit
def test_logs_never_reach_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """Stdout carries the JSON-RPC stream; a stray log line disconnects the client."""
    configure_logging(level="DEBUG", log_format="json")
    get_logger("test").info("hello", key="value")
    logging.getLogger("third_party").warning("library noise")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "hello" in captured.err


@pytest.mark.unit
def test_json_format_emits_one_parsable_object_per_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Log aggregators need machine-readable lines, not prose."""
    configure_logging(level="INFO", log_format="json")
    get_logger("test").info("server_started", database_available=True)

    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["event"] == "server_started"
    assert payload["database_available"] is True
    assert payload["level"] == "info"
    assert "timestamp" in payload


@pytest.mark.unit
def test_sensitive_fields_are_redacted(capsys: pytest.CaptureFixture[str]) -> None:
    """Enrichment payloads carry personal data; redaction is central, not per call site."""
    configure_logging(level="INFO", log_format="json")
    get_logger("test").info(
        "enriched",
        email="ada@example.com",
        api_key="sk-live-123",
        company_domain="example.com",
    )

    err = capsys.readouterr().err
    assert "ada@example.com" not in err
    assert "sk-live-123" not in err
    assert err.count(REDACTION_PLACEHOLDER) == 2
    # Non-sensitive context must survive, or the logs stop being useful.
    assert "example.com" in err


@pytest.mark.unit
def test_the_enrichment_auth_header_name_is_redacted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No code logs a header today; this keeps a future debug line from leaking one."""
    configure_logging(level="INFO", log_format="json")
    get_logger("test").warning(
        "provider_request_failed",
        **{"x-api-key": "a-real-looking-key", "provider": "hunter"},
    )

    err = capsys.readouterr().err
    assert "a-real-looking-key" not in err
    assert REDACTION_PLACEHOLDER in err
    assert "hunter" in err, "non-sensitive context survives redaction"
