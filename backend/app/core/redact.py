"""Credential-safe redaction helpers used across logging, errors, and exports.

Provider/broker secrets must never reach the browser, logs, WebSocket events, or
exported files. This module centralises the rules:

- :func:`redact_text` replaces known secret values and common secret *key names*
  in arbitrary strings (e.g. exception messages from vendor SDKs).
- :func:`redact_dict` recursively scrubs a mapping, dropping secret values and
  renaming un-secure copies so a caller cannot accidentally persist them.
- :func:`sanitize_error` turns a raw exception into a short, safe, single-line
  message with no stack trace.

The redaction is best-effort: callers MUST also avoid placing secrets in
persisted/returned structures at the source (see ``exness_provider_service``).
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "***redacted***"

# Key names (case-insensitive, substring match on the *key*) whose values are
# always treated as secrets and never surfaced.
SECRET_KEY_SUBSTRINGS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "api-key",
    "credential",
    "encrypted",
    "private_key",
    "auth",
    "pairing_code",
    "login",
    "account_number",
    "access_token",
)

# Patterns for credential-looking values inside free text (e.g. login numbers,
# JWT-like blobs). Conservative so it cannot mangle normal prose.
_TOKEN_PATTERNS = (
    re.compile(r"\beyJ[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\b"),
    re.compile(r"\b\d{6,12}\b"),  # MT5 login numbers are ~6-9 digits; avoid short years
)


def redact_text(text: str, secrets: tuple[str, ...] = ()) -> str:
    """Return ``text`` with known secrets and token-like values replaced."""
    if not text:
        return text
    out = text
    for secret in secrets:
        if secret:
            out = out.replace(str(secret), REDACTED)
    for pattern in _TOKEN_PATTERNS:
        out = pattern.sub(REDACTED, out)
    return out


def _is_secret_key(key: str) -> bool:
    k = key.lower().replace("_", "-")
    return any(part in k for part in SECRET_KEY_SUBSTRINGS)


def redact_dict(
    data: Any,
    secrets: tuple[str, ...] = (),
    secret_keys: tuple[str, ...] = (),
) -> Any:
    """Recursively redact a nested dict/list of primitives.

    Values under a secret-looking key are replaced with ``***redacted***`` and
    values whose plaintext matches a known secret are replaced too.
    """
    explicit = tuple(s.lower() for s in secret_keys)
    if isinstance(data, dict):
        return {
            str(k): (
                REDACTED
                if _is_secret_key(str(k)) or str(k).lower() in explicit
                else redact_dict(v, secrets, secret_keys)
            )
            for k, v in data.items()
        }
    if isinstance(data, (list, tuple)):
        return [redact_dict(v, secrets, secret_keys) for v in data]
    if isinstance(data, str):
        return redact_text(data, secrets)
    return data


def sanitize_error(exc: Exception, secrets: tuple[str, ...] = ()) -> str:
    """Return a short redacted single-line message for an exception."""
    msg = redact_text(str(exc) or exc.__class__.__name__, secrets)
    msg = " ".join(msg.split())[:400]
    return msg or exc.__class__.__name__