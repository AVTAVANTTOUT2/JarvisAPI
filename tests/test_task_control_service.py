"""Parcours complet : capture → plan → validation → exécution → rapport.

Le runtime d'exécution est remplacé par un double qui compte ses démarrages.
C'est la façon la plus directe de vérifier l'invariant : si un chemin lance
une exécution sans approbation, le compteur le dit.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
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
from jarvis.task_control.service import TaskControlService, resolve_execution_grant


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
    # Le runtime n'a encore rien annoncé : la tâche est confiée, pas en cours.
    assert task.status is TaskStatus.QUEUED
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
        entry["title"] == "Autorisation requise"
        for entry in service.notifications.created
    )


@pytest.mark.asyncio
async def test_evenement_dun_run_inconnu_est_ignore(service):
    assert (
        await service.on_runtime_event("agent.run.started", {"run_id": "run_x"}) is None
    )


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
async def test_engineering_receipts_render_tests_commit_and_branch(service):
    task = await service.create_task(title="Livrer le correctif")
    task = await service.decide_plan(
        task.task_id, 1, decision=PlanDecision.APPROVED, actor="session:1"
    )

    class _Receipt:
        artifact_id = ""
        reference = ""
        sha256 = "a" * 64
        size_bytes = 100

        def __init__(self, kind: str, details: dict[str, Any]) -> None:
            self.type = kind
            self.artifact_id = f"receipt:{kind}"
            self.reference = f"jarvis://receipts/{self.artifact_id}"
            self.metadata = {"details": details}

    service.agentic.artifacts_by_run[task.agentic_run_id] = [
        _Receipt(
            "jarvis_test_receipt",
            {
                "validations": [
                    {
                        "command": ["python3", "-m", "pytest", "tests/test_fix.py", "-q"],
                        "returncode": 0,
                    }
                ]
            },
        ),
        _Receipt(
            "jarvis_effect_receipt",
            {"commit_sha": "b" * 40, "branch_name": "jarvis/agentic/fix"},
        ),
    ]
    await service.on_runtime_event(
        "agent.run.completed",
        {"run_id": task.agentic_run_id, "status": "completed", "progress": 1.0},
    )

    report = service.repository.latest_report(task.task_id)
    assert report is not None
    assert "_Aucun test exécuté._" not in report.markdown
    assert "tests/test_fix.py" in report.markdown
    assert "b" * 40 in report.markdown
    assert "jarvis/agentic/fix" in report.markdown
    assert report.data["tests"] == [
        "`python3 -m pytest tests/test_fix.py -q` — réussi (code 0)"
    ]


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


@pytest.mark.asyncio
async def test_agentic_planning_failure_never_starts_without_approval(
    monkeypatch: pytest.MonkeyPatch,
):
    from api import agentic_processing
    from jarvis.task_control import ingest as ingest_module

    async def unavailable(*_args, **_kwargs):
        return None

    monkeypatch.setattr(ingest_module, "create_task_from_user_request", unavailable)

    response = await agentic_processing._plan_instead_of_running(
        "analyse mes messages et prépare un rapport",
        conversation_id=42,
        channel="websocket",
        voice_mode=False,
        persist_assistant=False,
    )

    assert response is not None
    assert response["action"] is None
    assert response["action_result"] == {
        "ok": False,
        "accepted": False,
        "awaiting_plan_approval": False,
        "error": "planning_unavailable",
    }
    assert "Rien n'a été lancé" in response["text"]


# ── Fidélité des permissions approuvées ────────────────────────────────────
#
# Le plan lu par l'utilisateur doit annoncer exactement les capacités remises
# au runtime. Avant ce contrat, le plan affichait la liste du planificateur et
# le run recevait celle du profil de capacités : deux sources, un consentement
# faux.


def _routing(category: str, profile_id: str, permissions: list[str]) -> dict[str, Any]:
    from jarvis.agentic.turn_context import AGENTIC_ROUTING_METADATA_KEY

    return {
        AGENTIC_ROUTING_METADATA_KEY: {
            "category": category,
            "capability_profile_id": profile_id,
            "permissions": permissions,
        }
    }


@pytest.mark.asyncio
async def test_plan_de_lecture_affiche_les_permissions_donnees_au_run(service):
    task = await service.create_task(
        title="Analyser mes messages",
        metadata=_routing(
            "agentic_readonly", "readonly-research", ["workspace:read", "memory:read"]
        ),
    )
    plan = service.repository.get_plan(task.task_id, 1)
    assert plan.execution_permissions == ("workspace:read", "memory:read")

    await service.decide_plan(
        task.task_id, 1, decision=PlanDecision.APPROVED, actor="session:1"
    )
    assert service.agentic.starts[0]["permissions"] == plan.execution_permissions


@pytest.mark.asyncio
async def test_plan_decriture_annonce_workspace_write_et_tests_run(service):
    task = await service.create_task(
        title="Crée une petite application HTML de liste de tâches",
        metadata=_routing(
            "agentic_reversible",
            "coding",
            ["workspace:read", "workspace:write", "tests:run"],
        ),
    )
    plan = service.repository.get_plan(task.task_id, 1)
    # Le défaut d'origine : ces deux permissions n'apparaissaient qu'après
    # l'approbation. Elles doivent être lisibles avant la décision.
    assert "workspace:write" in plan.execution_permissions
    assert "tests:run" in plan.execution_permissions
    assert plan.to_dict()["execution_permissions"] == list(plan.execution_permissions)

    # Même sans métadonnées de routage (tâche saisie à la main), une demande
    # d'écriture dérive le profil `coding` : le plan annonce l'écriture au lieu
    # de la masquer.
    manual = service.repository.require_task(task.task_id)
    derived = resolve_execution_grant(
        replace(manual, metadata={}),
        "Crée une petite application HTML de liste de tâches sur mon Bureau",
    )
    assert derived.capability_profile_id == "coding"
    assert "workspace:write" in derived.permissions

    await service.decide_plan(
        task.task_id, 1, decision=PlanDecision.APPROVED, actor="session:1"
    )
    assert service.agentic.starts[0]["permissions"] == plan.execution_permissions


@pytest.mark.asyncio
async def test_les_permissions_entrent_dans_le_digest(service):
    from dataclasses import replace as dataclass_replace

    from jarvis.task_control.models import compute_plan_digest

    task = await service.create_task(title="Comparer deux devis")
    plan = service.repository.get_plan(task.task_id, 1)
    elevated = dataclass_replace(
        plan, execution_permissions=plan.execution_permissions + ("workspace:write",)
    )
    assert compute_plan_digest(elevated) != plan.digest


@pytest.mark.asyncio
async def test_elevation_apres_approbation_refusee_avant_tout_runtime(service):
    task = await service.create_task(
        title="Analyser le dépôt",
        metadata=_routing("agentic_readonly", "readonly-research", ["workspace:read"]),
    )
    task = await service.decide_plan(
        task.task_id,
        1,
        decision=PlanDecision.APPROVED,
        actor="session:1",
        autostart=False,
    )
    assert service.agentic.starts == []

    # Le routage réclame maintenant l'écriture : le plan approuvé ne l'annonçait
    # pas, donc rien ne démarre.
    service.repository.update_task(
        task.task_id,
        metadata=_routing(
            "agentic_reversible",
            "coding",
            ["workspace:read", "workspace:write", "tests:run"],
        ),
    )
    with pytest.raises(TaskExecutionRefused):
        await service.start_execution(task.task_id)
    assert service.agentic.starts == []
    assert service.repository.require_task(task.task_id).agentic_run_id is None


@pytest.mark.asyncio
async def test_revision_avec_permissions_differentes_exige_une_nouvelle_approbation(
    service,
):
    task = await service.create_task(
        title="Analyser le dépôt",
        metadata=_routing("agentic_readonly", "readonly-research", ["workspace:read"]),
    )
    first = service.repository.get_plan(task.task_id, 1)
    service.repository.update_task(
        task.task_id,
        metadata=_routing(
            "agentic_reversible",
            "coding",
            ["workspace:read", "workspace:write", "tests:run"],
        ),
    )
    task = await service.decide_plan(
        task.task_id,
        1,
        decision=PlanDecision.REVISION_REQUESTED,
        actor="session:1",
        comment="Il faut aussi écrire le correctif",
    )

    second = service.repository.get_plan(task.task_id, 2)
    assert second.execution_permissions != first.execution_permissions
    assert second.digest != first.digest
    assert task.status is TaskStatus.AWAITING_PLAN_APPROVAL
    assert task.approved_plan_version is None
    assert service.agentic.starts == []

    await service.decide_plan(
        task.task_id, 2, decision=PlanDecision.APPROVED, actor="session:1"
    )
    assert service.agentic.starts[0]["permissions"] == second.execution_permissions


@pytest.mark.asyncio
async def test_revision_retire_les_permissions_interdites(service):
    task = await service.create_task(
        title="Corrige la régression login et lance les tests",
        metadata=_routing(
            "agentic_reversible",
            "coding",
            ["workspace:read", "workspace:write", "tests:run"],
        ),
    )
    first = service.repository.get_plan(task.task_id, 1)
    assert "tests:run" in first.execution_permissions

    task = await service.decide_plan(
        task.task_id,
        1,
        decision=PlanDecision.REVISION_REQUESTED,
        actor="session:1",
        comment="Ne lance pas les tests",
    )
    second = service.repository.get_plan(task.task_id, 2)
    assert "tests:run" not in second.execution_permissions
    assert second.digest != first.digest


@pytest.mark.asyncio
async def test_ancien_plan_sans_permissions_est_refuse_fail_closed(service):
    """Un plan approuvé avant ce contrat ne démarre pas et n'hérite de rien."""

    task = await service.create_task(title="Tâche héritée")
    with database.get_db() as conn:
        conn.execute(
            "UPDATE control_task_plans SET execution_permissions_json = '[]' "
            "WHERE task_id = ?",
            (task.task_id,),
        )
    legacy = service.repository.get_plan(task.task_id, 1)
    assert legacy.execution_permissions == ()

    task = await service.decide_plan(
        task.task_id,
        1,
        decision=PlanDecision.APPROVED,
        actor="session:1",
        autostart=False,
    )
    with pytest.raises(TaskExecutionRefused):
        await service.start_execution(task.task_id)
    assert service.agentic.starts == []


