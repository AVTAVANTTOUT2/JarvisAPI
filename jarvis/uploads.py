"""Stockage borné et validé des fichiers envoyés à JARVIS."""

from __future__ import annotations

import codecs
import logging
import mimetypes
import os
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from time import time
from typing import TYPE_CHECKING
from uuid import uuid4

import config

if TYPE_CHECKING:
    from fastapi import UploadFile

logger = logging.getLogger(__name__)

CHUNK_SIZE = 64 * 1024
GENERIC_MIME_TYPES = {"", "application/octet-stream"}
TEXT_MIME_TYPES = {
    "application/csv",
    "application/javascript",
    "application/json",
    "application/x-javascript",
}

CONVERSATION_EXTENSIONS = frozenset(
    {".pdf", ".txt", ".md", ".csv", ".json", ".py", ".js", ".ts", ".html", ".css"}
)
SCHOOL_EXTENSIONS = frozenset({".pdf", ".txt", ".md", ".png", ".jpg", ".jpeg", ".webp"})

_quota_lock = threading.Lock()


class UploadRejected(ValueError):
    """Erreur d'upload destinée à être traduite en réponse HTTP."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class StoredUpload:
    """Métadonnées d'un fichier validé et déplacé vers son nom interne."""

    path: Path
    stored_name: str
    original_name: str
    extension: str
    size: int
    detected_mime: str


def _managed_root() -> Path:
    return Path(config.UPLOAD_DIR).expanduser().resolve()


def _lexical_absolute(path: str | Path) -> Path:
    """Normalise ``..`` sans suivre un éventuel lien symbolique final."""
    return Path(os.path.abspath(os.fspath(path)))


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def normalize_upload_name(raw_name: str | None) -> tuple[str, str]:
    """Retourne un nom d'affichage sûr et son extension normalisée."""
    if not raw_name:
        raise UploadRejected(400, "Nom de fichier manquant")

    # Les navigateurs peuvent transmettre des séparateurs Windows même sur macOS.
    basename = raw_name.replace("\\", "/").rsplit("/", 1)[-1]
    basename = unicodedata.normalize("NFKC", basename)
    basename = "".join(ch for ch in basename if unicodedata.category(ch)[0] != "C")
    basename = basename.strip().strip(".").strip()
    if not basename:
        raise UploadRejected(400, "Nom de fichier invalide")

    extension = Path(basename).suffix.lower()
    stem = Path(basename).stem.strip().strip(".").strip() or "document"
    # Le nom n'est jamais utilisé comme chemin, mais reste borné pour la DB/UI.
    max_stem = max(1, 180 - len(extension))
    safe_name = f"{stem[:max_stem]}{extension}"
    return safe_name, extension


def _target_directory(namespace: str) -> Path:
    relative = Path(namespace)
    if relative.is_absolute() or not relative.parts or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        raise ValueError("namespace d'upload invalide")
    root = _managed_root()
    target = _lexical_absolute(root / relative)
    if not _is_within(target, root):
        raise ValueError("namespace d'upload hors racine")
    target.mkdir(parents=True, exist_ok=True)
    return target


def _declared_mime_allowed(extension: str, content_type: str | None) -> bool:
    declared = (content_type or "").split(";", 1)[0].strip().lower()
    if declared in GENERIC_MIME_TYPES:
        return True
    if extension == ".pdf":
        return declared == "application/pdf"
    if extension in {".png"}:
        return declared == "image/png"
    if extension in {".jpg", ".jpeg"}:
        return declared == "image/jpeg"
    if extension == ".webp":
        return declared == "image/webp"
    return declared.startswith("text/") or declared in TEXT_MIME_TYPES


def _validate_text(path: Path) -> None:
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    with path.open("rb") as source:
        while chunk := source.read(CHUNK_SIZE):
            if b"\x00" in chunk:
                raise UploadRejected(415, "Le fichier texte contient des données binaires")
            try:
                decoder.decode(chunk, final=False)
            except UnicodeDecodeError as exc:
                raise UploadRejected(415, "Le fichier texte doit être encodé en UTF-8") from exc
    try:
        decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        raise UploadRejected(415, "Le fichier texte doit être encodé en UTF-8") from exc


