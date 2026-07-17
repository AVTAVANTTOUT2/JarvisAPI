"""Délégation Cursor CLI — E2E avec faux CLI, worktree réel, états persistants."""

from __future__ import annotations

import asyncio
import stat
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
from integrations.cursor_cli import CursorCliInfo, build_agent_command  # noqa: E402
from integrations.cursor_delegation import (  # noqa: E402
    CursorDelegationError,
    CursorDelegationService,
    _redact_secrets,
)


# ── Helpers ──────────────────────────────────────────────────


def _make_git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=path, capture_output=True)
    (path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, capture_output=True, check=True)
    return path


def _make_fake_cli(path: Path, *, verdict: str = "COMPLETED", exit_code: int = 0,
                   sleep_s: float = 0.0, no_markers: bool = False) -> Path:
    """Faux binaire `agent` : imprime un rapport structuré et sort.

    NB : script construit ligne par ligne — un shebang indenté (dedent cassé
    par l'interpolation) provoque un « Exec format error » via subprocess.
    """
    lines = [
        "#!/bin/bash",
        'if [ "$1" = "--version" ]; then echo "9.9.9-test"; exit 0; fi',
        'if [ "$1" = "--help" ]; then',
        '  echo "--print --force --trust --workspace <path> --output-format"',
        "  exit 0",
        "fi",
        'if [ "$1" = "status" ]; then echo "logged in as test"; exit 0; fi',
        f"sleep {sleep_s}",
        'echo "travail simulé dans $(pwd)"',
    ]
    if not no_markers:
        lines += [
            'echo "JARVIS_CURSOR_RESULT_BEGIN"',
            f'echo "Verdict: {verdict}"',
            'echo "Root cause:"',
            'echo "test double"',
            'echo "Files changed:"',
            'echo "app.py"',
            'echo "Tests:"',
            'echo "ok"',
            'echo "JARVIS_CURSOR_RESULT_END"',
        ]
    lines.append(f"exit {exit_code}")
    cli = path / "agent"
    cli.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cli.chmod(cli.stat().st_mode | stat.S_IEXEC)
    return cli


