"""Recherche locale unifiée et classée sur les données personnelles JARVIS."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .conversations import search_conversations
from .core import get_db

_MIN_QUERY_LENGTH = 2
_MAX_QUERY_LENGTH = 200
_MAX_RESULTS = 100


def _like_pattern(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _compact(value: Any, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}…"


def _excerpt(value: Any, query: str, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    match_index = text.casefold().find(query.casefold())
    if match_index < 0:
        return _compact(text, limit)
    start = max(0, match_index - limit // 3)
    end = min(len(text), start + limit)
    start = max(0, end - limit)
    return f"{'…' if start else ''}{text[start:end].strip()}{'…' if end < len(text) else ''}"


def _score(query: str, title: Any, body: Any = "", *, base: int = 0) -> int:
    needle = query.casefold()
    normalized_title = str(title or "").casefold()
    normalized_body = str(body or "").casefold()
    if normalized_title == needle:
        return base + 100
    if normalized_title.startswith(needle):
        return base + 80
    if needle in normalized_title:
        return base + 60
    if needle in normalized_body:
        return base + 30
    return base


def _rows(sql: str, params: Iterable[Any]) -> list[dict[str, Any]]:
    with get_db() as conn:
        return [dict(row) for row in conn.execute(sql, tuple(params)).fetchall()]


def unified_search(query: str, limit: int = 50) -> list[dict[str, Any]]:
    """Retourne des résultats homogènes, classés et directement navigables.

    Le moteur FTS5 existant est utilisé pour les conversations. Les autres
    domaines s'appuient sur des requêtes SQLite bornées ; les caractères ``%``
    et ``_`` sont échappés pour qu'une saisie utilisateur ne devienne jamais un
    joker involontaire.
    """
    normalized = " ".join((query or "").strip().split())[:_MAX_QUERY_LENGTH]
    if len(normalized) < _MIN_QUERY_LENGTH or not any(char.isalnum() for char in normalized):
        return []

    result_limit = max(1, min(int(limit or 50), _MAX_RESULTS))
    per_source = max(8, min(30, result_limit))
    like = _like_pattern(normalized)
    results: list[dict[str, Any]] = []

    for row in search_conversations(normalized, limit=per_source):
        title = row.get("title") or "Conversation sans titre"
        snippet = row.get("matching_message") or ""
        results.append(
            {
                "type": "conversation",
                "category": "conversations",
                "id": row.get("id"),
                "checkpoint_id": row.get("checkpoint_id"),
                "title": title,
                "subtitle": _excerpt(snippet, normalized),
                "meta": row.get("match_date") or row.get("last_message_at"),
                "url": f"/chat?conversation={row.get('id')}",
                "score": _score(normalized, title, snippet, base=20),
            }
        )

    people = _rows(
        """
        SELECT id, name, relationship, ai_description, last_mentioned
        FROM people
        WHERE name LIKE ? ESCAPE '\\' COLLATE NOCASE
           OR relationship LIKE ? ESCAPE '\\' COLLATE NOCASE
           OR personality_notes LIKE ? ESCAPE '\\' COLLATE NOCASE
           OR ai_description LIKE ? ESCAPE '\\' COLLATE NOCASE
        ORDER BY last_mentioned IS NULL, last_mentioned DESC
        LIMIT ?
        """,
        (like, like, like, like, per_source),
    )
    for row in people:
        body = f"{row.get('relationship') or ''} {row.get('ai_description') or ''}"
        results.append(
            {
                "type": "person",
                "category": "contacts",
                "id": row["id"],
                "title": row["name"],
                "subtitle": _excerpt(body, normalized),
                "meta": row.get("last_mentioned"),
                "url": f"/contacts?person={row['id']}",
                "score": _score(normalized, row["name"], body, base=15),
            }
        )

    tasks = _rows(
        """
        SELECT id, title, description, priority, status, due_date, category
        FROM tasks
        WHERE title LIKE ? ESCAPE '\\' COLLATE NOCASE
           OR description LIKE ? ESCAPE '\\' COLLATE NOCASE
           OR category LIKE ? ESCAPE '\\' COLLATE NOCASE
        ORDER BY status = 'done', due_date IS NULL, due_date
        LIMIT ?
        """,
        (like, like, like, per_source),
    )
    for row in tasks:
        results.append(
            {
                "type": "task",
                "category": "tasks",
                "id": row["id"],
                "title": row["title"],
                "subtitle": _excerpt(row.get("description"), normalized),
                "meta": " · ".join(
                    str(value)
                    for value in (row.get("status"), row.get("priority"), row.get("due_date"))
                    if value
                ),
                "url": f"/tasks?task={row['id']}",
                "score": _score(normalized, row["title"], row.get("description"), base=10),
            }
        )

    documents = _rows(
        """
        SELECT id, title, content, doc_type, created_at
        FROM school_documents
        WHERE title LIKE ? ESCAPE '\\' COLLATE NOCASE
           OR content LIKE ? ESCAPE '\\' COLLATE NOCASE
           OR doc_type LIKE ? ESCAPE '\\' COLLATE NOCASE
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (like, like, like, per_source),
    )
    for row in documents:
        results.append(
            {
                "type": "document",
                "category": "documents",
                "id": row["id"],
                "title": row["title"],
                "subtitle": _excerpt(row.get("content"), normalized),
                "meta": row.get("doc_type") or row.get("created_at"),
                "url": f"/documents?document={row['id']}",
                "score": _score(normalized, row["title"], row.get("content"), base=10),
            }
        )

    memories = _rows(
        """
        SELECT 'episode' AS source_type, id, COALESCE(summary, content) AS title,
               content AS body, agent AS meta, created_at
        FROM episodes
        WHERE summary LIKE ? ESCAPE '\\' COLLATE NOCASE
           OR content LIKE ? ESCAPE '\\' COLLATE NOCASE
           OR tags LIKE ? ESCAPE '\\' COLLATE NOCASE
        UNION ALL
        SELECT 'fact' AS source_type, id, category AS title,
               content AS body, confidence AS meta, updated_at AS created_at
        FROM user_facts
        WHERE is_current = 1
          AND (category LIKE ? ESCAPE '\\' COLLATE NOCASE
               OR content LIKE ? ESCAPE '\\' COLLATE NOCASE)
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (like, like, like, like, like, per_source),
    )
    for row in memories:
        results.append(
            {
                "type": row["source_type"],
                "category": "memory",
                "id": row["id"],
                "title": _compact(row["title"], 100),
                "subtitle": _excerpt(row.get("body"), normalized),
                "meta": row.get("meta") or row.get("created_at"),
                "url": f"/data?entry={row['source_type']}-{row['id']}",
                "score": _score(normalized, row["title"], row.get("body"), base=5),
            }
        )

    results.sort(
        key=lambda item: (
            -int(item.get("score") or 0),
            str(item.get("category") or ""),
            str(item.get("title") or "").casefold(),
        )
    )
    return results[:result_limit]