def _validate_signature(path: Path, extension: str) -> str:
    with path.open("rb") as source:
        header = source.read(16)

    if extension == ".pdf":
        if not header.startswith(b"%PDF-"):
            raise UploadRejected(415, "Signature PDF invalide")
        return "application/pdf"
    if extension == ".png":
        if not header.startswith(b"\x89PNG\r\n\x1a\n"):
            raise UploadRejected(415, "Signature PNG invalide")
        return "image/png"
    if extension in {".jpg", ".jpeg"}:
        if not header.startswith(b"\xff\xd8\xff"):
            raise UploadRejected(415, "Signature JPEG invalide")
        return "image/jpeg"
    if extension == ".webp":
        if len(header) < 12 or header[:4] != b"RIFF" or header[8:12] != b"WEBP":
            raise UploadRejected(415, "Signature WebP invalide")
        return "image/webp"

    _validate_text(path)
    return mimetypes.guess_type(f"x{extension}")[0] or "text/plain"


def upload_disk_usage(root: Path | None = None) -> int:
    """Calcule l'espace occupé sous la racine d'upload, fichiers temporaires inclus."""
    root = (root or _managed_root()).resolve()
    if not root.is_dir():
        return 0
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file() or path.is_symlink():
                total += path.lstat().st_size
        except OSError:
            continue
    return total


async def store_upload(
    upload: "UploadFile",
    *,
    namespace: str,
    allowed_extensions: frozenset[str],
) -> StoredUpload:
    """Écrit un upload par blocs, le valide puis le publie sous un UUID."""
    original_name, extension = normalize_upload_name(upload.filename)
    if extension not in allowed_extensions:
        raise UploadRejected(415, f"Type de fichier non autorisé : {extension or 'sans extension'}")
    if not _declared_mime_allowed(extension, upload.content_type):
        raise UploadRejected(415, "Le type MIME déclaré ne correspond pas au fichier")

    target_dir = _target_directory(namespace)
    token = uuid4().hex
    temporary_path = target_dir / f".{token}.part"
    final_name = f"{token}{extension}"
    final_path = target_dir / final_name
    size = 0
    max_bytes = max(1, int(config.UPLOAD_MAX_BYTES))

    try:
        with temporary_path.open("xb") as destination:
            while chunk := await upload.read(CHUNK_SIZE):
                size += len(chunk)
                if size > max_bytes:
                    raise UploadRejected(
                        413,
                        f"Fichier trop volumineux (maximum {max_bytes} octets)",
                    )
                destination.write(chunk)

        if size == 0:
            raise UploadRejected(400, "Le fichier est vide")

        detected_mime = _validate_signature(temporary_path, extension)
        quota_bytes = int(config.UPLOAD_QUOTA_BYTES)
        with _quota_lock:
            if quota_bytes > 0 and upload_disk_usage() > quota_bytes:
                raise UploadRejected(507, "Quota disque des uploads dépassé")
            temporary_path.replace(final_path)

        return StoredUpload(
            path=final_path,
            stored_name=final_name,
            original_name=original_name,
            extension=extension,
            size=size,
            detected_mime=detected_mime,
        )
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def remove_managed_upload(path: str | Path) -> bool:
    """Supprime un fichier uniquement s'il se trouve sous ``UPLOAD_DIR``."""
    root = _managed_root()
    candidate = _lexical_absolute(path)
    if candidate == root or not _is_within(candidate, root):
        logger.warning("[uploads] suppression refusée hors racine : %s", path)
        return False

    removed = False
    try:
        if candidate.is_file() or candidate.is_symlink():
            candidate.unlink()
            removed = True
    except OSError as exc:
        logger.warning("[uploads] suppression impossible %s : %s", candidate, exc)
        return False

    parent = candidate.parent
    while parent != root and _is_within(parent, root):
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent
    return removed


def purge_orphan_uploads(
    referenced_paths: set[str | Path],
    *,
    grace_seconds: int | None = None,
    now: float | None = None,
) -> dict:
    """Supprime les fichiers non référencés assez anciens sous ``UPLOAD_DIR``."""
    root = _managed_root()
    if not root.is_dir():
        return {"removed": 0, "freed_bytes": 0}

    referenced = {_lexical_absolute(path) for path in referenced_paths if path}
    grace = (
        max(0, int(config.UPLOAD_ORPHAN_GRACE_SECONDS))
        if grace_seconds is None
        else max(0, int(grace_seconds))
    )
    cutoff = (time() if now is None else now) - grace
    removed = 0
    freed = 0

    for path in list(root.rglob("*")):
        try:
            if not (path.is_file() or path.is_symlink()):
                continue
            absolute = _lexical_absolute(path)
            stat = path.lstat()
            if absolute in referenced or stat.st_mtime > cutoff:
                continue
            size = stat.st_size
            if remove_managed_upload(path):
                removed += 1
                freed += size
        except OSError as exc:
            logger.warning("[uploads] purge impossible %s : %s", path, exc)

    return {"removed": removed, "freed_bytes": freed}
