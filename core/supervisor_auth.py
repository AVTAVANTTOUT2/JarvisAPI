"""Authentification du canal local supervisor vers le backend."""

from __future__ import annotations

import hmac
import secrets
from pathlib import Path

import config
from core.file_security import ensure_private_file, write_private_bytes

SUPERVISOR_CONTROL_HEADER = "X-Jarvis-Control-Token"
_MIN_TOKEN_CHARS = 40


def _token_path() -> Path:
    configured = getattr(config, "SUPERVISOR_CONTROL_TOKEN_FILE", "")
    if configured:
        return Path(configured)
    return Path(config.DB_PATH).parent / ".supervisor_control_token"


def load_supervisor_control_token(*, create: bool = False) -> str | None:
    """Charge le jeton privé partagé, et le crée atomiquement si demandé."""
    path = _token_path()
    if path.exists():
        ensure_private_file(path)
        token = path.read_text(encoding="utf-8").strip()
        if len(token) < _MIN_TOKEN_CHARS:
            raise RuntimeError("jeton de contrôle supervisor invalide ou trop court")
        return token

    if not create:
        return None

    token = secrets.token_urlsafe(32)
    try:
        write_private_bytes(path, token.encode("utf-8"), exclusive=True)
        return token
    except FileExistsError:
        # Un autre processus a gagné la création atomique du fichier.
        return load_supervisor_control_token(create=False)


def supervisor_control_headers() -> dict[str, str]:
    """En-têtes authentifiés pour un appel local supervisor → backend."""
    token = load_supervisor_control_token(create=True)
    if token is None:  # garde-fou de typage ; create=True doit toujours produire un jeton
        raise RuntimeError("jeton de contrôle supervisor indisponible")
    return {SUPERVISOR_CONTROL_HEADER: token}


def verify_supervisor_control_token(provided: str | None) -> bool:
    """Compare en temps constant le jeton présenté au secret privé local."""
    if not provided:
        return False
    try:
        expected = load_supervisor_control_token(create=False)
    except (OSError, RuntimeError):
        return False
    return bool(expected) and hmac.compare_digest(provided, expected)
