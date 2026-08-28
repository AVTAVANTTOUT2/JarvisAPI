"""Scénarios de bout en bout du pilotage de tâches.

Chaque test suit un parcours produit complet, du déclencheur au rapport, avec
un runtime factice qui compte ses démarrages et ses effets. Ce compteur est le
cœur du dispositif : si un chemin quelconque lance une exécution ou produit un
effet sans validation, il le dit.

Aucun e-mail, message ou commande réels ne sont émis — le transport d'effet
est un double qui enregistre ce qu'il aurait envoyé.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

import config
import database
from jarvis.event_bus import EventBus, JarvisEvent
from jarvis.task_control.context import (
    build_task_control_context,
    find_task_by_title,
    should_include_task_control,
)
from jarvis.task_control.detection import (
    DetectionInput,
    TaskCandidateDetector,
    detection_input_from_email,
    detection_input_from_message,
)
from jarvis.task_control.models import (
    PlanDecision,
    PlanStep,
    TaskPlan,
    TaskSourceChannel,
    TaskSourceType,
    TaskStatus,
    new_id,
)
from jarvis.task_control.service import TaskControlService


# ── Doubles ────────────────────────────────────────────────────────────────


@dataclass
class _Approval:
    approval_id: str
    action: str
    tool: str
    summary: str
    sanitized_arguments: dict[str, Any] = field(default_factory=dict)
    risks: tuple[str, ...] = ()
    scope: str = "run"
    expires_at: datetime | None = None
    decision: Any = field(default_factory=lambda: _Decision("pending"))
    decision_by: str | None = None
    decision_id: str | None = None
    decision_at: datetime | None = None


@dataclass
class _Artifact:
    artifact_id: str
    type: str
    reference: str
    sha256: str | None = None
    size_bytes: int | None = None


@dataclass
class _Run:
    run_id: str
    status: Any = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    verification: Any = None


class _Decision:
    """Substitut minimal d'`ApprovalDecision` du domaine agentique."""

    def __init__(self, value: str) -> None:
        self.value = value


@dataclass
class _Agentic:
    """Runtime factice. Le transport d'effet ne sort jamais de la mémoire."""

    starts: list[dict[str, Any]] = field(default_factory=list)
    cancels: list[str] = field(default_factory=list)
    sent_effects: list[str] = field(default_factory=list)
    approvals_by_run: dict[str, list[_Approval]] = field(default_factory=dict)
    artifacts_by_run: dict[str, list[_Artifact]] = field(default_factory=dict)
    runs: dict[str, _Run] = field(default_factory=dict)

    async def create_and_start(self, **kwargs: Any) -> _Run:
        self.starts.append(kwargs)
        run = _Run(run_id=f"run_{len(self.starts)}")
        self.runs[run.run_id] = run
        return run

    async def cancel(self, run_id: str) -> None:
        self.cancels.append(run_id)

    async def decide_approval(
        self,
        run_id,
        approval_id,
        decision,
        *,
        decided_by,
        decision_id,
    ):
        for approval in self.approvals_by_run.get(run_id, []):
            if approval.approval_id != approval_id:
                continue
            if getattr(approval.decision, "value", "pending") != "pending":
                if (
                    approval.decision.value == decision.value
                    and approval.decision_id == decision_id
                ):
                    return approval
                raise RuntimeError("approbation déjà décidée")
            approval.decision = decision
            approval.decision_by = decided_by
            approval.decision_id = decision_id
            approval.decision_at = datetime.now()
            if decision.value == "approved":
                # L'effet n'a lieu qu'ici : approuver le plan ne l'a pas
                # produit, et le refus n'y mène jamais.
                self.sent_effects.append(approval.action)
            approvals = self.approvals_by_run.get(run_id, [])
            pending = any(item.decision.value == "pending" for item in approvals)
            denied = any(item.decision.value == "denied" for item in approvals)
            self.runs[run_id].status = _Decision(
                "awaiting_approval" if pending else "blocked" if denied else "running"
            )
            return approval
        raise RuntimeError("approbation absente")

    def get(self, run_id: str) -> _Run:
        return self.runs[run_id]

    def approvals(self, run_id: str) -> list[_Approval]:
        return self.approvals_by_run.get(run_id, [])

    def artifacts(self, run_id: str) -> list[_Artifact]:
        return self.artifacts_by_run.get(run_id, [])


