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

from jarvis.exceptions import (
    DataLeakError,
    DeepSeekBackendError,
    JARVISError,
    LocalBackendError,
)
from jarvis.models import DataSource, EmailPayload, RouterStats
from jarvis.router import JARVISRouter

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
