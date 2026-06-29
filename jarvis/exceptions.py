"""Exceptions du package JARVIS dual-LLM.

Hiérarchie centralisée pour permettre un ``except JARVISError`` global tout en
gardant des types précis (jamais de ``except Exception`` nu côté appelant).
"""

from __future__ import annotations


class JARVISError(Exception):
    """Base de toutes les erreurs JARVIS."""


class LocalBackendError(JARVISError):
    """Échec du backend local MLX-LM (subprocess, timeout, modèle absent…)."""


class DeepSeekBackendError(JARVISError):
    """Échec du backend DeepSeek (HTTP, auth, quota, réponse malformée…)."""


class DataLeakError(JARVISError):
    """Une donnée interdite (messages bruts / métadonnées DB) allait fuiter.

    Levée par :class:`jarvis.pii.boundary.DataBoundary` avant tout appel réseau
    vers DeepSeek. C'est un garde-fou non-négociable : aucune requête ne part si
    cette exception est levée.
    """
