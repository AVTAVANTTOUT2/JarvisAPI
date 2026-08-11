"""Registre et cycle de vie des profils utilisateur isolés."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from uuid import uuid4

from .core import (
    DEFAULT_PROFILE_ID,
    get_db,
    init_db,
    normalize_profile_id,
    profile_database_path,
    use_profile,
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _profile_slug(display_name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", display_name).encode("ascii", "ignore").decode()
    slug = _SLUG_RE.sub("-", ascii_name.casefold()).strip("-")[:20] or "profil"
    return normalize_profile_id(f"{slug}-{uuid4().hex[:8]}")


def list_user_profiles(*, include_inactive: bool = False) -> list[dict]:
    """Liste le registre depuis la base historique, unique source de vérité."""
    with use_profile(DEFAULT_PROFILE_ID), get_db() as conn:
        where = "" if include_inactive else "WHERE is_active = 1"
        rows = conn.execute(
            f"""
            SELECT id, display_name, is_active, created_at, last_used_at
            FROM user_profiles
            {where}
            ORDER BY CASE id WHEN 'default' THEN 0 ELSE 1 END, display_name COLLATE NOCASE
            """  # noqa: S608 - fragment interne constant
        ).fetchall()
    return [dict(row) for row in rows]


def user_profile_exists(profile_id: str) -> bool:
    """Refuse les profils absents, désactivés ou dont la base a disparu."""
    selected = normalize_profile_id(profile_id)
    if selected == DEFAULT_PROFILE_ID:
        return True
    with use_profile(DEFAULT_PROFILE_ID), get_db() as conn:
        row = conn.execute(
            "SELECT is_active FROM user_profiles WHERE id = ?",
            (selected,),
        ).fetchone()
    return bool(row and row["is_active"] and profile_database_path(selected).is_file())


def create_user_profile(display_name: str) -> dict:
    """Crée une base complète et privée ; aucun domaine ne partage ses lignes."""
    name = " ".join(str(display_name or "").split())
    if not 1 <= len(name) <= 80:
        raise ValueError("Le nom du profil doit contenir entre 1 et 80 caractères")
    profile_id = _profile_slug(name)

    with use_profile(DEFAULT_PROFILE_ID), get_db() as conn:
        conn.execute(
            "INSERT INTO user_profiles (id, display_name) VALUES (?, ?)",
            (profile_id, name),
        )

    try:
        with use_profile(profile_id):
            init_db()
    except Exception:
        with use_profile(DEFAULT_PROFILE_ID), get_db() as conn:
            conn.execute("DELETE FROM user_profiles WHERE id = ?", (profile_id,))
        raise

    return next(profile for profile in list_user_profiles() if profile["id"] == profile_id)


def deactivate_user_profile(profile_id: str) -> bool:
    """Désactive sans supprimer la base, afin de garder une récupération possible."""
    selected = normalize_profile_id(profile_id)
    if selected == DEFAULT_PROFILE_ID:
        raise ValueError("Le profil principal ne peut pas être désactivé")
    with use_profile(DEFAULT_PROFILE_ID), get_db() as conn:
        cursor = conn.execute(
            "UPDATE user_profiles SET is_active = 0 WHERE id = ? AND is_active = 1",
            (selected,),
        )
    return cursor.rowcount > 0


def touch_user_profile(profile_id: str) -> None:
    """Mémorise un usage réussi sans ouvrir la base du profil cible."""
    selected = normalize_profile_id(profile_id)
    with use_profile(DEFAULT_PROFILE_ID), get_db() as conn:
        conn.execute(
            "UPDATE user_profiles SET last_used_at = CURRENT_TIMESTAMP WHERE id = ?",
            (selected,),
        )


def profile_data_path(profile_id: str) -> Path:
    """Expose le chemin pour les opérations internes de sauvegarde à venir."""
    return profile_database_path(normalize_profile_id(profile_id))
