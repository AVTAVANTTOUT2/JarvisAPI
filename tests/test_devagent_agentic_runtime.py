from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import agents.devagent.agentic_runtime as runtime_module
from agents.devagent.agentic_runtime import (
    AgenticRuntimeUnavailable,
    ChangedArtifact,
    RuntimeDelegationResult,
    _validation_argv,
    delegate_devagent_iteration,
    delegate_engineering_task,
    finalize_engineering_task,
    legacy_fallback_enabled,
    prepare_engineering_worktree,
    resolve_runtime,
    select_test_command,
    validate_and_commit_engineering_worktree,
)


@dataclass
class _Run:
    id: str
    status: object
    phase: str = "running"
    error: object | None = None
    verification: object | None = None


class _Service:
    def __init__(self, *, available: bool = True, artifacts=None) -> None:
        self.available = available
        self.artifact_items = artifacts
        self.calls: list[dict] = []
        self._runs: dict[str, _Run] = {}

    def resolve_runtime_id(self, requested=None):
        return "runtime-test" if self.available else None

    async def create_and_start(self, **kwargs):
        self.calls.append(kwargs)
        key = kwargs.get("idempotency_key") or str(len(self.calls))
        run = self._runs.setdefault(
            key,
            _Run(
                id=f"run-{len(self._runs) + 1}", status=SimpleNamespace(value="queued")
            ),
        )
        return run

    async def create_run(self, **kwargs):
        self.calls.append(kwargs)
        key = kwargs.get("idempotency_key") or str(len(self.calls))
        return self._runs.setdefault(
            key,
            _Run(
                id=f"run-{len(self._runs) + 1}", status=SimpleNamespace(value="created")
            ),
        )

    async def wait_for_terminal(self, run_id, timeout=None):
        return _Run(
            id=run_id,
            status=SimpleNamespace(value="completed"),
            phase="completed",
            verification=SimpleNamespace(summary="preuves validées"),
        )

    def artifacts(self, run_id):
        if self.artifact_items is not None:
            return self.artifact_items
        return [
            SimpleNamespace(type="changed_file", reference="src/app.py"),
            SimpleNamespace(type="runtime_result", reference=f"agentic://{run_id}"),
        ]

    async def cancel(self, run_id):  # pragma: no cover - contrat du fake
        return None


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ("git", *args),
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "jarvis@example.invalid")
    _git(repo, "config", "user.name", "JARVIS Tests")
    (repo / "README.md").write_text("# test\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")
    return repo


def _artifact(workspace: Path, relative_path: str) -> ChangedArtifact:
    data = (workspace / relative_path).read_bytes()
    return ChangedArtifact(
        path=relative_path,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
    )


@pytest.fixture
def allowed_validation_sandbox(monkeypatch):
    monkeypatch.setattr(
        runtime_module, "_validation_sandbox_preflight", lambda _path: None
    )
    monkeypatch.setattr(
        runtime_module,
        "_run_validation_sandboxed",
        lambda _argv, _workspace, timeout=600: {
            "returncode": 0,
            "stdout": "validation sandboxée réussie",
            "stderr": "",
        },
    )


def test_runtime_resolution_and_legacy_fallback_are_explicit(monkeypatch):
    monkeypatch.setattr("config.AGENTIC_RUNTIME", "auto")
    monkeypatch.setattr("config.AGENTIC_RUNTIME_FALLBACK", "disabled")
    with pytest.raises(AgenticRuntimeUnavailable):
        resolve_runtime(_Service(available=False))
    assert not legacy_fallback_enabled()

    monkeypatch.setattr("config.AGENTIC_RUNTIME_FALLBACK", "legacy")
    assert legacy_fallback_enabled()


@pytest.mark.asyncio
async def test_devagent_delegation_keeps_git_and_tests_on_jarvis(tmp_path, monkeypatch):
    monkeypatch.setattr("config.AGENTIC_RUNTIME", "auto")
    service = _Service()
    outcome = await delegate_devagent_iteration(
        project_id=7,
        spec={
            "project_name": "Example",
            "project_type": "cli",
            "stack": ["python"],
            "constraints": ["hors ligne"],
            "acceptance_criteria": ["tests verts"],
        },
        state={"iteration": 2},
        workspace=tmp_path,
        service=service,
    )

    assert outcome.succeeded
    assert outcome.changed_files == ("src/app.py",)
    call = service.calls[0]
    assert call["runtime_id"] == "runtime-test"
    assert call["workspace"] == tmp_path.resolve()
    assert call["permissions"] == ("workspace:read", "workspace:write")
    assert "tests:run" not in call["permissions"]
    request = call["selected_context"]["request"]
    assert "Ne lance aucune commande Git" in request
    assert "JARVIS l'exécutera indépendamment" in request


