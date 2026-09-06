"""Redaction and sanitization engine for evaluation traces and reports.

Ensures that API keys, authentication headers, passwords, phone numbers,
and personal email addresses are sanitized before evaluation traces are
written to disk or logged.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "authorization",
    "auth",
    "credential",
    "private_key",
    "bearer",
    "hunter_api_key",
}

_EMAIL_PATTERN = re.compile(
    r"\b([a-zA-Z0-9_.+-])[a-zA-Z0-9_.+-]*@([a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)\b"
)
_PHONE_PATTERN = re.compile(r"(?:\+\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}\b")
_AUTH_HEADER_PATTERN = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9_\-\.]{8,}\b")


def redact_string(value: str) -> str:
    """Sanitize sensitive patterns inside a string.

    Args:
        value: Input string.

    Returns:
        String with credentials, emails, and phone numbers masked.
    """
    masked = _AUTH_HEADER_PATTERN.sub(r"\1[REDACTED_TOKEN]", value)
    masked = _PHONE_PATTERN.sub("[REDACTED_PHONE]", masked)
    masked = _EMAIL_PATTERN.sub(r"\1***@\2", masked)
    return masked


def redact_data(data: Any) -> Any:
    """Recursively sanitize any nested data structure.

    Args:
        data: Arbitrary object (dict, list, primitive, BaseModel).

    Returns:
        A deep copy of the structure with sensitive values redacted.
    """
    if isinstance(data, BaseModel):
        return redact_data(data.model_dump())

    if isinstance(data, dict):
        sanitized: dict[str, Any] = {}
        for key, value in data.items():
            key_str = str(key)
            if any(sensitive in key_str.lower() for sensitive in _SENSITIVE_KEYS):
                sanitized[key_str] = "[REDACTED_SECRET]"
            else:
                sanitized[key_str] = redact_data(value)
        return sanitized

    if isinstance(data, list):
        return [redact_data(item) for item in data]

    if isinstance(data, tuple):
        return tuple(redact_data(item) for item in data)

    if isinstance(data, set):
        return {redact_data(item) for item in data}

    if isinstance(data, str):
        return redact_string(data)

    return data