@pytest.fixture
def delegation_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Repo git + faux CLI + DB isolée + service neuf."""
    db_path = tmp_path / "jobs.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()

    repo = _make_git_repo(tmp_path / "repo")
    cli = _make_fake_cli(tmp_path)

    monkeypatch.setattr(config, "CURSOR_DELEGATION_ENABLED", True)
    monkeypatch.setattr(config, "CURSOR_CLI_PATH", str(cli))
    monkeypatch.setattr(config, "CURSOR_WORKTREE_ROOT", str(tmp_path / "wt"))
    monkeypatch.setattr(config, "CURSOR_DEFAULT_TIMEOUT_SEC", 30)
    monkeypatch.setattr(config, "CURSOR_MAX_CONCURRENT_JOBS", 2)
    monkeypatch.setattr(config, "CURSOR_ALLOW_PUSH", False)  # pas de remote
    monkeypatch.setattr(config, "CURSOR_ALLOW_PR", False)

    async def _fake_compose(**kwargs):
        return {
            "prompt": f"PROMPT COMPOSÉ: {kwargs['user_request'][:80]}",
            "template_id": kwargs.get("template_id", "feature_implementation"),
            "template_version": "test-1.0",
            "composer_model": "test",
            "base_prompt": "base",
        }

    monkeypatch.setattr(
        "integrations.cursor_delegation.compose_cursor_prompt", _fake_compose
    )

    service = CursorDelegationService()
    return {"service": service, "repo": repo, "cli": cli, "tmp": tmp_path}


def _enqueue_and_run(service: CursorDelegationService, repo: Path, **kwargs):
    async def _run():
        # Tests unitaires : contournent la confirmation (chemin loop/scheduler).
        job = await service.enqueue(
            title=kwargs.get("title", "Test job"),
            user_request=kwargs.get("user_request", "corrige app.py"),
            repository=str(repo),
            auto_start=False,
            require_confirmation=False,
            **{
                k: v
                for k, v in kwargs.items()
                if k not in ("title", "user_request", "require_confirmation", "auto_start")
            },
        )
        # queued → run explicite
        from database.cursor_jobs import update_cursor_job

        if job["status"] == "awaiting_confirmation":
            update_cursor_job(job["job_id"], status="queued")
        return await service.run_job(job["job_id"])

    return asyncio.run(_run())


# ── Tests build_agent_command ────────────────────────────────


def test_build_command_requires_headless_mode():
    info = CursorCliInfo(available=True, path="/bin/agent", supports_print=False)
    with pytest.raises(RuntimeError, match="headless"):
        build_agent_command(info, "prompt", workspace="/tmp")


def test_build_command_includes_output_format_and_workspace():
    info = CursorCliInfo(
        available=True, path="/bin/agent",
        supports_print=True, supports_force=True, supports_trust=True,
        supports_workspace=True,
    )
    cmd = build_agent_command(info, "le prompt", workspace="/x/y")
    assert "--print" in cmd
    assert "--output-format" in cmd and "text" in cmd
    assert "--workspace" in cmd and "/x/y" in cmd
    assert "--force" not in cmd  # fail-closed par défaut
    assert "--trust" not in cmd  # trust off tant que non demandé
    assert cmd[-1] == "le prompt"

    cmd_trust = build_agent_command(
        info, "le prompt", workspace="/wt/job", force=False, trust=True
    )
    assert "--trust" in cmd_trust
    assert "--force" not in cmd_trust


# ── Tests E2E service ────────────────────────────────────────


def test_job_completed_with_worktree_and_markers(delegation_env):
    service, repo = delegation_env["service"], delegation_env["repo"]
    job = _enqueue_and_run(service, repo)

    assert job["status"] == "completed"
    assert job["branch_name"].startswith("jarvis/cursor/job-")
    assert job["structured_result"]["verdict"] == "COMPLETED"
    assert job["structured_result"]["cli_returncode"] == 0
    assert "travail simulé" in job["raw_output"]
    # Branche isolée réellement créée, main intact
    branches = subprocess.run(
        ["git", "branch", "--list"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert "jarvis/cursor/" in branches
    head = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()
    assert head == "main"


def test_job_failed_on_nonzero_exit(delegation_env, monkeypatch):
    service, repo, tmp = delegation_env["service"], delegation_env["repo"], delegation_env["tmp"]
    cli = _make_fake_cli(tmp, verdict="COMPLETED", exit_code=3)
    monkeypatch.setattr(config, "CURSOR_CLI_PATH", str(cli))

    job = _enqueue_and_run(service, repo)
    assert job["status"] == "failed"
    assert "exit=3" in (job["error_message"] or "")
    assert job["structured_result"]["cli_returncode"] == 3


def test_job_partial_without_markers(delegation_env, monkeypatch):
    service, repo, tmp = delegation_env["service"], delegation_env["repo"], delegation_env["tmp"]
    cli = _make_fake_cli(tmp, no_markers=True)
    monkeypatch.setattr(config, "CURSOR_CLI_PATH", str(cli))

    job = _enqueue_and_run(service, repo)
    # Pas de marqueurs → PARTIAL, pas de PR, mais pas d'échec CLI
    assert job["status"] == "completed"
    assert job["structured_result"]["verdict"] == "PARTIAL"
    assert job["structured_result"]["parsed"] is False


def test_job_blocked_verdict_fails(delegation_env, monkeypatch):
    service, repo, tmp = delegation_env["service"], delegation_env["repo"], delegation_env["tmp"]
    cli = _make_fake_cli(tmp, verdict="BLOCKED")
    monkeypatch.setattr(config, "CURSOR_CLI_PATH", str(cli))

    job = _enqueue_and_run(service, repo)
    assert job["status"] == "failed"


def test_job_timeout(delegation_env, monkeypatch):
    service, repo, tmp = delegation_env["service"], delegation_env["repo"], delegation_env["tmp"]
    cli = _make_fake_cli(tmp, sleep_s=20)
    monkeypatch.setattr(config, "CURSOR_CLI_PATH", str(cli))
    monkeypatch.setattr(config, "CURSOR_DEFAULT_TIMEOUT_SEC", 2)

    job = _enqueue_and_run(service, repo)
    assert job["status"] == "failed"
    assert "timeout" in (job["error_message"] or "").lower()


def test_enqueue_refused_when_disabled(delegation_env, monkeypatch):
    service, repo = delegation_env["service"], delegation_env["repo"]
    monkeypatch.setattr(config, "CURSOR_DELEGATION_ENABLED", False)
    with pytest.raises(CursorDelegationError, match="CURSOR_DELEGATION_ENABLED"):
        asyncio.run(service.enqueue(title="x", user_request="y", repository=str(repo)))


def test_enqueue_refused_when_cli_missing(delegation_env, monkeypatch):
    service, repo = delegation_env["service"], delegation_env["repo"]
    monkeypatch.setattr(config, "CURSOR_CLI_PATH", "/nonexistent/agent-missing")
    # Neutralise le PATH fallback (agent réel installé sur la machine de dev)
    monkeypatch.setattr(
        "integrations.cursor_cli.shutil.which", lambda *_a, **_k: None
    )
    with pytest.raises(CursorDelegationError):
        asyncio.run(service.enqueue(title="x", user_request="y", repository=str(repo)))


def test_enqueue_refused_when_concurrent_limit_reached(delegation_env, monkeypatch):
    """TOCTOU : le slot est réservé atomiquement à l'INSERT, pas au count préalable."""
    service, repo = delegation_env["service"], delegation_env["repo"]
    from database.cursor_jobs import create_cursor_job

    monkeypatch.setattr(config, "CURSOR_MAX_CONCURRENT_JOBS", 1)
    create_cursor_job({
        "job_id": "job-slot-taken",
        "title": "occupé",
        "user_request": "x",
        "status": "awaiting_confirmation",
        "repository": str(repo),
    })
    with pytest.raises(CursorDelegationError, match="Limite de jobs"):
        asyncio.run(service.enqueue(title="y", user_request="z", repository=str(repo)))


