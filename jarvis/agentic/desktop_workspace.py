"""Cible Desktop bornée pour un run agentique demandé sur le Bureau macOS."""

from __future__ import annotations

import re
from pathlib import Path


_DESKTOP_DEST_RE = re.compile(
    r"\b(?:sur|dans)\s+(?:le|mon)\s+bureau\b|"
    r"\bbureau de mon mac\b|"
    r"\b(?:on the |sur le )?desktop\b",
    re.IGNORECASE,
)
_FOLDER_NAME_RE = re.compile(
    r"(?:appel[eé]e?|nomm[eé]e?|dossier)\s+([A-Za-z][A-Za-z0-9_-]{0,62})",
    re.IGNORECASE,
)
_SAFE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,62}$")


def resolve_desktop_workspace(
    text: str,
    *,
    home: Path | None = None,
    create: bool = True,
) -> Path | None:
    """Retourne ``~/Desktop/<nom>`` si la demande cible le Bureau avec un nom sûr.

    Aucun chemin n'est créé hors de ce dossier. Un nom avec ``..``, un slash
    ou un Desktop absent/symbolique est refusé.
    """

    if not _DESKTOP_DEST_RE.search(text or ""):
        return None
    root = (home or Path.home()).expanduser()
    desktop = root / "Desktop"
    if desktop.is_symlink() or not desktop.is_dir():
        return None
    match = _FOLDER_NAME_RE.search(text)
    if match is None:
        return None
    name = match.group(1)
    if not _SAFE_NAME_RE.fullmatch(name):
        return None
    try:
        desktop_resolved = desktop.resolve(strict=True)
    except OSError:
        return None
    candidate = desktop / name
    if candidate.is_symlink():
        return None
    if create:
        try:
            candidate.mkdir(mode=0o700, exist_ok=True)
        except OSError:
            return None
    if candidate.is_symlink() or not candidate.is_dir():
        return None
    try:
        target = candidate.resolve(strict=True)
        target.relative_to(desktop_resolved)
    except (OSError, ValueError):
        return None
    if target == desktop_resolved:
        return None
    return target
