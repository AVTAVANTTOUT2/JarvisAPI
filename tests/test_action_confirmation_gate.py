"""Régressions — gate de confirmation pour actions à effet de bord."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_chat_actions():
    spec = importlib.util.spec_from_file_location(
        "chat_actions_module",
        REPO_ROOT / "api" / "chat_actions.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_actions_requiring_confirmation_include_calendar_and_tasks() -> None:
    mod = _load_chat_actions()
    required = mod.ACTIONS_REQUIRING_CONFIRMATION
    assert "calendar_create" in required
    assert "task" in required
    assert "reminder" in required
    assert "name_place" in required
    assert "weather" not in required


def test_read_only_and_launch_actions_stay_immediate() -> None:
    """Le critère du garde est l'écriture durable, pas la sensibilité ressentie.

    `find_file` ne modifie rien. `open_app` lance une application sans rien
    écrire, et deux tests d'intégration exigent qu'un « ouvre OBS » vocal
    ouvre OBS sans second tour de parole — les inclure transformait chaque
    commande directe en question.
    """
    mod = _load_chat_actions()
    assert "find_file" not in mod.ACTIONS_REQUIRING_CONFIRMATION
    assert "open_app" not in mod.ACTIONS_REQUIRING_CONFIRMATION
    assert mod._should_defer_action(
        "J'ouvre Safari.",
        {"type": "open_app", "app_name": "Safari"},
    ) is False


def test_calendar_creation_deferred_until_the_user_says_yes() -> None:
    mod = _load_chat_actions()
    assert mod._should_defer_action(
        "Événement ajouté à ton agenda.",
        {"type": "calendar_create", "summary": "Dentiste"},
    ) is True


def test_model_supplied_confirmed_flag_never_lifts_the_gate() -> None:
    """Un `confirmed: true` écrit par le modèle ne vaut pas confirmation.

    `_should_defer_action` ne voit que des blocs ```action``` extraits de la
    réponse du modèle : les deux appelants rendent la main avant, quand
    l'action vient d'une proposition serveur consommée. Sans ce refus, il
    suffisait au modèle d'ajouter le champ pour créer une tâche, un événement
    de calendrier ou ouvrir une application sans que personne ne dise oui.
    """
    mod = _load_chat_actions()
    for action_type in sorted(mod.ACTIONS_REQUIRING_CONFIRMATION):
        assert mod._should_defer_action(
            "C'est fait.",
            {"type": action_type, "confirmed": True},
        ) is True, action_type


def test_unlisted_action_stays_immediate_without_a_question() -> None:
    """Le garde reste ciblé : une lecture sans effet de bord n'est pas différée."""
    mod = _load_chat_actions()
    assert mod._should_defer_action(
        "Il fait 18 degrés à Lille.",
        {"type": "weather"},
    ) is False
