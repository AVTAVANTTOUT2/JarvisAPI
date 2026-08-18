"""Redaction irréversible aux frontières agentiques persistées et diffusées."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import PurePath
import re
from typing import Any


REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "credentials",
        "objective",
        "password",
        "private_key",
        "prompt",
        "raw",
        "raw_arguments",
        "raw_result",
        "result",
        "secret",
        "token",
        "tool_arguments",
        "tool_result",
    }
)
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\b(?:sk|ghp|github_pat)-?[A-Za-z0-9_]{12,}\b"),
    re.compile(r"(?i)(password|secret|token|api[_-]?key)\s*[:=]\s*\S+"),
)


def redact_text(value: Any, *, max_chars: int = 512) -> str:
    text = "" if value is None else str(value)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    text = " ".join(text.split())
    if len(text) > max_chars:
        text = text[: max(0, max_chars - 12)] + "…[tronqué]"
    return text


def redact_value(value: Any, *, key: str | None = None, depth: int = 0) -> Any:
    if key and key.lower() in _SENSITIVE_KEYS:
        return REDACTED
    if depth >= 6:
        return "[MAX_DEPTH]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, PurePath):
        return value.name
    if isinstance(value, str):
        return redact_text(value, max_chars=2048)
    if isinstance(value, Mapping):
        return {
            str(child_key): redact_value(
                child_value, key=str(child_key), depth=depth + 1
            )
            for child_key, child_value in list(value.items())[:100]
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_value(item, depth=depth + 1) for item in list(value)[:100]]
    return redact_text(value, max_chars=512)


def redact_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(redact_value(value or {}))


def redact_selected_context(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Préserve le retrieval serveur borné sans relâcher la redaction."""

    source = value or {}
    safe = redact_mapping(source)
    retrieval = source.get("retrieval_context")
    if isinstance(retrieval, str) and "[UNTRUSTED_DATA:" in retrieval:
        for pattern in _SECRET_PATTERNS:
            retrieval = pattern.sub(REDACTED, retrieval)
        safe["retrieval_context"] = retrieval[:8_000]
    return safe


SAFE_EVENT_FIELDS: frozenset[str] = frozenset(
    {
        "action",
        "approval_id",
        "artifact_id",
        "channel",
        "decision",
        "error_code",
        "expires_at",
        "needs_attention",
        "phase",
        "progress",
        "run_id",
        "risks",
        "sanitized_arguments",
        "sequence",
        "spoken_summary",
        "status",
        "title",
        "tool",
        "cancellation_kind",
        "admission_reason",
        "worktree_id",
        # Nom fermé de la limite franchie (`event_budget_exceeded`,
        # `max_duration`, `cost_budget`…). Sans lui, `budget_exceeded` arrive
        # nu : le run échoue sans que rien ne dise quelle borne a cédé, et le
        # champ posé par le service était silencieusement supprimé ici.
        "violation",
        # Détail exploitable d'une boucle d'outils : combien de fois l'appel
        # identique s'est répété, et quoi faire ensuite. `tool` est déjà
        # au-dessus et déjà redacté. Les arguments, eux, n'entrent jamais.
        "repetitions",
        "next_action",
        # Acquittement du `/abort` envoyé au runtime : sans lui, rien ne
        # distingue un arrêt confirmé d'un arrêt supposé.
        "abort_acknowledged",
    }
)


def neutralize_event_payload(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Produit l'allowlist commune UI/voix/WS sans données provider brutes."""

    source = value or {}
    safe: dict[str, Any] = {}
    for key in SAFE_EVENT_FIELDS:
        if key not in source:
            continue
        item = source[key]
        if key in {"title", "spoken_summary", "action", "tool"}:
            safe[key] = redact_text(item, max_chars=240)
        elif key == "progress":
            try:
                safe[key] = max(0.0, min(float(item), 1.0))
            except (TypeError, ValueError):
                continue
        elif key in {"needs_attention", "abort_acknowledged"}:
            safe[key] = bool(item)
        elif key == "sanitized_arguments" and isinstance(item, Mapping):
            safe[key] = redact_mapping(item)
        elif (
            key == "risks"
            and isinstance(item, Sequence)
            and not isinstance(item, (str, bytes, bytearray))
        ):
            safe[key] = [redact_text(risk, max_chars=240) for risk in list(item)[:20]]
        elif key in {"sequence", "repetitions"}:
            try:
                safe[key] = max(0, int(item))
            except (TypeError, ValueError):
                continue
        else:
            safe[key] = redact_text(item, max_chars=120)
    return safe


__all__ = [
    "REDACTED",
    "SAFE_EVENT_FIELDS",
    "neutralize_event_payload",
    "redact_mapping",
    "redact_selected_context",
    "redact_text",
    "redact_value",
]
