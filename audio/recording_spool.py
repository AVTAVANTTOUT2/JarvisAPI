"""Spool audio durable, isolé par profil, pour les enregistrements continus.

Chaque chunk WebM est fsync puis renommé atomiquement avant que l'appelant ne
puisse accuser réception. La base ne contient que le chemin local, le checksum
et l'état du traitement ; le contenu audio ne passe jamais dans les logs.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import config
from database import (
    create_recording_session,
    current_profile_id,
    db_transaction,
    enqueue_ingestion_job,
    get_recording_session,
    list_expired_recording_sessions,
    list_pending_recording_sessions,
    mark_dead_recording_sessions_failed,
    update_recording_session,
)

_SPOOL_ROOT = Path(config.BASE_DIR) / "data" / "recording_spool"
_RAW_RETENTION = timedelta(days=7)
RECORDING_PROTOCOL_VERSION = 1
RECORDING_PROCESSING_MAX_ATTEMPTS = 3
RECORDING_MAX_SEGMENT_DURATION_MS = 60_000
RECORDING_MIN_SEGMENT_BYTES = 800
_STATE_FILE = ".state.json"
_CHUNK_RE = re.compile(
    r"^(?P<sequence>[0-9]{8})(?:-(?P<checksum>[0-9a-f]{64})-(?P<duration>[0-9]{8}))?\.chunk$"
)
_EMPTY_CHECKSUM = hashlib.sha256(b"").hexdigest()
_ALLOWED_AUDIO_TYPES = frozenset({"audio/webm", "audio/mp4", "audio/ogg"})


class RecordingSpoolError(ValueError):
    """Erreur publique stable du protocole d'upload d'enregistrement."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 409,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.context = dict(context or {})


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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _chain_checksum(previous: str, chunk_checksum: str) -> str:
    try:
        previous_bytes = bytes.fromhex(previous)
    except ValueError:
        previous_bytes = hashlib.sha256(previous.encode("utf-8")).digest()
    return hashlib.sha256(previous_bytes + bytes.fromhex(chunk_checksum)).hexdigest()


def _normalise_mime_type(value: str) -> str:
    return str(value or "").split(";", 1)[0].strip().casefold()


def _validate_audio_container(data: bytes, mime_type: str) -> str:
    normalised = _normalise_mime_type(mime_type)
    if normalised not in _ALLOWED_AUDIO_TYPES:
        raise RecordingSpoolError(
            "recording_container_unsupported",
            "Conteneur audio non pris en charge",
            status_code=415,
            context={"allowed": sorted(_ALLOWED_AUDIO_TYPES)},
        )
    if len(data) < RECORDING_MIN_SEGMENT_BYTES:
        raise RecordingSpoolError(
            "recording_chunk_truncated",
            "Segment audio tronqué",
            status_code=422,
        )
    signature_ok = (
        (normalised == "audio/webm" and data.startswith(b"\x1aE\xdf\xa3"))
        or (normalised == "audio/mp4" and len(data) >= 12 and data[4:8] == b"ftyp")
        or (normalised == "audio/ogg" and data.startswith(b"OggS"))
    )
    if not signature_ok:
        raise RecordingSpoolError(
            "recording_container_invalid",
            "Signature du conteneur audio invalide",
            status_code=422,
        )
    return normalised


