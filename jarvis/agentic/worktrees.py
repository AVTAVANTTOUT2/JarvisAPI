"""Cycle de vie provider-neutral des worktrees agentiques JARVIS.

OpenCode ne crée, ne liste et ne détruit aucun worktree. Ce module est la
seule autorité de rétention : un répertoire n'est retiré que s'il est propre,
sauvegardé, hors run actif, hors PR ouverte, et strictement sous la racine
autorisée. Aucun commit WIP n'est créé implicitement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .redaction import redact_text


logger = logging.getLogger(__name__)

INVENTORY_VERSION = 1
INVENTORY_NAME = "inventory.json"
AUTHORIZED_RELATIVE_ROOT = Path(".jarvis") / "worktrees"
MAX_PATCH_BYTES = 256 * 1024
MAX_PROOF_FILES = 400
MAX_FILE_HASH_BYTES = 8 * 1024 * 1024
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_SECRET_NAME = re.compile(
    r"(token|secret|password|cookie|authorization|api[_-]?key|\.env|id_rsa|id_ed25519)",
    re.I,
)

STATE_ACTIVE = "active"
STATE_DELIVERED = "delivered"
STATE_FAILED = "failed"
STATE_CANCELLED = "cancelled"
STATE_RETAINED_DIRTY = "retained_dirty"
STATE_RETAINED_UNPUSHED = "retained_unpushed"
STATE_RETAINED_MANUAL = "retained_manual"
STATE_EXPIRED = "expired"
STATE_REMOVED = "removed"

REMOVABLE_STATES = frozenset({STATE_DELIVERED, STATE_CANCELLED, STATE_EXPIRED})
RETAINED_STATES = frozenset(
    {STATE_RETAINED_DIRTY, STATE_RETAINED_UNPUSHED, STATE_RETAINED_MANUAL}
)
KNOWN_STATES = frozenset(
    {
        STATE_ACTIVE,
        STATE_DELIVERED,
        STATE_FAILED,
        STATE_CANCELLED,
        STATE_RETAINED_DIRTY,
        STATE_RETAINED_UNPUSHED,
        STATE_RETAINED_MANUAL,
        STATE_EXPIRED,
        STATE_REMOVED,
    }
)

OpenPrChecker = Callable[[str], bool]
RunInUseChecker = Callable[[str], bool]
PathInUseChecker = Callable[[Path], bool]


class WorktreeLifecycleError(RuntimeError):
    """Erreur typée du cycle de vie, sans secret ni chemin utilisateur brut."""


@dataclass(frozen=True, slots=True)
class WorktreeRecord:
    worktree_id: str
    path: str
    branch: str
    repo_root: str
    head: str | None
    state: str
    created_at: str
    updated_at: str
    retention_reason: str | None = None
    run_id: str | None = None
    proof_path: str | None = None

    def to_mapping(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WorktreeInspection:
    record: WorktreeRecord
    clean: bool
    untracked: bool
    unique_unpushed: int
    removable: bool
    disk_bytes: int
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorktreeGcReport:
    inspected: int
    removed: int
    retained: int
    skipped: int
    dry_run: bool
    items: tuple[dict[str, Any], ...]
    metrics: Mapping[str, Any]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _config_int(name: str, default: int) -> int:
    try:
        import config as jarvis_config
    except ImportError:
        return default
    raw = getattr(jarvis_config, name, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def authorized_root(repo_root: Path) -> Path:
    root = repo_root.expanduser().resolve(strict=False) / AUTHORIZED_RELATIVE_ROOT
    return root


def inventory_path(repo_root: Path) -> Path:
    return authorized_root(repo_root) / INVENTORY_NAME


def _git(
    cwd: Path, args: Sequence[str], *, timeout: int = 30
) -> subprocess.CompletedProcess[str]:
    if not cwd.exists() and not cwd.is_symlink():
        return subprocess.CompletedProcess(
            args=["git", *args], returncode=1, stdout="", stderr=""
        )
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except OSError:
        return subprocess.CompletedProcess(
            args=["git", *args], returncode=1, stdout="", stderr=""
        )


def _is_symlink(path: Path) -> bool:
    try:
        return path.is_symlink()
    except OSError:
        return True


def _is_under(path: Path, root: Path) -> bool:
    try:
        resolved = path.resolve(strict=False)
        base = root.resolve(strict=False)
    except OSError:
        return False
    try:
        resolved.relative_to(base)
    except ValueError:
        return False
    return True


def _dir_size(path: Path) -> int:
    total = 0
    if not path.exists() or _is_symlink(path):
        return 0
    for current, dirnames, filenames in os.walk(path, followlinks=False):
        current_path = Path(current)
        dirnames[:] = [
            name
            for name in dirnames
            if not _is_symlink(current_path / name)
        ]
        for name in filenames:
            file_path = current_path / name
            if _is_symlink(file_path):
                continue
            try:
                total += file_path.stat().st_size
            except OSError:
                continue
    return total


def _worktree_status(workspace: Path) -> tuple[bool, bool]:
    probe = _git(workspace, ("status", "--porcelain", "--untracked-files=all"))
    if probe.returncode != 0:
        return False, True
    dirty = False
    untracked = False
    for line in probe.stdout.splitlines():
        if not line:
            continue
        dirty = True
        if line.startswith("??"):
            untracked = True
    return (not dirty), untracked


def _unique_unpushed(workspace: Path, branch: str) -> int:
    remote = _git(workspace, ("rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}"))
    if remote.returncode != 0:
        ahead = _git(workspace, ("rev-list", "--count", "HEAD"))
        try:
            return max(0, int((ahead.stdout or "0").strip() or "0"))
        except ValueError:
            return 1
    counted = _git(
        workspace,
        ("rev-list", "--count", f"{remote.stdout.strip()}..HEAD"),
    )
    try:
        return max(0, int((counted.stdout or "0").strip() or "0"))
    except ValueError:
        return 1


def _head_sha(workspace: Path) -> str | None:
    probe = _git(workspace, ("rev-parse", "HEAD"))
    if probe.returncode != 0:
        return None
    value = (probe.stdout or "").strip()
    return value or None


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if _is_symlink(path) or _is_symlink(path.parent):
        raise WorktreeLifecycleError("inventaire worktree ambigu")
    descriptor, temporary = tempfile.mkstemp(prefix=".inventory-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(0o600)
            path.parent.chmod(0o700)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


class WorktreeLifecycle:
    """Inventaire durable, réconciliation et GC borné des worktrees JARVIS."""

    def __init__(
        self,
        repo_root: Path,
        *,
        open_pr: OpenPrChecker | None = None,
        run_in_use: RunInUseChecker | None = None,
        path_in_use: PathInUseChecker | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        if _is_symlink(repo_root):
            raise WorktreeLifecycleError("racine Git symbolique refusée")
        self.repo_root = repo_root.expanduser().resolve(strict=True)
        self.root = authorized_root(self.repo_root)
        self.path = inventory_path(self.repo_root)
        self.open_pr = open_pr
        self.run_in_use = run_in_use
        self.path_in_use = path_in_use
        self.clock = clock or _utc_now
        self._guard = threading.RLock()

    def _empty_inventory(self) -> dict[str, Any]:
        return {"version": INVENTORY_VERSION, "worktrees": {}}

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty_inventory()
        if _is_symlink(self.path):
            raise WorktreeLifecycleError("inventaire worktree ambigu")
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("version") != INVENTORY_VERSION:
            raise WorktreeLifecycleError("inventaire worktree invalide")
        items = raw.get("worktrees")
        if not isinstance(items, dict):
            raise WorktreeLifecycleError("inventaire worktree invalide")
        return raw

    def _store(self, payload: Mapping[str, Any]) -> None:
        _atomic_write_json(self.path, payload)

    def record(
        self,
        *,
        worktree_id: str,
        path: Path,
        branch: str,
        state: str,
        run_id: str | None = None,
        retention_reason: str | None = None,
        proof_path: str | None = None,
        head: str | None = None,
    ) -> WorktreeRecord:
        if not _JOB_ID_RE.fullmatch(worktree_id):
            raise WorktreeLifecycleError("identifiant de worktree invalide")
        if state not in KNOWN_STATES:
            raise WorktreeLifecycleError("état de worktree inconnu")
        with self._guard:
            payload = self._load()
            existing = payload["worktrees"].get(worktree_id)
            created_at = self.clock()
            if isinstance(existing, Mapping) and isinstance(
                existing.get("created_at"), str
            ):
                created_at = str(existing["created_at"])
            record = WorktreeRecord(
                worktree_id=worktree_id,
                path=str(path),
                branch=branch,
                repo_root=str(self.repo_root),
                head=head or _head_sha(path),
                state=state,
                created_at=created_at,
                updated_at=self.clock(),
                retention_reason=retention_reason,
                run_id=run_id,
                proof_path=proof_path,
            )
            payload["worktrees"][worktree_id] = record.to_mapping()
            self._store(payload)
            return record

    def get(self, worktree_id: str) -> WorktreeRecord | None:
        with self._guard:
            item = self._load()["worktrees"].get(worktree_id)
        if not isinstance(item, Mapping):
            return None
        return self._from_mapping(item)

    def list_records(self) -> tuple[WorktreeRecord, ...]:
        with self._guard:
            items = self._load()["worktrees"]
        records: list[WorktreeRecord] = []
        for item in items.values():
            if isinstance(item, Mapping):
                records.append(self._from_mapping(item))
        return tuple(records)

    @staticmethod
    def _from_mapping(item: Mapping[str, Any]) -> WorktreeRecord:
        return WorktreeRecord(
            worktree_id=str(item.get("worktree_id") or ""),
            path=str(item.get("path") or ""),
            branch=str(item.get("branch") or ""),
            repo_root=str(item.get("repo_root") or ""),
            head=str(item["head"]) if isinstance(item.get("head"), str) else None,
            state=str(item.get("state") or STATE_RETAINED_MANUAL),
            created_at=str(item.get("created_at") or _utc_now()),
            updated_at=str(item.get("updated_at") or _utc_now()),
            retention_reason=(
                str(item["retention_reason"])
                if isinstance(item.get("retention_reason"), str)
                else None
            ),
            run_id=str(item["run_id"]) if isinstance(item.get("run_id"), str) else None,
            proof_path=(
                str(item["proof_path"])
                if isinstance(item.get("proof_path"), str)
                else None
            ),
        )

    def inspect(self, record: WorktreeRecord) -> WorktreeInspection:
        workspace = Path(record.path)
        reasons: list[str] = []
        if _is_symlink(workspace) or not _is_under(workspace, self.root):
            reasons.append("path_untrusted")
            return WorktreeInspection(
                record=record,
                clean=False,
                untracked=True,
                unique_unpushed=0,
                removable=False,
                disk_bytes=0,
                reasons=tuple(reasons),
            )
        if not workspace.exists():
            reasons.append("missing")
            removable = record.state in REMOVABLE_STATES | {STATE_REMOVED}
            return WorktreeInspection(
                record=record,
                clean=True,
                untracked=False,
                unique_unpushed=0,
                removable=removable,
                disk_bytes=0,
                reasons=tuple(reasons),
            )
        clean, untracked = _worktree_status(workspace)
        unique = _unique_unpushed(workspace, record.branch)
        if not clean:
            reasons.append("dirty")
        if untracked:
            reasons.append("untracked")
        if unique:
            reasons.append("unpushed")
        if self.open_pr is not None:
            try:
                if self.open_pr(record.branch):
                    reasons.append("open_pr")
            except Exception:
                reasons.append("pr_probe_failed")
        if record.run_id and self.run_in_use is not None:
            try:
                if self.run_in_use(record.run_id):
                    reasons.append("run_active")
            except Exception:
                reasons.append("run_probe_failed")
        if self.path_in_use is not None:
            try:
                if self.path_in_use(workspace):
                    reasons.append("process_in_use")
            except Exception:
                reasons.append("process_probe_failed")
        if record.state == STATE_ACTIVE:
            reasons.append("active")
        if record.state == STATE_FAILED:
            reasons.append("failed_retain")
        removable = (
            not reasons
            and record.state in REMOVABLE_STATES
            and clean
            and not untracked
            and unique == 0
        )
        return WorktreeInspection(
            record=record,
            clean=clean,
            untracked=untracked,
            unique_unpushed=unique,
            removable=removable,
            disk_bytes=_dir_size(workspace),
            reasons=tuple(reasons),
        )

    def _write_retention_proof(
        self, record: WorktreeRecord, inspection: WorktreeInspection
    ) -> str | None:
        workspace = Path(record.path)
        if not workspace.exists() or _is_symlink(workspace):
            return None
        proof_root = self.root / "retention" / record.worktree_id
        proof_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        files: list[dict[str, Any]] = []
        for current, dirnames, filenames in os.walk(workspace, followlinks=False):
            current_path = Path(current)
            dirnames[:] = [
                name
                for name in dirnames
                if name != ".git" and not _is_symlink(current_path / name)
            ]
            for name in filenames:
                if len(files) >= MAX_PROOF_FILES:
                    break
                file_path = current_path / name
                if _is_symlink(file_path) or _SECRET_NAME.search(name):
                    continue
                relative = str(file_path.relative_to(workspace))
                if _SECRET_NAME.search(relative):
                    continue
                try:
                    info = file_path.stat()
                    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_HASH_BYTES:
                        files.append({"path": relative, "skipped": True})
                        continue
                    digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
                except OSError:
                    continue
                files.append(
                    {
                        "path": relative,
                        "sha256": digest,
                        "size": int(info.st_size),
                    }
                )
        patch = _git(workspace, ("diff", "HEAD"), timeout=60)
        patch_text = redact_text(patch.stdout or "", max_chars=MAX_PATCH_BYTES)
        manifest = {
            "worktree_id": record.worktree_id,
            "branch": record.branch,
            "head": inspection.record.head or _head_sha(workspace),
            "state": record.state,
            "reasons": list(inspection.reasons),
            "created_at": self.clock(),
            "files": files,
            "patch_truncated": len(patch.stdout or "") > MAX_PATCH_BYTES,
        }
        _atomic_write_json(proof_root / "manifest.json", manifest)
        patch_path = proof_root / "changes.patch"
        patch_path.write_text(patch_text, encoding="utf-8")
        if os.name != "nt":
            patch_path.chmod(0o600)
        return str(proof_root)

    def reconcile(self) -> tuple[WorktreeRecord, ...]:
        updated: list[WorktreeRecord] = []
        for record in self.list_records():
            if record.state == STATE_REMOVED:
                continue
            inspection = self.inspect(record)
            if "missing" in inspection.reasons and record.state in REMOVABLE_STATES:
                updated.append(
                    self.record(
                        worktree_id=record.worktree_id,
                        path=Path(record.path),
                        branch=record.branch,
                        state=STATE_REMOVED,
                        run_id=record.run_id,
                        retention_reason="missing_after_reconcile",
                        head=record.head,
                    )
                )
                continue
            if inspection.reasons and record.state not in RETAINED_STATES | {
                STATE_FAILED,
                STATE_ACTIVE,
            }:
                state = STATE_RETAINED_MANUAL
                if "dirty" in inspection.reasons or "untracked" in inspection.reasons:
                    state = STATE_RETAINED_DIRTY
                elif "unpushed" in inspection.reasons:
                    state = STATE_RETAINED_UNPUSHED
                proof = self._write_retention_proof(record, inspection)
                updated.append(
                    self.record(
                        worktree_id=record.worktree_id,
                        path=Path(record.path),
                        branch=record.branch,
                        state=state,
                        run_id=record.run_id,
                        retention_reason=",".join(inspection.reasons),
                        proof_path=proof,
                        head=record.head,
                    )
                )
        return tuple(updated)

    def metrics(self, inspections: Sequence[WorktreeInspection] | None = None) -> dict[str, Any]:
        rows = inspections or tuple(self.inspect(item) for item in self.list_records())
        active = sum(1 for item in rows if item.record.state == STATE_ACTIVE)
        retained = sum(1 for item in rows if item.record.state in RETAINED_STATES)
        return {
            "active": active,
            "retained": retained,
            "total": len(rows),
            "disk_bytes": sum(item.disk_bytes for item in rows),
            "retention_reasons": sorted(
                {
                    item.record.retention_reason
                    for item in rows
                    if item.record.retention_reason
                }
            ),
        }

    def gc(self, *, dry_run: bool = True) -> WorktreeGcReport:
        max_count = _config_int("AGENTIC_WORKTREE_MAX_COUNT", 8)
        max_bytes = _config_int(
            "AGENTIC_WORKTREE_MAX_TOTAL_BYTES", 2 * 1024 * 1024 * 1024
        )
        with self._guard:
            self.reconcile()
            inspections = [self.inspect(item) for item in self.list_records()]
            actions: list[dict[str, Any]] = []
            removed = 0
            retained = 0
            skipped = 0
            live = [
                item for item in inspections if item.record.state != STATE_REMOVED
            ]
            pressure = len(live) > max_count or sum(
                item.disk_bytes for item in live
            ) > max_bytes
            for item in inspections:
                if item.record.state == STATE_REMOVED:
                    skipped += 1
                    continue
                if not item.removable:
                    if item.reasons:
                        if not dry_run:
                            self._retain(item)
                        retained += 1
                        actions.append(
                            {
                                "worktree_id": item.record.worktree_id,
                                "action": "retain",
                                "reasons": list(item.reasons),
                            }
                        )
                    else:
                        skipped += 1
                    continue
                if (
                    item.record.state not in REMOVABLE_STATES
                    and not pressure
                ):
                    skipped += 1
                    continue
                actions.append(
                    {
                        "worktree_id": item.record.worktree_id,
                        "action": "remove",
                        "reasons": [],
                    }
                )
                if dry_run:
                    removed += 1
                    continue
                if self._remove(item.record):
                    removed += 1
                else:
                    retained += 1
            if not dry_run:
                self._prune_missing()
            return WorktreeGcReport(
                inspected=len(inspections),
                removed=removed,
                retained=retained,
                skipped=skipped,
                dry_run=dry_run,
                items=tuple(actions),
                metrics=self.metrics(),
            )

    def _retain(self, item: WorktreeInspection) -> None:
        proof = None
        if "dirty" in item.reasons or "untracked" in item.reasons:
            proof = self._write_retention_proof(item.record, item)
        state = STATE_RETAINED_MANUAL
        if "dirty" in item.reasons or "untracked" in item.reasons:
            state = STATE_RETAINED_DIRTY
        elif "unpushed" in item.reasons:
            state = STATE_RETAINED_UNPUSHED
        self.record(
            worktree_id=item.record.worktree_id,
            path=Path(item.record.path),
            branch=item.record.branch,
            state=state,
            run_id=item.record.run_id,
            retention_reason=",".join(item.reasons),
            proof_path=proof,
            head=item.record.head,
        )

    def _remove(self, record: WorktreeRecord) -> bool:
        workspace = Path(record.path)
        if _is_symlink(workspace) or not _is_under(workspace, self.root):
            logger.warning("suppression worktree refusée: chemin non autorisé")
            return False
        if workspace.exists():
            removed = _git(
                self.repo_root,
                ("worktree", "remove", str(workspace)),
                timeout=60,
            )
            if removed.returncode != 0:
                logger.warning("git worktree remove a échoué pour %s", record.worktree_id)
                return False
        delete_branch = _git(
            self.repo_root,
            ("branch", "-d", record.branch),
            timeout=30,
        )
        if delete_branch.returncode != 0:
            logger.info("branche %s conservée", record.branch)
        self.record(
            worktree_id=record.worktree_id,
            path=workspace,
            branch=record.branch,
            state=STATE_REMOVED,
            run_id=record.run_id,
            retention_reason=None,
            head=record.head,
        )
        return True

    def _prune_missing(self) -> None:
        missing_ok = True
        for record in self.list_records():
            workspace = Path(record.path)
            if workspace.exists() or record.state != STATE_REMOVED:
                continue
            if _is_symlink(workspace) or not _is_under(workspace, self.root):
                missing_ok = False
        if missing_ok:
            _git(self.repo_root, ("worktree", "prune"), timeout=30)


def inspect_json(repo_root: Path) -> dict[str, Any]:
    manager = WorktreeLifecycle(repo_root)
    inspections = [manager.inspect(item) for item in manager.list_records()]
    return {
        "repo_root": str(manager.repo_root),
        "authorized_root": str(manager.root),
        "metrics": manager.metrics(inspections),
        "worktrees": [
            {
                **item.record.to_mapping(),
                "clean": item.clean,
                "untracked": item.untracked,
                "unique_unpushed": item.unique_unpushed,
                "removable": item.removable,
                "disk_bytes": item.disk_bytes,
                "reasons": list(item.reasons),
            }
            for item in inspections
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cycle de vie des worktrees agentiques")
    parser.add_argument(
        "command",
        choices=("inspect", "gc", "reconcile"),
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--repo",
        default=str(Path(__file__).resolve().parents[2]),
    )
    args = parser.parse_args(argv)
    repo = Path(args.repo)
    manager = WorktreeLifecycle(repo)
    if args.command == "inspect":
        payload = inspect_json(repo)
        print(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))
        return 0
    if args.command == "reconcile":
        updated = manager.reconcile()
        print(json.dumps([item.to_mapping() for item in updated], ensure_ascii=True))
        return 0
    report = manager.gc(dry_run=not args.apply)
    payload = {
        "inspected": report.inspected,
        "removed": report.removed,
        "retained": report.retained,
        "skipped": report.skipped,
        "dry_run": report.dry_run,
        "items": list(report.items),
        "metrics": dict(report.metrics),
    }
    print(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
