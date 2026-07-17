"""Environnement minimal pour le processus Cursor CLI — jamais os.environ entier."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Mapping

# Noms (sous-chaînes) interdits sauf allowlist explicite.
_SENSITIVE_NAME_RE = re.compile(
    r"(?i)(KEY|TOKEN|SECRET|PASSWORD|PASS|COOKIE|AUTH|CREDENTIAL|PRIVATE|CERT)"
)

# Variables explicitement autorisées même si le nom matche le filtre
# (aucune clé secrète ici — uniquement des réglages OS/git).
_ENV_ALLOWLIST: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TERM",
        "TMPDIR",
        "TMP",
        "TEMP",
        "USER",
        "LOGNAME",
        "SHELL",
        "GIT_TERMINAL_PROMPT",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_GLOBAL",
        "NO_OPEN_BROWSER",
        "CI",
        "TZ",
    }
)


def build_cursor_safe_env(
    *,
    isolated_home: Path | None = None,
    extra: Mapping[str, str] | None = None,
    parent_environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Construit un environnement minimal pour Cursor.

    Ne recopie jamais ``os.environ`` en bloc. Les variables dont le nom
    contient KEY/TOKEN/SECRET/… sont exclues sauf allowlist documentée.
    """
    parent = parent_environ if parent_environ is not None else os.environ
    home = isolated_home or Path(tempfile.mkdtemp(prefix="jarvis-cursor-home-"))
    home.mkdir(parents=True, exist_ok=True)

    path = parent.get("PATH", "/usr/bin:/bin:/usr/local/bin")
    # PATH épuré : on garde le PATH système (nécessaire pour git/gh/node)
    # mais on ne propage aucune autre variable sensible.

    safe: dict[str, str] = {
        "PATH": path,
        "HOME": str(home),
        "LANG": parent.get("LANG") or "fr_FR.UTF-8",
        "LC_ALL": parent.get("LC_ALL") or parent.get("LANG") or "fr_FR.UTF-8",
        "TERM": "dumb",
        "GIT_TERMINAL_PROMPT": "0",
        "NO_OPEN_BROWSER": "1",
        "TMPDIR": parent.get("TMPDIR") or str(home / "tmp"),
    }
    Path(safe["TMPDIR"]).mkdir(parents=True, exist_ok=True)

    # Optionnel : USER/LOGNAME non sensibles
    for key in ("USER", "LOGNAME", "TZ", "CI"):
        val = parent.get(key)
        if val and key in _ENV_ALLOWLIST and not _SENSITIVE_NAME_RE.search(key):
            safe[key] = val

    if extra:
        for key, value in extra.items():
            if key not in _ENV_ALLOWLIST and _SENSITIVE_NAME_RE.search(key):
                continue
            safe[str(key)] = str(value)

    # Filet de sécurité final
    return {
        k: v
        for k, v in safe.items()
        if k in _ENV_ALLOWLIST or not _SENSITIVE_NAME_RE.search(k)
    }


def env_contains_sentinel(env: Mapping[str, str], sentinel_name: str) -> bool:
    """True si la variable sentinelle est présente (tests de non-fuite)."""
    return sentinel_name in env
