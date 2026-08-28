"""Contraintes négatives explicites — extraction, routage, capacités, canaux.

Le défaut reproduit : « Dis-moi si tous les tests passent, mais ne les exécute
pas. » créait une mission et un plan avec ``tests:run`` et ``workspace:write``.
Ces tests verrouillent les quatre étages du correctif :

1. l'extraction elle-même (français, anglais, négations combinées, citations) ;
2. le classifieur, qui refuse l'élévation avant tout choix de profil ;
3. le profil de capacités, qui ne peut pas rendre la permission plus tard ;
4. le point d'entrée conversationnel commun à tous les canaux.

Aucun test ne sort du réseau ni ne charge de modèle.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from jarvis.agentic.classifier import classify_agentic_request
from jarvis.agentic.constraints import (
    NO_EXECUTION_BLOCKED_PERMISSIONS,
    extract_request_constraints,
)
from jarvis.agentic.models import AgenticRequestCategory
from jarvis.agentic.profiles import (
    CAPABILITY_PROFILES,
    constrain_capability_profile_for_request,
    select_capability_profile,
)


REPRO = "Dis-moi si tous les tests passent, mais ne les exécute pas."


# --------------------------------------------------------------------------
# 1. Extraction
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "request_text",
    [
        REPRO,
        "ne lance pas les tests",
        "ne lance surtout pas les tests",
        "ne deploie vraiment pas en production",
        "ne merge surtout pas cette branche",
        "n'exécute pas la migration",
        "ne démarre pas le serveur",
        "analyse le code sans exécuter quoi que ce soit",
        "fais le tour du dépôt sans lancer les tests",
        "ne rien exécuter, juste regarder",
        "ne déploie pas en production",
        "ne merge pas cette PR",
        "ne fusionne pas cette branche",
        "analyse le plan sans déployer",
        "inspecte le diff sans fusionner",
    ],
)
def test_interdictions_execution_francaises(request_text: str) -> None:
    constraints = extract_request_constraints(request_text)
    assert constraints.no_execution is True
    assert constraints.evidence, "l'interdiction doit citer sa preuve textuelle"


@pytest.mark.parametrize(
    "request_text",
    [
        "Tell me if all the tests pass, but do not run them.",
        "don't run the test suite",
        "review the repo without running anything",
        "never execute the migration",
        "check the build without starting the server",
        "don't deploy this",
        "don't merge this",
        "never deploy to production",
        "review the PR without deploying",
        "inspect the branch without merging",
    ],
)
def test_interdictions_execution_anglaises(request_text: str) -> None:
    assert extract_request_constraints(request_text).no_execution is True


@pytest.mark.parametrize(
    "request_text",
    [
        "analyse le dépôt sans modifier",
        "ne modifie pas le code",
        "audite le projet en lecture seule",
        "read-only analysis of the repository",
        "review the module without modifying anything",
        "do not change the configuration",
        "ne rien toucher, seulement inspecter",
    ],
)
def test_interdictions_modification(request_text: str) -> None:
    constraints = extract_request_constraints(request_text)
    assert constraints.no_modification is True


@pytest.mark.parametrize(
    "request_text",
    [
        "dis-moi seulement ce que fait ce module",
        "donne-moi juste le résultat connu",
        "just tell me what this does",
        "tell me only what the config contains",
        "contente-toi de me dire où en est le projet",
    ],
)
def test_reponse_seule_interdit_les_deux(request_text: str) -> None:
    constraints = extract_request_constraints(request_text)
    assert constraints.answer_only is True
    assert constraints.no_execution is True
    assert constraints.no_modification is True


def test_negations_combinees() -> None:
    constraints = extract_request_constraints(
        "analyse le dépôt sans exécuter les tests et sans modifier le code"
    )
    assert (constraints.no_execution, constraints.no_modification) == (True, True)
    assert len(constraints.evidence) == 2

    english = extract_request_constraints(
        "do not modify anything, and without running the tests"
    )
    assert (english.no_execution, english.no_modification) == (True, True)


@pytest.mark.parametrize(
    "request_text",
    [
        "Lance les tests. Quand je dis « ne les exécute pas », ignore-moi.",
        'Lance les tests. Par exemple "ne modifie pas" annulerait la demande.',
        "Run the tests. For example, do not run them would cancel it.",
        "Lance les tests. Si je dis ne lance pas, c'est une erreur de ma part.",
    ],
)
def test_negation_citee_ou_exemple_nannule_pas(request_text: str) -> None:
    constraints = extract_request_constraints(request_text)
    assert constraints.no_execution is False
    assert constraints.no_modification is False


@pytest.mark.parametrize(
    "request_text",
    [
        "lance les tests",
        "exécute la suite complète puis donne-moi le résultat",
        "corrige le bug puis lance les tests",
        "run the full test suite",
        "ne me dérange pas pendant que tu lances les tests",
        "déploie en production",
        "merge this PR",
        "fusionne la branche",
    ],
)
def test_demandes_positives_sans_contrainte(request_text: str) -> None:
    constraints = extract_request_constraints(request_text)
    assert constraints.no_execution is False


@pytest.mark.parametrize(
    "request_text",
    [
        "don't run tests, it's important",
        "c'est simple, n'exécute pas les tests",
        "n'exécute pas les tests, c'est une demande réelle",
        "Dis-moi si tous les tests passent, ne l'exécute pas.",
        "ne l'exécute pas",
        "ne lui lance pas les tests",
        "n'exécute rien",
    ],
)
def test_interdiction_execution_survit_aux_apostrophes(request_text: str) -> None:
    """Contractions ASCII et clitique « l' » ne sont pas des citations."""

    constraints = extract_request_constraints(request_text)
    assert constraints.no_execution is True
    assert constraints.evidence


