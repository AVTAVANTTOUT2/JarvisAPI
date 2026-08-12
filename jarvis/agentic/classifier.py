"""Classification déterministe et garde anti-récursion des demandes."""

from __future__ import annotations

from .models import AgenticClassification, AgenticRequestCategory


class AgenticRecursionError(ValueError):
    """Un appel issu d'un runtime tente de repasser par le classificateur."""


_HIGH_RISK_TERMS = frozenset(
    {
        "production",
        "secret",
        "credential",
        "mot de passe",
        "firewall",
        "dns",
        "paiement",
        "virement",
        "supprime définitivement",
        "merge",
    }
)
_EXTERNAL_EFFECT_TERMS = frozenset(
    {
        "envoie",
        "publie",
        "commande",
        "achète",
        "réserve",
        "poste",
        "push",
        "crée une pull request",
        "démarre le live",
    }
)
_READONLY_TERMS = frozenset(
    {"analyse", "recherche", "compare", "résume", "inspecte", "audite", "explique"}
)
_REVERSIBLE_TERMS = frozenset(
    {"modifie", "corrige", "implémente", "édite", "configure", "refactorise"}
)
_WORKFLOW_TERMS = frozenset(
    {"puis", "ensuite", "étapes", "workflow", "chaque", "tous les", "de bout en bout"}
)


def classify_agentic_request(
    request: str,
    *,
    origin: str = "user",
    bypass_agentic_reclassification: bool = False,
    adaptive: bool = False,
    requires_multiple_steps: bool | None = None,
    uses_multiple_services: bool = False,
    long_running: bool = False,
    readonly: bool = False,
    reversible: bool = False,
    external_effect: bool = False,
    high_risk: bool = False,
) -> AgenticClassification:
    """Classe sans modèle ni dépendance provider, avec signaux explicites testables."""

    normalized_origin = (origin or "user").strip().lower()
    if normalized_origin == "agent_runtime":
        if not bypass_agentic_reclassification:
            raise AgenticRecursionError(
                "un appel agent_runtime doit fixer bypass_agentic_reclassification=true"
            )
        return AgenticClassification(
            AgenticRequestCategory.DIRECT_ACTION,
            "appel de capacité runtime: classification court-circuitée",
            bypassed=True,
        )
    if bypass_agentic_reclassification:
        raise AgenticRecursionError("le bypass est réservé à origin=agent_runtime")

    text = " ".join((request or "").lower().split())
    if not text:
        return AgenticClassification(
            AgenticRequestCategory.DIRECT_ACTION,
            "demande vide ou déterministe",
        )

    detected_high_risk = high_risk or any(term in text for term in _HIGH_RISK_TERMS)
    if detected_high_risk:
        return AgenticClassification(
            AgenticRequestCategory.AGENTIC_HIGH_RISK,
            "effet sensible ou périmètre critique",
        )

    detected_external = external_effect or any(
        term in text for term in _EXTERNAL_EFFECT_TERMS
    )
    if detected_external:
        return AgenticClassification(
            AgenticRequestCategory.AGENTIC_EXTERNAL_EFFECT,
            "effet externe nécessitant policy et vérification",
        )

    detected_readonly = readonly or any(term in text for term in _READONLY_TERMS)
    detected_reversible = reversible or any(term in text for term in _REVERSIBLE_TERMS)
    detected_multistep = (
        requires_multiple_steps
        if requires_multiple_steps is not None
        else any(term in text for term in _WORKFLOW_TERMS)
    )
    agentic_shape = (
        adaptive or uses_multiple_services or long_running or detected_multistep
    )
    if detected_readonly and agentic_shape:
        return AgenticClassification(
            AgenticRequestCategory.AGENTIC_READONLY,
            "analyse adaptative sans effet externe",
        )
    if detected_reversible and (agentic_shape or adaptive):
        return AgenticClassification(
            AgenticRequestCategory.AGENTIC_REVERSIBLE,
            "effet local réversible multi-étapes",
        )
    if agentic_shape:
        return AgenticClassification(
            AgenticRequestCategory.WORKFLOW,
            "enchaînement déterministe de plusieurs étapes",
        )
    # Un verbe de lecture ou de modification ne suffit pas à transformer une
    # demande conversationnelle courte en run autonome. Sans signal adaptatif,
    # multi-étapes ou long, le pipeline déterministe/conversationnel conserve la
    # main. Cela évite notamment qu'un simple « explique » crée un daemon et
    # préserve les confirmations déjà en attente.
    if detected_readonly:
        return AgenticClassification(
            AgenticRequestCategory.DIRECT_ACTION,
            "lecture ou analyse isolée sans orchestration adaptative",
        )
    if detected_reversible:
        return AgenticClassification(
            AgenticRequestCategory.DIRECT_ACTION,
            "effet local isolé sans orchestration adaptative",
        )
    return AgenticClassification(
        AgenticRequestCategory.DIRECT_ACTION,
        "action simple et déterministe",
    )


__all__ = ["AgenticRecursionError", "classify_agentic_request"]
