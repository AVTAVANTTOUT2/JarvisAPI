"""Recherche locale unifiée et classée sur les données personnelles JARVIS."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .core import get_db

_MIN_QUERY_LENGTH = 2
_MAX_QUERY_LENGTH = 200
_MAX_RESULTS = 100
_COORDINATOR_MAX_HITS = 8
_LEGACY_SOURCE_TYPES = (
    "conversation",
    "message",
    "person",
    "task",
    "document",
    "episode",
    "fact",
)


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


def _legacy_identifier(value: Any) -> int | str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if ":" in text:
        _, text = text.rsplit(":", 1)
    try:
        return int(text)
    except ValueError:
        return text


def _conversation_checkpoints(conversation_ids: set[int]) -> dict[int, str | None]:
    if not conversation_ids:
        return {}
    ordered = sorted(conversation_ids)
    placeholders = ",".join("?" for _ in ordered)
    rows = _rows(
        f"SELECT id, checkpoint_id FROM conversations WHERE id IN ({placeholders})",
        ordered,
    )
    return {int(row["id"]): row.get("checkpoint_id") for row in rows}


def _legacy_result(
    hit: Any,
    query: str,
    checkpoints: dict[int, str | None],
) -> dict[str, Any] | None:
    source_type = str(getattr(hit, "source_type", "") or "")
    source_id = _legacy_identifier(getattr(hit, "source_id", None))
    title = str(getattr(hit, "title", "") or "")
    body = str(getattr(hit, "content", None) or getattr(hit, "excerpt", "") or "")
    occurred_at = getattr(hit, "occurred_at", None)
    metadata = dict(getattr(hit, "metadata", {}) or {})

    if source_type in {"conversation", "message"}:
        conversation_id = getattr(hit, "conversation_id", None) or source_id
        try:
            conversation_id = int(conversation_id)
        except (TypeError, ValueError):
            return None
        return {
            "type": "conversation",
            "category": "conversations",
            "id": conversation_id,
            "checkpoint_id": checkpoints.get(conversation_id),
            "title": title or "Conversation sans titre",
            "subtitle": _excerpt(body, query),
            "meta": occurred_at,
            "url": f"/chat?conversation={conversation_id}",
            "score": _score(query, title, body, base=20),
        }

    if source_type == "person":
        return {
            "type": "person",
            "category": "contacts",
            "id": source_id,
            "title": title,
            "subtitle": _excerpt(body, query),
            "meta": occurred_at,
            "url": f"/contacts?person={source_id}",
            "score": _score(query, title, body, base=15),
        }

    if source_type == "task":
        return {
            "type": "task",
            "category": "tasks",
            "id": source_id,
            "title": title,
            "subtitle": _excerpt(body, query),
            "meta": " · ".join(
                str(value)
                for value in (
                    metadata.get("status"),
                    metadata.get("priority"),
                    metadata.get("due_at"),
                )
                if value
            ),
            "url": f"/tasks?task={source_id}",
            "score": _score(query, title, body, base=10),
        }

    if source_type == "document":
        # L'ancienne recherche unifiée ne couvrait que school_documents. Les
        # pièces jointes de conversation gardent leur politique dédiée.
        if metadata.get("adapter") != "school_documents":
            return None
        return {
            "type": "document",
            "category": "documents",
            "id": source_id,
            "title": title,
            "subtitle": _excerpt(body, query),
            "meta": metadata.get("file_type") or occurred_at,
            "url": f"/documents?document={source_id}",
            "score": _score(query, title, body, base=10),
        }

    if source_type in {"episode", "fact"}:
        meta = (
            metadata.get("agent")
            if source_type == "episode"
            else metadata.get("confidence")
        )
        return {
            "type": source_type,
            "category": "memory",
            "id": source_id,
            "title": _compact(title, 100),
            "subtitle": _excerpt(body, query),
            "meta": meta or occurred_at,
            "url": f"/data?entry={source_type}-{source_id}",
            "score": _score(query, title, body, base=5),
        }
    return None


def unified_search(query: str, limit: int = 50) -> list[dict[str, Any]]:
    """Retourne des résultats homogènes, classés et directement navigables.

    Le nouveau coordinateur fournit désormais les candidats et ce module ne
    conserve que l'adaptation vers le contrat historique du frontend. Le
    coordinateur borne actuellement une recherche à huit hits : le paramètre
    ``limit`` historique reste accepté et borné à 100, mais un appel ne peut pas
    retourner plus de huit éléments tant que le coordinateur n'expose pas de
    pagination.
    """
    normalized = " ".join((query or "").strip().split())[:_MAX_QUERY_LENGTH]
    if len(normalized) < _MIN_QUERY_LENGTH or not any(
        char.isalnum() for char in normalized
    ):
        return []

    result_limit = max(1, min(int(limit or 50), _MAX_RESULTS))
    from jarvis.retrieval import RetrievalRequest, search_knowledge

    retrieval = search_knowledge(
        RetrievalRequest(
            query=normalized,
            interaction_mode="legacy_unified_search",
            source_types=_LEGACY_SOURCE_TYPES,
            max_candidates=20,
            max_hits=min(result_limit, _COORDINATOR_MAX_HITS),
            char_budget=8_000,
        )
    )
    conversation_ids = {
        int(conversation_id)
        for hit in retrieval.hits
        if hit.source_type in {"conversation", "message"}
        for conversation_id in (
            hit.conversation_id or _legacy_identifier(hit.source_id),
        )
        if conversation_id is not None and str(conversation_id).isdigit()
    }
    checkpoints = _conversation_checkpoints(conversation_ids)
    deduplicated: dict[tuple[str, Any], dict[str, Any]] = {}
    for hit in retrieval.hits:
        item = _legacy_result(hit, normalized, checkpoints)
        if item is None:
            continue
        key = (str(item["type"]), item.get("id"))
        previous = deduplicated.get(key)
        if previous is None or int(item["score"]) > int(previous["score"]):
            deduplicated[key] = item

    results = list(deduplicated.values())

    results.sort(
        key=lambda item: (
            -int(item.get("score") or 0),
            str(item.get("category") or ""),
            str(item.get("title") or "").casefold(),
        )
    )
    return results[:result_limit]
