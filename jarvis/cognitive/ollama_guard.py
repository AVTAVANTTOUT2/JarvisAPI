"""Garde-fou : Ollama réservé au Screen Watcher (et contrôle process).

Toute requête HTTP vers l'API Ollama doit passer par ``ollama_http_request``
qui vérifie le module appelant contre une allowlist stricte.
"""

from __future__ import annotations

import inspect
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Modules autorisés à appeler l'API Ollama (chemins relatifs au dépôt, style posix).
OLLAMA_ALLOWED_MODULES: frozenset[str] = frozenset(
    {
        "scripts/screen_watcher.py",
        "integrations/ollama_control.py",
        "integrations/ollama_client.py",
        "jarvis/cognitive/ollama_guard.py",
    }
)

# Alias de noms de modules Python (import path) tolérés.
_ALLOWED_MODULE_NAMES: frozenset[str] = frozenset(
    {
        "scripts.screen_watcher",
        "integrations.ollama_control",
        "integrations.ollama_client",
        "jarvis.cognitive.ollama_guard",
    }
)


class OllamaPolicyError(RuntimeError):
    """Appel Ollama depuis un module non autorisé."""


def _normalize_path(path: str) -> str:
    p = path.replace("\\", "/")
    markers = ("/scripts/", "/integrations/", "/jarvis/", "/agents/", "/api/")
    for marker in markers:
        idx = p.find(marker)
        if idx >= 0:
            return p[idx + 1 :]  # drop leading slash from marker match → scripts/...
    return Path(p).name


def assert_ollama_caller_allowed(stack: list[inspect.FrameInfo] | None = None) -> str:
    """Vérifie que l'appelant est dans l'allowlist. Retourne le module autorisé."""
    frames = stack if stack is not None else inspect.stack()[1:]
    for frame in frames:
        mod = frame.frame.f_globals.get("__name__", "") or ""
        if mod in _ALLOWED_MODULE_NAMES:
            return mod
        filename = frame.filename or ""
        rel = _normalize_path(filename)
        if rel in OLLAMA_ALLOWED_MODULES:
            return rel
        # screen_watcher peut être importé comme module top-level selon le path
        if filename.endswith("screen_watcher.py"):
            return "scripts/screen_watcher.py"
        if filename.endswith("ollama_control.py"):
            return "integrations/ollama_control.py"
    callers = [
        f"{f.filename}:{f.lineno}:{f.function}" for f in frames[:8]
    ]
    raise OllamaPolicyError(
        "Appel Ollama interdit hors Screen Watcher / ollama_control. "
        f"Pile: {callers}"
    )


def ollama_reasoning_consumers(repo_root: Path | None = None) -> list[str]:
    """Scanne le dépôt et liste les fichiers qui appellent l'API Ollama.

    Retourne les chemins relatifs (posix) qui contiennent un appel HTTP
    vers Ollama hors allowlist — utilisé par les tests de contrat.
    """
    root = repo_root or Path(__file__).resolve().parents[2]
    offenders: list[str] = []
    patterns = (
        "/api/generate",
        "/api/chat",
        "OLLAMA_URL",
        "ollama_url",
        "11434",
    )
    skip_dirs = {
        ".git", "venv", "node_modules", ".worktrees", "frontend", "web", "pwa",
        "android", "data", "__pycache__", ".jarvis", "Architecture", "docs",
        "tv",
    }
    for path in root.rglob("*.py"):
        parts = set(path.parts)
        if parts & skip_dirs:
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if rel in OLLAMA_ALLOWED_MODULES:
            continue
        if rel.startswith("tests/") or rel.startswith("jarvis/tests/"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        # Heuristique : appel HTTP réel vers Ollama, pas une simple mention config
        has_http = any(
            x in text
            for x in (
                'f"{ollama_url}/api/',
                "f'{ollama_url}/api/",
                "/api/generate",
                "/api/chat",
                "client.post(",
                "httpx.",
                "requests.",
                "aiohttp",
            )
        )
        mentions_ollama = any(p in text for p in patterns) and (
            "ollama" in text.lower() or "11434" in text
        )
        if has_http and mentions_ollama and "/api/" in text:
            # Exclure les fichiers qui ne font que lire la config / health sans generate
            if "check_ollama_health" in text and "/api/generate" not in text and "/api/chat" not in text:
                continue
            if "ollama_control" in rel:
                continue
            offenders.append(rel)
    return sorted(set(offenders))


async def ollama_http_request(
    method: str,
    url: str,
    *,
    json: dict[str, Any] | None = None,
    timeout: float = 60.0,
) -> Any:
    """Unique porte d'entrée HTTP Ollama — enforce allowlist puis httpx."""
    caller = assert_ollama_caller_allowed()
    logger.debug("[ollama_guard] allow %s → %s %s", caller, method, url)
    import httpx

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.request(method.upper(), url, json=json)
        response.raise_for_status()
        return response.json()
