"""Parcours complet : capture → plan → validation → exécution → rapport.

Le runtime d'exécution est remplacé par un double qui compte ses démarrages.
C'est la façon la plus directe de vérifier l'invariant : si un chemin lance
une exécution sans approbation, le compteur le dit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

import config
import database
from jarvis.event_bus import EventBus
from jarvis.task_control.detection import DetectedTask, TaskCandidateDetector
from jarvis.task_control.models import (
    CandidateDecision,
    InvalidTaskTransition,
    PlanDecision,
    PlanStep,
    TaskExecutionRefused,
    TaskPlan,
    TaskSource,
    TaskSourceChannel,
    TaskSourceType,
    TaskStatus,
    new_id,
)
from jarvis.task_control.service import TaskControlService


# ── Doubles ────────────────────────────────────────────────────────────────


@dataclass
class FakeRun:
    run_id: str
    status: Any = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    verification: Any = None


@dataclass
class FakeAgenticService:
    """Runtime factice. Compte les démarrages — c'est tout ce qui compte ici."""

    starts: list[dict[str, Any]] = field(default_factory=list)
    cancels: list[str] = field(default_factory=list)
    approvals_by_run: dict[str, list[Any]] = field(default_factory=dict)
    artifacts_by_run: dict[str, list[Any]] = field(default_factory=dict)
    decisions: list[tuple[str, str, str]] = field(default_factory=list)
    fail_start: bool = False

    async def create_and_start(self, **kwargs: Any) -> FakeRun:
        if self.fail_start:
            raise RuntimeError("runtime indisponible")
        self.starts.append(kwargs)
        return FakeRun(run_id=f"run_{len(self.starts)}")

    async def cancel(self, run_id: str) -> None:
        self.cancels.append(run_id)

    async def decide_approval(self, run_id, approval_id, *, decision, actor):
        self.decisions.append((run_id, approval_id, decision.value))
        return FakeRun(run_id=run_id)

    def get(self, run_id: str) -> FakeRun | None:
        return FakeRun(run_id=run_id)

    def approvals(self, run_id: str) -> list[Any]:
        return self.approvals_by_run.get(run_id, [])

    def artifacts(self, run_id: str) -> list[Any]:
        return self.artifacts_by_run.get(run_id, [])


@dataclass
class FakeNotifications:
    created: list[dict[str, Any]] = field(default_factory=list)

    def create(self, **kwargs: Any) -> int:
        self.created.append(kwargs)
        return len(self.created)


async def _stub_planner(task, *, version: int, context=None) -> TaskPlan:
    return TaskPlan(
        plan_id=new_id("plan"),
        task_id=task.task_id,
        version=version,
        objective=f"Objectif v{version}",
        summary="Plan de test",
        steps=(
            PlanStep(index=1, title="Lire le contexte", tools=("read_file",)),
            PlanStep(index=2, title="Produire le livrable"),
        ),
        expected_deliverables=("Rapport",),
    )


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def task_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "task_control.db"
    monkeypatch.setattr(config, "DB_PATH", str(path))
    monkeypatch.setattr(database, "DB_PATH", path)
    database.init_db()
    return path


@pytest.fixture
def service(task_db: Path) -> TaskControlService:
    return TaskControlService(
        agentic_service=FakeAgenticService(),
        notifications=FakeNotifications(),
        bus=EventBus(),
        planner=_stub_planner,
        detector=TaskCandidateDetector(),
    )


# ── Création et planification ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_creation_manuelle_sarrete_a_lattente_de_plan(service):
    task = await service.create_task(title="Préparer le rapport trimestriel")
    assert task.status is TaskStatus.AWAITING_PLAN_APPROVAL
    assert task.plan_version == 1
    assert task.approved_plan_version is None
    assert service.agentic.starts == []


@pytest.mark.asyncio
async def test_le_plan_est_lisible_et_versionne(service):
    task = await service.create_task(title="Analyser le dépôt")
    plans = service.repository.list_plans(task.task_id)
    assert [plan.version for plan in plans] == [1]
    assert plans[0].decision is PlanDecision.PENDING
    assert len(plans[0].steps) == 2


@pytest.mark.asyncio
async def test_creation_sans_autoplan_reste_en_created(service):
    task = await service.create_task(title="Sans plan", autoplan=False)
    assert task.status is TaskStatus.CREATED
    assert service.agentic.starts == []


