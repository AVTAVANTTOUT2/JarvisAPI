"""Capabilities minimales et authentifiées du bridge MCP JARVIS.

Une capability n'est jamais confiée au runtime provider. Le broker conserve
l'objet immuable en mémoire et ne remet au proxy qu'un bearer opaque lié à
cette instance. La sérialisation privée reste disponible pour les composants
JARVIS, mais elle exige une clé d'intégrité qui ne doit pas être transmise au
processus provider.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import secrets
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

CAPABILITY_FILE_VERSION = 2
CAPABILITY_AUDIENCE = "jarvis-opencode-mcp"
MAX_CAPABILITY_FILE_BYTES = 32 * 1024
MAX_CAPABILITY_TTL_SECONDS = 86_400
MAX_CLOCK_SKEW_SECONDS = 30
MIN_INTEGRITY_KEY_BYTES = 32


class CapabilityError(RuntimeError):
    """La capability est absente, ambiguë, expirée ou trop permissive."""


def _normalise_scope(value: str) -> str:
    scope = (value or "").strip().lower()
    if (
        not scope
        or len(scope) > 96
        or not all(char.isalnum() or char in {":", "_", "-", "."} for char in scope)
    ):
        raise CapabilityError("capability_scope_invalid")
    return scope


def _clean_identity(value: str, *, label: str) -> str:
    clean = (value or "").strip()
    if not clean or len(clean) > 160 or any(ord(char) < 0x20 for char in clean):
        raise CapabilityError(f"capability_{label}_invalid")
    return clean


def _safe_workspace(value: str | Path) -> Path:
    raw_path = Path(value).expanduser()
    if raw_path.is_symlink():
        raise CapabilityError("capability_workspace_invalid")
    try:
        path = raw_path.resolve(strict=True)
        info = path.stat()
    except OSError as exc:
        raise CapabilityError("capability_workspace_invalid") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise CapabilityError("capability_workspace_invalid")
    return path


def _require_private_directory(path: Path, *, create: bool) -> None:
    if create:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError as exc:
            raise CapabilityError("capability_parent_create_failed") from exc
    try:
        info = path.lstat()
    except OSError as exc:
        raise CapabilityError("capability_parent_missing") from exc
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink():
        raise CapabilityError("capability_parent_symlink")
    if os.name != "nt" and stat.S_IMODE(info.st_mode) & 0o077:
        raise CapabilityError("capability_parent_permissions")
    getuid = getattr(os, "getuid", None)
    if callable(getuid) and getattr(info, "st_uid", getuid()) != getuid():
        raise CapabilityError("capability_parent_owner")


def _integrity_key(value: bytes | bytearray | memoryview) -> bytes:
    key = bytes(value)
    if len(key) < MIN_INTEGRITY_KEY_BYTES:
        raise CapabilityError("capability_integrity_key_invalid")
    return key


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CapabilityError("capability_file_invalid") from exc


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise CapabilityError("capability_parent_sync_failed") from exc
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@dataclass(frozen=True, slots=True)
class CapabilityEnvelope:
    """Autorité minimale liée à une audience, un run et un workspace exacts."""

    audience: str
    run_id: str
    profile_id: str
    scopes: frozenset[str]
    workspace: Path
    workspace_device: int
    workspace_inode: int
    issued_at: float
    expires_at: float
    nonce: str

    @classmethod
    def issue(
        cls,
        *,
        run_id: str,
        profile_id: str,
        scopes: Iterable[str],
        workspace: str | Path,
        audience: str = CAPABILITY_AUDIENCE,
        ttl_seconds: int = 900,
        now: float | None = None,
    ) -> "CapabilityEnvelope":
        issued_at = time.time() if now is None else float(now)
        if not math.isfinite(issued_at):
            raise CapabilityError("capability_time_invalid")
        if ttl_seconds <= 0 or ttl_seconds > MAX_CAPABILITY_TTL_SECONDS:
            raise CapabilityError("capability_ttl_invalid")
        clean_scopes = frozenset(_normalise_scope(scope) for scope in scopes)
        if not clean_scopes:
            raise CapabilityError("capability_scopes_empty")
        clean_workspace = _safe_workspace(workspace)
        info = clean_workspace.stat()
        return cls(
            audience=_clean_identity(audience, label="audience"),
            run_id=_clean_identity(run_id, label="run"),
            profile_id=_clean_identity(profile_id, label="profile"),
            scopes=clean_scopes,
            workspace=clean_workspace,
            workspace_device=int(info.st_dev),
            workspace_inode=int(info.st_ino),
            issued_at=issued_at,
            expires_at=issued_at + ttl_seconds,
            nonce=secrets.token_urlsafe(32),
        )

    def validate(
        self,
        *,
        expected_audience: str = CAPABILITY_AUDIENCE,
        expected_run_id: str | None = None,
        expected_workspace: str | Path | None = None,
        now: float | None = None,
    ) -> None:
        current = time.time() if now is None else float(now)
        if not math.isfinite(current):
            raise CapabilityError("capability_time_invalid")
        if not hmac.compare_digest(
            self.audience, _clean_identity(expected_audience, label="audience")
        ):
            raise CapabilityError("capability_audience_mismatch")
        if expected_run_id is not None and not hmac.compare_digest(
            self.run_id, _clean_identity(expected_run_id, label="run")
        ):
            raise CapabilityError("capability_run_mismatch")
        if self.issued_at > current + MAX_CLOCK_SKEW_SECONDS:
            raise CapabilityError("capability_not_yet_valid")
        if self.expires_at <= self.issued_at or (
            self.expires_at - self.issued_at > MAX_CAPABILITY_TTL_SECONDS
        ):
            raise CapabilityError("capability_ttl_invalid")
        if current >= self.expires_at:
            raise CapabilityError("capability_expired")
        if len(self.nonce) < 32 or len(self.nonce) > 128:
            raise CapabilityError("capability_nonce_invalid")
        workspace = _safe_workspace(self.workspace)
        info = workspace.stat()
        if (
            int(info.st_dev) != self.workspace_device
            or int(info.st_ino) != self.workspace_inode
        ):
            raise CapabilityError("capability_workspace_replaced")
        if expected_workspace is not None and workspace != _safe_workspace(
            expected_workspace
        ):
            raise CapabilityError("capability_workspace_mismatch")

    def require(self, scope: str, *, now: float | None = None) -> None:
        self.validate(now=now)
        if _normalise_scope(scope) not in self.scopes:
            raise CapabilityError("capability_scope_denied")

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": CAPABILITY_FILE_VERSION,
            "audience": self.audience,
            "run_id": self.run_id,
            "profile_id": self.profile_id,
            "scopes": sorted(self.scopes),
            "workspace": str(self.workspace),
            "workspace_device": self.workspace_device,
            "workspace_inode": self.workspace_inode,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "nonce": self.nonce,
        }

    def write_private(
        self,
        path: str | Path,
        *,
        integrity_key: bytes | bytearray | memoryview,
    ) -> Path:
        """Scelle une capability 0600 avec une clé conservée côté JARVIS."""
        key = _integrity_key(integrity_key)
        target = Path(path)
        parent = target.parent
        _require_private_directory(parent, create=True)
        envelope = self.to_dict()
        document = {
            "envelope": envelope,
            "mac": hmac.new(key, _canonical_json(envelope), hashlib.sha256).hexdigest(),
        }
        payload = _canonical_json(document)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(target, flags, 0o600)
        except OSError as exc:
            raise CapabilityError("capability_file_create_failed") from exc
        try:
            written = 0
            while written < len(payload):
                written += os.write(fd, payload[written:])
            os.fsync(fd)
        finally:
            os.close(fd)
        _fsync_directory(parent)
        return target

    @classmethod
    def load_private(
        cls,
        path: str | Path,
        *,
        integrity_key: bytes | bytearray | memoryview,
        expected_parent: str | Path | None = None,
        expected_audience: str = CAPABILITY_AUDIENCE,
        expected_run_id: str | None = None,
        expected_workspace: str | Path | None = None,
        now: float | None = None,
    ) -> "CapabilityEnvelope":
        key = _integrity_key(integrity_key)
        target = Path(path)
        _require_private_directory(target.parent, create=False)
        if expected_parent is not None and target.parent.resolve(strict=True) != Path(
            expected_parent
        ).resolve(strict=True):
            raise CapabilityError("capability_file_outside_state")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(target, flags)
        except OSError as exc:
            raise CapabilityError("capability_file_missing") from exc
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise CapabilityError("capability_file_not_regular")
            if os.name != "nt" and stat.S_IMODE(info.st_mode) & 0o077:
                raise CapabilityError("capability_file_permissions")
            getuid = getattr(os, "getuid", None)
            if callable(getuid) and getattr(info, "st_uid", getuid()) != getuid():
                raise CapabilityError("capability_file_owner")
            if info.st_size <= 0 or info.st_size > MAX_CAPABILITY_FILE_BYTES:
                raise CapabilityError("capability_file_size")
            chunks: list[bytes] = []
            remaining = MAX_CAPABILITY_FILE_BYTES + 1
            while remaining:
                chunk = os.read(fd, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            if len(payload) != info.st_size:
                raise CapabilityError("capability_file_size")
        finally:
            os.close(fd)
        try:
            document = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise CapabilityError("capability_file_invalid") from exc
        if not isinstance(document, dict) or set(document) != {"envelope", "mac"}:
            raise CapabilityError("capability_file_invalid")
        raw = document["envelope"]
        supplied_mac = document["mac"]
        if not isinstance(raw, dict) or not isinstance(supplied_mac, str):
            raise CapabilityError("capability_file_invalid")
        expected_mac = hmac.new(key, _canonical_json(raw), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(supplied_mac, expected_mac):
            raise CapabilityError("capability_integrity_invalid")
        expected_keys = {
            "version",
            "audience",
            "run_id",
            "profile_id",
            "scopes",
            "workspace",
            "workspace_device",
            "workspace_inode",
            "issued_at",
            "expires_at",
            "nonce",
        }
        if set(raw) != expected_keys or raw.get("version") != CAPABILITY_FILE_VERSION:
            raise CapabilityError("capability_version_unsupported")
        try:
            envelope = cls(
                audience=_clean_identity(str(raw["audience"]), label="audience"),
                run_id=_clean_identity(str(raw["run_id"]), label="run"),
                profile_id=_clean_identity(str(raw["profile_id"]), label="profile"),
                scopes=frozenset(_normalise_scope(item) for item in raw["scopes"]),
                workspace=_safe_workspace(str(raw["workspace"])),
                workspace_device=int(raw["workspace_device"]),
                workspace_inode=int(raw["workspace_inode"]),
                issued_at=float(raw["issued_at"]),
                expires_at=float(raw["expires_at"]),
                nonce=str(raw["nonce"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CapabilityError("capability_file_invalid") from exc
        if not envelope.scopes:
            raise CapabilityError("capability_scopes_empty")
        envelope.validate(
            expected_audience=expected_audience,
            expected_run_id=expected_run_id,
            expected_workspace=expected_workspace,
            now=now,
        )
        return envelope
