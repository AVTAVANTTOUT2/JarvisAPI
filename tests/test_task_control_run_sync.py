"""Synchronisation entre l'état d'une tâche pilotée et celui de son run.

Le défaut corrigé ici n'était pas une erreur de calcul mais une course :
``create_and_start`` programme le démarrage sans l'attendre, ses premiers
événements partaient donc avant que ``task.agentic_run_id`` soit persisté, et
``find_task_by_run`` ne trouvait rien. Task Control écrivait ensuite un
``running`` supposé. Une mission retenue par la mémoire s'affichait « En cours »
alors qu'aucun travail n'avait commencé, et l'actualisation n'y changeait rien.

Ces cas exercent la chronologie réelle, y compris avec le vrai service
agentique et un runtime de test, parce que c'est l'ordre des écritures — pas
leur contenu — qui était faux.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

import config
import database
from jarvis.agentic import (
    AgenticRunStatus,
    AgenticService,
    RuntimeRegistry,
    discover_runtime_plugins,
)
from jarvis.agentic.turn_context import AGENTIC_ROUTING_METADATA_KEY
from jarvis.event_bus import EventBus
from jarvis.task_control.detection import TaskCandidateDetector
from jarvis.task_control.models import (
    PlanDecision,
    PlanStep,
    TaskPlan,
    TaskStatus,
    new_id,
)
from jarvis.task_control.service import TaskControlService

from tests.test_agentic_registry import _plugin

#: Le runtime de test du dépôt ne déclare aucune capacité, ce qui ferait
#: échouer tout run portant une permission. Ici on en déclare une seule,
#: celle que le routage demande, pour mesurer la synchronisation et non un
#: refus de capacité.
_RUNTIME_CODE = """
from jarvis.agentic.models import RuntimeHealth, RuntimeHealthStatus, ToolCapability

class FakeRuntime:
    runtime_id = "fake-runtime"
    capabilities = (ToolCapability(name="read", scope="workspace:read"),)

    async def health(self):
        return RuntimeHealth(RuntimeHealthStatus.HEALTHY, version="1.0.0")

    async def create_run(self, run, context):
        return "opaque-session"

    async def start(self, run):
        return None

    async def pause(self, run_id):
        return None

    async def resume(self, run_id):
        return None

    async def cancel(self, run_id):
        return None

    async def answer_approval(self, run_id, approval):
        return None

    async def stream_events(self, run_id):
        if False:
            yield run_id

    async def get_artifacts(self, run_id):
        return ()

    async def dispose(self):
        return None

def create_runtime(manifest):
    return FakeRuntime()
