"""Contrat HTTP du pilotage de tâches.

Deux propriétés y sont vérifiées au niveau du transport, parce que c'est là
que l'utilisateur les touche : le client ne peut pas se déclarer approuvé, et
approuver un plan périmé est refusé plutôt qu'accepté silencieusement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import config
import database
from jarvis.event_bus import EventBus
from jarvis.task_control.detection import TaskCandidateDetector
from jarvis.task_control.models import PlanStep, TaskPlan, new_id
from jarvis.task_control.service import TaskControlService


@dataclass
class _Run:
    run_id: str
    status: Any = None
    started_at: Any = None
    finished_at: Any = None
    verification: Any = None


@dataclass
class _Agentic:
    starts: list[dict[str, Any]] = field(default_factory=list)

    async def create_and_start(self, **kwargs: Any) -> _Run:
        self.starts.append(kwargs)
        return _Run(run_id=f"run_{len(self.starts)}")

    async def cancel(self, run_id: str) -> None:
        return None

    async def decide_approval(self, run_id, approval_id, *, decision, actor):
        return _Run(run_id=run_id)

    def get(self, run_id: str) -> _Run:
        return _Run(run_id=run_id)

    def approvals(self, run_id: str) -> list[Any]:
        return []

    def artifacts(self, run_id: str) -> list[Any]:
        return []


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
        objective=f"Objectif v{version}",
        summary="Plan de test",
        steps=(PlanStep(index=1, title="Étape unique"),),
    )


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "task-control-api.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.init_db()

    from api import router_task_control

    service = TaskControlService(
        agentic_service=_Agentic(),
        notifications=_Notifications(),
        bus=EventBus(),
        planner=_planner,
        detector=TaskCandidateDetector(),
    )
    monkeypatch.setattr(
        router_task_control, "get_task_control_service", lambda: service
    )
    app = FastAPI()
    app.include_router(router_task_control.router)
    with TestClient(app) as client:
        yield client, service


# ── Création et lecture ────────────────────────────────────────────────────


def test_creation_renvoie_une_tache_en_attente_de_plan(api):
    client, service = api
    response = client.post(
        "/api/task-control/tasks",
        json={"title": "Préparer le rapport", "source_channel": "macos"},
    )
    assert response.status_code == 201
    task = response.json()["task"]
    assert task["status"] == "awaiting_plan_approval"
    assert task["approved_plan_version"] is None
    assert service.agentic.starts == []


def test_le_client_ne_peut_pas_declarer_un_etat(api):
    """Un champ inconnu est refusé, pas ignoré."""

    client, _ = api
    response = client.post(
        "/api/task-control/tasks",
        json={"title": "Tâche", "status": "running", "approved_plan_digest": "0" * 64},
    )
    assert response.status_code == 422


def test_patch_refuse_les_champs_de_pouvoir(api):
    client, _ = api
    created = client.post(
        "/api/task-control/tasks", json={"title": "Tâche"}
    ).json()["task"]
    response = client.patch(
        f"/api/task-control/tasks/{created['task_id']}",
        json={"approved_plan_version": 1},
    )
    assert response.status_code == 422


def test_detail_expose_plan_courant_et_commentaires(api):
    client, _ = api
    created = client.post(
        "/api/task-control/tasks",
        json={"title": "Analyser", "comment": "contexte initial"},
    ).json()["task"]
    detail = client.get(f"/api/task-control/tasks/{created['task_id']}").json()
    assert detail["current_plan"]["version"] == 1
    assert len(detail["plans"]) == 1
    assert detail["comments"][0]["body"] == "contexte initial"


def test_sections_et_compteurs(api):
    client, _ = api
    client.post("/api/task-control/tasks", json={"title": "Une"})
    listing = client.get("/api/task-control/tasks?section=to_approve").json()
    assert len(listing["tasks"]) == 1
    assert listing["counts"]["to_approve"] == 1
    assert listing["counts"]["running"] == 0


def test_section_inconnue_refusee(api):
    client, _ = api
    assert client.get("/api/task-control/tasks?section=nimporte").status_code == 400


def test_tache_absente_renvoie_404(api):
    client, _ = api
    assert client.get("/api/task-control/tasks/task_absent").status_code == 404


def test_identifiant_malforme_refuse(api):
    client, _ = api
    assert client.get("/api/task-control/tasks/..%2Fetc").status_code in (400, 404)


# ── Décisions de plan ──────────────────────────────────────────────────────


def test_approbation_demarre_lexecution(api):
    client, service = api
    created = client.post(
        "/api/task-control/tasks", json={"title": "Rédiger"}
    ).json()["task"]
    response = client.post(
        f"/api/task-control/tasks/{created['task_id']}/plans/1/decision",
        json={"decision": "approved"},
    )
    assert response.status_code == 200
    # « running » n'est plus affirmé au moment de l'approbation : la tâche est
    # remise au runtime, et son état suit les événements réels du run.
    assert response.json()["task"]["status"] == "queued"
    assert len(service.agentic.starts) == 1


def test_le_detail_expose_les_permissions_donnees_au_run(api):
    """L'écran de validation lit la liste exacte que le runtime recevra."""

    client, service = api
    created = client.post(
        "/api/task-control/tasks", json={"title": "Analyser le dépôt"}
    ).json()["task"]
    detail = client.get(f"/api/task-control/tasks/{created['task_id']}").json()
    announced = detail["current_plan"]["execution_permissions"]
    assert announced

    client.post(
        f"/api/task-control/tasks/{created['task_id']}/plans/1/decision",
        json={"decision": "approved"},
    )
    assert list(service.agentic.starts[0]["permissions"]) == announced


