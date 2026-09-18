"""Allowlist d'activité task-control — rien hors champs nommés ne traverse.

`build_activity` est la frontière affichage : un runtime qui mettrait du raisonnement
ou des secrets dans le payload ne doit trouver aucun champ où les faire passer.
"""

from __future__ import annotations

from jarvis.task_control.activity import _safe_payload, build_activity, build_user_activity
from jarvis.task_control.models import TaskActivityLevel, TaskActivityType


def test_safe_payload_ne_conserve_que_les_champs_lisibles():
    raw = {
        "tool": "pytest",
        "reasoning": "je pense que…",
        "system_prompt": "SECRET",
        "admission_reason": "memory_pressure",
        "stdout": "leak",
    }
    assert _safe_payload(raw) == {
        "tool": "pytest",
        "admission_reason": "memory_pressure",
    }


def test_safe_payload_accepte_none_et_vide():
    assert _safe_payload(None) == {}
    assert _safe_payload({}) == {}


def test_build_activity_ignore_les_evenements_internes():
    assert (
        build_activity(
            task_id="task_1",
            run_id="run_1",
            event_type="agent.provider.internal",
            payload={"tool": "x", "reasoning": "hidden"},
        )
        is None
    )


def test_build_activity_n_expose_pas_les_champs_hors_allowlist_dans_le_resume():
    activity = build_activity(
        task_id="task_1",
        run_id="run_1",
        event_type="agent.run.started",
        payload={
            "reasoning": "chaîne de pensée privée",
            "system_prompt": "sk-secret",
            "spoken_summary": "Démarrage confirmé.",
        },
    )
    assert activity is not None
    assert activity.event_type == TaskActivityType.AGENT_STARTED
    assert activity.summary == "Exécution démarrée. Démarrage confirmé."
    assert "chaîne" not in activity.summary
    assert "sk-secret" not in activity.summary
    assert activity.level == TaskActivityLevel.SUMMARY


def test_resource_wait_affiche_un_motif_connu_uniquement():
    known = build_activity(
        task_id="task_1",
        run_id="run_1",
        event_type="agent.run.resource_wait",
        payload={"admission_reason": "memory_pressure"},
    )
    assert known is not None
    assert known.summary == "En attente de ressources. Motif : mémoire insuffisante."

    unknown = build_activity(
        task_id="task_1",
        run_id="run_1",
        event_type="agent.run.resource_wait",
        payload={"admission_reason": "custom_leak_please_show"},
    )
    assert unknown is not None
    assert unknown.summary == "En attente de ressources."
    assert "custom_leak" not in unknown.summary


def test_tool_override_pytest_complete_devient_test_result():
    activity = build_activity(
        task_id="task_1",
        run_id="run_1",
        event_type="agent.tool.completed",
        payload={"tool": "pytest"},
    )
    assert activity is not None
    assert activity.event_type == TaskActivityType.TEST_RESULT
    assert activity.summary == "Exécution des tests — terminé"
    assert activity.tool_name == "pytest"


def test_phase_changed_sans_phase_connue_ni_valeur_retourne_none():
    assert (
        build_activity(
            task_id="task_1",
            run_id="run_1",
            event_type="agent.run.phase_changed",
            payload={},
        )
        is None
    )


def test_phase_changed_runtime_completed_marque_etape_terminee():
    activity = build_activity(
        task_id="task_1",
        run_id="run_1",
        event_type="agent.run.phase_changed",
        payload={"phase": "runtime_completed"},
    )
    assert activity is not None
    assert activity.event_type == TaskActivityType.PLAN_STEP_COMPLETED
    assert activity.summary == "Exécution terminée"


def test_approval_resolved_mappe_la_decision():
    denied = build_activity(
        task_id="task_1",
        run_id="run_1",
        event_type="agent.approval.resolved",
        payload={"decision": "denied"},
    )
    assert denied is not None
    assert denied.event_type == TaskActivityType.PERMISSION_DECIDED
    assert denied.summary == "Autorisation refusée."
    assert denied.agent_role == "user"


def test_failed_avec_codes_ajoutes_au_resume():
    activity = build_activity(
        task_id="task_1",
        run_id="run_1",
        event_type="agent.run.failed",
        payload={"error_code": "doom_loop", "violation": "same_tool"},
    )
    assert activity is not None
    assert activity.event_type == TaskActivityType.ERROR
    assert activity.summary == "Exécution en échec. (doom_loop / same_tool)"


def test_build_user_activity_redacte_le_resume():
    activity = build_user_activity(
        task_id="task_1",
        summary="Token sk-abcdefghijklmnopqrstuvwxyz012345",
    )
    assert activity.agent_role == "user"
    assert activity.event_type == TaskActivityType.USER_COMMENT
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in activity.summary
