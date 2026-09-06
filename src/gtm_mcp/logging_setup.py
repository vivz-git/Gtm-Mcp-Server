"""Structured logging configuration.

**Critical constraint:** under the stdio transport, stdout carries the JSON-RPC
message stream. Anything else written there corrupts the protocol and the client
disconnects. Every log sink configured here therefore writes to **stderr**, and
the transport-safety test in ``tests/unit/test_logging_setup.py`` guards it.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from gtm_mcp.settings import LogFormat, LogLevel

#: Keys whose values are replaced with a placeholder before a log line is
#: emitted. Enrichment payloads and CRM rows carry personal data, so redaction
#: is applied centrally rather than trusted to each call site.
REDACTED_KEYS: frozenset[str] = frozenset(
    {
        "api_key",
        "authorization",
        "database_url",
        "email",
        "enrichment_api_key",
        "password",
        "phone",
        "secret",
        "token",
    }
)

REDACTION_PLACEHOLDER = "[redacted]"


def _redact_sensitive(
    _logger: Any, _method: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """Replace values of known-sensitive keys with a placeholder.

    Args:
        _logger: Unused; required by the structlog processor signature.
        _method: Unused; required by the structlog processor signature.
        event_dict: The event being logged.

    Returns:
        The event dict with sensitive values replaced in place.
    """
    for key in event_dict:
        if key.lower() in REDACTED_KEYS and event_dict[key] is not None:
            event_dict[key] = REDACTION_PLACEHOLDER
    return event_dict


def configure_logging(*, level: LogLevel = "INFO", log_format: LogFormat = "json") -> None:
    """Configure structlog and the stdlib logging root to emit to stderr.

    Safe to call more than once; the last call wins.

    Args:
        level: Minimum level to emit.
        log_format: ``json`` for machine-readable output, ``console`` for
            colourised local development output.
    """
    # Route stdlib logging (SQLAlchemy, uvicorn, the SDK) to stderr as well, so
    # a third-party library cannot corrupt the stdio protocol stream on our behalf.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=getattr(logging, level),
        force=True,
    )

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if log_format == "json"
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact_sensitive,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level)),
        logger_factory=structlog.WriteLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a bound structured logger.

    Args:
        name: Logger name, conventionally the module's ``__name__``.

    Returns:
        A structlog bound logger writing to stderr.
    """
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