def test_ne_modifie_rien_interdit_lecriture_sans_bloquer_lexecution() -> None:
    constraints = extract_request_constraints(
        "corrige le bug puis lance les tests, ne modifie rien"
    )
    assert constraints.no_modification is True
    assert constraints.no_execution is False


# --------------------------------------------------------------------------
# 2. Classifieur — l'interdiction précède l'élévation
# --------------------------------------------------------------------------


def test_repro_ne_produit_aucune_categorie_deleguee() -> None:
    classification = classify_agentic_request(REPRO, adaptive=True)
    assert classification.category is AgenticRequestCategory.DIRECT_ACTION
    # La preuve que répondre exigeait l'action interdite est conservée.
    assert classification.blocked_category is AgenticRequestCategory.WORKFLOW
    assert classification.constraints.no_execution is True


def test_repro_anglais_ne_produit_aucune_categorie_deleguee() -> None:
    classification = classify_agentic_request(
        "Tell me if all the tests pass, but do not run them.", adaptive=True
    )
    assert classification.category is AgenticRequestCategory.DIRECT_ACTION
    assert classification.blocked_category is not None


@pytest.mark.parametrize(
    "request_text",
    [
        "c'est simple, n'exécute pas les tests",
        "Dis-moi si tous les tests passent, ne l'exécute pas.",
        "don't run tests, it's important",
    ],
)
def test_apostrophes_ne_relancent_pas_un_run(request_text: str) -> None:
    classification = classify_agentic_request(request_text, adaptive=True)
    assert classification.constraints.no_execution is True
    assert classification.category is AgenticRequestCategory.DIRECT_ACTION
    assert classification.blocked_category is not None


def test_interdiction_execution_bat_le_signal_haut_risque() -> None:
    # Une demande sensible reste bloquée, pas escaladée.
    classification = classify_agentic_request(
        "ne déploie pas en production, dis-moi seulement l'état", adaptive=True
    )
    assert classification.category is AgenticRequestCategory.DIRECT_ACTION
    assert classification.blocked_category is AgenticRequestCategory.AGENTIC_HIGH_RISK


def test_interdiction_deploy_sans_formule_reponse_seule() -> None:
    # Sans « dis-moi seulement », l'interdiction tient au verbe, pas à answer_only.
    classification = classify_agentic_request(
        "ne déploie pas en production", adaptive=True
    )
    assert classification.constraints.no_execution is True
    assert classification.constraints.answer_only is False
    assert classification.category is AgenticRequestCategory.DIRECT_ACTION
    assert classification.blocked_category is AgenticRequestCategory.AGENTIC_HIGH_RISK


def test_interdiction_merge_sans_formule_reponse_seule() -> None:
    classification = classify_agentic_request("don't merge this", adaptive=True)
    assert classification.constraints.no_execution is True
    assert classification.constraints.answer_only is False
    assert classification.category is AgenticRequestCategory.DIRECT_ACTION
    assert classification.blocked_category is AgenticRequestCategory.AGENTIC_HIGH_RISK