def test_refus_du_plan_nexecute_rien(api):
    client, service = api
    created = client.post(
        "/api/task-control/tasks", json={"title": "Envoyer"}
    ).json()["task"]
    response = client.post(
        f"/api/task-control/tasks/{created['task_id']}/plans/1/decision",
        json={"decision": "rejected", "comment": "pas maintenant"},
    )
    assert response.json()["task"]["status"] == "plan_rejected"
    assert service.agentic.starts == []


def test_digest_perime_est_refuse_en_409(api):
    """L'écran affichait un autre plan : refuser plutôt qu'approuver à l'aveugle."""

    client, service = api
    created = client.post(
        "/api/task-control/tasks", json={"title": "Corriger"}
    ).json()["task"]
    response = client.post(
        f"/api/task-control/tasks/{created['task_id']}/plans/1/decision",
        json={"decision": "approved", "plan_digest": "a" * 64},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "plan_digest_mismatch"
    assert service.agentic.starts == []


def test_digest_correct_est_accepte(api):
    client, service = api
    created = client.post(
        "/api/task-control/tasks", json={"title": "Corriger"}
    ).json()["task"]
    detail = client.get(f"/api/task-control/tasks/{created['task_id']}").json()
    digest = detail["current_plan"]["digest"]
    response = client.post(
        f"/api/task-control/tasks/{created['task_id']}/plans/1/decision",
        json={"decision": "approved", "plan_digest": digest},
    )
    assert response.status_code == 200
    assert len(service.agentic.starts) == 1


def test_seconde_decision_sur_la_meme_version_est_409(api):
    client, _ = api
    created = client.post(
        "/api/task-control/tasks", json={"title": "Classer"}
    ).json()["task"]
    path = f"/api/task-control/tasks/{created['task_id']}/plans/1/decision"
    assert client.post(path, json={"decision": "approved"}).status_code == 200
    assert client.post(path, json={"decision": "rejected"}).status_code == 409


def test_decision_inconnue_refusee(api):
    client, _ = api
    created = client.post(
        "/api/task-control/tasks", json={"title": "Classer"}
    ).json()["task"]
    response = client.post(
        f"/api/task-control/tasks/{created['task_id']}/plans/1/decision",
        json={"decision": "lance-le"},
    )
    assert response.status_code == 422


def test_version_de_plan_absente_renvoie_404(api):
    client, _ = api
    created = client.post(
        "/api/task-control/tasks", json={"title": "Classer"}
    ).json()["task"]
    response = client.post(
        f"/api/task-control/tasks/{created['task_id']}/plans/7/decision",
        json={"decision": "approved"},
    )
    assert response.status_code == 404


# ── Activité, annulation, rapport ──────────────────────────────────────────


def test_activite_supporte_la_reprise_sans_doublon(api):
    client, service = api
    created = client.post(
        "/api/task-control/tasks", json={"title": "Analyser"}
    ).json()["task"]
    first = client.get(
        f"/api/task-control/tasks/{created['task_id']}/activity"
    ).json()
    assert first["activity"]
    cursor = first["last_sequence"]
    second = client.get(
        f"/api/task-control/tasks/{created['task_id']}/activity?after_sequence={cursor}"
    ).json()
    assert second["activity"] == []
    assert second["last_sequence"] == cursor


def test_niveau_resume_inclut_moins_dentrees_que_technique(api):
    client, _ = api
    created = client.post(
        "/api/task-control/tasks", json={"title": "Analyser"}
    ).json()["task"]
    summary = client.get(
        f"/api/task-control/tasks/{created['task_id']}/activity?level=summary"
    ).json()["activity"]
    technical = client.get(
        f"/api/task-control/tasks/{created['task_id']}/activity?level=technical"
    ).json()["activity"]
    assert len(summary) <= len(technical)


def test_annulation_puis_rapport(api):
    client, _ = api
    created = client.post(
        "/api/task-control/tasks", json={"title": "Longue analyse"}
    ).json()["task"]
    cancel = client.post(
        f"/api/task-control/tasks/{created['task_id']}/cancel",
        json={"reason": "plus nécessaire"},
    )
    assert cancel.json()["task"]["status"] == "cancelled"
    report = client.get(f"/api/task-control/tasks/{created['task_id']}/report")
    assert report.status_code == 200
    assert report.json()["report"]["result_status"] == "cancelled"


def test_rapport_absent_renvoie_404(api):
    client, _ = api
    created = client.post(
        "/api/task-control/tasks", json={"title": "Sans rapport"}
    ).json()["task"]
    assert (
        client.get(f"/api/task-control/tasks/{created['task_id']}/report").status_code
        == 404
    )


def test_commentaire_avec_revision_produit_une_nouvelle_version(api):
    client, _ = api
    created = client.post(
        "/api/task-control/tasks", json={"title": "Rédiger"}
    ).json()["task"]
    response = client.post(
        f"/api/task-control/tasks/{created['task_id']}/comments",
        json={"body": "élargis le périmètre", "request_plan_revision": True},
    )
    assert response.status_code == 201
    assert response.json()["task"]["plan_version"] == 2
    assert response.json()["task"]["status"] == "awaiting_plan_approval"


def test_commentaire_simple_ne_replanifie_pas(api):
    client, _ = api
    created = client.post(
        "/api/task-control/tasks", json={"title": "Rédiger"}
    ).json()["task"]
    response = client.post(
        f"/api/task-control/tasks/{created['task_id']}/comments",
        json={"body": "petite précision"},
    )
    assert response.json()["task"]["plan_version"] == 1


# ── Candidats ──────────────────────────────────────────────────────────────


def test_candidats_vides_par_defaut(api):
    client, _ = api
    assert client.get("/api/task-candidates").json()["candidates"] == []


@pytest.mark.asyncio
async def test_acceptation_dun_candidat_cree_une_tache_a_valider(api):
    client, service = api
    from jarvis.task_control.detection import DetectedTask
    from jarvis.task_control.models import TaskSource, TaskSourceChannel, TaskSourceType

    detected = DetectedTask(
        is_actionable=True,
        confidence=0.6,
        suggested_title="Répondre au fournisseur",
        source=TaskSource(
            source_type=TaskSourceType.EMAIL,
            channel=TaskSourceChannel.EMAIL,
            reference="email:42",
        ),
        dedupe_key="k42",
    )
    candidate, task = await service.ingest_detection(detected)
    assert task is None

    response = client.post(
        f"/api/task-candidates/{candidate.candidate_id}/decision",
        json={"decision": "accepted"},
    )
    assert response.status_code == 200
    assert response.json()["task"]["status"] == "awaiting_plan_approval"
    assert service.agentic.starts == []
