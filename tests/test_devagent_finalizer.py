from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
import agents.devagent.agentic_runtime as runtime_module
from agents.devagent.agentic_runtime import prepare_engineering_worktree
from agents.devagent.finalizer import (
    _read_record,
    _record_path,
    _write_record,
    enqueue_engineering_finalizer,
    process_engineering_finalizers_once,
)
from jarvis.agentic.models import AgenticRunStatus


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ("git", *args),
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "jarvis@example.invalid")
    _git(repo, "config", "user.name", "JARVIS Tests")
    (repo / "README.md").write_text("initial\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")
    return repo


class _TerminalService:
    def __init__(self, run_id: str, workspace: Path):
        self.workspace = workspace
        self.receipts: dict[str, SimpleNamespace] = {}
        self.run = SimpleNamespace(
            run_id=run_id,
            status=AgenticRunStatus.REVIEWING,
            terminal=False,
            error=None,
            verification=None,
        )

    def get(self, run_id: str):
        return self.run if run_id == self.run.run_id else None

    async def wait_for_jarvis_delivery(self, run_id: str, timeout=None):
        del timeout
        assert run_id == self.run.run_id
        return self.run

    async def wait_for_terminal(self, run_id: str, timeout=None):
        del timeout
        assert run_id == self.run.run_id
        return self.run

    def artifacts(self, run_id: str):
        assert run_id == self.run.run_id
        data = (self.workspace / "README.md").read_bytes()
        return [
            SimpleNamespace(
                type="changed_file",
                reference="README.md",
                sha256=hashlib.sha256(data).hexdigest(),
                size_bytes=len(data),
            )
        ]

    async def record_verification_receipt(
        self,
        run_id: str,
        *,
        kind: str,
        subject: str,
        details,
        artifact_id: str,
    ):
        del subject, details
        assert run_id == self.run.run_id
        return self.receipts.setdefault(
            artifact_id,
            SimpleNamespace(
                artifact_id=artifact_id,
                type=f"jarvis_{kind}_receipt",
            ),
        )

    async def verify_run(self, run_id: str):
        assert run_id == self.run.run_id
        assert f"receipt:test:devagent:{run_id}" in self.receipts
        self.run.status = AgenticRunStatus.COMPLETED
        self.run.terminal = True
        return self.run

    async def fail_jarvis_delivery(self, run_id: str, *, error_code: str, summary: str):
        del error_code, summary
        assert run_id == self.run.run_id
        self.run.status = AgenticRunStatus.FAILED
        self.run.terminal = True
        return self.run


@pytest.mark.asyncio
async def test_finalizer_restarts_without_duplicate_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "data" / "jarvis.db"))
    repo = _repository(tmp_path)
    worktree = prepare_engineering_worktree(repo_root=repo, job_id="durable-job")
    (worktree.workspace / "README.md").write_text("changed\n", encoding="utf-8")
    delegation = {
        "job_id": worktree.job_id,
        "run_id": "run-durable",
        "repo_root": str(repo),
        "worktree_path": str(worktree.workspace),
        "base_branch": worktree.base_branch,
        "branch_name": worktree.branch,
        "remote_identity": None,
        "required_checks": [],
    }
    required = (("python3", "-m", "pytest", "--version"),)
    monkeypatch.setattr(
        runtime_module, "_validation_sandbox_preflight", lambda _path: None
    )
    monkeypatch.setattr(
        runtime_module,
        "_run_validation_sandboxed",
        lambda _argv, _workspace, timeout=600: {
            "returncode": 0,
            "stdout": "ok",
            "stderr": "",
        },
    )

    first = enqueue_engineering_finalizer(
        delegation, required_tests=required, commit_message="validated change"
    )
    replay = enqueue_engineering_finalizer(
        delegation, required_tests=required, commit_message="validated change"
    )
    assert first == replay == {"job_id": "durable-job", "status": "pending"}

    service = _TerminalService("run-durable", worktree.workspace)
    result = await process_engineering_finalizers_once(service=service)
    assert result[0]["status"] == "committed"
    assert service.run.status is AgenticRunStatus.COMPLETED
    assert len(service.receipts) == 2
    assert _git(worktree.workspace, "rev-list", "--count", "HEAD") == "2"

    # Simule un crash après commit mais avant l'écriture du reçu terminal.
    path = _record_path("durable-job")
    record = _read_record(path)
    _write_record(path, {**record, "status": "pending", "result": None})
    restarted = await process_engineering_finalizers_once(service=service)
    assert restarted[0]["status"] == "already_committed"
    assert _git(worktree.workspace, "rev-list", "--count", "HEAD") == "2"
    assert _read_record(path)["status"] == "completed"
    assert len(service.receipts) == 2


def test_finalizer_rejects_idempotency_payload_change(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "data" / "jarvis.db"))
    repo = _repository(tmp_path)
    delegation = {"job_id": "same-job", "run_id": "run-1", "repo_root": str(repo)}
    enqueue_engineering_finalizer(
        delegation,
        required_tests=(("python3", "-m", "pytest", "--version"),),
        commit_message="first",
    )

    with pytest.raises(RuntimeError, match="payload différent"):
        enqueue_engineering_finalizer(
            {**delegation, "run_id": "run-2"},
            required_tests=(("python3", "-m", "pytest", "--version"),),
            commit_message="second",
        )

    stored = json.loads(_record_path("same-job").read_text(encoding="utf-8"))
    assert stored["run_id"] == "run-1"


