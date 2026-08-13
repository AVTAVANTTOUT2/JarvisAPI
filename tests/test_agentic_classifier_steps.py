"""Distingue une lecture isolée d'une demande déléguée au runtime agentique.

Ces deux cas vérifient le comportement déterministe de
``classify_agentic_request`` :

- une analyse isolée sans terme de workflow reste une action directe
  (``direct_action``), car aucun signal adaptatif, multi-étapes ou long ne la
  transforme en run autonome ;
- la même analyse enchaînée à d'autres étapes (``puis``, ``ensuite``) devient
  une lecture agentique en lecture seule (``agentic_readonly``) déléguée au
  runtime agentique.
"""

from __future__ import annotations

from jarvis.agentic.classifier import classify_agentic_request


def test_analyse_sans_terme_de_workflow_reste_directe() -> None:
    result = classify_agentic_request(
        "analyse le fichier de documentation du dépôt", origin="voice"
    )
    assert result.category.value == "direct_action"


def test_analyse_multi_etapes_est_deleguee() -> None:
    result = classify_agentic_request(
        "analyse le fichier de documentation du dépôt, puis repère une amélioration, ensuite propose-la",
        origin="voice",
    )
    assert result.category.value == "agentic_readonly"
