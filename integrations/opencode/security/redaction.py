"""Redaction structurée avant journalisation ou persistance."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any, Iterable


REDACTED = "[REDACTED]"
_SENSITIVE_KEY = re.compile(
    r"(?:authorization|proxy-authorization|password|passwd|secret|token|api[_-]?key|cookie|credential|private[_-]?key)",
    re.IGNORECASE,
)
_PATTERNS = (
    re.compile(r"(?i)\b(Basic|Bearer)\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)([?&](?:token|key|secret|password)=)[^&#\s]+"),
    re.compile(r"\b(?:sk|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{12,}\b"),
)
_INLINE_SECRET = re.compile(
    r"(?i)\b((?:api[_-]?key|token|secret|password|credential)\s*[:=]\s*)[^\s,;\"']+"
)


def redact_text(value: str, secrets: Iterable[str] = ()) -> str:
    redacted = value
    for secret in sorted({item for item in secrets if item}, key=len, reverse=True):
        redacted = redacted.replace(secret, REDACTED)
    redacted = _PATTERNS[0].sub(lambda match: f"{match.group(1)} {REDACTED}", redacted)
    redacted = _PATTERNS[1].sub(lambda match: f"{match.group(1)}{REDACTED}", redacted)
    redacted = _PATTERNS[2].sub(REDACTED, redacted)
    redacted = _INLINE_SECRET.sub(lambda match: f"{match.group(1)}{REDACTED}", redacted)
    return redacted


def redact_mapping(value: Any, secrets: Iterable[str] = ()) -> Any:
    """Copie récursivement une structure en neutralisant les champs sensibles."""

    secret_values = tuple(item for item in secrets if item)
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED
            if _SENSITIVE_KEY.search(str(key))
            else redact_mapping(item, secret_values)
            for key, item in value.items()
        }
    if isinstance(value, str):
        return redact_text(value, secret_values)
    if isinstance(value, bytes):
        return REDACTED
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_mapping(item, secret_values) for item in value]
    return value
