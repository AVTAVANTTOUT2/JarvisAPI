"""Validation de chemins et d'URL avant toute opération privilégiée."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import re
import stat as stat_module
from urllib.parse import urlsplit


class PathSecurityError(ValueError):
    """Un chemin, membre d'archive ou endpoint sort de sa frontière autorisée."""


_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


def is_link_like(path: Path) -> bool:
    """Détecte les liens, jonctions et autres points de réanalyse Windows."""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & reparse_flag)


def ensure_within(
    root: Path,
    candidate: Path,
    *,
    must_exist: bool = False,
    reject_symlinks: bool = True,
) -> Path:
    """Résout ``candidate`` et garantit son confinement sous ``root``."""

    resolved_root = root.expanduser().resolve(strict=True)
    unresolved = candidate if candidate.is_absolute() else resolved_root / candidate
    if must_exist and not unresolved.exists():
        raise PathSecurityError(f"Chemin absent: {unresolved}")

    if reject_symlinks:
        try:
            relative = unresolved.absolute().relative_to(resolved_root.absolute())
        except ValueError as exc:
            raise PathSecurityError(f"Chemin hors frontière: {unresolved}") from exc
        current = resolved_root
        for component in relative.parts:
            current = current / component
            if is_link_like(current):
                raise PathSecurityError(
                    f"Lien ou point de réanalyse interdit: {current}"
                )

    resolved = unresolved.resolve(strict=must_exist)
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise PathSecurityError(f"Chemin hors frontière: {candidate}") from exc
    return resolved


def safe_archive_member(name: str) -> PurePosixPath:
    """Valide un nom ZIP/TAR sans l'extraire."""

    if not name or "\x00" in name or "\\" in name:
        raise PathSecurityError("Nom de membre d'archive invalide")
    if name.startswith(("/", "//")) or _WINDOWS_DRIVE.match(name):
        raise PathSecurityError(f"Membre d'archive absolu interdit: {name}")
    value = PurePosixPath(name)
    if value.is_absolute() or any(part in {"", ".", ".."} for part in value.parts):
        raise PathSecurityError(f"Traversée d'archive interdite: {name}")
    return value


def validate_loopback_url(url: str) -> str:
    """Accepte uniquement une origine HTTP explicite sur 127.0.0.1."""

    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
        raise PathSecurityError("Le serveur OpenCode doit utiliser http://127.0.0.1")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise PathSecurityError(
            "Credentials, query et fragment sont interdits dans l'URL serveur"
        )
    if parsed.path not in {"", "/"}:
        raise PathSecurityError("L'URL serveur doit être une origine sans chemin")
    try:
        port = parsed.port
    except ValueError as exc:
        raise PathSecurityError("Port serveur invalide") from exc
    if port is None or not (1 <= port <= 65535):
        raise PathSecurityError("Un port serveur explicite est obligatoire")
    return f"http://127.0.0.1:{port}"


def is_regular_file_without_links(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    return (
        not is_link_like(path)
        and stat_module.S_ISREG(metadata.st_mode)
        and metadata.st_nlink == 1
    )