@dataclass
class _Notifications:
    created: list[dict[str, Any]] = field(default_factory=list)

    def create(self, **kwargs: Any) -> int:
        self.created.append(kwargs)
        return len(self.created)

    def titles(self) -> list[str]:
        return [item["title"] for item in self.created]


async def _planner(task, *, version: int, context=None) -> TaskPlan:
    return TaskPlan(
        plan_id=new_id("plan"),
        task_id=task.task_id,
        version=version,
        objective=f"Traiter : {task.title}",
        summary="Plan de test",
        steps=(
            PlanStep(index=1, title="Rassembler le contexte", tools=("read_file",)),
            PlanStep(index=2, title="Produire le livrable"),
        ),
        expected_deliverables=("Rapport",),
    )


@pytest.fixture
def e2e(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    path = tmp_path / "task-e2e.db"
    monkeypatch.setattr(config, "DB_PATH", str(path))
    monkeypatch.setattr(database, "DB_PATH", path)
    database.init_db()
    bus = EventBus()
    seen: list[JarvisEvent] = []

    @bus.on(
        [
            "task.control.created",
            "task.control.plan_ready",
            "task.control.plan_decided",
            "task.control.started",
            "task.control.permission_required",
            "task.control.completed",
            "task.control.failed",
            "task.control.cancelled",
            "task.control.candidate_detected",
        ]
    )
    async def _collect(event: JarvisEvent) -> None:
        seen.append(event)

    service = TaskControlService(
        agentic_service=_Agentic(),
        notifications=_Notifications(),
        bus=bus,
        planner=_planner,
        detector=TaskCandidateDetector(),
    )
    return service, seen


def _event_types(seen: list[JarvisEvent]) -> list[str]:
    return [event.type for event in seen]


async def _await_events(
    seen: list[JarvisEvent], *expected: str, timeout: float = 2.0
) -> list[str]:
    """Attend l'arrivée des événements attendus sur le bus.

    Le bus distribue aux handlers par files indépendantes : un événement émis
    n'est pas encore reçu à l'instruction suivante. Attendre est la bonne
    façon de le tester ; forcer une livraison synchrone testerait un bus qui
    n'existe pas en production.
    """

    import asyncio

    deadline = asyncio.get_running_loop().time() + timeout
    wanted = set(expected)
    while asyncio.get_running_loop().time() < deadline:
        if wanted.issubset(set(_event_types(seen))):
            break
        await asyncio.sleep(0.01)
    return _event_types(seen)


# ── Scénario A — création manuelle ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_scenario_a_creation_manuelle(e2e):
    service, seen = e2e

    task = await service.create_task(title="Préparer le rapport trimestriel")
    assert task.status is TaskStatus.AWAITING_PLAN_APPROVAL
    assert service.agentic.starts == []
    assert "Plan prêt à être vérifié" in service.notifications.titles()

    task = await service.decide_plan(
        task.task_id, 1, decision=PlanDecision.APPROVED, actor="session:1"
    )
    # L'approbation confie la tâche au runtime ; « en cours » attend un
    # événement réel du run.
    assert task.status is TaskStatus.QUEUED
    assert len(service.agentic.starts) == 1

    run_id = task.agentic_run_id
    service.agentic.artifacts_by_run[run_id] = [
        _Artifact(artifact_id="a1", type="file", reference="data/outputs/rapport.md")
    ]
    started = await service.on_runtime_event(
        "agent.run.started", {"run_id": run_id, "status": "running"}
    )
    assert started.status is TaskStatus.RUNNING
    await service.on_runtime_event(
        "agent.tool.started",
        {"run_id": run_id, "tool": "read_file", "status": "running"},
    )
    final = await service.on_runtime_event(
        "agent.run.completed", {"run_id": run_id, "status": "completed", "progress": 1.0}
    )

    assert final.status is TaskStatus.COMPLETED
    report = service.repository.latest_report(task.task_id)
    assert report.result_status == "completed"
    assert "data/outputs/rapport.md" in report.markdown

    types = await _await_events(
        seen,
        "task.control.created",
        "task.control.plan_ready",
        "task.control.started",
        "task.control.completed",
    )
    assert "task.control.created" in types
    assert "task.control.plan_ready" in types
    assert "task.control.started" in types
    assert "task.control.completed" in types
    # L'ordre compte : le plan est prêt avant tout démarrage.
    assert types.index("task.control.plan_ready") < types.index("task.control.started")


# ── Scénario B — demande vocale ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scenario_b_demande_vocale(e2e):
    service, seen = e2e
    from jarvis.task_control.models import TaskSource

    task = await service.create_task(
        title="Analyser ce dépôt",
        source=TaskSource(
            source_type=TaskSourceType.USER_REQUEST,
            channel=TaskSourceChannel.VOICE,
        ),
    )
    assert task.status is TaskStatus.AWAITING_PLAN_APPROVAL
    assert service.agentic.starts == []

    # Le contexte vocal voit la tâche en attente, sans avoir démarré quoi que
    # ce soit, et sait la retrouver par son titre.
    assert should_include_task_control("où en est ma tâche ?")
    context = build_task_control_context(service=service)
    assert "Attention requise" in context["task_control_context"]
    resolved = find_task_by_title("Analyser ce dépôt", service=service)
    assert resolved == task.task_id

    task = await service.decide_plan(
        task.task_id, 1, decision=PlanDecision.APPROVED, actor="session:macos"
    )
    assert task.status is TaskStatus.QUEUED

    run_id = task.agentic_run_id
    running = await service.on_runtime_event(
        "agent.run.phase_changed",
        {"run_id": run_id, "status": "running", "phase": "runtime_started", "progress": 0.4},
    )
    assert running.status is TaskStatus.RUNNING
    focused = build_task_control_context(service=service, focus_task_id=task.task_id)
    assert "Analyser ce dépôt" in focused["task_control_focus"]

    await service.on_runtime_event(
        "agent.run.completed", {"run_id": run_id, "status": "completed", "progress": 1.0}
    )
    report = service.repository.latest_report(task.task_id)
    assert report is not None
    assert "task.control.completed" in await _await_events(seen, "task.control.completed")


# ── Scénario C — détection e-mail, plan refusé ─────────────────────────────


@pytest.mark.asyncio
async def test_scenario_c_detection_email_puis_refus(e2e):
    service, _ = e2e
    detector = service.detector

    payload = detection_input_from_email(
        {
            "id": "42",
            "sender": "fournisseur@example.invalid",
            "subject": "Rapport mensuel",
            "body": "Bonjour, peux-tu envoyer le rapport avant vendredi ? Merci.",
        }
    )
    detections = detector.detect(payload)
    assert detections, "la demande aurait dû être repérée"

    candidate, task = await service.ingest_detection(detections[0])
    assert candidate is not None
    assert task is None, "confiance moyenne : candidat, pas tâche"
    assert service.agentic.starts == []

    task = await service.accept_candidate(candidate.candidate_id)
    assert task.status is TaskStatus.AWAITING_PLAN_APPROVAL
    assert service.agentic.starts == []

    task = await service.decide_plan(
        task.task_id,
        1,
        decision=PlanDecision.REJECTED,
        actor="session:1",
        comment="je m'en occupe moi-même",
    )
    assert task.status is TaskStatus.PLAN_REJECTED
    assert service.agentic.starts == [], "un refus ne lance jamais rien"


@pytest.mark.asyncio
async def test_scenario_c_bis_newsletter_ne_produit_rien(e2e):
    service, _ = e2e
    payload = detection_input_from_email(
        {
            "id": "43",
            "sender": "newsletter@example.invalid",
            "subject": "Notre newsletter du mois — désabonnement en bas",
            "body": "Peux-tu découvrir nos offres ? Merci de cliquer avant vendredi.",
        }
    )
    assert service.detector.detect(payload) == []
    assert service.repository.list_candidates() == []


@pytest.mark.asyncio
async def test_scenario_c_ter_message_de_lutilisateur_ignore(e2e):
    service, _ = e2e
    payload = detection_input_from_message(
        {"rowid": 7, "text": "peux-tu envoyer le rapport avant vendredi ?", "is_from_me": True}
    )
    assert service.detector.detect(payload) == []


# ── Scénario D — autorisation d'effet e-mail ───────────────────────────────


@pytest.mark.asyncio
async def test_scenario_d_permission_email_refusee_puis_accordee(e2e):
    service, seen = e2e

    task = await service.create_task(title="Répondre au fournisseur")
    task = await service.decide_plan(
        task.task_id, 1, decision=PlanDecision.APPROVED, actor="session:1"
    )
    run_id = task.agentic_run_id

    service.agentic.approvals_by_run[run_id] = [
        _Approval(
            approval_id="ap_1",
            action="Envoyer un e-mail au fournisseur",
            tool="mail_send",
            summary="Envoi du rapport",
            sanitized_arguments={"destinataire": "contact@example.invalid"},
            risks=("Message sortant définitif",),
        )
    ]
    task = await service.on_runtime_event(
        "agent.approval.requested",
        {
            "run_id": run_id,
            "status": "awaiting_approval",
            "approval_id": "ap_1",
            "action": "Envoyer un e-mail au fournisseur",
        },
    )
    assert task.status is TaskStatus.AWAITING_PERMISSION
    assert "Autorisation requise" in service.notifications.titles()
    assert "task.control.permission_required" in await _await_events(
        seen, "task.control.permission_required"
    )

    pending = service.pending_approvals(task.task_id)
    assert len(pending) == 1
    assert pending[0]["sanitized_arguments"]["destinataire"] == "contact@example.invalid"

    # Refus : rien ne part.
    await service.decide_effect_approval(
        task.task_id, "ap_1", approved=False, actor="session:1"
    )
    assert service.agentic.sent_effects == []

    # Une seconde demande, approuvée cette fois : l'effet a lieu une seule fois.
    service.agentic.approvals_by_run[run_id].append(
        _Approval(
            approval_id="ap_2",
            action="Envoyer un e-mail au fournisseur",
            tool="mail_send",
            summary="Envoi du rapport",
        )
    )
    await service.decide_effect_approval(
        task.task_id, "ap_2", approved=True, actor="session:1"
    )
    assert service.agentic.sent_effects == ["Envoyer un e-mail au fournisseur"]

    replay = await service.decide_effect_approval(
        task.task_id, "ap_2", approved=True, actor="session:1"
    )
    assert replay["decision"] == "approved"
    assert len(service.agentic.sent_effects) == 1, "l'effet n'est pas rejouable"


# ── Scénario E — annulation ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scenario_e_annulation(e2e):
    service, seen = e2e

    task = await service.create_task(title="Longue analyse")
    task = await service.decide_plan(
        task.task_id, 1, decision=PlanDecision.APPROVED, actor="session:1"
    )
    run_id = task.agentic_run_id

    cancelled = await service.cancel_task(task.task_id, reason="Annulée depuis macOS")
    assert cancelled.status is TaskStatus.CANCELLED
    assert service.agentic.cancels == [run_id], "le runtime a été prévenu"

    report = service.repository.latest_report(task.task_id)
    assert report.result_status == "cancelled"
    assert "Annulée" in report.markdown or "annulée" in report.markdown.lower()
    assert "task.control.cancelled" in await _await_events(seen, "task.control.cancelled")

    # Une annulation est idempotente : la répéter ne relance ni ne recompte.
    again = await service.cancel_task(task.task_id)
    assert again.status is TaskStatus.CANCELLED
    assert service.agentic.cancels == [run_id]


# ── Scénario F — reprise après suspension ──────────────────────────────────


@pytest.mark.asyncio
async def test_scenario_f_reprise_sans_doublon(e2e):
    service, _ = e2e

    task = await service.create_task(title="Tâche suivie")
    task = await service.decide_plan(
        task.task_id, 1, decision=PlanDecision.APPROVED, actor="session:1"
    )
    run_id = task.agentic_run_id

    first = service.activity(task.task_id)
    cursor = first[-1].sequence

    # « Application suspendue » : des événements arrivent sans lecteur.
    for tool in ("read_file", "write_file", "run_tests"):
        await service.on_runtime_event(
            "agent.tool.started",
            {"run_id": run_id, "tool": tool, "status": "running"},
        )

    # « Reprise » : on ne redemande que ce qui manque.
    resumed = service.activity(task.task_id, after_sequence=cursor)
    assert len(resumed) == 3
    assert all(entry.sequence > cursor for entry in resumed)

    # Les rangs sont strictement croissants et sans trou côté lecture.
    everything = service.activity(task.task_id)
    sequences = [entry.sequence for entry in everything]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences)), "aucun doublon"

    # Rejouer un événement déjà traduit ne duplique pas l'activité.
    before = len(service.activity(task.task_id))
    await service.on_runtime_event(
        "agent.run.phase_changed",
        {"run_id": run_id, "status": "running", "phase": "runtime_started"},
    )
    await service.on_runtime_event(
        "agent.run.phase_changed",
        {"run_id": run_id, "status": "running", "phase": "runtime_started"},
    )
    after = len(service.activity(task.task_id))
    assert after == before + 2, "deux événements distincts, deux entrées"


