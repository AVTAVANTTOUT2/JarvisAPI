from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agents.engineering_team.models import EngineeringTask, TaskPhase, TeamState
from agents.engineering_team.providers import (
    SubscriptionProviders,
    subscription_environment,
)
from agents.engineering_team.state import StateStore
from agents.engineering_team.workflow import (
    EngineeringTeam,
    _codex_quota_retry_after,
    _timestamp_is_due,
)


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "engineering-team.json"
    path.write_text(
        json.dumps(
            {
                "loop": {
                    "base_branch": "main",
                    "branch_prefix": "codex/jarvis/",
                    "max_attempts_per_task": 3,
                    "max_merge_attempts": 3,
                    "max_tests_per_task": 3,
                    "test_timeout_seconds": 30,
                    "auto_merge": True,
                    "merge_method": "squash",
                },
                "roadmap": {"ready_label": "agent-ready"},
                "cursor": {"trusted_pr_authors": ["app/cursor"]},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_subscription_environment_forces_local_subscription() -> None:
    env = subscription_environment(
        {
            "HOME": "/tmp/user",
            "PATH": "/usr/bin",
            "OPENAI_API_KEY": "secret-openai",
            "ANTHROPIC_API_KEY": "secret-anthropic",
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "UNRELATED_SECRET": "secret",
        }
    )
    assert env["HOME"] == "/tmp/user"
    assert env["PATH"] == "/usr/bin"
    assert "OPENAI_API_KEY" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert "CLAUDE_CODE_USE_BEDROCK" not in env
    assert "UNRELATED_SECRET" not in env


def test_state_store_round_trip(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "runtime")
    task = EngineeringTask(
        task_id="abc123",
        title="Test",
        request="Faire le test",
        acceptance_criteria=["vert"],
        required_tests=["python -m pytest tests/test_x.py -q"],
    )
    state = TeamState(tasks=[task])
    store.save(state)
    loaded = store.load()
    assert loaded.tasks[0].task_id == "abc123"
    assert loaded.tasks[0].phase is TaskPhase.READY


def test_enqueue_requires_deterministic_test(tmp_path: Path) -> None:
    team = EngineeringTeam(
        root=tmp_path,
        config_path=_config(tmp_path),
        providers=SubscriptionProviders(tmp_path / "providers"),
    )
    with pytest.raises(ValueError, match="commande de test"):
        team.enqueue(
            title="Sans test",
            request="Ne doit pas passer",
            acceptance_criteria=[],
            required_tests=[],
        )


def test_selection_prioritizes_requested_changes_then_review(tmp_path: Path) -> None:
    team = EngineeringTeam(
        root=tmp_path,
        config_path=_config(tmp_path),
        providers=SubscriptionProviders(tmp_path / "providers"),
    )
    ready = EngineeringTask(
        "ready", "Ready", "R", [], ["python -m pytest -q"], priority=100
    )
    review = EngineeringTask(
        "review",
        "Review",
        "R",
        [],
        ["python -m pytest -q"],
        phase=TaskPhase.REVIEW_PENDING,
    )
    changes = EngineeringTask(
        "changes",
        "Changes",
        "R",
        [],
        ["python -m pytest -q"],
        phase=TaskPhase.CHANGES_REQUESTED,
    )
    assert team._select_next(TeamState(tasks=[ready, review, changes])) is changes
    assert team._select_next(TeamState(tasks=[ready, review])) is ready


def test_interrupted_task_is_recovered_for_next_cycle(tmp_path: Path) -> None:
    team = EngineeringTeam(root=tmp_path, config_path=_config(tmp_path))
    task = EngineeringTask(
        "interrupted",
        "Interrupted audit",
        "Resume safely",
        [],
        ["python -m pytest -q"],
        phase=TaskPhase.IMPLEMENTING,
    )
    state = TeamState(tasks=[task])
    events: list[dict[str, object]] = []

    team._recover_interrupted_tasks(state, events)

    assert task.phase == TaskPhase.CHANGES_REQUESTED
    assert "reprise automatique" in (task.last_error or "")
    assert events[0]["channel"] == "agents"


def test_codex_quota_backoff_uses_reported_subscription_reset() -> None:
    retry_after = _codex_quota_retry_after(
        "You've hit your usage limit; try again at Aug 9th, 2026 12:37 PM."
    )
    assert retry_after == "2026-08-09T10:37:00+00:00"
    assert not _timestamp_is_due(
        retry_after, now=datetime(2026, 8, 5, tzinfo=timezone.utc)
    )
    assert _timestamp_is_due(
        retry_after, now=datetime(2026, 8, 10, tzinfo=timezone.utc)
    )


def test_claude_is_not_used_as_implementer() -> None:
    source = Path("agents/engineering_team/workflow.py").read_text(encoding="utf-8")
    assert source.count("run_claude_review(") == 1
    assert (
        "writable=True"
        not in Path("agents/engineering_team/providers.py")
        .read_text(encoding="utf-8")
        .split("def run_claude_review", 1)[1]
    )


def _merge_task() -> EngineeringTask:
    return EngineeringTask(
        "merge",
        "Cursor PR",
        "Vérifier la correction Cursor",
        ["comportement corrigé"],
        ["python -m pytest tests/test_target.py -q"],
        phase=TaskPhase.MERGE_PENDING,
        source="cursor_pr",
        source_pr_number=42,
        pr_url="https://github.com/example/repo/pull/42",
        branch="elias/cursor-fix",
        reviewed_head_sha="abc123",
        review={"verdict": "approve", "summary": "ok", "findings": []},
    )


def test_merge_gate_rereviews_when_head_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    team = EngineeringTeam(root=tmp_path, config_path=_config(tmp_path))
    task = _merge_task()
    metadata = {
        "state": "OPEN",
        "isDraft": True,
        "author": {"login": "app/cursor"},
        "baseRefName": "main",
        "headRefName": task.branch,
        "headRefOid": "different",
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [],
    }

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        assert command[:3] == ["gh", "pr", "view"]
        return subprocess.CompletedProcess(command, 0, json.dumps(metadata), "")

    monkeypatch.setattr(team, "_run", fake_run)
    result = team._merge_if_ready(TeamState(tasks=[task]), task, [])
    assert result["status"] == "review_pending"
    assert task.phase is TaskPhase.REVIEW_PENDING
    assert task.reviewed_head_sha is None


def test_merge_gate_routes_failed_ci_back_to_codex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    team = EngineeringTeam(root=tmp_path, config_path=_config(tmp_path))
    task = _merge_task()
    metadata = {
        "state": "OPEN",
        "isDraft": True,
        "author": {"login": "app/cursor"},
        "baseRefName": "main",
        "headRefName": task.branch,
        "headRefOid": "abc123",
        "mergeStateStatus": "UNSTABLE",
        "statusCheckRollup": [
            {"name": "pytest", "status": "COMPLETED", "conclusion": "FAILURE"}
        ],
    }
    monkeypatch.setattr(
        team,
        "_run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, json.dumps(metadata), ""
        ),
    )
    result = team._merge_if_ready(TeamState(tasks=[task]), task, [])
    assert result["status"] == "changes_requested"
    assert task.phase is TaskPhase.CHANGES_REQUESTED
    assert task.review and "CI en échec" in task.review["summary"]


def test_merge_gate_merges_only_green_reviewed_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    team = EngineeringTeam(root=tmp_path, config_path=_config(tmp_path))
    task = _merge_task()
    metadata = {
        "state": "OPEN",
        "isDraft": True,
        "author": {"login": "app/cursor"},
        "baseRefName": "main",
        "headRefName": task.branch,
        "headRefOid": "abc123",
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [
            {"name": "pytest", "status": "COMPLETED", "conclusion": "SUCCESS"}
        ],
    }
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(command, 0, json.dumps(metadata), "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(team, "_run", fake_run)
    events: list[dict[str, object]] = []
    result = team._merge_if_ready(TeamState(tasks=[task]), task, events)
    assert result["status"] == "merged"
    assert task.phase is TaskPhase.DONE
    assert any(command[:3] == ["gh", "pr", "ready"] for command in commands)
    assert any(
        command[:3] == ["gh", "pr", "merge"] and "--squash" in command
        for command in commands
    )


def test_cursor_authored_pr_enters_codex_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    providers = SubscriptionProviders(tmp_path / "providers")
    team = EngineeringTeam(
        root=tmp_path, config_path=_config(tmp_path), providers=providers
    )
    pull_requests = [
        {
            "number": 12,
            "title": "Cursor fix",
            "body": "Fix detected bug",
            "url": "https://github.com/example/repo/pull/12",
            "headRefName": "elias/cursor-fix",
            "baseRefName": "main",
            "isDraft": True,
            "labels": [],
            "author": {"login": "app/cursor"},
            "files": [{"path": "api/example.py"}],
        },
        {
            "number": 13,
            "title": "Human PR",
            "body": "Do not ingest",
            "url": "https://github.com/example/repo/pull/13",
            "headRefName": "feature/human",
            "baseRefName": "main",
            "isDraft": False,
            "labels": [],
            "author": {"login": "someone"},
            "files": [],
        },
    ]
    monkeypatch.setattr(
        team,
        "_run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, json.dumps(pull_requests), ""
        ),
    )
    task = team._refresh_cursor_pr(TeamState(), [])
    assert task is not None
    assert task.source == "cursor_pr"
    assert task.source_pr_number == 12
    assert task.branch == "elias/cursor-fix"
    assert task.priority == 90
    assert task.required_tests == [
        "python -m pytest tests/ jarvis/tests agents/devagent -q"
    ]
    assert "PR Cursor #12" in task.request


def test_new_worktree_is_based_on_fresh_origin_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    team = EngineeringTeam(root=tmp_path, config_path=_config(tmp_path))
    task = EngineeringTask(
        "fresh",
        "Fresh task",
        "Implement",
        [],
        ["python -m pytest -q"],
    )
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        returncode = 1 if command[:3] == ["git", "show-ref", "--verify"] else 0
        return subprocess.CompletedProcess(command, returncode, "", "")

    monkeypatch.setattr(team, "_run", fake_run)
    team._prepare_worktree(task)
    assert [
        "git",
        "fetch",
        "origin",
        "main:refs/remotes/origin/main",
    ] in commands
    worktree_add = next(
        command for command in commands if command[:3] == ["git", "worktree", "add"]
    )
    assert worktree_add[-1] == "origin/main"


def test_cursor_worktree_uses_fetched_pr_ref_without_tracking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    team = EngineeringTeam(root=tmp_path, config_path=_config(tmp_path))
    task = EngineeringTask(
        "cursor",
        "Cursor PR",
        "Audit",
        [],
        ["python -m pytest -q"],
        source="cursor_pr",
        source_pr_number=177,
        branch="elias/missing-test-coverage-a0e4",
    )
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        returncode = 1 if command[:3] == ["git", "show-ref", "--verify"] else 0
        return subprocess.CompletedProcess(command, returncode, "", "")

    monkeypatch.setattr(team, "_run", fake_run)
    team._prepare_worktree(task)
    assert [
        "git",
        "fetch",
        "origin",
        "elias/missing-test-coverage-a0e4:refs/remotes/origin/elias/missing-test-coverage-a0e4",
    ] in commands
    assert [
        "git",
        "branch",
        "elias/missing-test-coverage-a0e4",
        "refs/remotes/origin/elias/missing-test-coverage-a0e4",
    ] in commands
    assert not any("--track" in command for command in commands)
