"""Machine à états, digest de plan et invariant de non-démarrage automatique.

Ce fichier ne teste pas « le code fait ce qu'il fait » : il verrouille la
promesse produit. Si une future modification laisse une tâche démarrer sans
plan approuvé, ou avec un plan différent de celui qui a été lu, ces tests
échouent.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from jarvis.task_control.models import (
    ALLOWED_TASK_TRANSITIONS,
    ControlTask,
    InvalidTaskTransition,
    PlanDecision,
    PlanStep,
    TaskExecutionRefused,
    TaskPlan,
    TaskPriority,
    TaskSource,
    TaskSourceChannel,
    TaskSourceType,
    TaskStatus,
    compute_plan_digest,
    ensure_executable,
    new_id,
    validate_task_transition,
)


def _task(**overrides) -> ControlTask:
    base = {
        "task_id": new_id("task"),
        "profile_id": "default",
        "title": "Préparer le rapport",
        "status": TaskStatus.APPROVED,
    }
    base.update(overrides)
    return ControlTask(**base)


def _plan(task: ControlTask, *, version: int = 1, **overrides) -> TaskPlan:
    base = {
        "plan_id": new_id("plan"),
        "task_id": task.task_id,
        "version": version,
        "objective": "Produire le rapport demandé",
        "summary": "Trois étapes",
        "steps": (
            PlanStep(index=1, title="Rassembler les données"),
            PlanStep(index=2, title="Rédiger"),
        ),
    }
    base.update(overrides)
    return TaskPlan(**base)


# ── Machine à états ────────────────────────────────────────────────────────


def test_candidate_ne_peut_pas_aller_directement_a_running():
    with pytest.raises(InvalidTaskTransition):
        validate_task_transition(TaskStatus.CANDIDATE, TaskStatus.RUNNING)


def test_awaiting_plan_approval_ne_mene_pas_a_running():
    """La seule sortie vers l'exécution passe par `approved`."""

    assert TaskStatus.RUNNING not in ALLOWED_TASK_TRANSITIONS[
        TaskStatus.AWAITING_PLAN_APPROVAL
    ]
    assert TaskStatus.QUEUED not in ALLOWED_TASK_TRANSITIONS[
        TaskStatus.AWAITING_PLAN_APPROVAL
    ]


def test_aucun_etat_anterieur_a_lapprobation_ne_mene_a_lexecution():
    pre_approval = {
        TaskStatus.CANDIDATE,
        TaskStatus.CREATED,
        TaskStatus.PLANNING,
        TaskStatus.AWAITING_PLAN_APPROVAL,
        TaskStatus.PLAN_REJECTED,
        TaskStatus.PLAN_REVISION_REQUESTED,
    }
    executing = {TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.RESOURCE_WAIT}
    for status in pre_approval:
        assert not (ALLOWED_TASK_TRANSITIONS[status] & executing), status


def test_archived_est_terminal():
    assert ALLOWED_TASK_TRANSITIONS[TaskStatus.ARCHIVED] == frozenset()


def test_transition_identique_est_acceptee():
    assert validate_task_transition(TaskStatus.RUNNING, TaskStatus.RUNNING) == (
        TaskStatus.RUNNING,
        TaskStatus.RUNNING,
    )


# ── Digest de plan ─────────────────────────────────────────────────────────


def test_digest_stable_entre_deux_calculs():
    task = _task()
    plan = _plan(task)
    assert plan.digest == compute_plan_digest(plan)


def test_digest_ignore_la_decision_mais_pas_les_etapes():
    task = _task()
    plan = _plan(task)
    decided = replace(
        plan,
        decision=PlanDecision.APPROVED,
        decision_by="user",
        digest="",
    )
    assert decided.digest == plan.digest

    modified = replace(
        plan,
        steps=plan.steps + (PlanStep(index=3, title="Envoyer par e-mail"),),
        digest="",
    )
    assert modified.digest != plan.digest


def test_digest_change_si_une_permission_est_ajoutee():
    task = _task()
    plan = _plan(task)
    escalated = replace(plan, permissions_expected=("mail:send",), digest="")
    assert escalated.digest != plan.digest


