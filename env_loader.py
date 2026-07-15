"""Charge les fichiers d'environnement JARVIS.

Ordre de chargement :
1. ``.env.config`` — paramètres applicatifs (ports, modèles, intervalles…)
2. ``.env`` — clés API et secrets (écrase une clé homonyme si présente)

Rétro-compatibilité : un unique ``.env`` contenant tout reste supporté tant que
``.env.config`` est absent ou complété progressivement.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
CONFIG_ENV_FILE = BASE_DIR / ".env.config"
SECRETS_ENV_FILE = BASE_DIR / ".env"

_ENV_LOADED = False

# Variables réservées au fichier secrets (``.env``).
SECRET_ENV_KEYS: frozenset[str] = frozenset(
    {
        "DEEPSEEK_API_KEY",
        "WEATHER_API_KEY",
        "TAVILY_API_KEY",
        "PORCUPINE_ACCESS_KEY",
        "LOCATION_API_TOKEN",
        "BACKUP_ENCRYPTION_PASSPHRASE",
    }
)


def load_jarvis_env(*, force: bool = False) -> None:
    """Charge ``.env.config`` puis ``.env`` (idempotent).

    ``override=True`` sur ``.env.config`` : le fichier gagne sur un vieux
    ``TTS_ENGINE=kokoro`` hérité du shell / LaunchAgent (sinon Edge Henri
    reste ignoré et Kokoro EN parle encore).
    """
    global _ENV_LOADED
    if _ENV_LOADED and not force:
        return
    if CONFIG_ENV_FILE.is_file():
        load_dotenv(CONFIG_ENV_FILE, override=True)
    if SECRETS_ENV_FILE.is_file():
        load_dotenv(SECRETS_ENV_FILE, override=True)
    _ENV_LOADED = True