@contextmanager
def _session_lock(path: Path) -> Iterator[None]:
    lock_path = path / ".lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
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
    duration_ms: int = 0
    _checksum_value: str = field(default=_EMPTY_CHECKSUM, repr=False)

    @classmethod
    def create(
        cls,
        *,
        conversation_id: int | None,
        label: str,
        client_recording_id: str | None = None,
    ) -> "RecordingSpool":
        profile_id = current_profile_id()
        session_id = (
            str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"jarvis-recording:{profile_id}:{client_recording_id}",
                )
            )
            if client_recording_id
            else str(uuid.uuid4())
        )
        existing = get_recording_session(session_id)
        if existing is not None:
            return cls.open(session_id)
        profile_dir = _SPOOL_ROOT / _partition(profile_id)
        _ensure_private_dir(profile_dir)
        path = profile_dir / session_id
        path.mkdir(mode=0o700, exist_ok=True)
        path = _validated_session_dir(path)
        with _session_lock(path):
            # Deux POST idempotents peuvent atteindre ce point ensemble. Le
            # verrou du dossier déterministe protège aussi la création DB.
            existing = get_recording_session(session_id)
            if existing is not None:
                spool = cls(session_id=session_id, path=path)
                spool._load_or_rebuild_state()
                return spool
            spool = cls(session_id=session_id, path=path)
            spool._write_state()
            _fsync_dir(profile_dir)
            create_recording_session(
                session_id=session_id,
                conversation_id=conversation_id,
                label=label,
                spool_path=str(path),
                state="capturing",
            )
        return spool

    @classmethod
    def open(cls, session_id: str) -> "RecordingSpool":
        session = get_recording_session(session_id)
        if session is None:
            raise LookupError("recording_session_not_found")
        if not session.spool_path:
            raise RecordingSpoolError(
                "recording_spool_unavailable",
                "L'audio brut de cette session n'est plus disponible",
                status_code=409,
                context={"state": session.state},
            )
        expected_partition = _partition(current_profile_id())
        path = _validated_session_dir(Path(session.spool_path))
        if path.parent.name != expected_partition or path.name != session.id:
            raise ValueError("recording_spool_profile_mismatch")
        spool = cls(session_id=session.id, path=path)
        spool._load_or_rebuild_state()
        if session.size_bytes != spool.size_bytes or session.checksum != spool.checksum:
            update_recording_session(
                session.id,
                size_bytes=spool.size_bytes,
                checksum=spool.checksum,
            )
        return spool

    @property
    def checksum(self) -> str:
        return self._checksum_value

    @property
    def next_sequence(self) -> int:
        return self._next_chunk

    @property
    def chunk_count(self) -> int:
        return self._next_chunk

    @property
    def _state_path(self) -> Path:
        return self.path / _STATE_FILE

    def _write_state(self) -> None:
        path = _validated_session_dir(self.path)
        payload = {
            "version": 1,
            "session_id": self.session_id,
            "next_sequence": self._next_chunk,
            "size_bytes": self.size_bytes,
            "duration_ms": self.duration_ms,
            "checksum": self._checksum_value,
        }
        temporary = path / f".{_STATE_FILE}.{uuid.uuid4().hex}.part"
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            view = memoryview(raw)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, self._state_path)
        self._state_path.chmod(0o600)
        _fsync_dir(path)

    def _load_state(self) -> bool:
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
            if payload.get("version") != 1 or payload.get("session_id") != self.session_id:
                return False
            next_sequence = int(payload["next_sequence"])
            size_bytes = int(payload["size_bytes"])
            duration_ms = int(payload["duration_ms"])
            checksum = str(payload["checksum"])
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return False
        if (
            next_sequence < 0
            or size_bytes < 0
            or duration_ms < 0
            or not re.fullmatch(r"[0-9a-f]{64}", checksum)
        ):
            return False
        self._next_chunk = next_sequence
        self.size_bytes = size_bytes
        self.duration_ms = duration_ms
        self._checksum_value = checksum
        return True

    @staticmethod
    def _chunk_metadata(path: Path) -> tuple[int, str, int]:
        match = _CHUNK_RE.fullmatch(path.name)
        if match is None:
            raise ValueError("recording_spool_chunk_invalid")
        checksum = match.group("checksum") or _sha256_file(path)
        return (
            int(match.group("sequence")),
            checksum,
            int(match.group("duration") or 0),
        )

    def _load_or_rebuild_state(self, *, verify: bool = False) -> None:
        if self._load_state():
            chunks = self.chunk_paths()
            if len(chunks) == self._next_chunk:
                if verify:
                    self._verify_chunks(chunks)
                return
            if len(chunks) < self._next_chunk:
                raise RecordingSpoolError(
                    "recording_chunk_corrupt",
                    "Un segment audio accuse a disparu du spool",
                    status_code=422,
                )
            # Un crash peut survenir après le fsync/rename du segment mais
            # avant le manifeste et l'ACK. Ce suffixe non accuse est récupéré
            # uniquement après vérification de son contenu réel.
        self.size_bytes = 0
        self.duration_ms = 0
        self._next_chunk = 0
        self._checksum_value = _EMPTY_CHECKSUM
        for expected, chunk in enumerate(self.chunk_paths()):
            sequence, checksum, duration_ms = self._chunk_metadata(chunk)
            if sequence != expected:
                raise ValueError("recording_spool_chunk_gap")
            if _sha256_file(chunk) != checksum:
                raise RecordingSpoolError(
                    "recording_chunk_corrupt",
                    "Le contenu d'un segment audio ne correspond plus a son checksum",
                    status_code=422,
                    context={"sequence": sequence},
                )
            self.size_bytes += chunk.stat().st_size
            self.duration_ms += duration_ms
            self._checksum_value = _chain_checksum(self._checksum_value, checksum)
            self._next_chunk += 1
        self._write_state()

    def _verify_chunks(self, chunks: list[Path] | None = None) -> None:
        """Vérifie le contenu ACKé et la cohérence exacte du manifeste."""

        paths = self.chunk_paths() if chunks is None else chunks
        size_bytes = 0
        duration_ms = 0
        checksum_value = _EMPTY_CHECKSUM
        for expected, chunk in enumerate(paths):
            sequence, stored_checksum, chunk_duration_ms = self._chunk_metadata(chunk)
            actual_checksum = _sha256_file(chunk)
            if sequence != expected or actual_checksum != stored_checksum:
                raise RecordingSpoolError(
                    "recording_chunk_corrupt",
                    "Le contenu d'un segment audio ne correspond plus a son checksum",
                    status_code=422,
                    context={"sequence": sequence},
                )
            size_bytes += chunk.stat().st_size
            duration_ms += chunk_duration_ms
            checksum_value = _chain_checksum(checksum_value, actual_checksum)
        if (
            len(paths) != self._next_chunk
            or size_bytes != self.size_bytes
            or duration_ms != self.duration_ms
            or checksum_value != self._checksum_value
        ):
            raise RecordingSpoolError(
                "recording_manifest_corrupt",
                "Le manifeste de l'enregistrement ne correspond plus aux segments",
                status_code=422,
            )

    def verify_integrity(self) -> None:
        """Vérifie sous verrou chaque octet durable avant traitement."""

        path = _validated_session_dir(self.path)
        with _session_lock(path):
            self._load_or_rebuild_state(verify=True)

    def chunk_paths(self) -> list[Path]:
        path = _validated_session_dir(self.path)
        chunks = sorted(path.glob("*.chunk"), key=lambda item: self._chunk_metadata(item)[0])
        if any(item.is_symlink() or not item.is_file() for item in chunks):
            raise ValueError("recording_spool_chunk_invalid")
        sequences = [self._chunk_metadata(item)[0] for item in chunks]
        if sequences != list(range(len(chunks))):
            raise ValueError("recording_spool_chunk_gap")
        return chunks

    def append(self, audio_bytes: bytes) -> None:
        if not audio_bytes:
            return
        self.append_chunk(
            sequence=self._next_chunk,
            audio_bytes=audio_bytes,
            expected_checksum=hashlib.sha256(audio_bytes).hexdigest(),
            duration_ms=0,
            mime_type="",
            validate_container=False,
        )

    def append_chunk(
        self,
        *,
        sequence: int,
        audio_bytes: bytes,
        expected_checksum: str,
        duration_ms: int,
        mime_type: str,
        validate_container: bool = True,
    ) -> dict[str, Any]:
        """Persiste un segment idempotent et retourne son ACK après fsync."""

        sequence = int(sequence)
        duration_ms = int(duration_ms)
        max_chunks = max(1, int(config.RECORDING_MAX_DURATION_MIN) * 60 * 2)
        max_bytes = max(1, int(config.RECORDING_CHUNK_SIZE_MB)) * 1024 * 1024
        if sequence < 0 or sequence >= max_chunks:
            raise RecordingSpoolError(
                "recording_sequence_invalid",
                "Séquence de segment invalide",
                status_code=422,
            )
        if (
            duration_ms < 0
            or duration_ms > RECORDING_MAX_SEGMENT_DURATION_MS
            or (validate_container and duration_ms == 0)
        ):
            raise RecordingSpoolError(
                "recording_chunk_duration_invalid",
                "Durée de segment invalide",
                status_code=422,
            )
        if not audio_bytes or len(audio_bytes) > max_bytes:
            raise RecordingSpoolError(
                "recording_chunk_too_large" if audio_bytes else "recording_chunk_empty",
                "Segment audio vide ou trop volumineux",
                status_code=413 if audio_bytes else 422,
                context={"max_bytes": max_bytes},
            )
        expected_checksum = str(expected_checksum or "").strip().casefold()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_checksum):
            raise RecordingSpoolError(
                "recording_chunk_checksum_invalid",
                "Checksum de segment invalide",
                status_code=422,
            )
        actual_checksum = hashlib.sha256(audio_bytes).hexdigest()
        if actual_checksum != expected_checksum:
            raise RecordingSpoolError(
                "recording_chunk_checksum_mismatch",
                "Le segment ne correspond pas à son checksum",
                status_code=422,
            )
        if validate_container:
            _validate_audio_container(audio_bytes, mime_type)

        path = _validated_session_dir(self.path)
        with _session_lock(path):
            session = get_recording_session(self.session_id)
            if session is None:
                raise RecordingSpoolError(
                    "recording_session_not_found",
                    "Session d'enregistrement introuvable",
                    status_code=404,
                )
            if session.state != "capturing" or session.error == "recording_cancelled":
                raise RecordingSpoolError(
                    "recording_session_not_capturing",
                    "La session n'accepte plus de segments",
                    context={"state": session.state},
                )
            if not self._load_state():
                self._load_or_rebuild_state()

            matching = list(path.glob(f"{sequence:08d}*.chunk"))
            if matching:
                if len(matching) != 1:
                    raise ValueError("recording_spool_chunk_invalid")
                stored_sequence, stored_checksum, stored_duration = self._chunk_metadata(
                    matching[0]
                )
                if (
                    stored_sequence != sequence
                    or stored_checksum != actual_checksum
                    or stored_duration != duration_ms
                ):
                    raise RecordingSpoolError(
                        "recording_chunk_conflict",
                        "Cette séquence contient déjà un autre segment",
                        context={"next_sequence": self._next_chunk},
                    )
                if sequence == self._next_chunk:
                    self.size_bytes += matching[0].stat().st_size
                    self.duration_ms += stored_duration
                    self._checksum_value = _chain_checksum(
                        self._checksum_value, stored_checksum
                    )
                    self._next_chunk += 1
                    self._write_state()
                    update_recording_session(
                        self.session_id,
                        size_bytes=self.size_bytes,
                        checksum=self.checksum,
                    )
                return self._ack(sequence, "duplicate")
            if sequence != self._next_chunk:
                raise RecordingSpoolError(
                    "recording_chunk_gap",
                    "Un segment précédent manque",
                    context={"next_sequence": self._next_chunk},
                )
            max_duration_ms = int(config.RECORDING_MAX_DURATION_MIN) * 60 * 1000
            if self.duration_ms + duration_ms > max_duration_ms:
                raise RecordingSpoolError(
                    "recording_duration_exceeded",
                    "Durée maximale d'enregistrement dépassée",
                    status_code=422,
                    context={"max_duration_ms": max_duration_ms},
                )

            name = f"{sequence:08d}-{actual_checksum}-{duration_ms:08d}"
            temporary = path / f".{name}.{uuid.uuid4().hex}.part"
            target = path / f"{name}.chunk"
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(temporary, flags, 0o600)
            try:
                view = memoryview(audio_bytes)
                while view:
                    written = os.write(descriptor, view)
                    view = view[written:]
                os.fsync(descriptor)
            except OSError as exc:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
                if exc.errno in {errno.ENOSPC, errno.EDQUOT}:
                    raise RecordingSpoolError(
                        "recording_storage_full",
                        "Stockage insuffisant pour le segment audio",
                        status_code=507,
                    ) from exc
                raise
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

            self._checksum_value = _chain_checksum(
                self._checksum_value, actual_checksum
            )
            self.size_bytes += len(audio_bytes)
            self.duration_ms += duration_ms
            self._next_chunk += 1
            self._write_state()
            update_recording_session(
                self.session_id,
                size_bytes=self.size_bytes,
                checksum=self.checksum,
                state="capturing",
            )
            return self._ack(sequence, "accepted")

    def _ack(self, sequence: int, status: str) -> dict[str, Any]:
        return {
            "ok": True,
            "protocol_version": RECORDING_PROTOCOL_VERSION,
            "session_id": self.session_id,
            "sequence": sequence,
            "status": status,
            "accepted": status == "accepted",
            "duplicate": status == "duplicate",
            "next_sequence": self._next_chunk,
            "received_chunks": self._next_chunk,
            "size_bytes": self.size_bytes,
            "duration_ms": self.duration_ms,
            "checksum": self.checksum,
        }

    def iter_chunks(self, *, min_size: int = 0) -> Iterator[bytes]:
        """Lit au plus un segment audio à la fois."""

        for chunk in self.chunk_paths():
            if chunk.stat().st_size >= min_size:
                yield chunk.read_bytes()

    def cancel_capture(self) -> None:
        """Scelle une capture annulée et efface son audio sous le même verrou."""

        path = _validated_session_dir(self.path)
        with _session_lock(path):
            session = get_recording_session(self.session_id)
            if session is None:
                raise RecordingSpoolError(
                    "recording_session_not_found",
                    "Session d'enregistrement introuvable",
                    status_code=404,
                )
            already_cancelled = session.error == "recording_cancelled"
            if session.state != "capturing" and not already_cancelled:
                raise RecordingSpoolError(
                    "recording_session_not_cancellable",
                    "Seule une capture en cours peut être annulée",
                    context={"state": session.state},
                )
            if not already_cancelled:
                # Bloque définitivement les uploads avant l'effacement. Si le
                # processus tombe ensuite, DELETE peut rejouer le nettoyage.
                update_recording_session(
                    self.session_id,
                    state="failed",
                    error="recording_cancelled",
                    retention_until=_utc_iso(),
                )
            for child in list(path.iterdir()):
                if child.name == ".lock":
                    continue
                if child.is_file() or child.is_symlink():
                    child.unlink()
            _fsync_dir(path)
            update_recording_session(
                self.session_id,
                spool_path="",
                size_bytes=0,
                checksum="",
            )
            self.size_bytes = 0
            self.duration_ms = 0
            self._next_chunk = 0
            self._checksum_value = _EMPTY_CHECKSUM

    def read_chunks(self) -> list[bytes]:
        return [chunk.read_bytes() for chunk in self.chunk_paths()]

    def _enqueue_locked(self, *, label: str, duration_seconds: int) -> str:
        # Les helpers réutilisent la connexion ambiante : session scellée et
        # job durable deviennent visibles dans le même commit, jamais entre les
        # deux états.
        with db_transaction():
            update_recording_session(
                self.session_id,
                label=label,
                state="queued",
                size_bytes=self.size_bytes,
                checksum=self.checksum,
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
                max_attempts=RECORDING_PROCESSING_MAX_ATTEMPTS,
                require_binding=False,
            )
        return self.session_id

    def seal_and_enqueue(
        self,
        *,
        label: str,
        duration_seconds: int,
        expected_chunks: int | None = None,
    ) -> dict[str, int | str]:
        """Scelle capture et job sous le verrou partagé avec les uploads."""

        path = _validated_session_dir(self.path)
        with _session_lock(path):
            session = get_recording_session(self.session_id)
            if session is None:
                raise RecordingSpoolError(
                    "recording_session_not_found",
                    "Session d'enregistrement introuvable",
                    status_code=404,
                )
            if session.state in {"queued", "processing", "completed", "ready"}:
                self._load_or_rebuild_state(verify=True)
                return {
                    "session_id": self.session_id,
                    "duration_seconds": (
                        (self.duration_ms + 999) // 1000
                        if self.duration_ms > 0
                        else max(0, int(duration_seconds))
                    ),
                    "received_chunks": self.chunk_count,
                    "idempotent": True,
                }
            if session.state != "capturing" or session.error == "recording_cancelled":
                raise RecordingSpoolError(
                    "recording_session_not_completable",
                    "La session ne peut pas être clôturée dans cet état",
                    context={"state": session.state},
                )
            self._load_or_rebuild_state(verify=True)
            if expected_chunks is not None and int(expected_chunks) != self.chunk_count:
                raise RecordingSpoolError(
                    "recording_chunks_incomplete",
                    "Des segments annoncés n'ont pas été reçus",
                    status_code=422,
                    context={
                        "expected_chunks": int(expected_chunks),
                        "received_chunks": self.chunk_count,
                    },
                )
            actual_duration = (
                (self.duration_ms + 999) // 1000
                if self.duration_ms > 0
                else max(0, int(duration_seconds))
            )
            if actual_duration > int(config.RECORDING_MAX_DURATION_MIN) * 60:
                update_recording_session(
                    self.session_id,
                    state="failed",
                    error="recording_duration_exceeded",
                )
                raise RecordingSpoolError(
                    "recording_duration_exceeded",
                    "Durée maximale d'enregistrement dépassée",
                    status_code=422,
                )
            if self.size_bytes < 3000:
                update_recording_session(
                    self.session_id,
                    state="failed",
                    error="recording_too_short",
                )
                raise RecordingSpoolError(
                    "recording_too_short",
                    "Audio trop court pour être transcrit",
                    status_code=422,
                )
            self._enqueue_locked(label=label, duration_seconds=actual_duration)
            return {
                "session_id": self.session_id,
                "duration_seconds": actual_duration,
                "received_chunks": self.chunk_count,
                "idempotent": False,
            }

    def enqueue(self, *, label: str, duration_seconds: int) -> str:
        """Réenfile un traitement existant sous verrou avec audio vérifié."""

        path = _validated_session_dir(self.path)
        with _session_lock(path):
            session = get_recording_session(self.session_id)
            if session is None:
                raise RecordingSpoolError(
                    "recording_session_not_found",
                    "Session d'enregistrement introuvable",
                    status_code=404,
                )
            if session.state not in {"capturing", "retry", "failed", "queued"}:
                raise RecordingSpoolError(
                    "recording_session_not_queueable",
                    "La session ne peut pas être enfilée dans cet état",
                    context={"state": session.state},
                )
            self._load_or_rebuild_state(verify=True)
            return self._enqueue_locked(label=label, duration_seconds=duration_seconds)

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
    if session is None or not session.retention_until or not session.spool_path:
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

    repaired = mark_dead_recording_sessions_failed()
    for session in list_pending_recording_sessions(limit=limit):
        if not session.spool_path:
            continue
        enqueue_ingestion_job(
            "recording",
            job_kind="recording_process",
            payload={"session_id": session.id, "duration_seconds": 0},
            dedupe_key=f"recording:{session.id}",
            max_attempts=RECORDING_PROCESSING_MAX_ATTEMPTS,
            require_binding=False,
        )
        repaired += 1
    return repaired


__all__ = [
    "RECORDING_MAX_SEGMENT_DURATION_MS",
    "RECORDING_PROCESSING_MAX_ATTEMPTS",
    "RECORDING_PROTOCOL_VERSION",
    "RecordingSpool",
    "RecordingSpoolError",
    "purge_expired_recordings",
    "purge_recording_audio",
    "reconcile_recording_sessions",
]
