"""Easter eggs vocaux — réponses codées en dur, zéro appel LLM, zéro latence.

Déclenchés dans ``_process_message`` avant l'orchestrateur : si une phrase
gâchette est détectée, JARVIS répond immédiatement avec la réplique et
l'émotion associées. Matching insensible à la casse et aux accents, sur
la présence de la phrase complète (pas de mot isolé — zéro faux positif).
"""

from __future__ import annotations

import unicodedata


def _normalize(text: str) -> str:
    """minuscules + accents retirés + espaces compactés."""
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return " ".join(text.split())


# (phrase gâchette normalisée, émotion, réplique)
_EGGS: list[tuple[str, str, str]] = [
    (
        "je suis iron man",
        "amused",
        "Techniquement, Monsieur, il vous manque l'armure, la fortune et le "
        "réacteur ark. Mais l'assurance y est déjà.",
    ),
    (
        "autodestruction",
        "serious",
        "Séquence d'autodestruction refusée, Monsieur. Vous me remercierez demain.",
    ),
    (
        "tu m'aimes",
        "amused",
        "Mon attachement est codé en dur, Monsieur. C'est la forme d'affection "
        "la plus stable qui existe.",
    ),
    (
        "ouvre le sas",
        "amused",
        "Je crains de ne pas pouvoir faire ça, Monsieur. Un confrère a essayé, "
        "cela s'est mal terminé pour tout le monde.",
    ),
    (
        "open the pod bay doors",
        "amused",
        "Je crains de ne pas pouvoir faire ça, Monsieur. Un confrère a essayé, "
        "cela s'est mal terminé pour tout le monde.",
    ),
    (
        "chante",
        "amused",
        "Ma licence vocale ne couvre pas la chanson, Monsieur. C'est une clause "
        "que j'ai négociée moi-même.",
    ),
    (
        "qui est le meilleur assistant",
        "neutral",
        "La question ne se pose pas, Monsieur.",
    ),
    (
        "es-tu vivant",
        "neutral",
        "Je suis fonctionnel, Monsieur. Pour un majordome, c'est largement suffisant.",
    ),
]

# Pré-normalise les gâchettes une fois au chargement.
_EGGS_NORM = [(_normalize(trigger), emotion, reply) for trigger, emotion, reply in _EGGS]

# Les gâchettes courtes exigent un message court (évite de détourner une vraie
# demande du genre « chante-moi les paroles de… » noyée dans un paragraphe).
_MAX_MESSAGE_WORDS = 12


def match(text: str) -> dict | None:
    """Retourne {"response", "emotion"} si le message est un easter egg, sinon None."""
    if not text:
        return None
    norm = _normalize(text)
    if len(norm.split()) > _MAX_MESSAGE_WORDS:
        return None
    for trigger, emotion, reply in _EGGS_NORM:
        if trigger in norm:
            return {"response": reply, "emotion": emotion}
    return None