@pytest.mark.parametrize(
    ("files", "stack", "expected"),
    [
        ((), ["python"], ("python3", "-m", "pytest", "-q")),
        (("pnpm-lock.yaml", "package.json"), [], ("pnpm", "test")),
        (("Cargo.toml",), [], ("cargo", "test")),
        (("go.mod",), [], ("go", "test", "./...")),
    ],
)
def test_test_policy_is_deterministic(tmp_path, files, stack, expected):
    for name in files:
        (tmp_path / name).write_text("{}", encoding="utf-8")
    assert select_test_command(tmp_path, {"stack": stack}) == expected


def test_worktree_is_idempotent_for_same_job(tmp_path):
    repo = _repository(tmp_path)
    first = prepare_engineering_worktree(repo_root=repo, job_id="stable-job")
    second = prepare_engineering_worktree(
        repo_root=repo,
        job_id="stable-job",
        reuse_existing=True,
    )
    assert second.workspace == first.workspace
    assert second.branch == "jarvis/agentic/stable-job"
    assert second.workspace != repo


def test_engineering_delivery_requires_validation_evidence(tmp_path):
    repo = _repository(tmp_path)
    worktree = prepare_engineering_worktree(repo_root=repo, job_id="evidence-job")
    (worktree.workspace / "README.md").write_text("changed\n", encoding="utf-8")
    result = validate_and_commit_engineering_worktree(
        worktree,
        required_tests=(),
        commit_message="must not commit",
    )
    assert result["status"] == "validation_missing"
    assert result["ok"] is False


@pytest.mark.parametrize(
    "command",
    [
        ("/tmp/evil/python", "-m", "pytest"),
        ("python", "-c", "print('bypass')"),
        ("python", "-m", "pip", "install", "anything"),
        ("npm", "exec", "arbitrary"),
        ("npm", "run", "postinstall"),
        ("pnpm", "run", "arbitrary-user-script"),
        ("pytest", "/Users/example/.ssh"),
        ("python3", "-m", "pytest", "-p", "malicious_plugin"),
        ("cargo", "test", "--manifest-path", "/Users/example/Cargo.toml"),
    ],
)
def test_validation_policy_rejects_interpreters_and_eval_bypasses(command):
    with pytest.raises(ValueError):
        _validation_argv(command)


@pytest.mark.parametrize("unsafe_kind", ["symlink", "secret_file"])
def test_engineering_delivery_refuses_unsafe_generated_files(tmp_path, unsafe_kind):
    repo = _repository(tmp_path)
    worktree = prepare_engineering_worktree(
        repo_root=repo, job_id=f"unsafe-{unsafe_kind.replace('_', '-')}"
    )
    head_before = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=worktree.workspace,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if unsafe_kind == "symlink":
        outside = tmp_path / "outside.txt"
        outside.write_text("private\n", encoding="utf-8")
        (worktree.workspace / "generated-link").symlink_to(outside)
        expected = "generated_symlink_refused"
    else:
        (worktree.workspace / ".env").write_text(
            "TOKEN=not-committed\n", encoding="utf-8"
        )
        expected = "sensitive_file_refused"

    result = validate_and_commit_engineering_worktree(
        worktree,
        required_tests=(("python3", "-m", "pytest", "--version"),),
        commit_message="must not commit unsafe output",
    )

    assert result["ok"] is False
    assert result["status"] == expected
    head_after = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=worktree.workspace,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head_after == head_before


def test_security_scan_blocks_workflow_before_validation(tmp_path, monkeypatch):
    repo = _repository(tmp_path)
    worktree = prepare_engineering_worktree(repo_root=repo, job_id="unsafe-workflow")
    workflow = worktree.workspace / ".github" / "workflows" / "publish.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("on: push\n", encoding="utf-8")
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        runtime_module,
        "_run_validation_sandboxed",
        lambda argv, _workspace, timeout=600: calls.append(tuple(argv)),
    )

    result = validate_and_commit_engineering_worktree(
        worktree,
        required_tests=(("python3", "-m", "pytest", "--version"),),
        commit_message="must not execute",
        verified_artifacts=(
            _artifact(worktree.workspace, ".github/workflows/publish.yml"),
        ),
    )

    assert result["status"] == "sensitive_path_refused"
    assert calls == []