@pytest.mark.asyncio
async def test_le_run_persiste_exactement_les_permissions_approuvees(
    task_db: Path, monkeypatch: pytest.MonkeyPatch
):
    """L'invariant de bout en bout, avec le vrai service agentique.

    Le défaut d'origine se voyait sur la ligne persistée du run : le plan lu
    annonçait `workspace:read`, le run stocké portait aussi `workspace:write`
    et `tests:run`. Ce test lit la même ligne.
    """

    from jarvis.agentic.service import AgenticService

    monkeypatch.setattr(config, "AGENTIC_RUNTIME_FALLBACK", "disabled")
    agentic = AgenticService()
    service = TaskControlService(
        agentic_service=agentic,
        notifications=FakeNotifications(),
        bus=EventBus(),
        planner=_stub_planner,
        detector=TaskCandidateDetector(),
    )
    try:
        task = await service.create_task(
            title="Crée une petite application HTML",
            metadata=_routing(
                "agentic_reversible",
                "coding",
                ["workspace:read", "workspace:write", "tests:run"],
            ),
        )
        plan = service.repository.get_plan(task.task_id, 1)
        task = await service.decide_plan(
            task.task_id, 1, decision=PlanDecision.APPROVED, actor="session:1"
        )

        run = agentic.get(task.agentic_run_id)
        assert run is not None
        assert run.permissions == plan.execution_permissions
    finally:
        await agentic.dispose()


def test_migration_ajoute_la_colonne_sans_accorder_de_droits(tmp_path: Path):
    """Une base d'avant le contrat gagne la colonne, vide — pas des droits."""

    import sqlite3

    from database.task_control import TASK_CONTROL_SCHEMA, migrate_task_control_tables

    legacy_schema = TASK_CONTROL_SCHEMA.replace(
        "    execution_permissions_json TEXT NOT NULL DEFAULT '[]',\n", ""
    )
    assert "execution_permissions_json" not in legacy_schema

    conn = sqlite3.connect(tmp_path / "legacy.db")
    try:
        conn.executescript(legacy_schema)
        migrate_task_control_tables(conn)
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(control_task_plans)")
        }
        assert "execution_permissions_json" in columns
        # Idempotence : rejouer la migration ne casse rien.
        migrate_task_control_tables(conn)
    finally:
        conn.close()
