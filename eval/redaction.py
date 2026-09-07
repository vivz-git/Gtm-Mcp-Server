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
#: A CRM record identifier is a UUID, and a digit-heavy one looks enough like a
#: phone number for the pattern above to eat pieces of it — which destroys the
#: one value a reader needs to follow a trace. Matched first and passed through.
_UUID_PATTERN = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_UUID_OR_PHONE_PATTERN = re.compile(
    f"(?P<uuid>{_UUID_PATTERN.pattern})|(?P<phone>{_PHONE_PATTERN.pattern})"
)
_AUTH_HEADER_PATTERN = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9_\-\.]{8,}\b")
#: A DSN carries the database password in its userinfo section. A live agent's
#: prose is free text, so the credential is matched by shape, not by key name.
_DSN_PATTERN = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^\s/@:]+:[^\s/@]+@")
#: Assignment of a known-sensitive name in free text, e.g. ``api_key=sk-...``.
_ASSIGNED_SECRET_PATTERN = re.compile(
    r"(?i)\b((?:api[_-]?key|token|secret|password|credential)\s*[=:]\s*)\S+"
)


def redact_string(value: str) -> str:
    """Sanitize sensitive patterns inside a string.

    Args:
        value: Input string.

    Returns:
        String with credentials, emails, and phone numbers masked.
    """
    masked = _AUTH_HEADER_PATTERN.sub(r"\1[REDACTED_TOKEN]", value)
    masked = mask_phone_numbers(masked)
    masked = _EMAIL_PATTERN.sub(r"\1***@\2", masked)
    return masked


def mask_phone_numbers(value: str) -> str:
    """Replace phone numbers, leaving record identifiers intact.

    Args:
        value: Text that may contain a phone number, a UUID, or both.

    Returns:
        The text with phone numbers masked and UUIDs untouched.
    """

    def _replace(match: re.Match[str]) -> str:
        uuid = match.group("uuid")
        return uuid if uuid is not None else "[REDACTED_PHONE]"

    return _UUID_OR_PHONE_PATTERN.sub(_replace, value)


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


def redact_credentials(value: str) -> str:
    """Mask credentials in free text, leaving everything else intact.

    Used on a live agent's final response, which the scoring engine matches
    golden phrases against: masking the contact names and demo email addresses
    the scenario requires would change the score rather than protect anything,
    since that corpus is synthetic. A credential has no such excuse, so bearer
    tokens, DSN passwords and ``key=value`` secrets are removed even though
    nothing should ever have put one there.

    Args:
        value: Text written by an agent.

    Returns:
        The text with any credential-shaped substring replaced.
    """
    masked = _AUTH_HEADER_PATTERN.sub(r"\1[REDACTED_TOKEN]", value)
    masked = _DSN_PATTERN.sub(r"\1[REDACTED_CREDENTIALS]@", masked)
    return _ASSIGNED_SECRET_PATTERN.sub(r"\1[REDACTED_SECRET]", masked)


def redact_agent_response(value: str) -> str:
    """Sanitize an agent's prose for persistence in a report.

    Applied *after* scoring, never before: the golden expectations match names
    and email addresses in this text, so masking those would change the score
    rather than protect anything — the corpus is synthetic and the email is the
    join key the evaluation is checking for. A phone number has no such role, so
    it is masked here exactly as it already is inside tool traces.

    Args:
        value: The agent's final response.

    Returns:
        The response with credentials and phone numbers masked.
    """
    return mask_phone_numbers(redact_credentials(value))