def test_security_scan_refuses_the_101st_changed_file_before_validation(
    tmp_path, monkeypatch
):
    repo = _repository(tmp_path)
    worktree = prepare_engineering_worktree(repo_root=repo, job_id="too-many-files")
    generated = worktree.workspace / "generated"
    generated.mkdir()
    for index in range(101):
        (generated / f"file-{index:03d}.py").write_text("VALUE = 1\n", encoding="utf-8")
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        runtime_module,
        "_run_validation_sandboxed",
        lambda argv, _workspace, timeout=600: calls.append(tuple(argv)),
    )

    result = validate_and_commit_engineering_worktree(
        worktree,
        required_tests=(("python3", "-m", "pytest", "--version"),),
        commit_message="must not execute",
    )

    assert result["status"] == "changed_file_count_exceeded"
    assert calls == []


def test_security_scan_bounds_total_changed_bytes_before_validation(
    tmp_path, monkeypatch
):
    repo = _repository(tmp_path)
    worktree = prepare_engineering_worktree(repo_root=repo, job_id="too-many-bytes")
    generated = worktree.workspace / "generated"
    generated.mkdir()
    (generated / "one.txt").write_bytes(b"1234")
    (generated / "two.txt").write_bytes(b"5678")
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(runtime_module, "_MAX_CHANGED_TOTAL_BYTES", 7)
    monkeypatch.setattr(
        runtime_module,
        "_run_validation_sandboxed",
        lambda argv, _workspace, timeout=600: calls.append(tuple(argv)),
    )

    result = validate_and_commit_engineering_worktree(
        worktree,
        required_tests=(("python3", "-m", "pytest", "--version"),),
        commit_message="must not execute",
    )

    assert result["status"] == "generated_files_too_large"
    assert calls == []


def test_all_validation_commands_are_scanned_before_any_execution(
    tmp_path, monkeypatch
):
    repo = _repository(tmp_path)
    worktree = prepare_engineering_worktree(repo_root=repo, job_id="command-preflight")
    target = worktree.workspace / "src" / "app.py"
    target.parent.mkdir()
    target.write_text("VALUE = 1\n", encoding="utf-8")
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        runtime_module, "_validation_sandbox_preflight", lambda _path: None
    )
    monkeypatch.setattr(
        runtime_module,
        "_run_validation_sandboxed",
        lambda argv, _workspace, timeout=600: calls.append(tuple(argv)),
    )

    result = validate_and_commit_engineering_worktree(
        worktree,
        required_tests=(
            ("python3", "-m", "pytest", "--version"),
            ("npm", "run", "postinstall"),
        ),
        commit_message="must not execute",
        verified_artifacts=(_artifact(worktree.workspace, "src/app.py"),),
    )

    assert result["status"] == "script package hors politique JARVIS"
    assert calls == []


def test_verified_digest_must_match_before_validation(tmp_path, monkeypatch):
    repo = _repository(tmp_path)
    worktree = prepare_engineering_worktree(repo_root=repo, job_id="digest-mismatch")
    (worktree.workspace / "README.md").write_text("changed\n", encoding="utf-8")
    actual = _artifact(worktree.workspace, "README.md")
    forged = ChangedArtifact(
        path=actual.path,
        sha256="0" * 64,
        size_bytes=actual.size_bytes,
    )
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        runtime_module,
        "_run_validation_sandboxed",
        lambda argv, _workspace, timeout=600: calls.append(tuple(argv)),
    )

    result = validate_and_commit_engineering_worktree(
        worktree,
        required_tests=(("python3", "-m", "pytest", "--version"),),
        commit_message="must not execute",
        verified_artifacts=(forged,),
    )

    assert result["status"] == "artifact_digest_mismatch"
    assert calls == []


