"""Configuration centralisée du package dual-LLM (lecture d'environnement).

Toute la configuration vient de variables d'environnement — zéro valeur secrète
en dur. La clé DeepSeek n'est lue qu'au moment où le backend en a besoin
(``require_deepseek_api_key``) pour qu'un import du package sans clé reste
possible (tests du backend local, anonymizer, boundary).
"""

from __future__ import annotations

import os

from env_loader import load_jarvis_env
from jarvis.exceptions import DeepSeekBackendError

load_jarvis_env()

# ── Backend local MLX-LM ─────────────────────────────────────
LOCAL_MODEL: str = os.environ.get(
    "JARVIS_LOCAL_MODEL", "mlx-community/Qwen3-30B-A3B-4bit"
)
LOCAL_VENV: str = os.environ.get("JARVIS_VENV", os.path.expanduser("~/mlx-env"))
LOCAL_GENERATION_TIMEOUT_SEC: float = float(
    os.environ.get("JARVIS_LOCAL_TIMEOUT", "300")
)
LOCAL_HEALTHCHECK_TIMEOUT_SEC: float = float(
    os.environ.get("JARVIS_LOCAL_HEALTH_TIMEOUT", "8")
)
LOCAL_HEALTH_CACHE_TTL_SEC: float = float(
    os.environ.get("JARVIS_LOCAL_HEALTH_TTL", "30")
)

# ── Backend DeepSeek ─────────────────────────────────────────
DEEPSEEK_BASE_URL: str = os.environ.get(
    "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"
)
DEEPSEEK_MODEL: str = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")
DEEPSEEK_TIMEOUT_SEC: float = float(os.environ.get("DEEPSEEK_TIMEOUT", "60"))
DEEPSEEK_MAX_RETRIES: int = int(os.environ.get("DEEPSEEK_MAX_RETRIES", "2"))

# ── PII / NER ────────────────────────────────────────────────
SPACY_MODEL: str = os.environ.get("JARVIS_SPACY_MODEL", "fr_core_news_sm")
PII_USE_SPACY: bool = os.environ.get("JARVIS_PII_USE_SPACY", "true").lower() == "true"


def require_deepseek_api_key() -> str:
    """Retourne la clé DeepSeek ou lève si absente.

    Lecture paresseuse : on ne lit la clé qu'au moment d'un appel réseau réel,
    pour qu'un import du package sans ``DEEPSEEK_API_KEY`` reste fonctionnel.
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise DeepSeekBackendError(
            "DEEPSEEK_API_KEY absente de l'environnement. "
            "Définis-la dans .env : DEEPSEEK_API_KEY=\"sk-...\" "
            "(jamais en dur dans le code source)."
        )
    return api_key
