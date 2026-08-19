"""Derniers messages — iMessage + chat JARVIS.

Lit les iMessages depuis le miroir ``jarvis.db`` (pas ``chat.db``)
et les messages chat JARVIS, puis les fusionne triés par timestamp.
"""

from __future__ import annotations

import logging
from database import dbapi as sqlite3
from pathlib import Path
from typing import Any

import config as cfg
from integrations.apple_data import apple_epoch_to_datetime
from integrations.imessage_reader import imessage_reader

logger = logging.getLogger(__name__)

def get_recent_messages() -> list[dict[str, Any]]:
    """Retourne les derniers messages (iMessage + chat JARVIS) fusionnés."""
    imessages = _get_imessages()
    chat_msgs = _get_chat_messages()
    combined = imessages + chat_msgs
    combined.sort(key=lambda m: m.get("timestamp", ""), reverse=True)
    return combined[:cfg.MAX_MESSAGES]


def _resolve_handle_name(handle: str) -> str:
    """Résout un handle iMessage (numéro/email) vers un nom depuis la table people."""
    db_path = _resolve_jarvis_db()
    if not db_path or not handle:
        return handle
    try:
        # Matcher via les relationship_profiles
        conn = sqlite3.connect_readonly(db_path)
        cur = conn.execute(
            """SELECT p.name FROM people p
               JOIN relationship_profiles rp ON rp.person_id = p.id
               WHERE rp.handle = ? LIMIT 1""",
            (handle,),
        )
        rp_row = cur.fetchone()
        conn.close()
        if rp_row:
            return rp_row[0]
    except Exception:
        pass
    return handle


def _get_imessages() -> list[dict[str, Any]]:
    """Lit les derniers iMessages reçus depuis le miroir JARVIS."""
    if not imessage_reader.is_available():
        logger.debug("miroir iMessage indisponible")
        return []
    try:
        rows = imessage_reader.get_recent_messages(
            limit=cfg.MAX_IMESSAGES,
            incoming_only=True,
        )
    except Exception as exc:
        logger.warning("iMessage miroir indisponible: %s", exc)
        return []

    results: list[dict[str, Any]] = []
    for row in rows:
        date = apple_epoch_to_datetime(row["date"])
        handle = row["handle"] or "Inconnu"
        display_name = _resolve_handle_name(handle)
        text = (row["text"] or "").strip()[:80]

        results.append({
            "source": "imessage",
            "handle": handle,
            "display_name": display_name,
            "text": text,
            "timestamp": date.isoformat() if date else "",
        })

    return results


def _get_chat_messages() -> list[dict[str, Any]]:
    """Lit les derniers messages chat JARVIS depuis jarvis.db."""
    db_path = _resolve_jarvis_db()
    if not db_path:
        return []
    try:
        conn = sqlite3.connect_readonly(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """SELECT m.id, m.content, m.role, m.agent, m.created_at, c.title
               FROM messages m
               JOIN conversations c ON c.id = m.conversation_id
               WHERE m.role = 'user'
               ORDER BY m.created_at DESC
               LIMIT ?""",
            (cfg.MAX_CHAT_MESSAGES,),
        )
        rows = cur.fetchall()
        conn.close()
    except sqlite3.OperationalError as exc:
        logger.warning("Chat messages indisponibles: %s", exc)
        return []

    results: list[dict[str, Any]] = []
    for row in rows:
        results.append({
            "source": "jarvis",
            "display_name": "user",
            "text": (row["content"] or "").strip()[:80],
            "timestamp": row["created_at"] or "",
            "conversation_title": row["title"] or "Sans titre",
        })
    return results


def _resolve_jarvis_db() -> str | None:
    root = Path(__file__).resolve().parent.parent.parent
    db_full = root / "data" / "jarvis.db"
    if db_full.exists():
        return str(db_full)
    return None
