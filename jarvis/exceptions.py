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
    """Exception historique : une donnée interdite allait quitter la machine.

    ``DataBoundary.check`` ne lève plus cette exception. Les messages, e-mails
    et métadonnées DB partent tels quels ; les secrets sont masqués dans le
    texte retourné, pas refusés. Conservée pour les ``except DataLeakError``
    des appelants externes.
    """
