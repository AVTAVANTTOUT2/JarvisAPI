"""Finalisation durable des délégations techniques asynchrones.

Le runtime modifie un worktree, mais seul JARVIS exécute les validations et
crée le commit. Les reçus persistés permettent de reprendre après redémarrage
sans répéter un commit déjà créé.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import stat
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence

import config
from agents.devagent.delivery import (
    normalize_remote_identity,
    normalize_required_checks,
)
from database.core import current_profile_id, use_profile
from jarvis.agentic.redaction import redact_text

if TYPE_CHECKING:
    from agents.devagent.delivery import EngineeringDeliveryTransport

_RECORD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}\.json$")
_process_lock: asyncio.Lock | None = None


def _lock() -> asyncio.Lock:
    global _process_lock
    if _process_lock is None:
        _process_lock = asyncio.Lock()
    return _process_lock


def _state_root() -> Path:
    configured = Path(str(config.DB_PATH)).expanduser()
    database_path = (
        configured if configured.is_absolute() else Path(config.BASE_DIR) / configured
    )
    root = database_path.resolve(strict=False).parent / "agentic-finalizers"
    parent = root.parent
    if parent.is_symlink():
        raise RuntimeError("racine de données symbolique refusée")
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink():
        raise RuntimeError("racine de finalisation symbolique refusée")
    root.mkdir(mode=0o700, exist_ok=True)
    if os.name != "nt":
        os.chmod(root, 0o700)
        info = root.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise RuntimeError("racine de finalisation non privée")
    return root.resolve(strict=True)


def _record_path(job_id: str) -> Path:
    name = f"{job_id}.json"
    if not _RECORD_RE.fullmatch(name):
        raise ValueError("job_id de finalisation invalide")
    return _state_root() / name


def _normalize_record(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") not in {1, 2, 3}:
        raise RuntimeError("reçu de finalisation invalide")
    normalized = dict(value)
    if normalized["schema_version"] == 1:
        # Les reçus historiques n'autorisaient aucune publication externe.
        normalized.update(
            {
                "publish_external": False,
                "pr_title": redact_text(
                    normalized.get("commit_message", ""), max_chars=240
                ),
                "pr_body": "",
                "checks_timeout": 900.0,
            }
        )
    if normalized["schema_version"] in {1, 2}:
        # Les reçus antérieurs ne liaient ni origin ni checks CI. Ils restent
        # finalisables localement, mais toute publication est désactivée.
        normalized.update(
            {
                "schema_version": 3,
                "publish_external": False,
                "branch_name": str(
                    normalized.get("branch_name")
                    or f"jarvis/agentic/{normalized.get('job_id', '')}"
                ),
                "base_branch": str(normalized.get("base_branch") or "HEAD"),
                "remote_identity": None,
                "required_checks": [],
            }
        )
    publish_external = normalized.get("publish_external")
    pr_title = normalized.get("pr_title")
    pr_body = normalized.get("pr_body")
    checks_timeout = normalized.get("checks_timeout")
    base_branch = normalized.get("base_branch")
    branch_name = normalized.get("branch_name")
    raw_remote_identity = normalized.get("remote_identity")
    raw_required_checks = normalized.get("required_checks")
    if not isinstance(raw_required_checks, list):
        raise RuntimeError("paramètres de publication invalides")
    try:
        required_checks = normalize_required_checks(tuple(raw_required_checks))
        remote_identity = (
            None
            if raw_remote_identity is None
            else normalize_remote_identity(raw_remote_identity)
        )
    except (RuntimeError, ValueError) as exc:
        raise RuntimeError("paramètres de publication invalides") from exc
    if (
        not isinstance(publish_external, bool)
        or not isinstance(pr_title, str)
        or not isinstance(pr_body, str)
        or len(pr_title) > 240
        or len(pr_body.encode("utf-8")) > 20_000
        or isinstance(checks_timeout, bool)
        or not isinstance(checks_timeout, (int, float))
        or not 1 <= float(checks_timeout) <= 7_200
        or not isinstance(base_branch, str)
        or not base_branch.strip()
        or len(base_branch) > 200
        or any(ord(char) < 0x20 for char in base_branch)
        or not isinstance(branch_name, str)
        or not branch_name.strip()
        or len(branch_name) > 200
        or any(ord(char) < 0x20 for char in branch_name)
        or (publish_external and (remote_identity is None or not required_checks))
    ):
        raise RuntimeError("paramètres de publication invalides")
    normalized["checks_timeout"] = float(checks_timeout)
    normalized["base_branch"] = base_branch.strip()
    normalized["branch_name"] = branch_name.strip()
    normalized["remote_identity"] = (
        remote_identity.to_dict() if remote_identity is not None else None
    )
    normalized["required_checks"] = list(required_checks)
    return normalized


def _read_record(path: Path) -> dict[str, Any]:
    root = _state_root()
    if (
        path.parent.resolve(strict=True) != root
        or path.is_symlink()
        or not path.is_file()
    ):
        raise RuntimeError("reçu de finalisation ambigu")
    if os.name != "nt":
        info = path.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise RuntimeError("reçu de finalisation non privé")
    value = json.loads(path.read_text(encoding="utf-8"))
    return _normalize_record(value)


def _write_record(path: Path, value: Mapping[str, Any]) -> None:
    root = _state_root()
    if path.parent.resolve(strict=True) != root or path.is_symlink():
        raise RuntimeError("cible de finalisation ambiguë")
    temporary = root / f".{path.stem}.{uuid.uuid4().hex}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        payload = json.dumps(dict(value), ensure_ascii=False, sort_keys=True).encode()
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    if os.name != "nt":
        os.chmod(path, 0o600)
    directory_fd = os.open(root, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _redacted_bounded(value: object, *, max_chars: int, max_bytes: int) -> str:
    text = redact_text(value, max_chars=max_chars)
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def enqueue_engineering_finalizer(
    delegation: Mapping[str, Any],
    *,
    required_tests: Sequence[str | Sequence[str]],
    commit_message: str,
    publish_external: bool = False,
    pr_title: str | None = None,
    pr_body: str = "",
    checks_timeout: float = 900.0,
    required_checks: Sequence[str] = (),
) -> dict[str, Any]:
    """Persiste idempotemment le travail de validation/commit à effectuer."""

    from agents.devagent.agentic_runtime import _validation_argv

    job_id = str(delegation.get("job_id") or "").strip()
    run_id = str(delegation.get("run_id") or "").strip()
    repo_root = str(delegation.get("repo_root") or "").strip()
    if not job_id or not run_id or not repo_root or not required_tests:
        raise ValueError("finalisation: job, run, dépôt et validations requis")
    if not isinstance(publish_external, bool):
        raise ValueError("finalisation: publication invalide")
    if (
        isinstance(checks_timeout, bool)
        or not isinstance(checks_timeout, (int, float))
        or not 1 <= float(checks_timeout) <= 7_200
    ):
        raise ValueError("finalisation: délai CI invalide")
    stable_required_checks = normalize_required_checks(required_checks)
    base_branch = str(delegation.get("base_branch") or "").strip()
    branch_name = str(delegation.get("branch_name") or "").strip()
    raw_remote_identity = delegation.get("remote_identity")
    remote_identity = (
        None
        if raw_remote_identity is None
        else normalize_remote_identity(raw_remote_identity)
    )
    if publish_external and (
        remote_identity is None
        or not stable_required_checks
        or not base_branch
        or not branch_name
    ):
        raise ValueError("finalisation: origin et checks CI requis")
    base_branch = base_branch or "HEAD"
    branch_name = branch_name or f"jarvis/agentic/{job_id}"
    resolved_repo = Path(repo_root).expanduser().resolve(strict=True)
    configured_workspace = str(delegation.get("worktree_path") or "").strip()
    workspace = (
        Path(configured_workspace).expanduser().resolve(strict=True)
        if configured_workspace
        else (resolved_repo / ".jarvis" / "worktrees" / "agentic" / job_id).resolve(
            strict=False
        )
    )
    validation_workspace = workspace if workspace.is_dir() else resolved_repo
    canonical_tests = [
        list(_validation_argv(command, validation_workspace))
        for command in required_tests
    ]
    safe_commit_message = _redacted_bounded(
        commit_message, max_chars=120, max_bytes=480
    )
    safe_pr_title = _redacted_bounded(
        " ".join(str(pr_title or safe_commit_message).splitlines()),
        max_chars=240,
        max_bytes=960,
    )
    safe_pr_body = _redacted_bounded(pr_body, max_chars=20_000, max_bytes=20_000)
    record = {
        "schema_version": 3,
        "job_id": job_id,
        "run_id": run_id,
        "profile_id": current_profile_id(),
        "repo_root": str(resolved_repo),
        "worktree_path": str(workspace),
        "base_branch": base_branch,
        "branch_name": branch_name,
        "remote_identity": (
            remote_identity.to_dict() if remote_identity is not None else None
        ),
        "required_checks": list(stable_required_checks),
        "required_tests": canonical_tests,
        "commit_message": safe_commit_message,
        "publish_external": publish_external,
        "pr_title": safe_pr_title,
        "pr_body": safe_pr_body,
        "checks_timeout": float(checks_timeout),
        "status": "pending",
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        "attempts": 0,
    }
    path = _record_path(job_id)
    if path.exists() or path.is_symlink():
        existing = _read_record(path)
        immutable = (
            "job_id",
            "run_id",
            "profile_id",
            "repo_root",
            "worktree_path",
            "base_branch",
            "branch_name",
            "remote_identity",
            "required_checks",
            "required_tests",
            "commit_message",
            "publish_external",
            "pr_title",
            "pr_body",
            "checks_timeout",
        )
        if any(existing.get(key) != record.get(key) for key in immutable):
            raise RuntimeError("rejeu de finalisation avec un payload différent")
        return {"job_id": job_id, "status": existing.get("status", "pending")}
    _write_record(path, record)
    return {"job_id": job_id, "status": "pending"}


def fail_engineering_finalizer_launch(
    job_id: str,
    *,
    run_id: str,
    error_code: str,
) -> None:
    """Ferme une intention dont le runtime a explicitement refusé le départ."""

    path = _record_path(str(job_id))
    record = _read_record(path)
    if str(record.get("run_id") or "") != str(run_id):
        raise RuntimeError("run de finalisation incohérent")
    if record.get("status") in {"completed", "failed"}:
        return
    _write_record(
        path,
        {
            **record,
            "status": "failed",
            "error_code": redact_text(error_code, max_chars=120),
            "updated_at": datetime.now(UTC).isoformat(),
        },
    )


async def process_engineering_finalizers_once(
    *,
    service: Any | None = None,
    delivery_transport: EngineeringDeliveryTransport | None = None,
) -> list[dict[str, Any]]:
    """Finalise les runs terminaux; les autres restent durablement en attente."""

    from agents.devagent.agentic_runtime import (
        finalize_engineering_task,
        get_runtime_service,
    )

    selected_service = service or get_runtime_service()
    processed: list[dict[str, Any]] = []
    async with _lock():
        for path in sorted(_state_root().glob("*.json")):
            if not _RECORD_RE.fullmatch(path.name):
                continue
            record: dict[str, Any] = {}
            try:
                record = _read_record(path)
                if record.get("status") in {"completed", "failed"}:
                    continue
                profile_id = str(record.get("profile_id") or "").strip()
                with use_profile(profile_id):
                    run = selected_service.get(str(record["run_id"]))
                run_status = str(
                    getattr(
                        getattr(run, "status", None),
                        "value",
                        getattr(run, "status", ""),
                    )
                )
                if run is None or (
                    not bool(getattr(run, "terminal", False))
                    and run_status != "reviewing"
                ):
                    continue
                running = {
                    **record,
                    "status": "running",
                    "attempts": int(record.get("attempts") or 0) + 1,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
                _write_record(path, running)
                with use_profile(profile_id):
                    result = await finalize_engineering_task(
                        running,
                        required_tests=tuple(
                            tuple(item) for item in running["required_tests"]
                        ),
                        commit_message=str(running["commit_message"]),
                        repo_root=Path(str(running["repo_root"])),
                        service=selected_service,
                        publish_external=bool(running["publish_external"]),
                        delivery_transport=delivery_transport,
                        pr_title=str(running["pr_title"]),
                        pr_body=str(running["pr_body"]),
                        checks_timeout=float(running["checks_timeout"]),
                    )
                terminal_status = "completed" if result.get("ok") else "failed"
                finished = {
                    **running,
                    "status": terminal_status,
                    "updated_at": datetime.now(UTC).isoformat(),
                    "result": result,
                }
                _write_record(path, finished)
                processed.append({"job_id": running["job_id"], **result})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                try:
                    failed = {
                        **record,
                        "status": "pending",
                        "updated_at": datetime.now(UTC).isoformat(),
                        "last_error": redact_text(exc, max_chars=300),
                    }
                    _write_record(path, failed)
                except Exception:
                    pass
    return processed


async def run_engineering_finalizer_worker(
    stop: asyncio.Event,
    *,
    interval_s: float = 30.0,
) -> None:
    """Worker borné, réveillé au démarrage puis périodiquement."""

    while not stop.is_set():
        try:
            await process_engineering_finalizers_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            # L'état durable conserve le diagnostic; le prochain tick réessaie.
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(1.0, interval_s))
        except TimeoutError:
            continue


__all__ = [
    "enqueue_engineering_finalizer",
    "fail_engineering_finalizer_launch",
    "process_engineering_finalizers_once",
    "run_engineering_finalizer_worker",
]