# ── Décisions de plan ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approbation_declenche_lexecution_une_seule_fois(service):
    task = await service.create_task(title="Rédiger la note")
    task = await service.decide_plan(
        task.task_id, 1, decision=PlanDecision.APPROVED, actor="session:1"
    )
    assert task.status is TaskStatus.RUNNING
    assert task.agentic_run_id == "run_1"
    assert len(service.agentic.starts) == 1

    # Un second appel ne relance pas : l'approbation a déjà été consommée.
    again = await service.start_execution(task.task_id)
    assert again.agentic_run_id == "run_1"
    assert len(service.agentic.starts) == 1


@pytest.mark.asyncio
async def test_le_plan_approuve_est_transmis_au_runtime(service):
    task = await service.create_task(title="Corriger le devis")
    await service.decide_plan(
        task.task_id, 1, decision=PlanDecision.APPROVED, actor="session:1"
    )
    context = service.agentic.starts[0]["selected_context"]
    assert context["plan_version"] == 1
    assert context["plan_digest"]
    assert context["plan_steps"] == ["Lire le contexte", "Produire le livrable"]


@pytest.mark.asyncio
async def test_refus_du_plan_nexecute_rien(service):
    task = await service.create_task(title="Envoyer la relance")
    task = await service.decide_plan(
        task.task_id,
        1,
        decision=PlanDecision.REJECTED,
        actor="session:1",
        comment="Pas maintenant",
    )
    assert task.status is TaskStatus.PLAN_REJECTED
    assert service.agentic.starts == []


@pytest.mark.asyncio
async def test_revision_produit_une_nouvelle_version_a_valider(service):
    task = await service.create_task(title="Préparer la réunion")
    task = await service.decide_plan(
        task.task_id,
        1,
        decision=PlanDecision.REVISION_REQUESTED,
        actor="session:1",
        comment="Ajoute une relecture",
    )
    assert task.status is TaskStatus.AWAITING_PLAN_APPROVAL
    assert task.plan_version == 2
    assert task.approved_plan_version is None
    assert service.agentic.starts == []


@pytest.mark.asyncio
async def test_une_version_deja_decidee_ne_se_redecide_pas(service):
    task = await service.create_task(title="Classer les documents")
    await service.decide_plan(
        task.task_id, 1, decision=PlanDecision.APPROVED, actor="session:1"
    )
    with pytest.raises(InvalidTaskTransition):
        await service.decide_plan(
            task.task_id, 1, decision=PlanDecision.REJECTED, actor="session:1"
        )


@pytest.mark.asyncio
async def test_decider_une_version_perimee_est_refuse(service):
    """L'écran affichait v1 ; la tâche en est à v2. Approuver v1 serait aveugle."""

    task = await service.create_task(title="Mettre à jour le suivi")
    await service.decide_plan(
        task.task_id,
        1,
        decision=PlanDecision.REVISION_REQUESTED,
        actor="session:1",
        comment="autre angle",
    )
    from database.task_control import TaskPersistenceConflict

    with pytest.raises(TaskPersistenceConflict):
        await service.decide_plan(
            task.task_id, 1, decision=PlanDecision.APPROVED, actor="session:1"
        )
    assert service.agentic.starts == []


@pytest.mark.asyncio
async def test_demarrage_impossible_sans_approbation(service):
    task = await service.create_task(title="Tâche non approuvée")
    with pytest.raises(TaskExecutionRefused):
        await service.start_execution(task.task_id)
    assert service.agentic.starts == []


@pytest.mark.asyncio
async def test_digest_falsifie_bloque_le_demarrage(service):
    """Simule une base modifiée entre l'approbation et le démarrage."""

    task = await service.create_task(title="Tâche sensible")
    await service.decide_plan(
        task.task_id,
        1,
        decision=PlanDecision.APPROVED,
        actor="session:1",
        autostart=False,
    )
    service.repository.update_task(task.task_id, approved_plan_digest="0" * 64)
    with pytest.raises(TaskExecutionRefused, match="changé"):
        await service.start_execution(task.task_id)
    assert service.agentic.starts == []


# ── Exécution et activité ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_les_evenements_runtime_deviennent_de_lactivite(service):
    task = await service.create_task(title="Analyser le code")
    task = await service.decide_plan(
        task.task_id, 1, decision=PlanDecision.APPROVED, actor="session:1"
    )
    await service.on_runtime_event(
        "agent.tool.started",
        {"run_id": task.agentic_run_id, "tool": "read_file", "status": "running"},
    )
    entries = service.activity(task.task_id)
    summaries = [entry.summary for entry in entries]
    assert any("Lecture d'un fichier" in item for item in summaries)


