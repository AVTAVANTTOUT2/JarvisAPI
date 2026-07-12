"""Primitives de connexion SQLite partagées par les modules métier."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Iterator

import config

logger = logging.getLogger(__name__)

_DEFAULT_BUSY_TIMEOUT_MS = 5000


def _current_db_path() -> Path:
    """Résout le chemin à l'appel pour préserver `database.DB_PATH` configurable."""
    from . import DB_PATH

    return Path(DB_PATH)


def _validated_busy_timeout_ms() -> int:
    """Retourne la valeur de busy_timeout en ms, avec fallback si invalide."""
    value = config.SQLITE_BUSY_TIMEOUT_MS
    if not isinstance(value, int) or value <= 0:
        logger.warning(
            "SQLITE_BUSY_TIMEOUT_MS invalide (%r), fallback à %d ms",
            value,
            _DEFAULT_BUSY_TIMEOUT_MS,
        )
        return _DEFAULT_BUSY_TIMEOUT_MS
    return value


def get_connection() -> sqlite3.Connection:
    """Ouvre une connexion applicative configurée pour la concurrence locale."""
    conn = sqlite3.connect(str(_current_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {_validated_busy_timeout_ms()}")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    """Fournit une transaction avec commit, rollback et fermeture garantis."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
