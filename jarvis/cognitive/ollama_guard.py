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

# Modules autorisés (chemins relatifs posix) — documentation / scan statique.
OLLAMA_ALLOWED_MODULES: frozenset[str] = frozenset(
    {
        "scripts/screen_watcher.py",
        "integrations/ollama_control.py",
    }
)

# Alias de noms de modules Python (import path) tolérés UNIQUEMENT si le
# fichier résolu correspond aussi à l'allowlist canonique ci-dessous.
_ALLOWED_MODULE_NAMES: frozenset[str] = frozenset(
    {
        "scripts.screen_watcher",
        "integrations.ollama_control",
    }
)

# Modules « plomberie » : ils relaient l'appel mais ne comptent pas comme
# consommateur. On les SAUTE dans la pile au lieu de les autoriser — sinon
# le premier frame (ce fichier) autoriserait n'importe quel appelant.
_PLUMBING_MODULE_NAMES: frozenset[str] = frozenset(
    {
        "jarvis.cognitive.ollama_guard",
        "integrations.ollama_client",
    }
)
_PLUMBING_REL_PATHS: frozenset[str] = frozenset(
    {
        "jarvis/cognitive/ollama_guard.py",
        "integrations/ollama_client.py",
    }
)


class OllamaPolicyError(RuntimeError):
    """Appel Ollama depuis un module non autorisé."""


_REPO_ROOT = Path(__file__).resolve().parents[2]


def _allowed_caller_paths() -> frozenset[Path]:
    """Chemins canoniques absolus des seuls appelants Ollama autorisés."""
    return frozenset(
        {
            (_REPO_ROOT / "scripts" / "screen_watcher.py").resolve(),
            (_REPO_ROOT / "integrations" / "ollama_control.py").resolve(),
        }
    )


def _plumbing_caller_paths() -> frozenset[Path]:
    return frozenset(
        {
            (_REPO_ROOT / "jarvis" / "cognitive" / "ollama_guard.py").resolve(),
            (_REPO_ROOT / "integrations" / "ollama_client.py").resolve(),
        }
    )


def _resolve_frame_path(filename: str) -> Path | None:
    if not filename:
        return None
    try:
        return Path(filename).resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _is_plumbing_frame(frame: inspect.FrameInfo) -> bool:
    mod = frame.frame.f_globals.get("__name__", "") or ""
    if mod in _PLUMBING_MODULE_NAMES:
        return True
    resolved = _resolve_frame_path(frame.filename or "")
    if resolved is None:
        return False
    return resolved in _plumbing_caller_paths()


def _is_repo_frame(filename: str) -> bool:
    """Frame appartenant au code applicatif du dépôt (hors venv/site-packages)."""
    f = (filename or "").replace("\\", "/")
    if not f:
        return False
    if "/site-packages/" in f or "/venv/" in f or "/lib/python" in f:
        return False
    resolved = _resolve_frame_path(f)
    if resolved is None:
        return False
    try:
        resolved.relative_to(_REPO_ROOT)
        return True
    except ValueError:
        return False


def assert_ollama_caller_allowed(stack: list[inspect.FrameInfo] | None = None) -> str:
    """Vérifie que le PREMIER appelant applicatif est dans l'allowlist canonique.

    Comparaison par ``Path.resolve()`` exacte — un fichier homonyme hors
    dépôt (ex. ``/tmp/malicious/screen_watcher.py``) est refusé.
    """
    allowed = _allowed_caller_paths()
    frames = stack if stack is not None else inspect.stack()[1:]
    for frame in frames:
        if _is_plumbing_frame(frame):
            continue
        mod = frame.frame.f_globals.get("__name__", "") or ""
        filename = frame.filename or ""
        resolved = _resolve_frame_path(filename)
        if resolved is not None and resolved in allowed:
            try:
                return resolved.relative_to(_REPO_ROOT).as_posix()
            except ValueError:
                return str(resolved)
        # Module name seul ne suffit PAS — le fichier doit aussi matcher.
        if mod in _ALLOWED_MODULE_NAMES and resolved is not None and resolved in allowed:
            return mod
        if _is_repo_frame(filename):
            # Premier frame applicatif du dépôt hors allowlist → refus.
            break
        # Frames techniques (asyncio, httpx, stdlib) : on continue de remonter.
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
    root = (repo_root or Path(__file__).resolve().parents[2]).resolve()
    offenders: list[str] = []
    # Seuls les endpoints de GÉNÉRATION comptent comme raisonnement local.
    # /api/tags, /api/pull, /api/ps = gestion de modèles (autorisée partout).
    generation_markers = ("/api/generate", "/api/chat\"", "/api/chat'")
    skip_dirs = {
        ".git", "venv", "node_modules", ".worktrees", "frontend", "web", "pwa",
        "android", "data", "__pycache__", ".jarvis", "Architecture", "docs",
        "tv", "native_audio", "artifacts",
    }
    for path in root.rglob("*.py"):
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        # Exclusions calculées sur le chemin RELATIF au dépôt — un dépôt qui
        # vit lui-même sous un dossier .worktrees ne doit pas tout exclure.
        rel_parts = set(Path(rel).parts)
        if rel_parts & skip_dirs:
            continue
        if rel in OLLAMA_ALLOWED_MODULES or rel in _PLUMBING_REL_PATHS:
            continue
        if rel.startswith("tests/") or rel.startswith("jarvis/tests/"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        mentions_ollama = "ollama" in text.lower() or "11434" in text
        if not mentions_ollama:
            continue
        has_generation = any(m in text for m in generation_markers) or (
            "ollama_generate(" in text
        )
        has_http = any(
            x in text
            for x in (
                'f"{ollama_url}/api/',
                "f'{ollama_url}/api/",
                "client.post(",
                "httpx.",
                "requests.",
                "aiohttp",
                "ollama_generate(",
            )
        )
        if has_generation and has_http:
            offenders.append(rel)
    return sorted(set(offenders))


def _assert_ollama_url_allowed(url: str) -> None:
    """Restreint host/scheme à config.OLLAMA_URL (pas de proxy arbitraire)."""
    from urllib.parse import urlparse

    import config

    allowed = urlparse(getattr(config, "OLLAMA_URL", "http://localhost:11434") or "")
    target = urlparse(url)
    if target.scheme not in ("http", "https"):
        raise OllamaPolicyError(f"schéma Ollama interdit: {target.scheme}")
    if (target.hostname or "").lower() not in {
        (allowed.hostname or "localhost").lower(),
        "127.0.0.1",
        "localhost",
    }:
        raise OllamaPolicyError(f"hôte Ollama hors allowlist: {target.hostname}")
    path = target.path or ""
    allowed_paths = (
        "/api/generate",
        "/api/tags",
        "/api/ps",
        "/api/pull",
        "/api/show",
        "/api/chat",
    )
    if not any(path == p or path.startswith(p + "/") for p in allowed_paths):
        raise OllamaPolicyError(f"endpoint Ollama hors allowlist: {path}")


async def ollama_http_request(
    method: str,
    url: str,
    *,
    json: dict[str, Any] | None = None,
    timeout: float = 60.0,
) -> Any:
    """Unique porte d'entrée HTTP Ollama — enforce allowlist puis httpx."""
    caller = assert_ollama_caller_allowed()
    _assert_ollama_url_allowed(url)
    logger.debug("[ollama_guard] allow %s → %s %s", caller, method, url)
    import httpx

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.request(method.upper(), url, json=json)
        response.raise_for_status()
        return response.json()
