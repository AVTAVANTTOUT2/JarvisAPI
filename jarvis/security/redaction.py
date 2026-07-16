"""Redaction centralisée des secrets — logs, DB, API, WebSocket, notifications."""

from __future__ import annotations

import re
from typing import Any, Mapping

_REDACTED = "***REDACTED***"

# Patterns de secrets connus (ordre : plus spécifiques d'abord).
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?i)(-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
        r"[\s\S]*?"
        r"(-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
    ),
    re.compile(r"(?i)\b(Bearer)\s+[A-Za-z0-9._\-+=/]{8,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgho_[A-Za-z0-9]{20,}"),
    re.compile(r"\bghu_[A-Za-z0-9]{20,}"),
    re.compile(r"\bghs_[A-Za-z0-9]{20,}"),
    re.compile(r"\bghr_[A-Za-z0-9]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),
    re.compile(
        r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"
    ),  # JWT
    re.compile(
        r"(?i)\b((?:DEEPSEEK_|OPENAI_|ANTHROPIC_|FIREBASE_|CLOUDFLARE_|GITHUB_|AWS_)?"
        r"(?:API[_-]?KEY|SECRET|TOKEN|PASSWORD|PASS(?:PHRASE)?|COOKIE|AUTH|"
        r"CREDENTIAL|PRIVATE[_-]?KEY|CERT)(?:_?[A-Z0-9]*)?)\s*[=:]\s*\S+"
    ),
    re.compile(
        r"(?i)(https?://)([^/\s:@]+):([^/\s:@]+)@"
    ),  # credentials in URL
    re.compile(r"(?i)(Cookie:\s*)[^\n]+"),
)


def redact_sensitive_text(text: str | None) -> str:
    """Masque les secrets dans une chaîne. Idempotent."""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return text
    out = text
    # PEM blocks
    out = _SECRET_PATTERNS[0].sub(r"\1\n***REDACTED***\n\2", out)
    # Bearer
    out = _SECRET_PATTERNS[1].sub(rf"\1 {_REDACTED}", out)
    # GitHub pats / tokens
    for pat in _SECRET_PATTERNS[2:8]:
        out = pat.sub(_REDACTED, out)
    # sk-
    out = _SECRET_PATTERNS[8].sub(f"sk-{_REDACTED}", out)
    # JWT
    out = _SECRET_PATTERNS[9].sub(_REDACTED, out)
    # KEY=value style
    out = _SECRET_PATTERNS[10].sub(rf"\1={_REDACTED}", out)
    # URL user:pass@
    out = _SECRET_PATTERNS[11].sub(rf"\1{_REDACTED}:{_REDACTED}@", out)
    # Cookie header
    out = _SECRET_PATTERNS[12].sub(rf"\1{_REDACTED}", out)
    return out


def redact_sensitive_mapping(data: Mapping[str, Any] | list[Any] | Any) -> Any:
    """Redaction récursive sur dict / list / scalaires."""
    if data is None:
        return None
    if isinstance(data, str):
        return redact_sensitive_text(data)
    if isinstance(data, Mapping):
        out: dict[str, Any] = {}
        for key, value in data.items():
            key_l = str(key).lower()
            if any(
                tok in key_l
                for tok in (
                    "secret",
                    "token",
                    "password",
                    "passphrase",
                    "api_key",
                    "apikey",
                    "credential",
                    "private_key",
                    "cookie",
                    "authorization",
                )
            ):
                out[str(key)] = _REDACTED
            else:
                out[str(key)] = redact_sensitive_mapping(value)
        return out
    if isinstance(data, list):
        return [redact_sensitive_mapping(item) for item in data]
    if isinstance(data, tuple):
        return tuple(redact_sensitive_mapping(item) for item in data)
    return data


# Champs exposés dans la vue publique des jobs Cursor.
_PUBLIC_JOB_KEYS = frozenset(
    {
        "job_id",
        "title",
        "status",
        "branch_name",
        "prompt_template",
        "template_version",
        "risk_level",
        "commit_sha",
        "pr_url",
        "interaction_mode",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
        "allow_commit",
        "allow_push",
        "allow_pr",
        "allow_merge",
    }
)


def public_cursor_job_view(job: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Vue API normale : statut, branche, PR, résumé nettoyé — pas de brut."""
    if not job:
        return None
    view: dict[str, Any] = {}
    for key in _PUBLIC_JOB_KEYS:
        if key in job:
            view[key] = job[key]
    # Résumé / erreurs nettoyés
    err = job.get("error_message")
    if err:
        view["error_message"] = redact_sensitive_text(str(err))[:500]
    structured = job.get("structured_result")
    if isinstance(structured, Mapping):
        summary_bits = {
            "verdict": structured.get("verdict"),
            "test_ok": structured.get("test_ok"),
            "cli_returncode": structured.get("cli_returncode"),
        }
        if structured.get("body"):
            summary_bits["summary"] = redact_sensitive_text(
                str(structured.get("body"))
            )[:800]
        view["summary"] = redact_sensitive_mapping(summary_bits)
    # Durée approximative
    if job.get("started_at") and job.get("finished_at"):
        view["duration_hint"] = f"{job.get('started_at')} → {job.get('finished_at')}"
    return view


def diagnostic_cursor_job_view(job: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Vue diagnostic : plus de détail, toujours redacted — jamais d'env brut."""
    if not job:
        return None
    redacted = redact_sensitive_mapping(dict(job))
    # Ne jamais exposer un éventuel environnement injecté
    redacted.pop("environment", None)
    redacted.pop("env", None)
    # Truncate gros champs
    for key in ("raw_output", "prompt_sent", "user_request"):
        if key in redacted and isinstance(redacted[key], str):
            redacted[key] = redacted[key][:20_000]
    return redacted
