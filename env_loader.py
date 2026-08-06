"""Charge les fichiers d'environnement JARVIS.

Ordre de chargement :
1. ``.env.config`` — paramètres applicatifs (ports, modèles, intervalles…)
2. ``.env`` — clés API et secrets (écrase une clé homonyme si présente)

Rétro-compatibilité : un unique ``.env`` contenant tout reste supporté tant que
``.env.config`` est absent ou complété progressivement.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values, load_dotenv

from core.file_security import ensure_private_file

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


class EnvironmentPolicyError(RuntimeError):
    """Un fichier local viole la séparation ou les permissions attendues."""


def _prepare_env_file(path: Path) -> dict[str, str | None]:
    """Refuse les liens, force 0600 et parse sans publier les valeurs."""
    ensure_private_file(path)
    return dict(dotenv_values(path))


def _sanitize_dotenv_value(value: str | None) -> str | None:
    """Corrige le piège python-dotenv ``KEY=   # commentaire``.

    Quand la valeur est vide et qu'un commentaire inline suit, python-dotenv
    conserve ``# commentaire`` comme valeur. Une telle valeur n'est jamais
    intentionnelle dans la config JARVIS.
    """
    if value is None:
        return None
    stripped = value.strip()
    if stripped.startswith("#"):
        return ""
    return value


def _apply_env_values(values: dict[str, str | None], *, override: bool) -> None:
    """Publie les paires dans ``os.environ`` après sanitation des commentaires."""
    import os

    for key, value in values.items():
        if key is None:
            continue
        cleaned = _sanitize_dotenv_value(value)
        if cleaned is None:
            continue
        if not override and key in os.environ:
            continue
        os.environ[key] = cleaned


def load_jarvis_env(*, force: bool = False) -> None:
    """Charge ``.env.config`` puis ``.env`` (idempotent).

    ``override=True`` sur ``.env.config`` : le fichier gagne sur une variable
    héritée du shell ou du LaunchAgent. Sans cela, un réglage oublié dans
    l'environnement d'un service lancé au démarrage prime silencieusement sur
    la configuration versionnée — et le poste ne se comporte pas comme son
    fichier de configuration le dit.
    """
    global _ENV_LOADED
    if _ENV_LOADED and not force:
        return
    if CONFIG_ENV_FILE.is_file():
        config_values = _prepare_env_file(CONFIG_ENV_FILE)
        misplaced = sorted(SECRET_ENV_KEYS.intersection(config_values))
        if misplaced:
            names = ", ".join(misplaced)
            raise EnvironmentPolicyError(
                "Secrets interdits dans .env.config : "
                f"{names}. Déplacez-les dans .env."
            )
        load_dotenv(CONFIG_ENV_FILE, override=True)
        _apply_env_values(config_values, override=True)
    if SECRETS_ENV_FILE.is_file():
        secrets_values = _prepare_env_file(SECRETS_ENV_FILE)
        load_dotenv(SECRETS_ENV_FILE, override=True)
        _apply_env_values(secrets_values, override=True)
    _ENV_LOADED = True
