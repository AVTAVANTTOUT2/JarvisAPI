"""Primitives de connexion SQLite partagées par les modules métier."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Iterator


def _current_db_path() -> Path:
    """Résout le chemin à l'appel pour préserver `database.DB_PATH` configurable."""
    from . import DB_PATH

    return Path(DB_PATH)


def get_connection() -> sqlite3.Connection:
    """Ouvre une connexion applicative configurée pour la concurrence locale."""
    conn = sqlite3.connect(str(_current_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
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
