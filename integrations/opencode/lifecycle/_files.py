"""I/O privé et atomique pour le runtime OpenCode."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from integrations.opencode.security.paths import is_link_like


class RuntimeFileError(OSError):
    pass


def atomic_write_json(path: Path, value: Mapping[str, Any], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if is_link_like(path.parent) or is_link_like(path):
        raise RuntimeFileError(
            f"Écriture via lien ou point de réanalyse interdite: {path}"
        )
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(mode)
    finally:
        try:
            Path(temporary).unlink()
        except FileNotFoundError:
            pass


def read_json_object(path: Path) -> dict[str, Any]:
    if is_link_like(path):
        raise RuntimeFileError(f"Fichier d'état lié ou réanalysé interdit: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeFileError(f"Fichier d'état invalide: {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeFileError(f"L'état doit être un objet JSON: {path}")
    return value


def remove_tree_without_following_links(path: Path, *, boundary: Path) -> None:
    """Supprime une cible confinée sans jamais parcourir un lien symbolique."""

    boundary_abs = boundary.absolute()
    target_abs = path.absolute()
    try:
        target_abs.relative_to(boundary_abs)
    except ValueError as exc:
        raise RuntimeFileError(f"Suppression hors frontière interdite: {path}") from exc
    if target_abs == boundary_abs.parent or boundary_abs == Path(boundary_abs.anchor):
        raise RuntimeFileError("Frontière de suppression trop large")
    if path.is_symlink():
        path.unlink(missing_ok=True)
        return
    if is_link_like(path):
        raise RuntimeFileError(f"Suppression d'un point de réanalyse refusée: {path}")
    if path.is_file():
        path.unlink(missing_ok=True)
        return
    if not path.exists():
        return
    with os.scandir(path) as entries:
        for entry in entries:
            child = Path(entry.path)
            if entry.is_symlink():
                child.unlink()
            elif is_link_like(child):
                raise RuntimeFileError(
                    f"Point de réanalyse rencontré, suppression arrêtée: {child}"
                )
            elif entry.is_dir(follow_symlinks=False):
                remove_tree_without_following_links(child, boundary=boundary)
            else:
                child.unlink()
    path.rmdir()
