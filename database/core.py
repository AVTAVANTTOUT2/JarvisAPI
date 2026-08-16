"""Primitives SQLite, initialisation et contexte agrégé."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import datetime, timedelta
from pathlib import Path

from core.file_security import ensure_private_directory, ensure_private_file

from . import dbapi as sqlite3
from .migrations import run_migrations
from .schema import SCHEMA
from .time_buckets import local_datetime, utc_bounds_for_local_dates

logger = logging.getLogger(__name__)

DEFAULT_PROFILE_ID = "default"
_PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_active_profile_id: ContextVar[str] = ContextVar(
    "jarvis_active_profile_id",
    default=DEFAULT_PROFILE_ID,
)
_ambient_connection: ContextVar[sqlite3.Connection | None] = ContextVar(
    "jarvis_ambient_db_connection",
    default=None,
)
_ambient_connection_profile: ContextVar[str | None] = ContextVar(
    "jarvis_ambient_db_profile",
    default=None,
)


def normalize_profile_id(profile_id: str | None) -> str:
    """Valide un identifiant de profil avant tout usage comme segment de chemin."""
    candidate = str(profile_id or DEFAULT_PROFILE_ID).strip().casefold()
    if not _PROFILE_ID_RE.fullmatch(candidate):
        raise ValueError("Identifiant de profil invalide")
    return candidate


def current_profile_id() -> str:
    """Retourne le profil lié à la requête/tâche asyncio courante."""
    return _active_profile_id.get()


def activate_profile(profile_id: str) -> Token[str]:
    """Lie un profil au contexte courant et retourne le jeton de restauration."""
    return _active_profile_id.set(normalize_profile_id(profile_id))


def reset_profile(token: Token[str]) -> None:
    """Restaure le contexte de profil précédent."""
    _active_profile_id.reset(token)


@contextmanager
def use_profile(profile_id: str) -> Iterator[None]:
    """Isole toutes les connexions ouvertes dans le bloc sur un profil."""
    token = activate_profile(profile_id)
    try:
        yield
    finally:
        reset_profile(token)


def profile_database_path(profile_id: str | None = None) -> Path:
    """Résout la base d'un profil sans permettre de sortie de la racine dédiée."""
    from . import DB_PATH

    base_path = Path(DB_PATH)
    selected = normalize_profile_id(profile_id or current_profile_id())
    if selected == DEFAULT_PROFILE_ID:
        return base_path
    if str(base_path) == ":memory:":
        raise RuntimeError(
            "Les profils additionnels exigent une base SQLite sur disque"
        )
    return base_path.parent / "profiles" / selected / base_path.name


def profile_storage_path(base_path: str | Path, profile_id: str | None = None) -> Path:
    """Partitionne un répertoire de données selon le même profil que SQLite."""
    root = Path(base_path).expanduser()
    selected = normalize_profile_id(profile_id or current_profile_id())
    if selected == DEFAULT_PROFILE_ID:
        return root
    return root.parent / "profiles" / selected / root.name


def _current_db_path() -> Path:
    """Résout le chemin à l'appel pour préserver `database.DB_PATH` configurable."""
    return profile_database_path()


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
    conn = sqlite3.connect(str(db_path), profile_id=current_profile_id())
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
    ambient = _ambient_connection.get()
    if ambient is not None:
        if _ambient_connection_profile.get() != current_profile_id():
            raise RuntimeError("ambient_db_profile_mismatch")
        yield ambient
        return
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


@contextmanager
def db_transaction() -> Iterator[sqlite3.Connection]:
    """Regroupe explicitement plusieurs écritures métier dans une transaction."""

    ambient = _ambient_connection.get()
    if ambient is not None:
        if _ambient_connection_profile.get() != current_profile_id():
            raise RuntimeError("ambient_db_profile_mismatch")
        yield ambient
        return
    conn = get_connection()
    token = _ambient_connection.set(conn)
    profile_token = _ambient_connection_profile.set(current_profile_id())
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _ambient_connection.reset(token)
        _ambient_connection_profile.reset(profile_token)
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
            "patterns_active": _one(
                "SELECT COUNT(*) FROM patterns WHERE status = 'active'"
            ),
            "episodes": _one("SELECT COUNT(*) FROM episodes"),
            "people": _one("SELECT COUNT(*) FROM people"),
            "cross_insights": _one(
                "SELECT COUNT(*) FROM cross_insights WHERE status = 'active'"
            ),
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
