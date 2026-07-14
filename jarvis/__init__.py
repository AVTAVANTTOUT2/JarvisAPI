"""JARVIS — Architecture dual-LLM avec séparation stricte des données.

Deux backends LLM, deux rôles non-interchangeables :

- ``LocalBackend`` (MLX-LM / Qwen3-30B) : traite UNIQUEMENT les messages d'Elias.
  Les données ne quittent jamais le Mac.
- ``DeepSeekBackend`` (api.deepseek.com) : traite tout le reste (mail, RAG,
  tâches, documents, résumés non-messages). Toute PII est pseudonymisée par
  ``PIIAnonymizer`` avant l'envoi, et ``DataBoundary`` interdit toute fuite de
  données issues de la base messages.

Le point d'entrée unique est ``JARVISRouter`` (jarvis.router).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from jarvis.exceptions import (
    DataLeakError,
    DeepSeekBackendError,
    JARVISError,
    LocalBackendError,
)
from jarvis.models import DataSource, EmailPayload, RouterStats

if TYPE_CHECKING:
    from jarvis.router import JARVISRouter


def __getattr__(name: str) -> Any:
    """Charge le routeur à la demande sans coupler les modules bas niveau aux LLM."""
    if name == "JARVISRouter":
        from jarvis.router import JARVISRouter

        return JARVISRouter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "JARVISRouter",
    "DataSource",
    "EmailPayload",
    "RouterStats",
    "JARVISError",
    "LocalBackendError",
    "DeepSeekBackendError",
    "DataLeakError",
]