def test_create_cursor_job_within_capacity_atomic(tmp_path, monkeypatch):
    from database import init_db
    from database.cursor_jobs import (
        ACTIVE_SLOT_STATUSES,
        create_cursor_job_within_capacity,
        count_active_cursor_jobs,
    )

    db_path = tmp_path / "atomic.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    init_db()

    base = {
        "title": "t",
        "user_request": "r",
        "status": "awaiting_confirmation",
        "repository": str(tmp_path),
    }
    first = create_cursor_job_within_capacity({**base, "job_id": "job-a"}, max_concurrent=1)
    second = create_cursor_job_within_capacity({**base, "job_id": "job-b"}, max_concurrent=1)
    assert first is not None
    assert second is None
    assert count_active_cursor_jobs() == 1
    assert ACTIVE_SLOT_STATUSES  # constante exportée pour doc


def test_jobs_persist_and_resume_after_restart(delegation_env):
    """Les jobs queued sont relancés, les running orphelins marqués failed."""
    service, repo = delegation_env["service"], delegation_env["repo"]
    from database.cursor_jobs import create_cursor_job, get_cursor_job

    create_cursor_job({
        "job_id": "job-restart-queued", "title": "q", "user_request": "r",
        "status": "queued", "repository": str(repo),
    })
    create_cursor_job({
        "job_id": "job-restart-running", "title": "r", "user_request": "r",
        "status": "running", "repository": str(repo),
    })

    async def _resume():
        stats = service.resume_pending_jobs()
        # laisse la tâche asyncio du requeue démarrer puis finir
        await asyncio.sleep(3)
        return stats

    stats = asyncio.run(_resume())
    assert stats["orphaned"] == 1
    assert stats["requeued"] == 1
    orphan = get_cursor_job("job-restart-running")
    assert orphan["status"] == "failed"
    assert "redémarrage" in (orphan["error_message"] or "")
    requeued = get_cursor_job("job-restart-queued")
    assert requeued["status"] in ("completed", "failed", "running", "testing", "preparing")


def test_cancel_kills_running_process(delegation_env, monkeypatch):
    service, repo, tmp = delegation_env["service"], delegation_env["repo"], delegation_env["tmp"]
    cli = _make_fake_cli(tmp, sleep_s=30)
    monkeypatch.setattr(config, "CURSOR_CLI_PATH", str(cli))
    monkeypatch.setattr(config, "CURSOR_DEFAULT_TIMEOUT_SEC", 60)

    async def _run():
        job = await service.enqueue(
            title="long",
            user_request="long",
            repository=str(repo),
            auto_start=False,
            require_confirmation=False,
        )
        job_id = job["job_id"]
        task = asyncio.create_task(service.run_job(job_id))
        # attendre que le process démarre
        for _ in range(100):
            await asyncio.sleep(0.1)
            if job_id in service._procs:
                break
        assert job_id in service._procs, "le process CLI n'a pas démarré"
        cancelled = service.cancel(job_id)
        assert cancelled["status"] == "cancelled"
        await asyncio.wait_for(task, timeout=15)
        from database.cursor_jobs import get_cursor_job

        return get_cursor_job(job_id)

    final = asyncio.run(_run())
    assert final["status"] == "cancelled"


def test_rollback_removes_worktree_and_branch(delegation_env):
    service, repo = delegation_env["service"], delegation_env["repo"]
    job = _enqueue_and_run(service, repo)
    wt = Path(job["worktree_path"])
    assert wt.exists()

    rolled = service.rollback(job["job_id"])
    assert rolled["status"] == "rolled_back"
    assert not wt.exists()
    branches = subprocess.run(
        ["git", "branch", "--list"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert job["branch_name"] not in branches


def test_never_runs_on_protected_branch(delegation_env):
    """La branche de travail n'est jamais main/master."""
    service, repo = delegation_env["service"], delegation_env["repo"]
    job = _enqueue_and_run(service, repo)
    assert job["branch_name"] not in ("main", "master")
    assert job["branch_name"].startswith("jarvis/cursor/")


def test_secret_redaction():
    text = "DEEPSEEK_API_KEY=sk-abc123456789 et Bearer eyJhbGciOiJIUzI1NiIs.token"
    redacted = _redact_secrets(text)
    assert "sk-abc123456789" not in redacted
    assert "eyJhbGciOiJIUzI1NiIs" not in redacted
    assert "REDACTED" in redacted


def test_job_ids_are_unique(delegation_env):
    service = delegation_env["service"]
    ids = {service._new_job_id() for _ in range(50)}
    assert len(ids) == 50