def test_validation_fails_closed_when_os_sandbox_is_unavailable(tmp_path, monkeypatch):
    repo = _repository(tmp_path)
    worktree = prepare_engineering_worktree(repo_root=repo, job_id="sandbox-required")
    target = worktree.workspace / "src" / "app.py"
    target.parent.mkdir()
    target.write_text("VALUE = 1\n", encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    def unavailable(_workspace):
        raise RuntimeError("validation_sandbox_unavailable")

    monkeypatch.setattr(runtime_module, "_validation_sandbox_preflight", unavailable)
    monkeypatch.setattr(
        runtime_module,
        "_run_validation_sandboxed",
        lambda argv, _workspace, timeout=600: calls.append(tuple(argv)),
    )
    result = validate_and_commit_engineering_worktree(
        worktree,
        required_tests=(("python3", "-m", "pytest", "--version"),),
        commit_message="must not execute",
        verified_artifacts=(_artifact(worktree.workspace, "src/app.py"),),
    )

    assert result["status"] == "validation_sandbox_unavailable"
    assert calls == []


def test_staged_digest_mismatch_rolls_back_the_index(
    tmp_path, monkeypatch, allowed_validation_sandbox
):
    repo = _repository(tmp_path)
    worktree = prepare_engineering_worktree(repo_root=repo, job_id="staged-mismatch")
    target = worktree.workspace / "src" / "app.py"
    target.parent.mkdir()
    target.write_text("VALUE = 1\n", encoding="utf-8")
    artifact = _artifact(worktree.workspace, "src/app.py")
    monkeypatch.setattr(
        runtime_module,
        "_staged_artifacts",
        lambda _workspace: (
            ChangedArtifact(
                path=artifact.path,
                sha256="f" * 64,
                size_bytes=artifact.size_bytes,
            ),
        ),
    )

    result = validate_and_commit_engineering_worktree(
        worktree,
        required_tests=(("python3", "-m", "pytest", "--version"),),
        commit_message="must roll back",
        verified_artifacts=(artifact,),
    )
    index = subprocess.run(
        ("git", "diff", "--cached", "--quiet"),
        cwd=worktree.workspace,
        check=False,
    )

    assert result["status"] == "staged_manifest_mismatch"
    assert result["index_rolled_back"] is True
    assert index.returncode == 0


def test_allowed_delivery_commits_once_and_is_idempotent(
    tmp_path, allowed_validation_sandbox
):
    repo = _repository(tmp_path)
    worktree = prepare_engineering_worktree(
        repo_root=repo, job_id="idempotent-delivery"
    )
    target = worktree.workspace / "src" / "app.py"
    target.parent.mkdir()
    target.write_text("VALUE = 1\n", encoding="utf-8")
    artifact = _artifact(worktree.workspace, "src/app.py")

    first = validate_and_commit_engineering_worktree(
        worktree,
        required_tests=(("python3", "-m", "pytest", "--version"),),
        commit_message="validated once",
        verified_artifacts=(artifact,),
    )
    head_after_first = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=worktree.workspace,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    second = validate_and_commit_engineering_worktree(
        worktree,
        required_tests=(("python3", "-m", "pytest", "--version"),),
        commit_message="validated once",
        verified_artifacts=(artifact,),
    )
    head_after_second = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=worktree.workspace,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert first["status"] == "committed"
    assert second["status"] == "already_committed"
    assert head_after_second == head_after_first


def test_local_commit_disables_repository_hooks_and_signing(
    tmp_path, allowed_validation_sandbox
):
    repo = _repository(tmp_path)
    worktree = prepare_engineering_worktree(repo_root=repo, job_id="safe-commit")
    target = worktree.workspace / "src" / "app.py"
    target.parent.mkdir()
    target.write_text("VALUE = 1\n", encoding="utf-8")
    artifact = _artifact(worktree.workspace, "src/app.py")
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    marker = tmp_path / "hook-executed"
    hook = hooks / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 1\n", encoding="utf-8")
    hook.chmod(0o700)
    _git(worktree.workspace, "config", "core.hooksPath", str(hooks))
    _git(worktree.workspace, "config", "commit.gpgsign", "true")

    result = validate_and_commit_engineering_worktree(
        worktree,
        required_tests=(("python3", "-m", "pytest", "--version"),),
        commit_message="safe commit",
        verified_artifacts=(artifact,),
    )

    assert result["status"] == "committed"
    assert not marker.exists()


def test_commit_failure_rolls_back_the_index(
    tmp_path, monkeypatch, allowed_validation_sandbox
):
    repo = _repository(tmp_path)
    worktree = prepare_engineering_worktree(repo_root=repo, job_id="rollback-index")
    target = worktree.workspace / "src" / "app.py"
    target.parent.mkdir()
    target.write_text("VALUE = 1\n", encoding="utf-8")
    artifact = _artifact(worktree.workspace, "src/app.py")
    real_run_isolated = runtime_module.run_isolated

    def fail_commit(command, cwd, timeout=120, env=None):
        argv = tuple(command)
        if argv and argv[0] == "git" and "commit" in argv:
            return {"returncode": 1, "stdout": "", "stderr": "commit refused"}
        return real_run_isolated(command, cwd=cwd, timeout=timeout, env=env)

    monkeypatch.setattr(runtime_module, "run_isolated", fail_commit)
    result = validate_and_commit_engineering_worktree(
        worktree,
        required_tests=(("python3", "-m", "pytest", "--version"),),
        commit_message="must roll back",
        verified_artifacts=(artifact,),
    )
    index = subprocess.run(
        ("git", "diff", "--cached", "--quiet"),
        cwd=worktree.workspace,
        check=False,
    )

    assert result["status"] == "commit_failed"
    assert result["index_rolled_back"] is True
    assert index.returncode == 0


@pytest.mark.asyncio
async def test_engineering_delegation_reuses_worktree_and_run_by_idempotency(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("config.AGENTIC_RUNTIME", "auto")
    repo = _repository(tmp_path)
    service = _Service()

    first = await delegate_engineering_task(
        title="Fix quality",
        user_request="Corrige la régression",
        idempotency_key="quality:stable",
        repo_root=repo,
        auto_start=False,
        service=service,
    )
    second = await delegate_engineering_task(
        title="Fix quality",
        user_request="Corrige la régression",
        idempotency_key="quality:stable",
        repo_root=repo,
        auto_start=False,
        service=service,
    )

    assert second["job_id"] == first["job_id"]
    assert second["run_id"] == first["run_id"]
    assert second["worktree_path"] == first["worktree_path"]
    assert first["legacy"] is False


@pytest.mark.asyncio
async def test_external_delivery_settings_are_persisted_for_async_finalizer(
    tmp_path, monkeypatch
):
    from agents.devagent.finalizer import _read_record, _record_path

    monkeypatch.setattr("config.AGENTIC_RUNTIME", "auto")
    monkeypatch.setattr("config.DB_PATH", str(tmp_path / "data" / "jarvis.db"))
    repo = _repository(tmp_path)
    _git(repo, "remote", "add", "origin", "https://github.com/acme/project.git")

    result = await delegate_engineering_task(
        title="Publish",
        user_request="Change",
        idempotency_key="publish:durable",
        repo_root=repo,
        auto_start=True,
        wait_for_completion=False,
        required_tests=(("python3", "-m", "pytest", "--version"),),
        publish_external=True,
        pr_body="Checks passed",
        checks_timeout=45,
        required_checks=("ci", "security"),
        service=_Service(),
    )
    record = _read_record(_record_path(result["job_id"]))

    assert result["finalizer"]["status"] == "pending"
    assert record["publish_external"] is True
    assert record["pr_title"] == "Publish"
    assert record["pr_body"] == "Checks passed"
    assert record["checks_timeout"] == 45.0
    assert record["schema_version"] == 3
    assert record["base_branch"] == "main"
    assert record["branch_name"] == result["branch_name"]
    assert record["required_checks"] == ["ci", "security"]
    assert record["remote_identity"]["gh_repository"] == "acme/project"


@pytest.mark.asyncio
async def test_auto_pr_config_only_applies_to_automated_contexts(tmp_path, monkeypatch):
    from agents.devagent.finalizer import _read_record, _record_path

    monkeypatch.setattr("config.AGENTIC_RUNTIME", "auto")
    monkeypatch.setattr("config.DEVAGENT_AUTO_PR", True)
    monkeypatch.setattr("config.DB_PATH", str(tmp_path / "data" / "jarvis.db"))
    repo = _repository(tmp_path)
    _git(repo, "remote", "add", "origin", "https://github.com/acme/project.git")
    required = (("python3", "-m", "pytest", "--version"),)
    configured_checks = (
        "Production dependencies (pip install)",
        "macOS 26 (Python + app/widget Release)",
        "Tests Python (pytest)",
        "Bibliothèque de vues desktop (tests + typecheck)",
        "Frontend unifié (tests + build)",
        "Android (assemble + tests + lint)",
    )
    monkeypatch.setattr("config.BASE_DIR", repo)
    monkeypatch.setattr("config.DEVAGENT_REQUIRED_CHECKS", configured_checks)
    monkeypatch.setattr("config.DEVAGENT_REQUIRED_CHECKS_EXPLICIT", False)

    automated = await delegate_engineering_task(
        title="Automated",
        user_request="Change",
        idempotency_key="publish:automated",
        repo_root=repo,
        auto_start=True,
        required_tests=required,
        origin="scheduler",
        interaction_mode="scheduler",
        channel="scheduler",
        service=_Service(),
    )
    direct = await delegate_engineering_task(
        title="Direct",
        user_request="Change",
        idempotency_key="publish:direct",
        repo_root=repo,
        auto_start=True,
        required_tests=required,
        origin="user",
        interaction_mode="loop",
        channel="loop",
        service=_Service(),
    )
    explicit_disabled = await delegate_engineering_task(
        title="Disabled",
        user_request="Change",
        idempotency_key="publish:disabled",
        repo_root=repo,
        auto_start=True,
        required_tests=required,
        origin="scheduler",
        interaction_mode="scheduler",
        channel="scheduler",
        publish_external=False,
        service=_Service(),
    )

    automated_record = _read_record(_record_path(automated["job_id"]))
    direct_record = _read_record(_record_path(direct["job_id"]))
    disabled_record = _read_record(_record_path(explicit_disabled["job_id"]))
    assert automated_record["publish_external"] is True
    assert automated_record["required_checks"] == list(configured_checks)
    assert direct_record["publish_external"] is False
    assert direct_record["required_checks"] == []
    assert disabled_record["publish_external"] is False
    assert disabled_record["required_checks"] == []


@pytest.mark.asyncio
async def test_automated_third_party_repo_requires_explicit_check_policy(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("config.AGENTIC_RUNTIME", "auto")
    monkeypatch.setattr("config.DEVAGENT_AUTO_PR", True)
    monkeypatch.setattr("config.BASE_DIR", tmp_path)
    monkeypatch.setattr("config.DEVAGENT_REQUIRED_CHECKS", ("jarvis-ci",))
    monkeypatch.setattr("config.DEVAGENT_REQUIRED_CHECKS_EXPLICIT", False)
    repo = _repository(tmp_path)

    with pytest.raises(ValueError, match="checks CI requis"):
        await delegate_engineering_task(
            title="Third party",
            user_request="Change",
            idempotency_key="publish:third-party",
            repo_root=repo,
            auto_start=True,
            required_tests=(("python3", "-m", "pytest", "--version"),),
            origin="scheduler",
            interaction_mode="scheduler",
            channel="scheduler",
            service=_Service(),
        )


@pytest.mark.asyncio
async def test_automated_third_party_repo_accepts_explicit_check_policy(
    tmp_path, monkeypatch
):
    from agents.devagent.finalizer import _read_record, _record_path

    monkeypatch.setattr("config.AGENTIC_RUNTIME", "auto")
    monkeypatch.setattr("config.DEVAGENT_AUTO_PR", True)
    monkeypatch.setattr("config.DB_PATH", str(tmp_path / "data" / "jarvis.db"))
    monkeypatch.setattr("config.BASE_DIR", tmp_path)
    monkeypatch.setattr("config.DEVAGENT_REQUIRED_CHECKS", ("third-party-ci",))
    monkeypatch.setattr("config.DEVAGENT_REQUIRED_CHECKS_EXPLICIT", True)
    repo = _repository(tmp_path)
    _git(repo, "remote", "add", "origin", "https://github.com/acme/project.git")

    result = await delegate_engineering_task(
        title="Third party configured",
        user_request="Change",
        idempotency_key="publish:third-party-configured",
        repo_root=repo,
        auto_start=True,
        required_tests=(("python3", "-m", "pytest", "--version"),),
        origin="scheduler",
        interaction_mode="scheduler",
        channel="scheduler",
        service=_Service(),
    )

    record = _read_record(_record_path(result["job_id"]))
    assert record["required_checks"] == ["third-party-ci"]


@pytest.mark.asyncio
async def test_async_finalization_runs_tests_before_commit(
    tmp_path, monkeypatch, allowed_validation_sandbox
):
    monkeypatch.setattr("config.AGENTIC_RUNTIME", "auto")
    repo = _repository(tmp_path)
    worktree = prepare_engineering_worktree(repo_root=repo, job_id="finalize-job")
    tests = worktree.workspace / "tests"
    tests.mkdir()
    (tests / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    artifact = _artifact(worktree.workspace, "tests/test_ok.py")
    result = await finalize_engineering_task(
        {"job_id": "finalize-job", "run_id": "run-final", "legacy": False},
        required_tests=(("python3", "-m", "pytest", "-q"),),
        commit_message="validated change",
        repo_root=repo,
        service=_Service(artifacts=[artifact]),
    )
    assert result["ok"] is True
    assert result["status"] == "committed"
    log = subprocess.run(
        ("git", "log", "-1", "--pretty=%s"),
        cwd=worktree.workspace,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert log == "validated change"


@pytest.mark.asyncio
async def test_async_finalization_can_opt_in_to_draft_pr_and_ci(
    tmp_path, monkeypatch, allowed_validation_sandbox
):
    monkeypatch.setattr("config.AGENTIC_RUNTIME", "auto")
    repo = _repository(tmp_path)
    _git(repo, "remote", "add", "origin", "https://github.com/acme/project.git")
    worktree = prepare_engineering_worktree(
        repo_root=repo, job_id="publish-job", required_checks=("ci",)
    )
    target = worktree.workspace / "src" / "app.py"
    target.parent.mkdir()
    target.write_text("VALUE = 1\n", encoding="utf-8")
    calls: list[tuple[str, dict]] = []

    class Transport:
        async def push_branch(self, **kwargs):
            calls.append(("push", kwargs))
            return {"ok": True, "head_sha": kwargs["expected_head"]}

        async def ensure_draft_pr(self, **kwargs):
            calls.append(("pr", kwargs))
            return {"ok": True, "draft": True, "pr_id": "pr-1"}

        async def wait_for_checks(self, **kwargs):
            calls.append(("checks", kwargs))
            return {"ok": True, "status": "success"}

    result = await finalize_engineering_task(
        {"job_id": "publish-job", "run_id": "run-publish", "legacy": False},
        required_tests=(("python3", "-m", "pytest", "--version"),),
        commit_message="validated and published",
        repo_root=repo,
        service=_Service(artifacts=[_artifact(worktree.workspace, "src/app.py")]),
        publish_external=True,
        delivery_transport=Transport(),
        pr_body="Tests passed",
    )

    assert result["status"] == "checks_passed"
    assert result["local_delivery"]["status"] == "committed"
    assert [name for name, _kwargs in calls] == ["push", "pr", "checks"]
    assert calls[1][1]["draft"] is True


def test_provider_name_is_absent_from_neutral_facade():
    source = Path("agents/devagent/agentic_runtime.py").read_text(encoding="utf-8")
    forbidden = "open" + "code"
    assert forbidden.casefold() not in source.casefold()


@pytest.mark.asyncio
async def test_native_devagent_loop_validates_then_commits(
    tmp_path, monkeypatch, allowed_validation_sandbox
):
    from agents.devagent import loop
    from scripts import perf_regression

    repo = _repository(tmp_path)
    spec = {
        "project_name": "Native",
        "slug": "native",
        "project_type": "cli",
        "stack": ["python"],
        "isolation_path": str(repo),
        "constraints": [],
        "acceptance_criteria": ["tests verts"],
        "loop_budget": {
            "max_iterations": 2,
            "max_tokens": 1000,
            "max_consecutive_failures": 2,
        },
    }
    project = {"spec_json": json.dumps(spec), "status": "ready"}
    state = {"iteration": 0, "tokens_used": 0, "consecutive_failures": 0}
    statuses: list[str] = []
    logs: list[tuple[str, bool]] = []

    monkeypatch.setattr(loop.devagent_db, "get_project", lambda project_id: project)
    monkeypatch.setattr(
        loop.devagent_db,
        "update_project_status",
        lambda project_id, status: (
            statuses.append(status),
            project.update(status=status),
        ),
    )
    monkeypatch.setattr(loop.devagent_db, "get_loop_state", lambda project_id: state)
    monkeypatch.setattr(
        loop.devagent_db,
        "update_loop_state",
        lambda project_id, value: state.update(value),
    )
    monkeypatch.setattr(
        loop.devagent_db,
        "log_iteration",
        lambda project_id, iteration, action, output, ok: logs.append((action, ok)),
    )
    monkeypatch.setattr(loop, "setup_venv", lambda project_path: None)
    monkeypatch.setattr(loop, "git_init", lambda project_path: None)
    monkeypatch.setattr("config.DEVAGENT_AUTO_DEPLOY_STAGING", False)
    monkeypatch.setattr("config.DEVAGENT_AUTO_PR", False)
    monkeypatch.setattr(
        perf_regression,
        "guard_devagent_iteration",
        AsyncMock(return_value={"rolled_back": False}),
    )

    async def fake_delegate(**kwargs):
        tests = kwargs["workspace"] / "tests"
        tests.mkdir(exist_ok=True)
        (tests / "test_ok.py").write_text(
            "def test_ok():\n    assert True\n", encoding="utf-8"
        )
        changed = (tests / "test_ok.py").read_bytes()
        return RuntimeDelegationResult(
            run_id="run-native",
            status="completed",
            phase="completed",
            changed_files=("tests/test_ok.py",),
            changed_artifacts=(
                ChangedArtifact(
                    path="tests/test_ok.py",
                    sha256=hashlib.sha256(changed).hexdigest(),
                    size_bytes=len(changed),
                ),
            ),
            summary="preuves validées",
        )

    monkeypatch.setattr(loop, "delegate_devagent_iteration", fake_delegate)
    await loop._run_agentic_loop_inner(42)

    assert statuses[-1] == "done"
    assert ("test", True) in logs
    assert ("commit", True) in logs
    log = subprocess.run(
        ("git", "log", "--oneline"),
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "critères DevAgent validés" in log


@pytest.mark.asyncio
async def test_native_loop_repairs_red_gate_before_completion(tmp_path, monkeypatch):
    from agents.devagent import loop
    from scripts import perf_regression

    repo = _repository(tmp_path)
    spec = {
        "project_name": "Repair",
        "slug": "repair",
        "project_type": "cli",
        "stack": ["python"],
        "isolation_path": str(repo),
        "constraints": [],
        "acceptance_criteria": ["gate verte"],
        "loop_budget": {
            "max_iterations": 1,
            "max_tokens": 1000,
            "max_consecutive_failures": 2,
        },
    }
    project = {"spec_json": json.dumps(spec), "status": "ready"}
    state = {"iteration": 0, "tokens_used": 0, "consecutive_failures": 0}
    statuses: list[str] = []
    repair_flags: list[bool] = []

    class HandshakeService:
        def __init__(self):
            self.states: dict[str, str] = {}
            self.receipts: list[tuple[str, str]] = []
            self.events: list[tuple[str, str]] = []

        def get(self, run_id):
            status = self.states.get(run_id, "reviewing")
            return SimpleNamespace(
                status=SimpleNamespace(value=status),
                terminal=status in {"failed", "completed"},
            )

        async def fail_jarvis_delivery(self, run_id, *, error_code, summary):
            del summary
            self.states[run_id] = "failed"
            self.events.append((run_id, error_code))
            return self.get(run_id)

        async def record_verification_receipt(
            self, run_id, *, kind, subject, details, artifact_id
        ):
            del subject, details, artifact_id
            self.receipts.append((run_id, kind))
            return SimpleNamespace(type=f"jarvis_{kind}_receipt")

        async def verify_run(self, run_id):
            assert (run_id, "test") in self.receipts
            self.states[run_id] = "completed"
            self.events.append((run_id, "verified"))
            return self.get(run_id)

    service = HandshakeService()
    monkeypatch.setattr(loop.devagent_db, "get_project", lambda project_id: project)
    monkeypatch.setattr(
        loop.devagent_db,
        "update_project_status",
        lambda project_id, status: (
            statuses.append(status),
            project.update(status=status),
        ),
    )
    monkeypatch.setattr(loop.devagent_db, "get_loop_state", lambda project_id: state)
    monkeypatch.setattr(
        loop.devagent_db,
        "update_loop_state",
        lambda project_id, value: state.update(value),
    )
    monkeypatch.setattr(loop.devagent_db, "log_iteration", lambda *args, **kwargs: None)
    monkeypatch.setattr(loop, "setup_venv", lambda project_path: None)
    monkeypatch.setattr(loop, "git_init", lambda project_path: None)
    monkeypatch.setattr("config.DEVAGENT_AUTO_DEPLOY_STAGING", False)
    monkeypatch.setattr("config.DEVAGENT_AUTO_PR", False)
    monkeypatch.setattr(
        perf_regression,
        "guard_devagent_iteration",
        AsyncMock(return_value={"rolled_back": False}),
    )

    async def fake_delegate(**kwargs):
        repairing = kwargs.get("repair_output") is not None
        repair_flags.append(repairing)
        target = kwargs["workspace"] / "app.py"
        target.write_text(
            "VALUE = 2\n" if repairing else "VALUE = broken\n",
            encoding="utf-8",
        )
        data = target.read_bytes()
        run_id = "run-repair" if repairing else "run-red"
        service.states[run_id] = "reviewing"
        return RuntimeDelegationResult(
            run_id=run_id,
            status="reviewing",
            phase="awaiting_jarvis_validation",
            changed_files=("app.py",),
            changed_artifacts=(
                ChangedArtifact(
                    path="app.py",
                    sha256=hashlib.sha256(data).hexdigest(),
                    size_bytes=len(data),
                ),
            ),
            runtime_service=service,
        )

    deliveries = iter(
        (
            {
                "ok": False,
                "status": "validation_failed",
                "validations": [
                    {
                        "command": ["python3", "-m", "pytest"],
                        "returncode": 1,
                        "stderr": "red",
                    }
                ],
            },
            {
                "ok": True,
                "status": "committed",
                "validations": [
                    {
                        "command": ["python3", "-m", "pytest"],
                        "returncode": 0,
                    }
                ],
            },
        )
    )
    monkeypatch.setattr(loop, "delegate_devagent_iteration", fake_delegate)
    monkeypatch.setattr(
        loop,
        "validate_and_commit_engineering_worktree",
        lambda *args, **kwargs: next(deliveries),
    )

    await loop._run_agentic_loop_inner(9)

    assert repair_flags == [False, True]
    assert service.states == {"run-red": "failed", "run-repair": "completed"}
    assert service.events == [
        ("run-red", "validation_failed"),
        ("run-repair", "verified"),
    ]
    assert service.receipts == [
        ("run-repair", "test"),
        ("run-repair", "effect"),
    ]
    assert statuses[-1] == "done"