# ── Frontière : aucun raisonnement brut ne franchit l'activité ─────────────


@pytest.mark.asyncio
async def test_aucun_raisonnement_brut_ne_traverse_lactivite(e2e):
    service, seen = e2e

    task = await service.create_task(title="Tâche observée")
    task = await service.decide_plan(
        task.task_id, 1, decision=PlanDecision.APPROVED, actor="session:1"
    )
    poison = "CHAIN_OF_THOUGHT_SECRET_TOKEN"
    await service.on_runtime_event(
        "agent.tool.completed",
        {
            "run_id": task.agentic_run_id,
            "tool": "read_file",
            "status": "done",
            "reasoning": poison,
            "prompt": poison,
            "raw_result": poison,
            "thoughts": poison,
        },
    )
    dumped = " ".join(entry.summary for entry in service.activity(task.task_id))
    assert poison not in dumped

    await _await_events(seen, "task.control.started")
    payloads = " ".join(str(dict(event.data or {})) for event in seen)
    assert poison not in payloads


@pytest.mark.asyncio
async def test_le_contexte_vocal_ne_relaie_pas_lextrait_de_source(e2e):
    """L'extrait d'e-mail reste dans la tâche, jamais dans le contexte parlé."""

    service, _ = e2e
    detector = service.detector
    secret = "IBAN FR76 0000 1111 2222"
    detections = detector.detect(
        DetectionInput(
            body=f"Peux-tu envoyer le virement avant vendredi ? {secret}",
            source_type=TaskSourceType.EMAIL,
            channel=TaskSourceChannel.EMAIL,
            reference="email:99",
            sender="compta@example.invalid",
            subject="Virement",
        )
    )
    assert detections
    candidate, _ = await service.ingest_detection(detections[0])
    task = await service.accept_candidate(candidate.candidate_id)

    context = build_task_control_context(service=service, focus_task_id=task.task_id)
    joined = " ".join(context.values())
    assert secret not in joined
