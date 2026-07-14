"""Persistance des conversations, messages et workflows agentiques."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from typing import Any

from jarvis.event_bus import event_bus
from jarvis.events import ConversationUpdated, MessageSent

from .core import get_db
from .migrations import _fts_available, _fts_query

logger = logging.getLogger(__name__)


def save_message(conversation_id: int, role: str, content: str,
                 agent: str = None, model: str = None,
                 tokens_in: int = 0, tokens_out: int = 0, cost: float = 0.0) -> int:
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO messages (conversation_id, role, content, agent, model, tokens_in, tokens_out, cost)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (conversation_id, role, content, agent, model, tokens_in, tokens_out, cost)
        )
        message_id = int(cur.lastrowid)
    event_bus.emit_nowait(MessageSent(conversation_id, message_id, role, content))
    return message_id


def create_agentic_workflow(
    conversation_id: int,
    user_message: str,
    initial_action: dict,
) -> int:
    """Crée un workflow agentique en cours."""
    payload = json.dumps(
        [{"step": 0, "action": initial_action}],
        ensure_ascii=False,
        default=str,
    )
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO agentic_workflows
               (conversation_id, user_message, steps_json, status, total_steps, total_output_chars)
               VALUES (?, ?, ?, 'running', 0, 0)""",
            (conversation_id, user_message, payload),
        )
        return int(cur.lastrowid)


def update_agentic_workflow(
    workflow_id: int,
    *,
    steps_json: str,
    status: str,
    final_synthesis: str | None = None,
    total_steps: int = 0,
    total_output_chars: int = 0,
) -> None:
    """Met à jour un workflow agentique à la fin (ou en échec)."""
    with get_db() as conn:
        conn.execute(
            """UPDATE agentic_workflows
               SET steps_json = ?, status = ?, final_synthesis = ?,
                   total_steps = ?, total_output_chars = ?,
                   completed_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (
                steps_json,
                status,
                final_synthesis,
                total_steps,
                total_output_chars,
                workflow_id,
            ),
        )


def create_conversation(agent: str = None) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO conversations (agent) VALUES (?)", (agent,)
        )
        conversation_id = int(cur.lastrowid)
    event_bus.emit_nowait(
        ConversationUpdated(conversation_id, {"created": True, "agent": agent})
    )
    return conversation_id


def end_conversation(conv_id: int, summary: str = None) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE conversations SET ended_at = ?, summary = ? WHERE id = ?",
            (datetime.now().isoformat(), summary, conv_id)
        )


def get_conversations(limit: int = 50, archived: bool = False) -> list[dict]:
    """Liste des conversations triées par dernière activité."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT c.*,
                (SELECT content FROM messages WHERE conversation_id = c.id ORDER BY created_at DESC LIMIT 1) as last_message,
                (SELECT COUNT(*) FROM messages WHERE conversation_id = c.id) as msg_count
            FROM conversations c
            WHERE COALESCE(c.archived, 0) = ?
            ORDER BY COALESCE(c.last_message_at, c.started_at) DESC
            LIMIT ?
        """, (1 if archived else 0, limit)).fetchall()
        return [dict(r) for r in rows]


def get_conversation_detail(conv_id: int) -> dict | None:
    """Retourne la conversation avec ses messages et documents."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["messages"] = get_conversation_history(conv_id, limit=200)
        docs = conn.execute(
            "SELECT id, original_name, file_type, file_size, summary, created_at FROM conversation_documents WHERE conversation_id = ?",
            (conv_id,)
        ).fetchall()
        result["documents"] = [dict(d) for d in docs]
        return result


def update_conversation(conv_id: int, **kwargs: Any) -> None:
    """Met à jour un ou plusieurs champs de la conversation."""
    if not kwargs:
        return
    with get_db() as conn:
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [conv_id]
        cursor = conn.execute(f"UPDATE conversations SET {sets} WHERE id = ?", vals)
        updated = cursor.rowcount > 0
    if updated:
        event_bus.emit_nowait(ConversationUpdated(conv_id, dict(kwargs)))


def update_conversation_activity(conv_id: int) -> None:
    """Met à jour last_message_at et message_count après chaque message."""
    with get_db() as conn:
        conn.execute("""
            UPDATE conversations SET
                last_message_at = CURRENT_TIMESTAMP,
                message_count = (SELECT COUNT(*) FROM messages WHERE conversation_id = ?)
            WHERE id = ?
        """, (conv_id, conv_id))


def delete_conversation(conv_id: int) -> None:
    """Supprime une conversation et tous ses messages + documents."""
    with get_db() as conn:
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
        conn.execute("DELETE FROM conversation_documents WHERE conversation_id = ?", (conv_id,))
        conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))


