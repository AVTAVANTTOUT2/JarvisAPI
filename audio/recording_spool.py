"""Spool audio durable, isolé par profil, pour les enregistrements continus.

Chaque chunk WebM est fsync puis renommé atomiquement avant que l'appelant ne
puisse accuser réception. La base ne contient que le chemin local, le checksum
et l'état du traitement ; le contenu audio ne passe jamais dans les logs.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import config
from database import (
    create_recording_session,
    current_profile_id,
    db_transaction,
    enqueue_ingestion_job,
    get_recording_session,
    list_expired_recording_sessions,
    list_pending_recording_sessions,
    update_recording_session,
)

_SPOOL_ROOT = Path(config.BASE_DIR) / "data" / "recording_spool"
_RAW_RETENTION = timedelta(days=7)


def _utc_iso(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc)).isoformat()


def _partition(profile_id: str) -> str:
    return hashlib.sha256(profile_id.encode("utf-8")).hexdigest()[:24]


def _ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validated_session_dir(path: Path) -> Path:
    raw_root = _SPOOL_ROOT.absolute()
    raw_candidate = path.absolute()
    if (
        raw_root.is_symlink()
        or raw_candidate == raw_root
        or raw_root not in raw_candidate.parents
    ):
        raise ValueError("recording_spool_path_outside_root")
    current = raw_root
    for part in raw_candidate.relative_to(raw_root).parts:
        current /= part
        if current.is_symlink():
            raise ValueError("recording_spool_path_invalid")
    root = raw_root.resolve(strict=True)
    candidate = raw_candidate.resolve(strict=True)
    if candidate == root or root not in candidate.parents:
        raise ValueError("recording_spool_path_outside_root")
    if not candidate.is_dir():
        raise ValueError("recording_spool_path_invalid")
    return candidate


@dataclass(slots=True)
class RecordingSpool:
    session_id: str
    path: Path
    size_bytes: int = 0
    _next_chunk: int = 0
    _checksum: Any = field(default_factory=hashlib.sha256, repr=False)

    @classmethod
    def create(cls, *, conversation_id: int | None, label: str) -> "RecordingSpool":
        profile_id = current_profile_id()
        session_id = str(uuid.uuid4())
        profile_dir = _SPOOL_ROOT / _partition(profile_id)
        _ensure_private_dir(profile_dir)
        path = profile_dir / session_id
        path.mkdir(mode=0o700)
        _fsync_dir(profile_dir)
        create_recording_session(
            session_id=session_id,
            conversation_id=conversation_id,
            label=label,
            spool_path=str(path),
            state="capturing",
        )
        return cls(session_id=session_id, path=path)

    @classmethod
    def open(cls, session_id: str) -> "RecordingSpool":
        session = get_recording_session(session_id)
        if session is None:
            raise LookupError("recording_session_not_found")
        expected_partition = _partition(current_profile_id())
        path = _validated_session_dir(Path(session.spool_path))
        if path.parent.name != expected_partition or path.name != session.id:
            raise ValueError("recording_spool_profile_mismatch")
        spool = cls(session_id=session.id, path=path)
        for chunk in spool.chunk_paths():
            data = chunk.read_bytes()
            spool._checksum.update(data)
            spool.size_bytes += len(data)
            spool._next_chunk += 1
        return spool

    def chunk_paths(self) -> list[Path]:
        path = _validated_session_dir(self.path)
        chunks = sorted(path.glob("*.chunk"))
        if any(item.is_symlink() or not item.is_file() for item in chunks):
            raise ValueError("recording_spool_chunk_invalid")
        return chunks

    def append(self, audio_bytes: bytes) -> None:
        if not audio_bytes:
            return
        path = _validated_session_dir(self.path)
        name = f"{self._next_chunk:08d}"
        temporary = path / f".{name}.part"
        target = path / f"{name}.chunk"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o600)
        try:
            view = memoryview(audio_bytes)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
        except BaseException:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise
        finally:
            os.close(descriptor)
        os.replace(temporary, target)
        target.chmod(0o600)
        _fsync_dir(path)

        self._checksum.update(audio_bytes)
        self.size_bytes += len(audio_bytes)
        self._next_chunk += 1
        update_recording_session(
            self.session_id,
            size_bytes=self.size_bytes,
            checksum=self._checksum.hexdigest(),
            state="capturing",
        )

    def read_chunks(self) -> list[bytes]:
        return [chunk.read_bytes() for chunk in self.chunk_paths()]

    def enqueue(self, *, label: str, duration_seconds: int) -> str:
        # Les helpers réutilisent la connexion ambiante : session scellée et
        # job durable deviennent visibles dans le même commit, jamais entre les
        # deux états.
        with db_transaction():
            update_recording_session(
                self.session_id,
                label=label,
                state="queued",
                size_bytes=self.size_bytes,
                checksum=self._checksum.hexdigest(),
                error=None,
            )
            enqueue_ingestion_job(
                "recording",
                job_kind="recording_process",
                payload={
                    "session_id": self.session_id,
                    "duration_seconds": max(0, int(duration_seconds)),
                },
                dedupe_key=f"recording:{self.session_id}",
                require_binding=False,
            )
        return self.session_id

    def mark_succeeded(self, *, transcript: str, summary: str) -> None:
        update_recording_session(
            self.session_id,
            state="completed",
            transcript=transcript,
            summary=summary,
            error=None,
            retention_until=_utc_iso(datetime.now(timezone.utc) + _RAW_RETENTION),
        )

    def mark_failed(self, error_code: str, *, terminal: bool) -> None:
        session = get_recording_session(self.session_id)
        attempts = (session.attempts if session else 0) + 1
        update_recording_session(
            self.session_id,
            state="failed" if terminal else "retry",
            attempts=attempts,
            error=str(error_code)[:500],
            retention_until=(
                _utc_iso(datetime.now(timezone.utc) + _RAW_RETENTION)
                if terminal
                else None
            ),
        )


def purge_recording_audio(session_id: str) -> bool:
    """Supprime uniquement l'audio brut d'une session arrivée à rétention."""

    session = get_recording_session(session_id)
    if session is None or not session.retention_until:
        return False
    retention = datetime.fromisoformat(session.retention_until.replace("Z", "+00:00"))
    if retention > datetime.now(timezone.utc):
        return False
    path = _validated_session_dir(Path(session.spool_path))
    shutil.rmtree(path)
    update_recording_session(session_id, state="expired", spool_path="")
    return True


def purge_expired_recordings(*, limit: int = 100) -> int:
    purged = 0
    for session in list_expired_recording_sessions(limit=limit):
        try:
            purged += int(purge_recording_audio(session.id))
        except FileNotFoundError:
            # Une purge interrompue après suppression du dossier est rejouable.
            update_recording_session(session.id, state="expired", spool_path="")
            purged += 1
    return purged


def reconcile_recording_sessions(*, limit: int = 100) -> int:
    """Réenfile les sessions scellées laissées sans job par une ancienne panne."""

    repaired = 0
    for session in list_pending_recording_sessions(limit=limit):
        if not session.spool_path:
            continue
        enqueue_ingestion_job(
            "recording",
            job_kind="recording_process",
            payload={"session_id": session.id, "duration_seconds": 0},
            dedupe_key=f"recording:{session.id}",
            require_binding=False,
        )
        repaired += 1
    return repaired


__all__ = [
    "RecordingSpool",
    "purge_expired_recordings",
    "purge_recording_audio",
    "reconcile_recording_sessions",
]
