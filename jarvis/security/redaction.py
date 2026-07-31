"""Redaction centralisée des secrets — logs, DB, API, WebSocket, notifications."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from jarvis.pii import PIIAnonymizer

_REDACTED = "***REDACTED***"
_PII_ANONYMIZER = PIIAnonymizer()

_TOKEN_COUNTER_KEYS = frozenset(
    {
        "cachehittokens",
        "heavytaskmaxtokens",
        "maxtokens",
        "tokenbudget",
        "tokensin",
        "tokensout",
        "tokenstotal",
        "tokensused",
    }
)

# Métadonnées nécessaires à la reprise ou à l'exécution : leurs secrets sont
# masqués, mais elles ne passent pas au NER PII afin de ne pas corrompre un
# chemin, un enum ou un budget numérique.
_PERSISTENCE_METADATA_KEYS = frozenset(
    {
        "backend",
        "branchname",
        "clireturncode",
        "commitsha",
        "isolationpath",
        "iteration",
        "loopbudget",
        "maxconsecutivefailures",
        "maxiterations",
        "maxtokens",
        "phase",
        "projecttype",
        "prurl",
        "risklevel",
        "slug",
        "stack",
        "status",
        "testok",
        "tokensused",
        "verdict",
    }
)

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


def redact_persisted_text(text: str | None) -> str:
    """Masque secrets et PII avant une persistance durable.

    Contrairement à l'anonymisation des appels LLM, le mapping réversible est
    détruit immédiatement : une valeur persistée ne peut donc pas être
    dé-anonymisée ultérieurement.
    """
    secret_safe = redact_sensitive_text(text).replace(_REDACTED, "[REDACTED]")
    result = _PII_ANONYMIZER.anonymize(secret_safe)
    try:
        return result.anonymized_text
    finally:
        result.mapping.clear()


def redact_persisted_mapping(data: Mapping[str, Any] | list[Any] | Any) -> Any:
    """Redaction récursive secrets + PII pour les frontières de stockage."""
    sensitive_tokens = (
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

    def _redact_value(value: Any, *, pii: bool = True) -> Any:
        if isinstance(value, str):
            if pii:
                return redact_persisted_text(value)
            return redact_sensitive_text(value).replace(_REDACTED, "[REDACTED]")
        if isinstance(value, Mapping):
            redacted: dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                normalized_key = re.sub(r"[^a-z0-9]", "", key_text.lower())
                secret_key = (
                    normalized_key not in _TOKEN_COUNTER_KEYS
                    and any(token in key_text.lower() for token in sensitive_tokens)
                    and not isinstance(item, (bool, int, float))
                )
                if secret_key:
                    redacted[key_text] = "[REDACTED]"
                    continue
                child_pii = pii and normalized_key not in _PERSISTENCE_METADATA_KEYS
                redacted[key_text] = _redact_value(item, pii=child_pii)
            return redacted
        if isinstance(value, list):
            return [_redact_value(item, pii=pii) for item in value]
        if isinstance(value, tuple):
            return tuple(_redact_value(item, pii=pii) for item in value)
        return value

    return _redact_value(data)


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
    from jarvis.security.llm_data_boundary import redact_for_external_llm

    view: dict[str, Any] = {}
    for key in _PUBLIC_JOB_KEYS:
        if key in job:
            view[key] = job[key]
    # Résumé / erreurs nettoyés
    err = job.get("error_message")
    if err:
        view["error_message"] = redact_for_external_llm(err, max_chars=500)
    structured = job.get("structured_result")
    if isinstance(structured, Mapping):
        summary_bits = {
            "verdict": structured.get("verdict"),
            "test_ok": structured.get("test_ok"),
            "cli_returncode": structured.get("cli_returncode"),
        }
        if structured.get("body"):
            summary_bits["summary"] = redact_for_external_llm(
                structured.get("body"),
                max_chars=800,
            )
        view["summary"] = redact_persisted_mapping(summary_bits)
    # Durée approximative
    if job.get("started_at") and job.get("finished_at"):
        view["duration_hint"] = f"{job.get('started_at')} → {job.get('finished_at')}"
    return view


def diagnostic_cursor_job_view(job: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Vue diagnostic : plus de détail, toujours redacted — jamais d'env brut."""
    if not job:
        return None
    redacted = redact_persisted_mapping(dict(job))
    # Ne jamais exposer un éventuel environnement injecté
    redacted.pop("environment", None)
    redacted.pop("env", None)
    # Truncate gros champs
    for key in ("raw_output", "prompt_sent", "user_request"):
        if key in redacted and isinstance(redacted[key], str):
            redacted[key] = redacted[key][:20_000]
    return redacted
