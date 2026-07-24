"""Réduction et rédaction des journaux d'actions avant persistance.

Les logs servent au diagnostic, pas à reconstruire les données manipulées.
Cette frontière applique donc une politique restrictive avant tout INSERT :

- les actions/résultats et champs de contenu sont remplacés par un marqueur ;
- les secrets, jetons, PII et chemins locaux sont masqués dans les chaînes ;
- la profondeur, le nombre d'éléments et la taille finale sont bornés ;
- aucun ``repr`` d'objet inconnu n'est utilisé (il pourrait contenir un secret).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

import config
from jarvis.pii import PIIAnonymizer

REDACTED = "[REDACTED]"
TRUNCATED = "[TRUNCATED]"
CLIPBOARD_REDACTED = "[CLIPBOARD_CONTENT_REDACTED]"

_MAX_DEPTH = 4
_MAX_COLLECTION_ITEMS = 25
_MAX_STRING_CHARS = 320
_MAX_LABEL_CHARS = 64

_SENSITIVE_KEYS = {
    "accesstoken",
    "action",
    "apikey",
    "auth",
    "authorization",
    "authtoken",
    "body",
    "clientsecret",
    "clipboard",
    "clipboardcontent",
    "content",
    "cookie",
    "credentials",
    "description",
    "emailbody",
    "error",
    "filename",
    "filepath",
    "html",
    "idtoken",
    "message",
    "messages",
    "password",
    "passwd",
    "passphrase",
    "privatekey",
    "prompt",
    "query",
    "raw",
    "refreshtoken",
    "result",
    "response",
    "secret",
    "sessioncookie",
    "sessionid",
    "setcookie",
    "systemprompt",
    "title",
    "token",
}

_TERMINAL_KEYS = {
    "args",
    "argv",
    "cmd",
    "command",
    "error",
    "output",
    "script",
    "stderr",
    "stdin",
    "stdout",
}

_MAIL_KEYS = {
    "attachment",
    "attachments",
    "bcc",
    "cc",
    "email",
    "emails",
    "from",
    "recipient",
    "recipients",
    "subject",
    "to",
}

_SECRET_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\b(?:sk|gh[pousr])[-_][A-Za-z0-9_-]{12,}\b", re.IGNORECASE),
    re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|"
        r"password|passwd|authorization)\b(\s*[=:]\s*)([^\s,;\"']+)"
    ),
)

_LOCAL_PATH_PATTERNS = (
    re.compile(
        r"(?<!\w)/(?:Users|home|private|tmp|var|opt|Volumes|Applications)"
        r"(?:/[^\s\"']*)?"
    ),
    re.compile(r"(?i)\b[A-Z]:\\(?:Users\\)?[^\\\s\"']+(?:\\[^\s\"']*)?"),
)

_LABEL_RE = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_PII_ANONYMIZER = PIIAnonymizer()


def sanitize_log_label(value: Any, *, fallback: str = "unknown") -> str:
    """Retourne une étiquette courte ne pouvant pas transporter de contenu."""
    if not isinstance(value, str):
        return fallback
    candidate = value.strip()[:_MAX_LABEL_CHARS]
    return candidate if _LABEL_RE.fullmatch(candidate) else fallback


def _normalize_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _redact_string(value: str) -> str:
    text = value
    for pattern in _SECRET_PATTERNS:
        if pattern.groups == 3:
            text = pattern.sub(lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", text)
        else:
            text = pattern.sub(REDACTED, text)
    for pattern in _LOCAL_PATH_PATTERNS:
        text = pattern.sub("[LOCAL_PATH]", text)
    if len(text) > _MAX_STRING_CHARS:
        text = text[:_MAX_STRING_CHARS] + TRUNCATED
    return text


def _is_sensitive_key(key: Any, action_type: str) -> bool:
    normalized = _normalize_key(key)
    if normalized in _SENSITIVE_KEYS:
        return True
    if action_type == "terminal" and normalized in _TERMINAL_KEYS:
        return True
    if action_type.startswith("mail") and normalized in _MAIL_KEYS:
        return True
    return False


def _sanitize_value(value: Any, *, action_type: str, depth: int = 0) -> Any:
    if depth >= _MAX_DEPTH:
        return TRUNCATED
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if depth == 0:
            return REDACTED
        return _redact_string(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return REDACTED
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        items = list(value.items())
        for key, item in items[:_MAX_COLLECTION_ITEMS]:
            safe_key = str(key)[:_MAX_LABEL_CHARS]
            if _is_sensitive_key(key, action_type):
                sanitized[safe_key] = REDACTED
            else:
                sanitized[safe_key] = _sanitize_value(
                    item,
                    action_type=action_type,
                    depth=depth + 1,
                )
        if len(items) > _MAX_COLLECTION_ITEMS:
            sanitized["_truncated_items"] = len(items) - _MAX_COLLECTION_ITEMS
        return sanitized
    if isinstance(value, Sequence):
        items = list(value)
        sanitized_items = [
            _sanitize_value(item, action_type=action_type, depth=depth + 1)
            for item in items[:_MAX_COLLECTION_ITEMS]
        ]
        if len(items) > _MAX_COLLECTION_ITEMS:
            sanitized_items.append(TRUNCATED)
        return sanitized_items
    return f"[{type(value).__name__}_REDACTED]"


def _mask_pii(serialized: str) -> str:
    result = _PII_ANONYMIZER.anonymize(serialized)
    try:
        return result.anonymized_text
    finally:
        # Le mapping contient les valeurs originales et ne doit pas survivre.
        result.mapping.clear()


def _bounded_json(value: Any, max_chars: int) -> str:
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(serialized) <= max_chars:
        return serialized

    prefix = serialized[: max(0, max_chars - 80)]
    wrapped = json.dumps(
        {"truncated": True, "preview": prefix},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    while len(wrapped) > max_chars and prefix:
        prefix = prefix[:-1]
        wrapped = json.dumps(
            {"truncated": True, "preview": prefix},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    return wrapped[:max_chars]


def redact_action_log_payload(payload: Any, action_type: str) -> str:
    """Produit le JSON sûr qui peut être écrit dans ``llm_action_logs``."""
    safe_action_type = sanitize_log_label(action_type)
    if safe_action_type == "clipboard":
        structured: Any = {"redacted": CLIPBOARD_REDACTED}
    else:
        structured = _sanitize_value(payload, action_type=safe_action_type)

    serialized = json.dumps(structured, ensure_ascii=False, separators=(",", ":"))
    serialized = _mask_pii(serialized)
    try:
        pii_safe_value = json.loads(serialized)
    except json.JSONDecodeError:
        pii_safe_value = {"redacted": REDACTED}

    max_chars = max(256, min(int(config.ACTION_LOG_MAX_PAYLOAD_CHARS), 16_384))
    return _bounded_json(pii_safe_value, max_chars)
