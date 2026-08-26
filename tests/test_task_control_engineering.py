"""Contrats du vertical TaskControl -> OpenCode -> finalizer JARVIS."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from typing import Any

import pytest

import config
import database
from jarvis.event_bus import EventBus
from jarvis.task_control.detection import TaskCandidateDetector
from jarvis.task_control.engineering import ENGINEERING_DELIVERY_METADATA_KEY
from jarvis.task_control.models import (
    PlanDecision,
    PlanStep,
    TaskExecutionRefused,
    TaskPlan,
    TaskStatus,
    new_id,
)
from jarvis.task_control.service import TaskControlService


@dataclass
class _Run:
    run_id: str
    status: str = "queued"


@dataclass
class _Agentic:
    starts: list[dict[str, Any]] = field(default_factory=list)
    cancels: list[str] = field(default_factory=list)

    async def create_and_start(self, **kwargs: Any) -> _Run:
        self.starts.append(kwargs)
        return _Run(run_id=str(kwargs["run_id"]))

    def get(self, run_id: str) -> _Run:
        return _Run(run_id=run_id)

    def artifacts(self, _run_id: str) -> tuple[Any, ...]:
        return ()

    def approvals(self, _run_id: str) -> tuple[Any, ...]:
        return ()

    async def cancel(self, run_id: str) -> None:
        self.cancels.append(run_id)


@dataclass
class _Notifications:
    created: list[dict[str, Any]] = field(default_factory=list)

    def create(self, **kwargs: Any) -> int:
        self.created.append(kwargs)
        return len(self.created)


async def _planner(task, *, version: int, context=None) -> TaskPlan:
    return TaskPlan(
        plan_id=new_id("plan"),
        task_id=task.task_id,
        version=version,
        objective=task.description,
        summary="Corriger le défaut dans un worktree isolé.",
        steps=(PlanStep(index=1, title="Modifier le code borné"),),
        success_criteria=("Le calcul demandé est correct.",),
    )


def _run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def engineering_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[TaskControlService, _Agentic, Path]:
    database_path = tmp_path / "jarvis.db"
    monkeypatch.setattr(config, "DB_PATH", str(database_path))
    monkeypatch.setattr(database, "DB_PATH", database_path)
    database.init_db()

    repo = tmp_path / "fixture-repo"
    repo.mkdir()
    (repo / "calculator.py").write_text(
        "def add(left: int, right: int) -> int:\n    return left - right\n",
        encoding="utf-8",
    )
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_calculator.py").write_text(
        "from calculator import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    _run_git(repo, "init", "--quiet")
    _run_git(repo, "add", "calculator.py", "tests/test_calculator.py")
    _run_git(
        repo,
        "-c",
        "user.name=JARVIS Test",
        "-c",
        "user.email=jarvis-test@invalid",
        "commit",
        "--quiet",
        "-m",
        "fixture baseline",
    )

    agentic = _Agentic()
    service = TaskControlService(
        agentic_service=agentic,
        notifications=_Notifications(),
        bus=EventBus(),
        planner=_planner,
        detector=TaskCandidateDetector(),
    )
    return service, agentic, repo


@pytest.mark.asyncio
async def test_approval_precedes_worktree_runtime_and_finalizer(
    engineering_service: tuple[TaskControlService, _Agentic, Path],
) -> None:
    service, agentic, repo = engineering_service
    task = await service.create_engineering_task(
        title="Corriger calculator.add",
        user_request="Remplace la soustraction erronée par une addition.",
        repo_root=repo,
        required_tests=("python3 -m pytest tests/test_calculator.py -q",),
        acceptance_criteria=("calculator.add(2, 3) retourne 5",),
        idempotency_key="task-control-real-opencode-fixture",
        runtime_id="opencode",
        runtime_version="1.18.16",
    )

    assert task.status is TaskStatus.AWAITING_PLAN_APPROVAL
    assert agentic.starts == []
    assert not (repo / ".jarvis").exists()
    assert _run_git(repo, "branch", "--list", "jarvis/agentic/*") == ""

    plan = service.repository.get_plan(task.task_id, 1)
    assert plan is not None
    contract = task.metadata[ENGINEERING_DELIVERY_METADATA_KEY]
    assert f"sha256:{contract['digest']}" in "\n".join(plan.success_criteria)
    assert "JARVIS exécute: python3 -m pytest tests/test_calculator.py -q" in (
        plan.success_criteria
    )
    assert f"Dépôt borné: {repo}" in plan.success_criteria
    assert "Commit local JARVIS: Corriger calculator.add" in plan.success_criteria
    assert "Publication externe: interdite" in plan.success_criteria
    assert "opencode@1.18.16" in plan.tools_expected
    assert plan.execution_permissions == (
        "workspace:read",
        "workspace:write",
        "tests:run",
    )

    task = await service.decide_plan(
        task.task_id,
        1,
        decision=PlanDecision.APPROVED,
        actor="session:test",
    )

    assert task.status is TaskStatus.QUEUED
    assert len(agentic.starts) == 1
    launch = agentic.starts[0]
    workspace = Path(launch["workspace"])
    assert launch["runtime_id"] == "opencode"
    assert workspace.is_dir()
    assert workspace != repo
    assert launch["selected_context"]["jarvis_owns_delivery"] is True
    assert launch["selected_context"]["required_tests"] == [
        ["python3", "-m", "pytest", "tests/test_calculator.py", "-q"]
    ]
    assert "aucun test" in launch["selected_context"]["request"]
    assert _run_git(repo, "branch", "--list", "jarvis/agentic/*")

    records = list((Path(config.DB_PATH).parent / "agentic-finalizers").glob("*.json"))
    assert len(records) == 1
    receipt = json.loads(records[0].read_text(encoding="utf-8"))
    assert receipt["run_id"] == task.agentic_run_id
    assert receipt["publish_external"] is False
    assert receipt["required_tests"] == [
        ["python3", "-m", "pytest", "tests/test_calculator.py", "-q"]
    ]


@pytest.mark.asyncio
async def test_contract_tampering_after_plan_is_refused_before_mutation(
    engineering_service: tuple[TaskControlService, _Agentic, Path],
) -> None:
    service, agentic, repo = engineering_service
    task = await service.create_engineering_task(
        title="Corriger calculator.add",
        user_request="Corrige la fonction.",
        repo_root=repo,
        required_tests=("python3 -m pytest tests/test_calculator.py -q",),
        idempotency_key="tamper-proof-contract",
        runtime_id="opencode",
        runtime_version="1.18.16",
    )
    metadata = deepcopy(dict(task.metadata))
    metadata[ENGINEERING_DELIVERY_METADATA_KEY]["required_tests"] = [
        ["python3", "-m", "pytest", "-q"]
    ]
    service.repository.update_task(task.task_id, metadata=metadata)

    with pytest.raises(TaskExecutionRefused, match="contrat de livraison"):
        await service.decide_plan(
            task.task_id,
            1,
            decision=PlanDecision.APPROVED,
            actor="session:test",
        )

    assert agentic.starts == []
    assert not (repo / ".jarvis").exists()
    assert _run_git(repo, "branch", "--list", "jarvis/agentic/*") == ""


@pytest.mark.asyncio
async def test_runtime_version_drift_is_refused_before_mutation(
    engineering_service: tuple[TaskControlService, _Agentic, Path],
) -> None:
    service, agentic, repo = engineering_service
    task = await service.create_engineering_task(
        title="Corriger calculator.add",
        user_request="Corrige la fonction.",
        repo_root=repo,
        required_tests=("python3 -m pytest tests/test_calculator.py -q",),
        idempotency_key="runtime-version-drift",
        runtime_id="opencode",
        runtime_version="1.18.16",
    )
    agentic.registry = SimpleNamespace(
        manifest=lambda _runtime_id: SimpleNamespace(version="1.18.17")
    )

    with pytest.raises(TaskExecutionRefused, match="version du runtime"):
        await service.decide_plan(
            task.task_id,
            1,
            decision=PlanDecision.APPROVED,
            actor="session:test",
        )

    assert agentic.starts == []
    assert not (repo / ".jarvis").exists()


@pytest.mark.asyncio
async def test_worktree_preparation_failure_becomes_a_terminal_report(
    engineering_service: tuple[TaskControlService, _Agentic, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, agentic, repo = engineering_service
    task = await service.create_engineering_task(
        title="Corriger calculator.add",
        user_request="Corrige la fonction.",
        repo_root=repo,
        required_tests=("python3 -m pytest tests/test_calculator.py -q",),
        idempotency_key="worktree-failure",
        runtime_id="opencode",
        runtime_version="1.18.16",
    )

    def fail_worktree(**_kwargs: Any) -> None:
        raise RuntimeError("fixture worktree failure")

    monkeypatch.setattr(
        "agents.devagent.agentic_runtime.prepare_engineering_worktree",
        fail_worktree,
    )
    failed = await service.decide_plan(
        task.task_id,
        1,
        decision=PlanDecision.APPROVED,
        actor="session:test",
    )

    assert failed.status is TaskStatus.FAILED
    assert failed.current_phase == "worktree_failed"
    assert failed.final_report_id is not None
    assert failed.agentic_run_id is None
    assert agentic.starts == []


@pytest.mark.asyncio
async def test_finalizer_persistence_failure_cancels_the_started_run(
    engineering_service: tuple[TaskControlService, _Agentic, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, agentic, repo = engineering_service
    task = await service.create_engineering_task(
        title="Corriger calculator.add",
        user_request="Corrige la fonction.",
        repo_root=repo,
        required_tests=("python3 -m pytest tests/test_calculator.py -q",),
        idempotency_key="finalizer-failure",
        runtime_id="opencode",
        runtime_version="1.18.16",
    )

    def fail_finalizer(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("fixture finalizer failure")

    monkeypatch.setattr(
        "agents.devagent.finalizer.enqueue_engineering_finalizer",
        fail_finalizer,
    )
    failed = await service.decide_plan(
        task.task_id,
        1,
        decision=PlanDecision.APPROVED,
        actor="session:test",
    )

    assert failed.status is TaskStatus.FAILED
    assert failed.current_phase == "finalizer_enqueue_failed"
    assert failed.final_report_id is not None
    assert failed.agentic_run_id is not None
    assert agentic.cancels == [failed.agentic_run_id]
