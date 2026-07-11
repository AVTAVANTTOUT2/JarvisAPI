"""Persistance des réglages applicatifs clé-valeur."""

from __future__ import annotations

from .core import get_db


def get_setting(key: str, default: str = "") -> str:
    """Lit un réglage et retourne `default` lorsqu'il est absent."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
    return row[0] if row else default


def set_setting(key: str, value: str) -> None:
    """Crée ou remplace un réglage applicatif."""
    with get_db() as conn:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
