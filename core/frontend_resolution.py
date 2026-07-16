"""Résolution desktop frontend — indépendante de FastAPI / supervisor.

Priorité :
1. ``frontend/out`` (Next.js export) si build exploitable
2. ``web/dist`` (Vite) en fallback
3. ``missing`` sinon

Aucun I/O réseau, aucune dépendance FastAPI/React.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

FrontendKind = Literal["next_canonical", "vite_fallback", "missing"]

CANONICAL_REL = "frontend/out"
FALLBACK_REL = "web/dist"

# Segments SPA / pages exportées (aligné sur api/frontend.py)
DESKTOP_SPA_SEGMENTS: frozenset[str] = frozenset({
    "chat", "voice", "tasks", "documents", "memory", "status",
    "dashboard", "contacts", "map", "analytics", "search", "data",
    "conversations", "calendar", "logs", "monitoring",
    "voice-debug", "control", "mission", "mobile",
    "mails", "config",
})

_ASSET_EXTENSIONS: frozenset[str] = frozenset({
    ".js", ".mjs", ".css", ".map", ".json", ".webmanifest",
    ".ico", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    ".woff", ".woff2", ".ttf", ".eot", ".txt", ".xml",
    ".mp3", ".wav", ".ogg", ".webm", ".mp4",
})


@dataclass(frozen=True)
class FrontendResolution:
    """Décision de sélection d'un build desktop."""

    kind: FrontendKind
    root: Path | None
    relative_path: str | None
    reason: str
    checked: tuple[str, ...]
    canonical_available: bool
    fallback_available: bool

    def to_public_dict(self) -> dict[str, object]:
        """Dictionnaire sûr pour un endpoint de diagnostic (chemins relatifs)."""
        return {
            "selected": self.kind,
            "path": self.relative_path,
            "canonical_available": self.canonical_available,
            "fallback_available": self.fallback_available,
            "reason": self.reason,
            "checked": list(self.checked),
        }


def is_usable_next_build(path: Path) -> bool:
    """Build Next ``output: 'export'`` exploitable : index + ``_next/static``."""
    return (path / "index.html").is_file() and (path / "_next" / "static").is_dir()


def is_usable_vite_build(path: Path) -> bool:
    """Build Vite exploitable : au minimum ``index.html``."""
    return (path / "index.html").is_file()


def resolve_desktop_frontend(
    repo_root: Path,
    *,
    canonical_rel: str = CANONICAL_REL,
    fallback_rel: str = FALLBACK_REL,
) -> FrontendResolution:
    """Sélectionne le frontend desktop sans dépendre de ``os.getcwd()``."""
    root = Path(repo_root).resolve()
    canonical = (root / canonical_rel).resolve()
    fallback = (root / fallback_rel).resolve()
    return resolve_desktop_frontend_roots(
        canonical,
        fallback,
        canonical_label=canonical_rel,
        fallback_label=fallback_rel,
        checked=(canonical_rel, fallback_rel),
    )


def resolve_desktop_frontend_roots(
    canonical: Path,
    fallback: Path,
    *,
    canonical_label: str = CANONICAL_REL,
    fallback_label: str = FALLBACK_REL,
    checked: tuple[str, ...] | None = None,
) -> FrontendResolution:
    """Même priorité que :func:`resolve_desktop_frontend` sur chemins absolus."""
    canonical = Path(canonical).resolve()
    fallback = Path(fallback).resolve()
    checked_paths = checked or (canonical_label, fallback_label)

    canonical_ok = is_usable_next_build(canonical)
    fallback_ok = is_usable_vite_build(fallback)

    if canonical_ok:
        return FrontendResolution(
            kind="next_canonical",
            root=canonical,
            relative_path=canonical_label,
            reason="frontend/out is a usable Next.js static export",
            checked=checked_paths,
            canonical_available=True,
            fallback_available=fallback_ok,
        )

    if fallback_ok:
        reason = (
            "frontend/out is missing or incomplete; using legacy Vite fallback"
            if not canonical.exists()
            else "frontend/out exists but is incomplete (missing index.html or _next/static); "
            "using legacy Vite fallback"
        )
        return FrontendResolution(
            kind="vite_fallback",
            root=fallback,
            relative_path=fallback_label,
            reason=reason,
            checked=checked_paths,
            canonical_available=False,
            fallback_available=True,
        )

    return FrontendResolution(
        kind="missing",
        root=None,
        relative_path=None,
        reason="no usable desktop frontend build found",
        checked=checked_paths,
        canonical_available=False,
        fallback_available=False,
    )


def _looks_like_static_asset(request_path: str) -> bool:
    name = Path(request_path).name
    if not name or name in {".", ".."}:
        return False
    suffix = Path(name).suffix.lower()
    return bool(suffix) and suffix in _ASSET_EXTENSIONS


def lookup_desktop_static_file(
    resolution: FrontendResolution,
    request_path: str,
) -> Path | None:
    """Résout un fichier à servir, ou ``None`` si 404.

    - Next : fichiers exportés, ``segment/index.html``, SPA whitelist → index.
    - Vite : fichier exact ou fallback SPA ``index.html``.
    - Assets absents (extension connue) → toujours ``None`` (pas de HTML silencieux).
    """
    if resolution.kind == "missing" or resolution.root is None:
        return None

    root = resolution.root.resolve()
    raw = (request_path or "").lstrip("/")

    if not raw:
        index = root / "index.html"
        return index if index.is_file() else None

    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None

    if candidate.is_file():
        return candidate

    if candidate.is_dir():
        nested = candidate / "index.html"
        if nested.is_file():
            return nested

    stripped = raw.rstrip("/")
    dir_index = root / stripped / "index.html"
    if dir_index.is_file() and "/" not in stripped:
        return dir_index
    if dir_index.is_file() and stripped.count("/") == 0:
        return dir_index

    # /chat/ → chat/index.html
    if raw.endswith("/"):
        trailing = root / raw / "index.html"
        if trailing.is_file():
            return trailing

    html_twin = root / f"{stripped}.html"
    if html_twin.is_file():
        return html_twin

    if _looks_like_static_asset(raw):
        return None

    segment = stripped.split("/", 1)[0]

    if resolution.kind == "next_canonical":
        segment_index = root / segment / "index.html"
        if stripped == segment and segment_index.is_file():
            return segment_index
        if segment in DESKTOP_SPA_SEGMENTS:
            # Sous-route client (/chat/foo) → shell racine
            shell = root / "index.html"
            return shell if shell.is_file() else None
        return None

    # Vite SPA fallback (hors assets)
    index = root / "index.html"
    return index if index.is_file() else None


def log_lines_for_resolution(resolution: FrontendResolution) -> list[str]:
    """Messages de démarrage (une décision, pas par requête)."""
    if resolution.kind == "next_canonical":
        return [
            f"Desktop frontend: Next.js canonical build ({resolution.relative_path})",
        ]
    if resolution.kind == "vite_fallback":
        return [
            f"Desktop frontend: Vite fallback ({resolution.relative_path})",
            f"Reason: {resolution.reason}",
            f"Canonical frontend missing or incomplete: {CANONICAL_REL}",
            f"Using legacy Vite fallback: {FALLBACK_REL}",
        ]
    return [
        "Desktop frontend unavailable",
        f"Checked: {', '.join(resolution.checked)}",
    ]