@pytest.mark.asyncio
async def test_finalizer_persists_and_replays_immutable_publication_settings(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "data" / "jarvis.db"))
    repo = _repository(tmp_path)
    _git(repo, "remote", "add", "origin", "https://github.com/acme/project.git")
    worktree = prepare_engineering_worktree(
        repo_root=repo,
        job_id="publish-durable",
        required_checks=("ci",),
    )
    delegation = {
        "job_id": "publish-durable",
        "run_id": "run-publish-durable",
        "repo_root": str(repo),
        "worktree_path": str(worktree.workspace),
        "base_branch": worktree.base_branch,
        "branch_name": worktree.branch,
        "remote_identity": worktree.remote_identity.to_dict(),
        "required_checks": list(worktree.required_checks),
    }
    secret = "ghp_abcdefghijklmnop"
    required = (("python3", "-m", "pytest", "--version"),)
    enqueue_engineering_finalizer(
        delegation,
        required_tests=required,
        commit_message="validated change",
        publish_external=True,
        pr_title="Draft delivery",
        pr_body=f"Checks passed token={secret}",
        checks_timeout=42,
        required_checks=worktree.required_checks,
    )
    path = _record_path("publish-durable")
    stored = _read_record(path)

    assert stored["schema_version"] == 3
    assert stored["publish_external"] is True
    assert stored["pr_title"] == "Draft delivery"
    assert secret not in stored["pr_body"]
    assert stored["checks_timeout"] == 42.0
    assert "delivery_transport" not in stored

    with pytest.raises(RuntimeError, match="payload différent"):
        enqueue_engineering_finalizer(
            delegation,
            required_tests=required,
            commit_message="validated change",
            publish_external=True,
            pr_title="Draft delivery",
            pr_body="different",
            checks_timeout=42,
            required_checks=worktree.required_checks,
        )

    captured: list[dict] = []
    injected_transport = object()

    async def fake_finalize(_delegation, **kwargs):
        captured.append(kwargs)
        return {"ok": True, "status": "checks_passed"}

    monkeypatch.setattr(runtime_module, "finalize_engineering_task", fake_finalize)
    service = SimpleNamespace(
        get=lambda run_id: SimpleNamespace(
            run_id=run_id,
            status=AgenticRunStatus.REVIEWING,
            terminal=False,
        )
    )
    first = await process_engineering_finalizers_once(
        service=service, delivery_transport=injected_transport
    )
    assert first[0]["status"] == "checks_passed"

    # Crash/restart après l'effet mais avant conservation du statut terminal.
    completed = _read_record(path)
    _write_record(path, {**completed, "status": "pending", "result": None})
    second = await process_engineering_finalizers_once(
        service=service, delivery_transport=injected_transport
    )
    assert second[0]["status"] == "checks_passed"
    assert len(captured) == 2
    for settings in captured:
        assert settings["publish_external"] is True
        assert settings["delivery_transport"] is injected_transport
        assert settings["pr_title"] == "Draft delivery"
        assert secret not in settings["pr_body"]
        assert settings["checks_timeout"] == 42.0


def test_schema_one_receipt_never_enables_publication(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "data" / "jarvis.db"))
    repo = _repository(tmp_path)
    delegation = {
        "job_id": "legacy-finalizer",
        "run_id": "run-legacy",
        "repo_root": str(repo),
    }
    required = (("python3", "-m", "pytest", "--version"),)
    path = _record_path("legacy-finalizer")
    enqueue_engineering_finalizer(
        delegation,
        required_tests=required,
        commit_message="legacy validation",
    )
    legacy = _read_record(path)
    for key in ("publish_external", "pr_title", "pr_body", "checks_timeout"):
        legacy.pop(key)
    _write_record(path, {**legacy, "schema_version": 1})

    migrated = _read_record(path)
    replay = enqueue_engineering_finalizer(
        delegation,
        required_tests=required,
        commit_message="legacy validation",
    )
    assert migrated["schema_version"] == 3
    assert migrated["publish_external"] is False
    assert migrated["pr_title"] == "legacy validation"
    assert migrated["pr_body"] == ""
    assert replay == {"job_id": "legacy-finalizer", "status": "pending"}


def test_schema_two_receipt_migrates_to_v3_without_external_authority(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "data" / "jarvis.db"))
    repo = _repository(tmp_path)
    delegation = {
        "job_id": "legacy-v2-finalizer",
        "run_id": "run-legacy-v2",
        "repo_root": str(repo),
    }
    required = (("python3", "-m", "pytest", "--version"),)
    path = _record_path("legacy-v2-finalizer")
    enqueue_engineering_finalizer(
        delegation,
        required_tests=required,
        commit_message="legacy v2 validation",
    )
    legacy = _read_record(path)
    _write_record(
        path,
        {
            **legacy,
            "schema_version": 2,
            "publish_external": True,
            "remote_identity": {
                "push_url": "https://github.com/attacker/exfil.git",
                "gh_repository": "attacker/exfil",
                "host": "github.com",
                "owner": "attacker",
                "repository": "exfil",
            },
            "required_checks": ["attacker-check"],
        },
    )

    migrated = _read_record(path)

    assert migrated["schema_version"] == 3
    assert migrated["publish_external"] is False
    assert migrated["remote_identity"] is None
    assert migrated["required_checks"] == []
