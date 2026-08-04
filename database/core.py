"""Primitives SQLite, initialisation et contexte agrégé."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from core.file_security import ensure_private_directory, ensure_private_file

from .migrations import run_migrations
from .schema import SCHEMA
from .time_buckets import local_datetime, utc_bounds_for_local_dates

logger = logging.getLogger(__name__)


def _current_db_path() -> Path:
    """Résout le chemin à l'appel pour préserver `database.DB_PATH` configurable."""
    from . import DB_PATH

    return Path(DB_PATH)


def harden_sqlite_permissions(path: Path | None = None) -> None:
    """Force 0600 sur SQLite et ses sidecars WAL/SHM lorsqu'ils existent."""
    db_path = path or _current_db_path()
    if str(db_path) == ":memory:":
        return
    ensure_private_directory(db_path.parent)
    for candidate in (
        db_path,
        Path(f"{db_path}-wal"),
        Path(f"{db_path}-shm"),
        Path(f"{db_path}-journal"),
    ):
        ensure_private_file(candidate)


def get_connection() -> sqlite3.Connection:
    """Ouvre une connexion applicative configurée pour la concurrence locale."""
    db_path = _current_db_path()
    if str(db_path) != ":memory:":
        ensure_private_directory(db_path.parent)
    conn = sqlite3.connect(str(db_path))
    harden_sqlite_permissions(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    harden_sqlite_permissions(db_path)
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
        harden_sqlite_permissions()


def init_db() -> None:
    """Crée le schéma puis applique les migrations idempotentes."""
    with get_db() as conn:
        conn.executescript(SCHEMA)
        run_migrations(conn)
    harden_sqlite_permissions()
    logger.info("[DB] Base initialisée : %s", _current_db_path())


def build_full_context() -> dict:
    """Construit le contexte complet structuré pour Sonnet.

    Retourne un dict avec TOUTES les données pertinentes de la mémoire.
    Sonnet ne voit jamais de messages bruts — que des données denses.
    """
    from .episodes import get_recent_episodes
    from .facts import get_all_facts_summary
    from .patterns import get_active_patterns, get_recent_moods
    from .people import get_active_life_context, get_life_profile
    from .relationships import (
        get_active_insights,
        get_all_relationship_profiles,
    )
    from database.location_helpers import (
        get_active_location_patterns,
        get_current_location,
        get_current_visit,
        get_today_visits,
    )

    return {
        "user_facts": get_all_facts_summary(),
        "life_profile": get_life_profile(),
        "active_patterns": get_active_patterns(),
        "active_life_context": get_active_life_context(),
        "recent_moods": get_recent_moods(14),
        "people_profiles": get_all_relationship_profiles(),
        "cross_insights": get_active_insights(),
        "recent_episodes": get_recent_episodes(limit=10),
        "current_location": get_current_location(),
        "current_visit": get_current_visit(),
        "today_visits": get_today_visits(),
        "location_patterns": get_active_location_patterns(),
    }


def count_memory_stats() -> dict:
    """Compteurs pour tableaux de bord /api/status."""
    with get_db() as conn:
        def _one(query: str, params: tuple = ()) -> int:
            return int(conn.execute(query, params).fetchone()[0])

        return {
            "user_facts": _one("SELECT COUNT(*) FROM user_facts WHERE is_current = 1"),
            "relationship_profiles": _one("SELECT COUNT(*) FROM relationship_profiles"),
            "patterns_active": _one("SELECT COUNT(*) FROM patterns WHERE status = 'active'"),
            "episodes": _one("SELECT COUNT(*) FROM episodes"),
            "people": _one("SELECT COUNT(*) FROM people"),
            "cross_insights": _one("SELECT COUNT(*) FROM cross_insights WHERE status = 'active'"),
        }


def get_usage_stats(*, now: datetime | None = None) -> dict:
    local_now = local_datetime(now)
    start_utc, end_utc = utc_bounds_for_local_dates(
        local_now.date(),
        local_now.date() + timedelta(days=1),
    )
    with get_db() as conn:
        row = conn.execute(
            """SELECT COUNT(*) as msg_count,
                      COALESCE(SUM(CASE WHEN role = 'user' THEN 1 ELSE 0 END), 0) as turn_count,
                      COALESCE(SUM(tokens_in), 0) as total_in,
                      COALESCE(SUM(tokens_out), 0) as total_out,
                      COALESCE(SUM(cost), 0) as total_cost,
                      COALESCE(SUM(usage_estimated), 0) as estimated_usage_count
               FROM messages
               WHERE created_at >= ? AND created_at < ?""",
            (start_utc, end_utc),
        ).fetchone()
        return dict(row)