@pytest.mark.asyncio
async def test_lattente_dautorisation_remonte_en_attention(service):
    task = await service.create_task(title="Envoyer le rapport")
    task = await service.decide_plan(
        task.task_id, 1, decision=PlanDecision.APPROVED, actor="session:1"
    )
    updated = await service.on_runtime_event(
        "agent.approval.requested",
        {
            "run_id": task.agentic_run_id,
            "status": "awaiting_approval",
            "approval_id": "ap_1",
            "action": "envoyer un e-mail",
        },
    )
    assert updated.status is TaskStatus.AWAITING_PERMISSION
    assert updated.needs_attention is True
    assert any(
        entry["title"] == "Autorisation requise" for entry in service.notifications.created
    )


@pytest.mark.asyncio
async def test_evenement_dun_run_inconnu_est_ignore(service):
    assert await service.on_runtime_event("agent.run.started", {"run_id": "run_x"}) is None


@pytest.mark.asyncio
async def test_completion_produit_un_rapport_avec_lieu_de_livraison(service):
    task = await service.create_task(title="Générer le bilan")
    task = await service.decide_plan(
        task.task_id, 1, decision=PlanDecision.APPROVED, actor="session:1"
    )

    class _Artifact:
        artifact_id = "art_1"
        type = "file"
        reference = "data/outputs/bilan.md"
        sha256 = None
        size_bytes = 42

    service.agentic.artifacts_by_run[task.agentic_run_id] = [_Artifact()]
    updated = await service.on_runtime_event(
        "agent.run.completed",
        {"run_id": task.agentic_run_id, "status": "completed", "progress": 1.0},
    )
    assert updated.status is TaskStatus.COMPLETED
    report = service.repository.latest_report(task.task_id)
    assert report is not None
    assert report.result_status == "completed"
    assert "data/outputs/bilan.md" in report.markdown
    assert report.data["deliveries"][0]["reference"] == "data/outputs/bilan.md"


@pytest.mark.asyncio
async def test_echec_de_demarrage_ne_laisse_pas_la_tache_en_route(service):
    service.agentic.fail_start = True
    task = await service.create_task(title="Tâche impossible")
    task = await service.decide_plan(
        task.task_id, 1, decision=PlanDecision.APPROVED, actor="session:1"
    )
    assert task.status is TaskStatus.FAILED
    assert task.result_status == "failed"


# ── Annulation ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_annulation_previent_le_runtime_et_produit_un_rapport(service):
    task = await service.create_task(title="Longue analyse")
    task = await service.decide_plan(
        task.task_id, 1, decision=PlanDecision.APPROVED, actor="session:1"
    )
    cancelled = await service.cancel_task(task.task_id, reason="plus nécessaire")
    assert cancelled.status is TaskStatus.CANCELLED
    assert service.agentic.cancels == [task.agentic_run_id]
    report = service.repository.latest_report(task.task_id)
    assert report is not None and report.result_status == "cancelled"


@pytest.mark.asyncio
async def test_annulation_avant_approbation_nappelle_pas_le_runtime(service):
    task = await service.create_task(title="À jeter")
    cancelled = await service.cancel_task(task.task_id)
    assert cancelled.status is TaskStatus.CANCELLED
    assert service.agentic.cancels == []


# ── Commentaires et périmètre ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_un_commentaire_ne_modifie_pas_le_plan_approuve(service):
    task = await service.create_task(title="Rédiger la synthèse")
    task = await service.decide_plan(
        task.task_id, 1, decision=PlanDecision.APPROVED, actor="session:1"
    )
    digest_before = task.approved_plan_digest
    await service.add_comment(task.task_id, "en fait, ajoute aussi les annexes")
    after = service.repository.require_task(task.task_id)
    assert after.approved_plan_digest == digest_before
    assert after.approved_plan_version == 1


@pytest.mark.asyncio
async def test_revision_de_perimetre_exige_une_nouvelle_validation(service):
    task = await service.create_task(title="Rédiger la synthèse")
    task = await service.decide_plan(
        task.task_id, 1, decision=PlanDecision.APPROVED, actor="session:1"
    )
    run_id = task.agentic_run_id
    revised = await service.request_plan_revision(task.task_id, "périmètre élargi")
    assert revised.status is TaskStatus.AWAITING_PLAN_APPROVAL
    assert revised.approved_plan_version is None
    assert revised.plan_version == 2
    # Le run est arrêté, mais la tâche n'est pas « annulée » : elle attend
    # une nouvelle validation, et aucun second run n'a démarré.
    assert service.agentic.cancels == [run_id]
    assert revised.agentic_run_id is None
    assert len(service.agentic.starts) == 1


