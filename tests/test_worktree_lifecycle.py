"""Cycle de vie des worktrees agentiques : rétention avant toute suppression."""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

from jarvis.agentic.worktrees import (
    STATE_DELIVERED,
    WorktreeLifecycle,
    inspect_json,
)


def _run(cwd: Path, *args: str) -> None:
    result = subprocess.run(
        args,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0


def _init_repo(path: Path) -> Path:
    path.mkdir()
    _run(path, "git", "init", "-b", "main")
    _run(path, "git", "config", "user.email", "jarvis@test.local")
    _run(path, "git", "config", "user.name", "JARVIS Test")
    (path / "README.md").write_text("init\n", encoding="utf-8")
    _run(path, "git", "add", "README.md")
    _run(path, "git", "commit", "-m", "init")
    return path


def _add_worktree(repo: Path, job_id: str) -> tuple[Path, str]:
    target = repo / ".jarvis" / "worktrees" / job_id
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    branch = f"jarvis/agentic/{job_id}"
    _run(
        repo,
        "git",
        "worktree",
        "add",
        "-b",
        branch,
        str(target),
        "HEAD",
    )
    return target, branch


def _push_branch(repo: Path, workspace: Path, branch: str, tmp_path: Path) -> None:
    bare = tmp_path / f"{repo.name}-origin.git"
    subprocess.run(
        ["git", "clone", "--bare", str(repo), str(bare)],
        check=True,
        capture_output=True,
        text=True,
    )
    _run(workspace, "git", "remote", "add", "origin", str(bare))
    _run(workspace, "git", "push", "-u", "origin", branch)


def test_delivered_clean_pushed_worktree_is_removed(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    workspace, branch = _add_worktree(repo, "clean1")
    _push_branch(repo, workspace, branch, tmp_path)
    manager = WorktreeLifecycle(repo)
    manager.record(
        worktree_id="clean1",
        path=workspace,
        branch=branch,
        state=STATE_DELIVERED,
    )
    report = manager.gc(dry_run=False)
    assert report.removed == 1
    assert not workspace.exists()
    assert manager.get("clean1") is not None
    assert manager.get("clean1").state == "removed"


def test_unpushed_commit_is_retained(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    workspace, branch = _add_worktree(repo, "ahead1")
    (workspace / "note.md").write_text("local\n", encoding="utf-8")
    _run(workspace, "git", "add", "note.md")
    _run(workspace, "git", "commit", "-m", "local")
    manager = WorktreeLifecycle(repo)
    manager.record(
        worktree_id="ahead1",
        path=workspace,
        branch=branch,
        state=STATE_DELIVERED,
    )
    report = manager.gc(dry_run=False)
    assert report.removed == 0
    assert workspace.exists()
    assert manager.get("ahead1").state == "retained_unpushed"


def test_dirty_tracked_file_is_retained(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    workspace, branch = _add_worktree(repo, "dirty1")
    (workspace / "README.md").write_text("changed\n", encoding="utf-8")
    manager = WorktreeLifecycle(repo)
    manager.record(
        worktree_id="dirty1",
        path=workspace,
        branch=branch,
        state=STATE_DELIVERED,
    )
    report = manager.gc(dry_run=False)
    assert report.removed == 0
    assert workspace.exists()
    assert manager.get("dirty1").state == "retained_dirty"
    assert manager.get("dirty1").proof_path


def test_untracked_file_is_retained(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    workspace, branch = _add_worktree(repo, "extra1")
    (workspace / "scratch.txt").write_text("tmp\n", encoding="utf-8")
    manager = WorktreeLifecycle(repo)
    manager.record(
        worktree_id="extra1",
        path=workspace,
        branch=branch,
        state=STATE_DELIVERED,
    )
    report = manager.gc(dry_run=False)
    assert report.removed == 0
    assert (workspace / "scratch.txt").exists()


def test_symlink_and_outside_path_are_never_removed(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    outside = tmp_path / "outside"
    outside.mkdir()
    hostile = repo / ".jarvis" / "worktrees" / "link1"
    hostile.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.symlink(outside, hostile)
    manager = WorktreeLifecycle(repo)
    manager.record(
        worktree_id="link1",
        path=hostile,
        branch="jarvis/agentic/link1",
        state=STATE_DELIVERED,
    )
    manager.record(
        worktree_id="escape1",
        path=tmp_path / "not-under-root",
        branch="jarvis/agentic/escape1",
        state=STATE_DELIVERED,
    )
    report = manager.gc(dry_run=False)
    assert report.removed == 0
    assert hostile.is_symlink()
    inspection = inspect_json(repo)
    reasons = {
        item["worktree_id"]: item["reasons"] for item in inspection["worktrees"]
    }
    assert "path_untrusted" in reasons["link1"]
    assert "path_untrusted" in reasons["escape1"]


def test_open_pr_and_active_run_block_removal(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    workspace, branch = _add_worktree(repo, "busy1")
    _push_branch(repo, workspace, branch, tmp_path)
    manager = WorktreeLifecycle(
        repo,
        open_pr=lambda _branch: True,
        run_in_use=lambda _run: True,
        path_in_use=lambda _path: True,
    )
    manager.record(
        worktree_id="busy1",
        path=workspace,
        branch=branch,
        state=STATE_DELIVERED,
        run_id="run-1",
    )
    report = manager.gc(dry_run=False)
    assert report.removed == 0
    assert workspace.exists()


def test_gc_is_dry_run_by_default(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    workspace, branch = _add_worktree(repo, "dry1")
    _push_branch(repo, workspace, branch, tmp_path)
    manager = WorktreeLifecycle(repo)
    manager.record(
        worktree_id="dry1",
        path=workspace,
        branch=branch,
        state=STATE_DELIVERED,
    )
    report = manager.gc()
    assert report.dry_run is True
    assert report.removed == 1
    assert workspace.exists()


def test_concurrent_gc_does_not_delete_twice(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    workspace, branch = _add_worktree(repo, "race1")
    _push_branch(repo, workspace, branch, tmp_path)
    manager = WorktreeLifecycle(repo)
    manager.record(
        worktree_id="race1",
        path=workspace,
        branch=branch,
        state=STATE_DELIVERED,
    )
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            manager.gc(dry_run=False)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()
    assert errors == []
    assert manager.get("race1").state == "removed"


def test_reconcile_after_restart_keeps_inventory(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    workspace, branch = _add_worktree(repo, "boot1")
    first = WorktreeLifecycle(repo)
    first.record(
        worktree_id="boot1",
        path=workspace,
        branch=branch,
        state="active",
    )
    second = WorktreeLifecycle(repo)
    second.reconcile()
    record = second.get("boot1")
    assert record is not None
    assert record.branch == branch
    assert workspace.exists()