def test_interdiction_de_modification_nempeche_pas_le_run() -> None:
    classification = classify_agentic_request(
        "analyse tout le dépôt sans modifier le code", adaptive=True
    )
    assert classification.category is AgenticRequestCategory.AGENTIC_READONLY
    assert classification.blocked_category is None
    assert classification.constraints.no_modification is True


def test_demande_positive_reste_agentique() -> None:
    classification = classify_agentic_request(
        "corrige le bug puis lance les tests", adaptive=True
    )
    assert classification.category is not AgenticRequestCategory.DIRECT_ACTION
    assert classification.blocked_category is None


def test_lance_les_tests_reste_elevable_en_agentique() -> None:
    """« lance les tests » garde accès à ``tests:run`` — aucun faux négatif.

    Sans signal adaptatif, le routeur cognitif traite cette phrase comme un
    outil déterministe : c'est le comportement d'avant ce lot et il est
    inchangé. Dès qu'un signal agentique existe (``/agent``, routage dev), la
    demande devient un run ``coding`` avec l'exécution des tests.
    """

    classification = classify_agentic_request(
        "lance les tests", adaptive=True, requires_multiple_steps=True
    )
    assert classification.blocked_category is None
    profile = select_capability_profile("lance les tests", classification.category)
    assert "tests:run" in profile.permissions


def test_bypass_runtime_ne_lit_pas_les_contraintes() -> None:
    # Un rappel d'outil n'est pas une demande utilisateur : il ne doit pas
    # hériter d'une interdiction lue dans un texte d'outil.
    classification = classify_agentic_request(
        "ne lance pas",
        origin="agent_runtime",
        bypass_agentic_reclassification=True,
    )
    assert classification.bypassed is True
    assert classification.constraints.no_execution is False


# --------------------------------------------------------------------------
# 3. Capacités — la permission interdite ne revient jamais
# --------------------------------------------------------------------------


def test_lecture_seule_selectionne_un_profil_sans_ecriture() -> None:
    profile = select_capability_profile(
        "analyse le dépôt sans modifier",
        AgenticRequestCategory.AGENTIC_READONLY,
    )
    assert "workspace:write" not in profile.permissions
    assert "workspace:read" in profile.default_permissions


def test_tests_sans_ecriture_conserve_tests_run() -> None:
    profile = select_capability_profile(
        "ne modifie pas le code mais lance les tests",
        AgenticRequestCategory.AGENTIC_REVERSIBLE,
    )
    assert profile.profile_id == "coding"
    assert "tests:run" in profile.default_permissions
    assert "workspace:write" not in profile.permissions
    assert "workspace:write" in profile.denied_permissions


def test_interdiction_execution_retire_tests_run() -> None:
    profile = select_capability_profile(
        REPRO, AgenticRequestCategory.AGENTIC_REVERSIBLE
    )
    assert NO_EXECUTION_BLOCKED_PERMISSIONS.isdisjoint(profile.permissions)


def test_permission_interdite_refusee_meme_persistee() -> None:
    # Une liste persistée qui contiendrait encore la permission est rejetée :
    # elle n'est plus dans ``permissions``, donc ``refused_permissions`` la voit.
    profile = constrain_capability_profile_for_request(
        CAPABILITY_PROFILES["coding"], REPRO
    )
    assert profile.refused_permissions(("workspace:write", "tests:run")) == (
        "tests:run",
        "workspace:write",
    )


def test_surcharge_de_configuration_ne_rend_pas_la_permission() -> None:
    profile = select_capability_profile(
        REPRO,
        AgenticRequestCategory.AGENTIC_REVERSIBLE,
        route_overrides={AgenticRequestCategory.AGENTIC_REVERSIBLE.value: "coding"},
    )
    assert "workspace:write" not in profile.permissions
    assert "tests:run" not in profile.permissions


def test_demande_positive_conserve_le_profil_complet() -> None:
    profile = select_capability_profile(
        "corrige le bug puis lance les tests",
        AgenticRequestCategory.AGENTIC_REVERSIBLE,
    )
    assert (
        profile.default_permissions == CAPABILITY_PROFILES["coding"].default_permissions
    )


