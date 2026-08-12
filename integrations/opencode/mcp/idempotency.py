"""Journal idempotent crash-safe, borné et local à un run MCP."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from .capabilities import CapabilityError

try:  # pragma: no cover - la branche Windows est exercée en CI Windows
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

try:  # pragma: no cover - la branche POSIX est exercée sur Linux/macOS
    import msvcrt
except ImportError:  # pragma: no cover
    msvcrt = None  # type: ignore[assignment]

JOURNAL_VERSION = 1
MAX_RECORDS = 512
MAX_JOURNAL_BYTES = 2 * 1024 * 1024
_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.RLock] = {}


def canonical_digest(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CapabilityError("idempotency_payload_invalid") from exc
    return hashlib.sha256(encoded).hexdigest()


def _thread_lock(path: Path) -> threading.RLock:
    key = os.path.abspath(os.fspath(path))
    with _LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.RLock())


def _validate_owned_private_file(fd: int, *, prefix: str) -> os.stat_result:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        raise CapabilityError(f"{prefix}_not_regular")
    if os.name != "nt" and stat.S_IMODE(info.st_mode) & 0o077:
        raise CapabilityError(f"{prefix}_permissions")
    getuid = getattr(os, "getuid", None)
    if callable(getuid) and getattr(info, "st_uid", getuid()) != getuid():
        raise CapabilityError(f"{prefix}_owner")
    return info


def _lock_fd(fd: int) -> None:
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_EX)
        return
    if msvcrt is None:  # pragma: no cover - plateformes Python non supportées
        raise CapabilityError("idempotency_lock_unsupported")
    if os.fstat(fd).st_size == 0:  # pragma: no cover - Windows
        os.write(fd, b"\0")
        os.fsync(fd)
    os.lseek(fd, 0, os.SEEK_SET)
    locking = getattr(msvcrt, "locking", None)
    lock_mode = getattr(msvcrt, "LK_LOCK", None)
    if not callable(locking) or lock_mode is None:  # pragma: no cover
        raise CapabilityError("idempotency_lock_unsupported")
    locking(fd, lock_mode, 1)


def _unlock_fd(fd: int) -> None:
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return
    if msvcrt is not None:  # pragma: no cover - Windows
        os.lseek(fd, 0, os.SEEK_SET)
        locking = getattr(msvcrt, "locking", None)
        unlock_mode = getattr(msvcrt, "LK_UNLCK", None)
        if not callable(locking) or unlock_mode is None:
            raise CapabilityError("idempotency_lock_unsupported")
        locking(fd, unlock_mode, 1)


class IdempotencyJournal:
    """Réserve durablement une clé avant l'effet, puis scelle son résultat.

    Un enregistrement ``pending`` n'est jamais rejoué automatiquement : après
    un crash, un opérateur JARVIS doit fournir le résultat observé via
    :meth:`recover_pending`. Cette règle fail-closed évite un second effet.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._thread_lock = _thread_lock(self.path)
        self._ensure_parent()
        with self._exclusive_lock():
            self._read_unlocked()

    def _ensure_parent(self) -> None:
        parent = self.path.parent
        try:
            parent.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError as exc:
            raise CapabilityError("idempotency_parent_create_failed") from exc
        try:
            info = parent.lstat()
        except OSError as exc:
            raise CapabilityError("idempotency_parent_missing") from exc
        if not stat.S_ISDIR(info.st_mode) or parent.is_symlink():
            raise CapabilityError("idempotency_parent_symlink")
        if os.name != "nt" and stat.S_IMODE(info.st_mode) & 0o077:
            raise CapabilityError("idempotency_parent_permissions")
        getuid = getattr(os, "getuid", None)
        if callable(getuid) and getattr(info, "st_uid", getuid()) != getuid():
            raise CapabilityError("idempotency_parent_owner")

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        with self._thread_lock:
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            try:
                fd = os.open(self.lock_path, flags, 0o600)
            except OSError as exc:
                raise CapabilityError("idempotency_lock_open_failed") from exc
            locked = False
            try:
                _validate_owned_private_file(fd, prefix="idempotency_lock_file")
                _lock_fd(fd)
                locked = True
                yield
            finally:
                if locked:
                    _unlock_fd(fd)
                os.close(fd)

    def _read_unlocked(self) -> dict[str, dict[str, Any]]:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(self.path, flags)
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise CapabilityError("idempotency_file_open_failed") from exc
        try:
            info = _validate_owned_private_file(fd, prefix="idempotency_file")
            if info.st_size <= 0 or info.st_size > MAX_JOURNAL_BYTES:
                raise CapabilityError("idempotency_file_size")
            chunks: list[bytes] = []
            remaining = MAX_JOURNAL_BYTES + 1
            while remaining:
                chunk = os.read(fd, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            if len(payload) != info.st_size:
                raise CapabilityError("idempotency_file_size")
        finally:
            os.close(fd)
        try:
            document = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise CapabilityError("idempotency_file_invalid") from exc
        if (
            not isinstance(document, dict)
            or set(document) != {"version", "records"}
            or document.get("version") != JOURNAL_VERSION
            or not isinstance(document.get("records"), dict)
        ):
            raise CapabilityError("idempotency_file_invalid")
        records = document["records"]
        if len(records) > MAX_RECORDS:
            raise CapabilityError("idempotency_journal_full")
        for key, record in records.items():
            self._validate_record(key, record)
        return records

    @staticmethod
    def _validate_record(key: Any, record: Any) -> None:
        if (
            not isinstance(key, str)
            or not key
            or len(key) > 160
            or any(ord(c) < 0x20 for c in key)
        ):
            raise CapabilityError("idempotency_record_invalid")
        if not isinstance(record, dict):
            raise CapabilityError("idempotency_record_invalid")
        state = record.get("state")
        expected = {"digest", "state", "reserved_at"}
        if state == "completed":
            expected |= {"result", "completed_at", "recovered"}
        if set(record) != expected or state not in {"pending", "completed"}:
            raise CapabilityError("idempotency_record_invalid")
        digest = record.get("digest")
        if not isinstance(digest, str) or len(digest) != 64:
            raise CapabilityError("idempotency_record_invalid")
        if not isinstance(record.get("reserved_at"), (int, float)):
            raise CapabilityError("idempotency_record_invalid")
        if state == "completed" and (
            not isinstance(record.get("result"), dict)
            or not isinstance(record.get("completed_at"), (int, float))
            or not isinstance(record.get("recovered"), bool)
        ):
            raise CapabilityError("idempotency_record_invalid")

    def _persist_unlocked(self, records: Mapping[str, Mapping[str, Any]]) -> None:
        try:
            payload = json.dumps(
                {"version": JOURNAL_VERSION, "records": records},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise CapabilityError("idempotency_result_invalid") from exc
        if len(payload) > MAX_JOURNAL_BYTES:
            raise CapabilityError("idempotency_journal_full")
        temporary = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
        )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(temporary, flags, 0o600)
        except OSError as exc:
            raise CapabilityError("idempotency_file_create_failed") from exc
        try:
            written = 0
            while written < len(payload):
                written += os.write(fd, payload[written:])
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.replace(temporary, self.path)
            if os.name != "nt":
                parent_fd = os.open(
                    self.path.parent,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                )
                try:
                    os.fsync(parent_fd)
                finally:
                    os.close(parent_fd)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _clean_key(key: str) -> str:
        clean = (key or "").strip()
        if not clean or len(clean) > 160 or any(ord(char) < 0x20 for char in clean):
            raise CapabilityError("idempotency_key_invalid")
        return clean

    @staticmethod
    def _replay_or_raise(
        existing: Mapping[str, Any], *, digest: str
    ) -> tuple[dict[str, Any], bool]:
        if existing.get("digest") != digest:
            raise CapabilityError("idempotency_key_conflict")
        if existing.get("state") == "pending":
            raise CapabilityError("idempotency_operation_pending")
        result = existing.get("result")
        if existing.get("state") != "completed" or not isinstance(result, dict):
            raise CapabilityError("idempotency_record_invalid")
        return dict(result), True

    def execute(
        self,
        *,
        key: str,
        payload: Any,
        operation: Callable[[], dict[str, Any]],
    ) -> tuple[dict[str, Any], bool]:
        clean_key = self._clean_key(key)
        digest = canonical_digest(payload)
        with self._exclusive_lock():
            records = self._read_unlocked()
            existing = records.get(clean_key)
            if existing is not None:
                return self._replay_or_raise(existing, digest=digest)
            if len(records) >= MAX_RECORDS:
                raise CapabilityError("idempotency_journal_full")
            records[clean_key] = {
                "digest": digest,
                "state": "pending",
                "reserved_at": time.time(),
            }
            self._persist_unlocked(records)

        result = operation()
        if not isinstance(result, dict):
            raise CapabilityError("idempotency_result_invalid")

        with self._exclusive_lock():
            records = self._read_unlocked()
            pending = records.get(clean_key)
            if not isinstance(pending, dict) or pending.get("digest") != digest:
                raise CapabilityError("idempotency_reservation_lost")
            if pending.get("state") != "pending":
                return self._replay_or_raise(pending, digest=digest)
            records[clean_key] = {
                **pending,
                "state": "completed",
                "result": result,
                "completed_at": time.time(),
                "recovered": False,
            }
            self._persist_unlocked(records)
        return dict(result), False

    def recover_pending(
        self,
        *,
        key: str,
        payload: Any,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Scelle explicitement le résultat observé après un crash ambigu."""
        clean_key = self._clean_key(key)
        digest = canonical_digest(payload)
        clean_result = dict(result)
        with self._exclusive_lock():
            records = self._read_unlocked()
            pending = records.get(clean_key)
            if not isinstance(pending, dict):
                raise CapabilityError("idempotency_pending_missing")
            if pending.get("digest") != digest:
                raise CapabilityError("idempotency_key_conflict")
            if pending.get("state") != "pending":
                raise CapabilityError("idempotency_already_completed")
            records[clean_key] = {
                **pending,
                "state": "completed",
                "result": clean_result,
                "completed_at": time.time(),
                "recovered": True,
            }
            self._persist_unlocked(records)
        return clean_result

    def inspect(self) -> dict[str, dict[str, Any]]:
        """Retourne une copie sûre pour le diagnostic opérateur JARVIS."""
        with self._exclusive_lock():
            records = self._read_unlocked()
        return json.loads(json.dumps(records, allow_nan=False))