# ── Invariant d'exécution ──────────────────────────────────────────────────


def test_execution_refusee_sans_plan():
    with pytest.raises(TaskExecutionRefused):
        ensure_executable(_task(), None)


def test_execution_refusee_si_plan_non_approuve():
    task = _task()
    plan = _plan(task)
    task = replace(
        task, approved_plan_version=1, approved_plan_digest=plan.digest
    )
    with pytest.raises(TaskExecutionRefused, match="approuvé"):
        ensure_executable(task, plan)


def test_execution_refusee_si_le_plan_a_change_apres_approbation():
    """Le cœur de la garantie : approuver v1 n'autorise pas à exécuter v1'."""

    task = _task()
    plan = _plan(task)
    approved_digest = plan.digest
    task = replace(
        task, approved_plan_version=1, approved_plan_digest=approved_digest
    )
    tampered = replace(
        plan,
        decision=PlanDecision.APPROVED,
        steps=plan.steps + (PlanStep(index=3, title="Pousser sur main"),),
        digest="",
    )
    with pytest.raises(TaskExecutionRefused, match="changé"):
        ensure_executable(task, tampered)


def test_execution_refusee_si_version_approuvee_differente():
    task = _task()
    plan = _plan(task, version=2)
    approved = replace(plan, decision=PlanDecision.APPROVED)
    task = replace(
        task, approved_plan_version=1, approved_plan_digest=approved.digest
    )
    with pytest.raises(TaskExecutionRefused, match="version"):
        ensure_executable(task, approved)


def test_execution_refusee_depuis_un_etat_non_approuve():
    task = _task(status=TaskStatus.AWAITING_PLAN_APPROVAL)
    plan = replace(_plan(task), decision=PlanDecision.APPROVED)
    task = replace(
        task, approved_plan_version=1, approved_plan_digest=plan.digest
    )
    with pytest.raises(TaskExecutionRefused, match="autorise pas"):
        ensure_executable(task, plan)


def test_execution_refusee_si_le_plan_appartient_a_une_autre_tache():
    task = _task()
    other = _task()
    plan = replace(_plan(other), decision=PlanDecision.APPROVED)
    task = replace(
        task, approved_plan_version=1, approved_plan_digest=plan.digest
    )
    with pytest.raises(TaskExecutionRefused, match="appartient"):
        ensure_executable(task, plan)


def test_execution_acceptee_quand_tout_concorde():
    task = _task()
    plan = replace(_plan(task), decision=PlanDecision.APPROVED)
    task = replace(
        task, approved_plan_version=plan.version, approved_plan_digest=plan.digest
    )
    assert ensure_executable(task, plan) is plan


# ── Bornes et normalisation ────────────────────────────────────────────────


def test_titre_borne_et_normalise():
    task = _task(title="  a" + "b" * 500 + "  ")
    assert len(task.title) <= 300


def test_plan_exige_au_moins_une_etape():
    task = _task()
    with pytest.raises(ValueError, match="au moins une étape"):
        TaskPlan(
            plan_id=new_id("plan"),
            task_id=task.task_id,
            version=1,
            objective="Faire",
            summary="",
            steps=(),
        )


def test_source_refuse_une_confiance_hors_bornes():
    with pytest.raises(ValueError):
        TaskSource(
            source_type=TaskSourceType.EMAIL,
            channel=TaskSourceChannel.EMAIL,
            confidence=1.4,
        )


def test_extrait_de_source_est_borne():
    source = TaskSource(
        source_type=TaskSourceType.EMAIL,
        channel=TaskSourceChannel.EMAIL,
        excerpt="x" * 2_000,
    )
    assert len(source.excerpt) <= 400


def test_priorite_invalide_refusee():
    with pytest.raises(ValueError):
        TaskPriority("cosmique")


def test_attention_requise_derivee_de_letat():
    task = _task(status=TaskStatus.AWAITING_PERMISSION)
    assert task.needs_attention is True
    assert _task(status=TaskStatus.RUNNING).needs_attention is False
