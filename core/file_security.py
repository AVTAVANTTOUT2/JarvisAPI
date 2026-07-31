"""Permissions minimales pour les fichiers persistants sensibles."""

from __future__ import annotations

import os
from pathlib import Path

PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


def ensure_private_directory(path: str | Path) -> Path:
    """Crée un dossier privé et corrige son mode sans suivre de symlink."""
    directory = Path(path)
    if directory.is_symlink():
        raise RuntimeError(f"dossier sensible refusé (lien symbolique) : {directory}")
    directory.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIRECTORY_MODE)
    directory.chmod(PRIVATE_DIRECTORY_MODE)
    return directory


def ensure_private_file(path: str | Path) -> Path:
    """Force le mode 0600 d'un fichier existant sans suivre de symlink."""
    private_file = Path(path)
    if private_file.is_symlink():
        raise RuntimeError(f"fichier sensible refusé (lien symbolique) : {private_file}")
    if private_file.exists():
        try:
            private_file.chmod(PRIVATE_FILE_MODE)
        except FileNotFoundError:
            # Les sidecars SQLite WAL/SHM peuvent disparaître entre exists()
            # et chmod() lorsqu'une autre connexion ferme son transaction.
            pass
    return private_file


def write_private_bytes(
    path: str | Path,
    data: bytes,
    *,
    exclusive: bool = False,
) -> Path:
    """Écrit et fsync des octets avec un descripteur créé directement en 0600."""
    destination = Path(path)
    ensure_private_directory(destination.parent)
    if destination.is_symlink():
        raise RuntimeError(f"fichier sensible refusé (lien symbolique) : {destination}")

    flags = os.O_WRONLY | os.O_CREAT
    flags |= os.O_EXCL if exclusive else os.O_TRUNC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(destination, flags, PRIVATE_FILE_MODE)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        if exclusive:
            destination.unlink(missing_ok=True)
        raise
    ensure_private_file(destination)
    return destination