# ── Détection ──────────────────────────────────────────────────────────────


def _detected(confidence: float, reference: str = "email:1") -> DetectedTask:
    return DetectedTask(
        is_actionable=True,
        confidence=confidence,
        suggested_title="Envoyer le rapport avant vendredi",
        suggested_description="Peux-tu envoyer le rapport avant vendredi ?",
        reason="formulation de demande",
        source=TaskSource(
            source_type=TaskSourceType.EMAIL,
            channel=TaskSourceChannel.EMAIL,
            reference=reference,
        ),
        dedupe_key=f"key-{reference}",
    )


@pytest.mark.asyncio
async def test_confiance_moyenne_produit_un_candidat_sans_tache(service):
    candidate, task = await service.ingest_detection(_detected(0.5))
    assert candidate is not None
    assert task is None
    assert candidate.decision is CandidateDecision.PENDING
    assert service.agentic.starts == []


@pytest.mark.asyncio
async def test_confiance_forte_produit_une_tache_en_attente_de_plan(service):
    candidate, task = await service.ingest_detection(_detected(0.95))
    assert task is not None
    assert task.status is TaskStatus.AWAITING_PLAN_APPROVAL
    assert service.agentic.starts == []


@pytest.mark.asyncio
async def test_deuxieme_detection_de_la_meme_source_ne_duplique_pas(service):
    first, _ = await service.ingest_detection(_detected(0.5))
    second, _ = await service.ingest_detection(_detected(0.5))
    assert first.candidate_id == second.candidate_id
    assert len(service.repository.list_candidates()) == 1


@pytest.mark.asyncio
async def test_candidat_accepte_puis_refuse_reste_sans_execution(service):
    candidate, _ = await service.ingest_detection(_detected(0.5))
    task = await service.accept_candidate(candidate.candidate_id)
    assert task.status is TaskStatus.AWAITING_PLAN_APPROVAL
    await service.decide_plan(
        task.task_id, 1, decision=PlanDecision.REJECTED, actor="session:1"
    )
    assert service.agentic.starts == []


@pytest.mark.asyncio
async def test_candidat_marque_faux_positif_ne_cree_pas_de_tache(service):
    candidate, _ = await service.ingest_detection(_detected(0.5))
    decided = service.decide_candidate(
        candidate.candidate_id, decision=CandidateDecision.FALSE_POSITIVE
    )
    assert decided.decision is CandidateDecision.FALSE_POSITIVE
    assert decided.created_task_id is None
    assert service.list_tasks() == []


# ── Porte de validation sur le chemin d'entrée agentique ───────────────────


@pytest.mark.asyncio
async def test_une_demande_agentique_devient_une_tache_a_valider(
    task_db, monkeypatch: pytest.MonkeyPatch
):
    """Le défaut du produit : une demande ne démarre pas, elle est planifiée."""

    import config as config_module
    from api import agentic_processing
    from jarvis.task_control import service as service_module

    monkeypatch.setattr(config_module, "AGENTIC_REQUIRE_PLAN_APPROVAL", True)
    control = TaskControlService(
        agentic_service=FakeAgenticService(),
        notifications=FakeNotifications(),
        bus=EventBus(),
        planner=_stub_planner,
        detector=TaskCandidateDetector(),
    )
    monkeypatch.setattr(service_module, "_service", control, raising=False)
    monkeypatch.setattr(agentic_processing, "save_message", lambda *a, **k: None)

    planned = await agentic_processing._plan_instead_of_running(
        "prépare-moi une analyse de ce dépôt",
        conversation_id=42,
        channel="voice",
        voice_mode=True,
        persist_assistant=False,
    )

    assert planned is not None
    assert planned["action_result"]["awaiting_plan_approval"] is True
    assert "validation" in planned["text"]
    assert control.agentic.starts == [], "aucune exécution n'a démarré"

    task = control.repository.require_task(planned["task_control"]["task_id"])
    assert task.status is TaskStatus.AWAITING_PLAN_APPROVAL
    assert task.source.source_type is TaskSourceType.USER_REQUEST
    assert task.source.channel is TaskSourceChannel.VOICE