def search_conversations(query: str, limit: int = 20) -> list[dict]:
    """Recherche dans les titres et le contenu des messages de toutes les conversations.

    Une conversation n'apparaît qu'une fois, avec son message correspondant le
    plus récent. Utilise l'index FTS5 (insensible aux accents, préfixe sur le
    dernier mot) quand il existe, sinon LIKE.
    """
    q = (query or "").strip()
    if not q:
        return []
    with get_db() as conn:
        rows: list = []
        fts_q = _fts_query(q)
        if fts_q and _fts_available(conn):
            try:
                rows = conn.execute("""
                    SELECT c.id, c.title, c.started_at, c.last_message_at, c.message_count,
                           m.content AS matching_message, MAX(m.created_at) AS match_date
                    FROM messages_fts f
                    JOIN messages m ON m.id = f.rowid
                    JOIN conversations c ON c.id = m.conversation_id
                    WHERE messages_fts MATCH ?
                    GROUP BY c.id
                    ORDER BY match_date DESC
                    LIMIT ?
                """, (fts_q, limit)).fetchall()
            except sqlite3.OperationalError as e:
                logger.warning("search_conversations FTS (%s) — fallback LIKE", e)
                rows = []
            if rows:
                # L'index FTS ne couvre que le contenu — ajoute les matchs de titre.
                seen = {r["id"] for r in rows}
                title_rows = conn.execute("""
                    SELECT c.id, c.title, c.started_at, c.last_message_at, c.message_count,
                           NULL AS matching_message, c.last_message_at AS match_date
                    FROM conversations c
                    WHERE c.title LIKE ?
                    ORDER BY c.last_message_at DESC
                    LIMIT ?
                """, (f"%{q}%", limit)).fetchall()
                rows = list(rows) + [r for r in title_rows if r["id"] not in seen]
                rows = rows[:limit]
        if not rows:
            rows = conn.execute("""
                SELECT c.id, c.title, c.started_at, c.last_message_at, c.message_count,
                       m.content AS matching_message, MAX(m.created_at) AS match_date
                FROM conversations c
                JOIN messages m ON m.conversation_id = c.id
                WHERE m.content LIKE ? OR c.title LIKE ?
                GROUP BY c.id
                ORDER BY match_date DESC
                LIMIT ?
            """, (f"%{q}%", f"%{q}%", limit)).fetchall()
        return [dict(r) for r in rows]


def save_conversation_document(
    conv_id: int,
    filename: str,
    original_name: str,
    file_path: str,
    file_type: str,
    file_size: int,
    extracted_text: str | None = None,
    summary: str | None = None,
) -> int:
    """Enregistre un document attaché à une conversation."""
    with get_db() as conn:
        cur = conn.execute("""
            INSERT INTO conversation_documents
                (conversation_id, filename, original_name, file_path, file_type, file_size, extracted_text, summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (conv_id, filename, original_name, file_path, file_type, file_size, extracted_text, summary))
        return cur.lastrowid


def get_conversation_documents(conv_id: int) -> list[dict]:
    """Retourne tous les documents attachés à une conversation."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM conversation_documents WHERE conversation_id = ? ORDER BY created_at",
            (conv_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_conversation_history(conv_id: int, limit: int = 50) -> list:
    """Récupère les derniers messages d'une conversation, ordre chronologique ASC."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT role, content, agent, created_at
               FROM messages
               WHERE conversation_id = ?
               ORDER BY created_at ASC
               LIMIT ?""",
            (conv_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_last_conversation_summary() -> str | None:
    """Résumé textuel de la conversation terminée la plus récente (si présent)."""
    with get_db() as conn:
        row = conn.execute(
            """SELECT summary FROM conversations
               WHERE summary IS NOT NULL AND TRIM(summary) != ''
                 AND ended_at IS NOT NULL
               ORDER BY datetime(ended_at) DESC LIMIT 1"""
        ).fetchone()
    if not row or not row["summary"]:
        return None
    s = str(row["summary"]).strip()
    return s or None


def get_messages_since(
    since_id: int, limit: int = 50
) -> list[dict[str, Any]]:
    """Récupère les messages (table messages) postérieurs à ``since_id``.

    Args:
        since_id: ID du dernier message déjà traité.
        limit: Nombre max de messages à retourner.

    Returns:
        Liste de dicts {id, content, role, created_at}. Vide si aucun nouveau message.
    """
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, content, role, created_at
               FROM messages
               WHERE id > ?
               ORDER BY id ASC
               LIMIT ?""",
            (since_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def save_message_insight(
    since_id: int,
    raw_response: str,
    message_count: int,
) -> int:
    """Persiste un insight généré à partir des messages.

    Args:
        since_id: ID du dernier message couvert par cet insight.
        raw_response: Contenu dé-anonymisé de la réponse DeepSeek (JSON stringifié).
        message_count: Nombre de messages analysés.

    Returns:
        ID de la ligne insérée.
    """
    import json as _json

    # Valide que le JSON est bien formé avant l'insertion.
    if isinstance(raw_response, dict):
        raw_response = _json.dumps(raw_response, ensure_ascii=False)
    else:
        try:
            _json.loads(raw_response)
        except (_json.JSONDecodeError, ValueError):
            # Enveloppe le texte brut dans un JSON pour éviter les corruptions.
            raw_response = _json.dumps(
                {"raw": raw_response}, ensure_ascii=False
            )

    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO message_insights
               (since_message_id, message_count, result_json)
               VALUES (?, ?, ?)""",
            (since_id, message_count, raw_response),
        )
        return cur.lastrowid
