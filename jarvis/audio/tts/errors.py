"""Erreurs de la synthèse vocale locale.

Chaque échec porte une cause explicite plutôt qu'un ``False`` silencieux : le
pipeline vocal doit pouvoir distinguer « le modèle n'est pas installé » (action
humaine requise, message précis) de « la synthèse a échoué sur ce texte »
(l'énoncé est perdu, le tour de parole doit être réarmé).

Aucune de ces erreurs ne doit déclencher un repli implicite vers un autre
moteur ou un service distant : c'est l'appelant qui décide, et l'utilisateur
qui voit un état TTS indisponible plutôt qu'une voix qu'il n'a pas choisie.
"""

from __future__ import annotations


class TTSError(Exception):
    """Racine commune — permet un ``except`` unique côté pipeline."""


class TTSUnavailableError(TTSError):
    """Le fournisseur ne peut pas servir : sidecar absent, runtime manquant."""


class TTSModelNotFoundError(TTSUnavailableError):
    """Poids ou voix absents du disque.

    Sous-classe de ``TTSUnavailableError`` : un modèle manquant est un cas
    particulier d'indisponibilité, et l'appelant qui n'a pas besoin du détail
    ne doit pas avoir à énumérer les deux.
    """


class TTSUnsupportedDeviceError(TTSUnavailableError):
    """Le backend réclame un matériel absent (CUDA, GPU discret, …)."""


class TTSSynthesisError(TTSError):
    """La synthèse a échoué alors que le moteur était prêt."""


class TTSCancelledError(TTSError):
    """Synthèse interrompue à la demande (barge-in, nouvelle requête)."""


__all__ = [
    "TTSCancelledError",
    "TTSError",
    "TTSModelNotFoundError",
    "TTSSynthesisError",
    "TTSUnavailableError",
    "TTSUnsupportedDeviceError",
]
