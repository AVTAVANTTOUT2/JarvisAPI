"""Scanner de code dupliqué — hash glissant sur lignes normalisées.

Détecte les blocs de code quasi-identiques (clones de type 1/2 : espaces et
commentaires ignorés) entre fichiers Python, par fenêtre glissante hashée
avec extension gloutonne des blocs adjacents qui matchent aussi.

Deux usages :
- **Codebase JARVIS** : scan périodique, **rapport uniquement** (notification
  + table ``duplicate_findings``) — jamais de réécriture automatique du code
  de l'assistant en production (« fiabilité > breadth »).
- **Projets DevAgent** : `refactor_devagent_project()` va plus loin — le
  projet est isolé, versionné et couvert de tests, donc un refactor
  assisté par LLM peut être appliqué et validé automatiquement (tests verts
  → commit ; tests rouges → `git checkout` pour annuler).
"""

from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import config
from database import get_duplicate_findings, upsert_duplicate_finding
from jarvis.notification_service import notification_service

logger = logging.getLogger(__name__)

DEFAULT_EXCLUDE_DIRS = frozenset({
    "__pycache__", "node_modules", "venv", ".venv", ".git", "dist", "build",
    "dev_projects", "data", ".pytest_cache", "generated",
})


@dataclass(frozen=True)
class DuplicateBlock:
    file_a: str
    start_a: int
    end_a: int
    file_b: str
    start_b: int
    end_b: int
    lines: int


def _line_entries(path: Path) -> list[tuple[int, str]]:
    """[(numéro de ligne réel, texte normalisé)] — lignes vides/commentaires exclues."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    entries = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        entries.append((lineno, stripped))
    return entries


def _window_hashes(entries: list[tuple[int, str]], size: int) -> list[str]:
    """Hash SHA-1 de chaque fenêtre glissante de `size` lignes normalisées."""
    hashes = []
    for i in range(len(entries) - size + 1):
        block = "\n".join(t for _, t in entries[i:i + size])
        hashes.append(hashlib.sha1(block.encode("utf-8")).hexdigest())
    return hashes


def _iter_source_files(root: Path, dirs: list[str], exclude_dirs: frozenset[str]) -> list[Path]:
    files: list[Path] = []
    for d in dirs:
        base = root / d if not d.endswith(".py") else None
        if base is not None and base.is_dir():
            files.extend(
                p for p in base.rglob("*.py")
                if not any(part in exclude_dirs for part in p.parts)
            )
        elif d.endswith(".py") and (root / d).is_file():
            files.append(root / d)
    return files


def find_duplicates(
    root: Path,
    dirs: list[str],
    min_lines: int = 6,
    exclude_dirs: frozenset[str] = DEFAULT_EXCLUDE_DIRS,
) -> list[DuplicateBlock]:
    """Blocs dupliqués entre les fichiers .py des `dirs` donnés (relatifs à `root`)."""
    files = _iter_source_files(root, dirs, exclude_dirs)
    entries = {f: _line_entries(f) for f in files}
    hashes = {f: _window_hashes(entries[f], min_lines) for f in files}

    index: dict[str, list[tuple[Path, int]]] = defaultdict(list)
    for f, hlist in hashes.items():
        for idx, h in enumerate(hlist):
            index[h].append((f, idx))

    reported: set[tuple[Path, int]] = set()
    results: list[DuplicateBlock] = []

    for occurrences in index.values():
        if len(occurrences) < 2:
            continue
        for i in range(len(occurrences)):
            for j in range(i + 1, len(occurrences)):
                fa, ia = occurrences[i]
                fb, ib = occurrences[j]
                if fa == fb and abs(ia - ib) < min_lines:
                    continue  # même fichier, fenêtres qui se chevauchent trivialement
                if (fa, ia) in reported or (fb, ib) in reported:
                    continue

                # Extension gloutonne : tant que la fenêtre suivante des deux
                # côtés matche aussi, on agrandit le bloc rapporté.
                length = min_lines
                while True:
                    next_a = ia + (length - min_lines) + 1
                    next_b = ib + (length - min_lines) + 1
                    if next_a >= len(hashes[fa]) or next_b >= len(hashes[fb]):
                        break
                    if hashes[fa][next_a] != hashes[fb][next_b]:
                        break
                    length += 1

                for k in range(length - min_lines + 1):
                    reported.add((fa, ia + k))
                    reported.add((fb, ib + k))

                results.append(DuplicateBlock(
                    file_a=str(fa.relative_to(root)), start_a=entries[fa][ia][0],
                    end_a=entries[fa][ia + length - 1][0],
                    file_b=str(fb.relative_to(root)), start_b=entries[fb][ib][0],
                    end_b=entries[fb][ib + length - 1][0],
                    lines=length,
                ))

    results.sort(key=lambda b: -b.lines)
    return results


def scan_and_report(root: Path | None = None) -> dict:
    """Scan la codebase JARVIS, persiste les nouveaux constats, notifie si besoin.

    Une seule notification résumée (pas une par bloc) pour éviter le spam.
    """
    if not config.DUPLICATE_SCAN_ENABLED:
        return {"ok": False, "reason": "disabled"}

    root = root or config.BASE_DIR
    dirs = [d.strip() for d in config.DUPLICATE_SCAN_DIRS.split(",") if d.strip()]
    blocks = find_duplicates(root, dirs, min_lines=config.DUPLICATE_SCAN_MIN_LINES)

    new_count = 0
    for b in blocks:
        if upsert_duplicate_finding(b.file_a, b.start_a, b.end_a, b.file_b, b.start_b, b.end_b, b.lines):
            new_count += 1

    if new_count > 0:
        top = blocks[:3]
        lines = [f"{b.file_a}:{b.start_a}-{b.end_a} ≈ {b.file_b}:{b.start_b}-{b.end_b} ({b.lines} lignes)"
                for b in top]
        notification_service.create(
            source="system", title=f"Code dupliqué détecté ({new_count} nouveau(x) bloc(s))",
            content="; ".join(lines), priority="low",
        )
        logger.info("[dup-scan] %d nouveau(x) bloc(s) dupliqué(s)", new_count)

    return {"ok": True, "total_blocks": len(blocks), "new_findings": new_count}


def list_open_duplicates(limit: int = 100) -> list[dict]:
    return get_duplicate_findings(status="open", limit=limit)