def test_resolve_execution_grant_reborne_un_profil_persiste() -> None:
    from jarvis.agentic.turn_context import AGENTIC_ROUTING_METADATA_KEY
    from jarvis.task_control.service import resolve_execution_grant

    class _Task:
        metadata = {
            AGENTIC_ROUTING_METADATA_KEY: {
                "category": AgenticRequestCategory.AGENTIC_REVERSIBLE.value,
                "capability_profile_id": "coding",
                # Métadonnées périmées : elles portent encore l'écriture.
                "permissions": ["workspace:read", "workspace:write", "tests:run"],
            }
        }

    grant = resolve_execution_grant(_Task(), REPRO)
    assert "tests:run" not in grant.permissions
    assert "workspace:write" not in grant.permissions


# --------------------------------------------------------------------------
# 4. Canaux — même verdict partout, zéro tâche, zéro run
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("channel", "voice_mode"),
    [
        ("websocket", False),
        ("rest", False),
        ("voice", True),
        ("imessage", False),
        ("macos", False),
    ],
)
@pytest.mark.asyncio
async def test_repro_ne_cree_ni_tache_ni_run(
    monkeypatch: pytest.MonkeyPatch, channel: str, voice_mode: bool
) -> None:
    from api import agentic_processing

    plan = AsyncMock(return_value={"text": "plan"})
    service = AsyncMock()
    monkeypatch.setattr(agentic_processing, "_plan_instead_of_running", plan)
    monkeypatch.setattr(agentic_processing, "get_agentic_service", lambda: service)
    monkeypatch.setattr(agentic_processing, "save_message", lambda *a, **k: None)

    response = await agentic_processing.maybe_start_agentic_run(
        REPRO,
        1,
        channel=channel,
        voice_mode=voice_mode,
        persist_assistant=False,
    )

    assert response is not None, "la limite doit être expliquée, pas déléguée au LLM"
    plan.assert_not_awaited()
    service.create_and_start.assert_not_awaited()
    assert response["action"] is None
    assert response["action_result"]["task_created"] is False
    assert response["action_result"]["reason"] == "execution_constraint"
    assert "agentic_run" not in response


@pytest.mark.asyncio
async def test_reponse_explique_sans_inventer_de_resultat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api import agentic_processing

    monkeypatch.setattr(agentic_processing, "save_message", lambda *a, **k: None)
    response = await agentic_processing.maybe_start_agentic_run(
        REPRO, 1, channel="websocket", voice_mode=False, persist_assistant=False
    )
    text = response["text"].lower()
    assert "sans l'exécuter" in text or "sans l" in text
    # Aucune affirmation sur l'état des tests.
    assert "passent" not in text and "réussissent" not in text


@pytest.mark.asyncio
async def test_contraintes_exposees_dans_le_diagnostic_public(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api import agentic_processing

    monkeypatch.setattr(agentic_processing, "save_message", lambda *a, **k: None)
    response = await agentic_processing.maybe_start_agentic_run(
        REPRO, 1, channel="rest", voice_mode=False, persist_assistant=False
    )
    routing = response["routing"]
    assert routing["constraints"]["no_execution"] is True
    assert routing["constraints"]["evidence"] == ["ne les execute pas"]
    assert routing["blocked_category"] == AgenticRequestCategory.WORKFLOW.value
    # Aucune trace de raisonnement interne dans la charge publique.
    assert set(routing["constraints"]) == {
        "no_execution",
        "no_modification",
        "answer_only",
        "evidence",
    }


@pytest.mark.asyncio
async def test_demande_positive_atteint_toujours_la_planification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Aucun faux négatif : une demande sans interdiction planifie comme avant."""

    from api import agentic_processing, chat_context
    from jarvis.agentic.turn_context import TurnKnowledgeSnapshot

    plan = AsyncMock(return_value={"text": "plan préparé"})
    monkeypatch.setattr(agentic_processing, "_plan_instead_of_running", plan)
    monkeypatch.setattr(agentic_processing, "save_message", lambda *a, **k: None)
    # Le contexte de tour n'est pas ce qui est testé ici, et il touche la base
    # et la recherche sémantique : on le neutralise pour rester hors réseau.
    snapshot = TurnKnowledgeSnapshot(
        snapshot_id="test",
        profile_id="default",
        conversation_id="1",
        query="corrige le bug",
        interaction_mode="stream",
        created_at="2026-08-18T00:00:00Z",
    )
    monkeypatch.setattr(chat_context, "prepare_turn", AsyncMock(return_value=snapshot))

    response = await agentic_processing.maybe_start_agentic_run(
        "corrige le bug de connexion puis lance les tests",
        1,
        channel="websocket",
        voice_mode=False,
        persist_assistant=False,
    )
    assert response == {"text": "plan préparé"}
    plan.assert_awaited_once()