"""

#: Le runtime de test ne déclare que `workspace:read`. Le routage doit donc
#: demander exactement cette permission, sinon `start_run` refuse le run pour
#: capacité non déclarée — un échec sans rapport avec ce qu'on mesure ici.
_ROUTING = {
    AGENTIC_ROUTING_METADATA_KEY: {
        "category": "agentic_readonly",
        "capability_profile_id": "readonly-research",
        "permissions": ["workspace:read"],
    }
}


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
        objective=task.title,
        summary="Plan de test",
        steps=(PlanStep(index=1, title="Lire le contexte"),),
    )


@pytest.fixture
def sync_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "run_sync.db"
    monkeypatch.setattr(config, "DB_PATH", str(path))
    monkeypatch.setattr(database, "DB_PATH", path)
    database.init_db()
    return path


def _control(agentic: Any, bus: EventBus) -> TaskControlService:
    service = TaskControlService(
        agentic_service=agentic,
        notifications=_Notifications(),
        bus=bus,
        planner=_planner,
        detector=TaskCandidateDetector(),
    )
    service.bind_runtime_events()
    return service


async def _until(predicate, *, tries: int = 200, delay: float = 0.01) -> bool:
    for _ in range(tries):
        if predicate():
            return True
        await asyncio.sleep(delay)
    return predicate()


def _summaries(service: TaskControlService, task_id: str) -> list[str]:
    return [entry.summary for entry in service.activity(task_id)]


async def _approved_task(service: TaskControlService, title: str):
    task = await service.create_task(title=title, metadata=dict(_ROUTING))
    return await service.decide_plan(
        task.task_id, 1, decision=PlanDecision.APPROVED, actor="session:1"
    )


# ── La course elle-même ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_evenement_emis_avant_la_fin_du_lancement_nest_pas_perdu(sync_db: Path):
    """Le cas exact du rapport : `resource_wait` arrive pendant le lancement."""

    bus = EventBus()

    @dataclass
    class _RacingRun:
        run_id: str
        status: Any = None
        started_at: datetime | None = None
        finished_at: datetime | None = None
        verification: Any = None

    class _RacingAgentic:
        """Émet ses premiers événements *avant* de rendre la main, comme le vrai."""

        def __init__(self) -> None:
            self.starts: list[dict[str, Any]] = []
            self.control: TaskControlService | None = None
            self.runs: dict[str, _RacingRun] = {}

        async def create_and_start(self, **kwargs: Any) -> _RacingRun:
            run_id = str(kwargs["run_id"])
            self.starts.append(kwargs)
            self.runs[run_id] = _RacingRun(run_id=run_id)
            assert self.control is not None
            await self.control.on_runtime_event(
                "agent.run.queued", {"run_id": run_id, "status": "queued"}
            )
            await self.control.on_runtime_event(
                "agent.run.resource_wait",
                {
                    "run_id": run_id,
                    "status": "queued",
                    "admission_reason": "memory_pressure",
                    "spoken_summary": "En attente de ressources.",
                },
            )
            return self.runs[run_id]

        def get(self, run_id: str) -> _RacingRun | None:
            return self.runs.get(run_id)

        def approvals(self, run_id: str) -> list[Any]:
            return []

        def artifacts(self, run_id: str) -> list[Any]:
            return []

        async def cancel(self, run_id: str) -> None:
            return None

    agentic = _RacingAgentic()
    service = _control(agentic, bus)
    agentic.control = service

    task = await _approved_task(service, "Analyser le dépôt")

    # Avant le correctif : les deux événements ne trouvaient aucune tâche, puis
    # `_launch_run` écrasait tout avec RUNNING.
    assert task.status is TaskStatus.RESOURCE_WAIT
    assert task.status is not TaskStatus.RUNNING
    summaries = _summaries(service, task.task_id)
    assert any("En attente de ressources" in item for item in summaries)
    assert any("mémoire insuffisante" in item for item in summaries)


@pytest.mark.asyncio
async def test_le_lancement_nefface_pas_letat_deja_atteint(sync_db: Path):
    """La relecture d'après-lancement ne régresse jamais un état plus avancé."""

    bus = EventBus()

    @dataclass
    class _FastRun:
        run_id: str
        status: Any = None
        started_at: datetime | None = None
        finished_at: datetime | None = None
        verification: Any = None

    class _FastAgentic:
        def __init__(self) -> None:
            self.starts: list[dict[str, Any]] = []
            self.control: TaskControlService | None = None

        async def create_and_start(self, **kwargs: Any) -> _FastRun:
            run_id = str(kwargs["run_id"])
            self.starts.append(kwargs)
            assert self.control is not None
            await self.control.on_runtime_event(
                "agent.run.started", {"run_id": run_id, "status": "running"}
            )
            return _FastRun(run_id=run_id)

        def get(self, run_id: str) -> _FastRun | None:
            return _FastRun(run_id=run_id)

        def approvals(self, run_id: str) -> list[Any]:
            return []

        def artifacts(self, run_id: str) -> list[Any]:
            return []

        async def cancel(self, run_id: str) -> None:
            return None

    agentic = _FastAgentic()
    service = _control(agentic, bus)
    agentic.control = service

    task = await _approved_task(service, "Tâche rapide")
    assert task.status is TaskStatus.RUNNING


# ── Traduction des états ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_queued_puis_provisioning_puis_running(sync_db: Path):
    """`running` n'apparaît qu'avec `agent.run.started`, pas avant."""

    from tests.test_task_control_service import FakeAgenticService

    service = _control(FakeAgenticService(), EventBus())
    task = await _approved_task(service, "Suivre les états")
    run_id = task.agentic_run_id
    assert task.status is TaskStatus.QUEUED

    queued = await service.on_runtime_event(
        "agent.run.queued", {"run_id": run_id, "status": "queued"}
    )
    assert queued.status is TaskStatus.QUEUED

    provisioning = await service.on_runtime_event(
        "agent.run.provisioning",
        {"run_id": run_id, "status": "provisioning", "phase": "workspace"},
    )
    # La préparation de l'espace de travail n'est pas de l'exécution.
    assert provisioning.status is TaskStatus.QUEUED
    assert provisioning.current_phase == "workspace"

    running = await service.on_runtime_event(
        "agent.run.started", {"run_id": run_id, "status": "running"}
    )
    assert running.status is TaskStatus.RUNNING


@pytest.mark.asyncio
async def test_rejeu_et_ordre_hors_sequence_sont_idempotents(sync_db: Path):
    from tests.test_task_control_service import FakeAgenticService

    service = _control(FakeAgenticService(), EventBus())
    task = await _approved_task(service, "Rejeu")
    run_id = task.agentic_run_id

    for _ in range(3):
        current = await service.on_runtime_event(
            "agent.run.started", {"run_id": run_id, "status": "running"}
        )
    assert current.status is TaskStatus.RUNNING

    # Un `queued` en retard ne fait pas reculer une tâche déjà en cours.
    late = await service.on_runtime_event(
        "agent.run.queued", {"run_id": run_id, "status": "queued"}
    )
    assert late.status is TaskStatus.RUNNING

    for _ in range(2):
        done = await service.on_runtime_event(
            "agent.run.completed",
            {"run_id": run_id, "status": "completed", "progress": 1.0},
        )
    assert done.status is TaskStatus.COMPLETED
    report = service.repository.latest_report(task.task_id)
    assert report is not None and report.version == 1
    assert len(service.agentic.starts) == 1


@pytest.mark.asyncio
async def test_double_start_execution_simultane_ne_cree_quun_run(sync_db: Path):
    from tests.test_task_control_service import FakeAgenticService

    service = _control(FakeAgenticService(), EventBus())
    task = await service.create_task(title="Un seul run", metadata=dict(_ROUTING))
    task = await service.decide_plan(
        task.task_id,
        1,
        decision=PlanDecision.APPROVED,
        actor="session:1",
        autostart=False,
    )

    results = await asyncio.gather(
        service.start_execution(task.task_id),
        service.start_execution(task.task_id),
    )
    assert len(service.agentic.starts) == 1
    assert len({item.agentic_run_id for item in results}) == 1


# ── Avec le vrai service agentique ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_pression_memoire_affiche_lattente_de_ressources(
    sync_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(config, "AGENTIC_MIN_FREE_MEMORY_MB", 2048)
    monkeypatch.setattr(config, "AGENTIC_MAX_QUEUE_WAIT_S", 120)
    bus = EventBus()
    agentic = AgenticService(
        registry=RuntimeRegistry(discover_runtime_plugins(_plugin(tmp_path, code=_RUNTIME_CODE))),
        bus=bus,
        read_free_memory_mb=lambda: 128.0,
    )
    service = _control(agentic, bus)
    try:
        task = await _approved_task(service, "Analyser sous pression mémoire")
        run_id = task.agentic_run_id
        assert run_id is not None

        await _until(
            lambda: service.repository.require_task(task.task_id).status
            is TaskStatus.RESOURCE_WAIT
        )
        current = service.repository.require_task(task.task_id)
        run = agentic.get(run_id)

        assert run is not None and run.status is AgenticRunStatus.QUEUED
        assert current.status is TaskStatus.RESOURCE_WAIT
        assert current.status is not TaskStatus.RUNNING
        assert any(
            "En attente de ressources" in item
            for item in _summaries(service, task.task_id)
        )
    finally:
        await agentic.dispose()


@pytest.mark.asyncio
async def test_annulation_en_file_termine_et_interdit_toute_reprise(
    sync_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(config, "AGENTIC_MIN_FREE_MEMORY_MB", 2048)
    monkeypatch.setattr(config, "AGENTIC_MAX_QUEUE_WAIT_S", 120)
    bus = EventBus()
    agentic = AgenticService(
        registry=RuntimeRegistry(discover_runtime_plugins(_plugin(tmp_path, code=_RUNTIME_CODE))),
        bus=bus,
        read_free_memory_mb=lambda: 128.0,
    )
    service = _control(agentic, bus)
    try:
        task = await _approved_task(service, "Annuler pendant la file")
        run_id = task.agentic_run_id
        await _until(
            lambda: service.repository.require_task(task.task_id).status
            is TaskStatus.RESOURCE_WAIT
        )

        cancelled = await service.cancel_task(task.task_id, reason="Plus nécessaire")
        assert cancelled.status is TaskStatus.CANCELLED
        assert "Plus nécessaire" in _summaries(service, task.task_id)
        assert service.repository.latest_report(task.task_id) is not None

        # Le run ne repart pas et aucun second run n'est créé.
        again = await service.start_execution(task.task_id)
        assert again.agentic_run_id == run_id
        assert again.status is TaskStatus.CANCELLED
        await asyncio.sleep(0.05)
        run = agentic.get(run_id)
        assert run is not None
        assert run.status is not AgenticRunStatus.RUNNING
    finally:
        await agentic.dispose()


@pytest.mark.asyncio
async def test_concurrence_1_garde_la_seconde_tache_en_file_puis_la_reprend(
    sync_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(config, "AGENTIC_MIN_FREE_MEMORY_MB", 128)
    monkeypatch.setattr(config, "AGENTIC_MAX_CONCURRENT_RUNS", 1)
    monkeypatch.setattr(config, "AGENTIC_MAX_QUEUE_WAIT_S", 600)
    bus = EventBus()
    agentic = AgenticService(
        registry=RuntimeRegistry(discover_runtime_plugins(_plugin(tmp_path, code=_RUNTIME_CODE))),
        bus=bus,
        read_free_memory_mb=lambda: 8192.0,
    )
    service = _control(agentic, bus)
    try:
        first = await _approved_task(service, "Première tâche")
        await _until(
            lambda: agentic.get(first.agentic_run_id) is not None
            and agentic.get(first.agentic_run_id).status is AgenticRunStatus.RUNNING
        )
        second = await _approved_task(service, "Seconde tâche")

        await asyncio.sleep(0.1)
        assert (
            agentic.get(second.agentic_run_id).status is AgenticRunStatus.QUEUED
        )
        # La seconde ne prétend pas travailler pendant que la première occupe
        # l'unique place.
        assert (
            service.repository.require_task(second.task_id).status
            is not TaskStatus.RUNNING
        )

        # La première libère la place : la seconde est reprise (FIFO).
        await agentic.cancel(first.agentic_run_id)
        assert await _until(
            lambda: agentic.get(second.agentic_run_id).status
            is AgenticRunStatus.RUNNING
        )
        assert await _until(
            lambda: service.repository.require_task(second.task_id).status
            is TaskStatus.RUNNING
        )
    finally:
        await agentic.dispose()
