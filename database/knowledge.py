"""Persistance regenerable de l'index de connaissances multi-source."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from .core import get_db
from .time_buckets import sqlite_utc_timestamp


_MAX_SEARCH_LIMIT = 20
_FTS_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def canonical_knowledge_uid(
    source_type: str, source_id: str | int, chunk_index: int = 0
) -> str:
    source = str(source_type).strip()
    identifier = str(source_id).strip()
    chunk = max(0, int(chunk_index))
    if not source or not identifier:
        raise ValueError("knowledge_source_required")
    return f"{source}:{identifier}:{chunk}"


def upsert_knowledge_item(
    *,
    source_type: str,
    source_id: str | int,
    searchable_text: str,
    title: str = "",
    summary: str = "",
    chunk_index: int = 0,
    conversation_id: int | None = None,
    people: Sequence[str] = (),
    occurred_at: str | None = None,
    source_updated_at: str | None = None,
    sensitivity: str = "personal",
    cloud_policy: str = "redact",
    trust: str = "untrusted_stored_data",
    metadata: Mapping[str, Any] | None = None,
) -> str:
    """Cree ou rafraichit une projection idempotente et invalide son vecteur."""

    text = str(searchable_text or "").strip()
    clean_title = str(title or "").strip()[:1_000]
    clean_summary = str(summary or "").strip()[:4_000]
    if not text:
        text = clean_summary or clean_title
    if not text:
        raise ValueError("knowledge_text_required")
    source = str(source_type).strip()
    identifier = str(source_id).strip()
    chunk = max(0, int(chunk_index))
    uid = canonical_knowledge_uid(source, identifier, chunk)
    people_json = _json_dumps(
        [str(person).strip() for person in people if str(person).strip()]
    )
    metadata_json = _json_dumps(dict(metadata or {}))
    content_hash = hashlib.sha256(
        _json_dumps(
            {
                "title": clean_title,
                "text": text,
                "summary": clean_summary,
                "people": people_json,
                "occurred_at": occurred_at,
                "updated_at": source_updated_at,
                "metadata": metadata_json,
            }
        ).encode("utf-8")
    ).hexdigest()
    indexed_at = sqlite_utc_timestamp()

    with get_db() as conn:
        previous = conn.execute(
            "SELECT id, content_hash FROM knowledge_items WHERE uid = ?",
            (uid,),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO knowledge_items(
                uid, source_type, source_id, chunk_index, conversation_id,
                title, searchable_text, summary, people_json, occurred_at,
                source_updated_at, indexed_at, content_hash, sensitivity,
                cloud_policy, trust, metadata_json, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(source_type, source_id, chunk_index) DO UPDATE SET
                uid = excluded.uid,
                conversation_id = excluded.conversation_id,
                title = excluded.title,
                searchable_text = excluded.searchable_text,
                summary = excluded.summary,
                people_json = excluded.people_json,
                occurred_at = excluded.occurred_at,
                source_updated_at = excluded.source_updated_at,
                indexed_at = CASE
                    WHEN knowledge_items.content_hash = excluded.content_hash
                    THEN knowledge_items.indexed_at
                    ELSE excluded.indexed_at
                END,
                content_hash = excluded.content_hash,
                sensitivity = excluded.sensitivity,
                cloud_policy = excluded.cloud_policy,
                trust = excluded.trust,
                metadata_json = excluded.metadata_json,
                deleted_at = NULL
            """,
            (
                uid,
                source,
                identifier,
                chunk,
                conversation_id,
                clean_title,
                text,
                clean_summary,
                people_json,
                occurred_at,
                source_updated_at,
                indexed_at,
                content_hash,
                str(sensitivity or "personal")[:40],
                str(cloud_policy or "redact")[:40],
                str(trust or "untrusted_stored_data")[:80],
                metadata_json,
            ),
        )
        if previous is not None and str(previous["content_hash"]) != content_hash:
            conn.execute(
                "DELETE FROM knowledge_embeddings WHERE knowledge_item_id = ?",
                (int(previous["id"]),),
            )
    return uid


def get_knowledge_item_row(uid: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM knowledge_items WHERE uid = ? AND deleted_at IS NULL",
            (str(uid).strip(),),
        ).fetchone()
    return _decode_item(row) if row is not None else None


def save_knowledge_references(
    conversation_id: int,
    references: Sequence[Mapping[str, Any]],
    *,
    keep: int = 32,
) -> int:
    """Mémorise une provenance bornée, sans dupliquer le contenu personnel."""

    identifier = int(conversation_id)
    if identifier <= 0:
        return 0
    now = _job_timestamp()
    normalized: list[tuple[str, str, str, int]] = []
    for rank, reference in enumerate(tuple(references)[:8]):
        uid = str(reference.get("uid") or "").strip()[:500]
        source_type = str(reference.get("source_type") or "").strip()[:120]
        source_id = str(reference.get("source_id") or "").strip()[:500]
        if uid and source_type and source_id:
            normalized.append((uid, source_type, source_id, rank))
    if not normalized:
        return 0

    with get_db() as conn:
        for uid, source_type, source_id, rank in normalized:
            conn.execute(
                """
                INSERT INTO knowledge_retrieval_references(
                    conversation_id, uid, source_type, source_id, rank, referenced_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(conversation_id, uid) DO UPDATE SET
                    source_type = excluded.source_type,
                    source_id = excluded.source_id,
                    rank = excluded.rank,
                    referenced_at = excluded.referenced_at
                """,
                (identifier, uid, source_type, source_id, rank, now),
            )
        retained = max(8, min(128, int(keep)))
        conn.execute(
            """
            DELETE FROM knowledge_retrieval_references
            WHERE conversation_id = ?
              AND id NOT IN (
                  SELECT id FROM knowledge_retrieval_references
                  WHERE conversation_id = ?
                  ORDER BY referenced_at DESC, rank ASC, id DESC
                  LIMIT ?
              )
            """,
            (identifier, identifier, retained),
        )
    return len(normalized)


def get_recent_knowledge_references(
    conversation_id: int,
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    identifier = int(conversation_id)
    if identifier <= 0:
        return []
    result_limit = max(1, min(32, int(limit)))
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT uid, source_type, source_id, rank, referenced_at
            FROM knowledge_retrieval_references
            WHERE conversation_id = ?
            ORDER BY referenced_at DESC, rank ASC, id DESC
            LIMIT ?
            """,
            (identifier, result_limit),
        ).fetchall()
    return [dict(row) for row in rows]


def delete_knowledge_item(source_type: str, source_id: str | int) -> int:
    with get_db() as conn:
        conn.execute(
            """
            DELETE FROM knowledge_retrieval_references
            WHERE source_type = ? AND source_id = ?
            """,
            (str(source_type), str(source_id)),
        )
        cursor = conn.execute(
            "DELETE FROM knowledge_items WHERE source_type = ? AND source_id = ?",
            (str(source_type), str(source_id)),
        )
        return int(cursor.rowcount)


def delete_knowledge_sources(source_types: Sequence[str] = ()) -> int:
    with get_db() as conn:
        if source_types:
            placeholders = ",".join("?" for _ in source_types)
            conn.execute(
                f"DELETE FROM knowledge_retrieval_references "
                f"WHERE source_type IN ({placeholders})",
                tuple(source_types),
            )
            cursor = conn.execute(
                f"DELETE FROM knowledge_items WHERE source_type IN ({placeholders})",
                tuple(source_types),
            )
            conn.execute(
                f"DELETE FROM knowledge_source_state WHERE source_type IN ({placeholders})",
                tuple(source_types),
            )
        else:
            conn.execute("DELETE FROM knowledge_retrieval_references")
            cursor = conn.execute("DELETE FROM knowledge_items")
            conn.execute("DELETE FROM knowledge_source_state")
        return int(cursor.rowcount)


def search_knowledge_items(
    query: str,
    *,
    source_types: Sequence[str] = (),
    conversation_id: int | None = None,
    person: str | None = None,
    from_iso: str | None = None,
    to_iso: str | None = None,
    limit: int = 20,
) -> tuple[list[dict[str, Any]], str]:
    """Recherche l'index avec FTS5, puis LIKE borne si FTS est absent."""

    result_limit = max(1, min(_MAX_SEARCH_LIMIT, int(limit)))
    cleaned_query = " ".join(str(query or "").strip().split())[:4_000]
    filters, params = _item_filters(
        source_types=source_types,
        conversation_id=conversation_id,
        person=person,
        from_iso=from_iso,
        to_iso=to_iso,
    )
    where_suffix = " AND " + " AND ".join(filters) if filters else ""

    if cleaned_query:
        fts_query = _fts_query(cleaned_query)
        if fts_query:
            try:
                with get_db() as conn:
                    rows = conn.execute(
                        f"""
                        SELECT k.*, bm25(knowledge_items_fts, 4.0, 1.0, 2.0) AS fts_rank
                        FROM knowledge_items_fts
                        JOIN knowledge_items k ON k.id = knowledge_items_fts.rowid
                        WHERE knowledge_items_fts MATCH ?
                          AND k.deleted_at IS NULL
                          {where_suffix}
                        ORDER BY fts_rank ASC,
                                 COALESCE(
                                     k.occurred_at, k.source_updated_at, k.indexed_at
                                 ) DESC
                        LIMIT ?
                        """,
                        (fts_query, *params, result_limit),
                    ).fetchall()
                return [_decode_item(row) for row in rows], "fts"
            except sqlite3.OperationalError:
                pass

    rows = _search_knowledge_like(
        cleaned_query,
        filters=filters,
        filter_params=params,
        limit=result_limit,
    )
    return rows, "like"


def upsert_knowledge_embedding(
    uid: str,
    *,
    model: str,
    content_hash: str,
    embedding: bytes,
) -> bool:
    with get_db() as conn:
        item = conn.execute(
            "SELECT id, content_hash FROM knowledge_items WHERE uid = ? AND deleted_at IS NULL",
            (uid,),
        ).fetchone()
        if item is None or str(item["content_hash"]) != str(content_hash):
            return False
        conn.execute(
            """
            INSERT INTO knowledge_embeddings(
                knowledge_item_id, model, content_hash, embedding, embedded_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(knowledge_item_id, model) DO UPDATE SET
                content_hash = excluded.content_hash,
                embedding = excluded.embedding,
                embedded_at = excluded.embedded_at
            """,
            (
                int(item["id"]),
                str(model),
                str(content_hash),
                embedding,
                sqlite_utc_timestamp(),
            ),
        )
    return True


def get_knowledge_embeddings(
    source_types: Sequence[str] = (),
    *,
    model: str | None = None,
    person: str | None = None,
    from_iso: str | None = None,
    to_iso: str | None = None,
    limit: int = 5_000,
) -> list[dict[str, Any]]:
    filters, params = _item_filters(
        source_types=source_types,
        conversation_id=None,
        person=person,
        from_iso=from_iso,
        to_iso=to_iso,
    )
    with get_db() as conn:
        where = "WHERE k.deleted_at IS NULL AND e.content_hash = k.content_hash"
        if filters:
            where += " AND " + " AND ".join(filters)
        if model:
            where += " AND e.model = ?"
            params.append(str(model))
        result_limit = max(1, min(20_000, int(limit)))
        rows = conn.execute(
            f"""
            SELECT e.embedding, e.model, e.content_hash AS embedding_content_hash,
                   e.embedded_at, k.*
            FROM knowledge_embeddings e
            JOIN knowledge_items k ON k.id = e.knowledge_item_id
            {where}
            ORDER BY e.embedded_at DESC
            LIMIT ?
            """,
            (*params, result_limit),
        ).fetchall()
    return [_decode_item(row) for row in rows]


def get_missing_knowledge_embeddings(
    *,
    model: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Sélectionne 80 % d'éléments chauds et 20 % de dette historique.

    Le quota chaud empêche un gros backfill de retarder de plusieurs jours les
    nouveaux mails/messages. Le quota froid garantit que la dette continue de
    décroître sans affamer les éléments récents.
    """

    result_limit = max(1, min(500, int(limit)))
    backfill_limit = max(1, result_limit // 5) if result_limit > 1 else 0
    hot_limit = result_limit - backfill_limit
    with get_db() as conn:
        hot_rows = conn.execute(
            """
            SELECT k.id, k.uid, k.content_hash, k.title,
                   k.searchable_text, k.summary
            FROM knowledge_items k
            LEFT JOIN knowledge_embeddings e
              ON e.knowledge_item_id = k.id AND e.model = ?
            WHERE k.deleted_at IS NULL
              AND (e.id IS NULL OR e.content_hash <> k.content_hash)
            ORDER BY k.indexed_at DESC, k.id DESC
            LIMIT ?
            """,
            (str(model), hot_limit),
        ).fetchall()
        cold_rows: Sequence[Any] = ()
        if backfill_limit:
            hot_ids = tuple(int(row["id"]) for row in hot_rows)
            exclusion = ""
            params: list[Any] = [str(model)]
            if hot_ids:
                placeholders = ",".join("?" for _ in hot_ids)
                exclusion = f" AND k.id NOT IN ({placeholders})"
                params.extend(hot_ids)
            params.append(backfill_limit)
            cold_rows = conn.execute(
                """
                SELECT k.id, k.uid, k.content_hash, k.title,
                       k.searchable_text, k.summary
                FROM knowledge_items k
                LEFT JOIN knowledge_embeddings e
                  ON e.knowledge_item_id = k.id AND e.model = ?
                WHERE k.deleted_at IS NULL
                  AND (e.id IS NULL OR e.content_hash <> k.content_hash)
                """
                + exclusion
                + " ORDER BY k.indexed_at ASC, k.id ASC LIMIT ?",
                tuple(params),
            ).fetchall()
    return [
        {
            key: row[key]
            for key in ("uid", "content_hash", "title", "searchable_text", "summary")
        }
        for row in (*hot_rows, *cold_rows)
    ]


def enqueue_knowledge_job(
    source_type: str,
    source_id: str | int,
    *,
    operation: str = "upsert",
) -> int:
    if operation not in {"upsert", "delete"}:
        raise ValueError("knowledge_job_operation_invalid")
    now = _job_timestamp()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO knowledge_index_jobs(
                source_type, source_id, operation, status, attempts,
                created_at, updated_at
            ) VALUES (?, ?, ?, 'pending', 0, ?, ?)
            ON CONFLICT(source_type, source_id) DO UPDATE SET
                operation = excluded.operation,
                status = 'pending',
                attempts = 0,
                next_attempt_at = NULL,
                last_error_code = NULL,
                claimed_at = NULL,
                completed_at = NULL,
                updated_at = excluded.updated_at
            """,
            (str(source_type), str(source_id), operation, now, now),
        )
        row = conn.execute(
            "SELECT id FROM knowledge_index_jobs WHERE source_type = ? AND source_id = ?",
            (str(source_type), str(source_id)),
        ).fetchone()
    return int(row["id"])


def claim_knowledge_jobs(
    limit: int = 100,
    *,
    lease_seconds: int = 300,
) -> list[dict[str, Any]]:
    now = _job_timestamp()
    lease_cutoff = (
        datetime.now(timezone.utc)
        - timedelta(seconds=max(30, min(86_400, int(lease_seconds))))
    ).strftime("%Y-%m-%d %H:%M:%S")
    result_limit = max(1, min(500, int(limit)))
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT * FROM knowledge_index_jobs
            WHERE (
                    status IN ('pending', 'retry')
                    AND (
                        next_attempt_at IS NULL
                        OR datetime(next_attempt_at) <= datetime(?)
                    )
                  )
               OR (
                    status = 'running'
                    AND (
                        claimed_at IS NULL
                        OR datetime(claimed_at) <= datetime(?)
                    )
                  )
            ORDER BY created_at, id
            LIMIT ?
            """,
            (now, lease_cutoff, result_limit),
        ).fetchall()
        ids = [int(row["id"]) for row in rows]
        if ids:
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"""
                UPDATE knowledge_index_jobs
                SET status = 'running', attempts = attempts + 1,
                    claimed_at = ?, updated_at = ?
                WHERE id IN ({placeholders})
                """,
                (now, now, *ids),
            )
            rows = conn.execute(
                f"SELECT * FROM knowledge_index_jobs WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
    return [dict(row) for row in rows]


def complete_knowledge_job(job_id: int, *, claim_token: str | None = None) -> None:
    now = _job_timestamp()
    with get_db() as conn:
        params: list[Any] = [now, now, int(job_id)]
        token_filter = ""
        if claim_token is not None:
            token_filter = " AND updated_at = ?"
            params.append(str(claim_token))
        conn.execute(
            """
            UPDATE knowledge_index_jobs
            SET status = 'done', completed_at = ?, updated_at = ?,
                last_error_code = NULL
            WHERE id = ? AND status = 'running'
            """
            + token_filter,
            params,
        )


def knowledge_job_claim_is_current(job_id: int, claim_token: str) -> bool:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM knowledge_index_jobs
            WHERE id = ? AND status = 'running' AND updated_at = ?
            """,
            (int(job_id), str(claim_token)),
        ).fetchone()
    return row is not None


def fail_knowledge_job(
    job_id: int,
    error_code: str,
    *,
    max_attempts: int = 5,
    claim_token: str | None = None,
) -> None:
    with get_db() as conn:
        params: list[Any] = [int(job_id)]
        token_filter = ""
        if claim_token is not None:
            token_filter = " AND updated_at = ?"
            params.append(str(claim_token))
        row = conn.execute(
            """
            SELECT attempts FROM knowledge_index_jobs
            WHERE id = ? AND status = 'running'
            """
            + token_filter,
            params,
        ).fetchone()
        if row is None:
            return
        attempts = int(row["attempts"] or 0)
        terminal = attempts >= max(1, int(max_attempts))
        delay = min(3_600, 2 ** min(attempts, 10))
        retry_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
        conn.execute(
            """
            UPDATE knowledge_index_jobs
            SET status = ?, next_attempt_at = ?, last_error_code = ?, updated_at = ?
            WHERE id = ? AND status = 'running'
            """
            + token_filter,
            (
                "dead" if terminal else "retry",
                None if terminal else retry_at,
                str(error_code)[:160],
                _job_timestamp(),
                int(job_id),
                *((str(claim_token),) if claim_token is not None else ()),
            ),
        )


def update_knowledge_source_state(
    source_key: str,
    source_type: str,
    *,
    status: str,
    cursor: str | int | None = None,
    item_count: int | None = None,
    last_indexed_at: str | None = None,
    last_backfill_at: str | None = None,
    error_code: str | None = None,
) -> None:
    if status not in {"ok", "degraded", "unavailable"}:
        raise ValueError("knowledge_source_status_invalid")
    now = sqlite_utc_timestamp()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO knowledge_source_state(
                source_key, source_type, status, cursor, item_count,
                last_indexed_at, last_backfill_at, last_error_code, updated_at
            ) VALUES (?, ?, ?, ?, COALESCE(?, 0), ?, ?, ?, ?)
            ON CONFLICT(source_key) DO UPDATE SET
                source_type = excluded.source_type,
                status = excluded.status,
                cursor = COALESCE(excluded.cursor, knowledge_source_state.cursor),
                item_count = CASE
                    WHEN ? IS NULL THEN knowledge_source_state.item_count
                    ELSE excluded.item_count
                END,
                last_indexed_at = COALESCE(
                    excluded.last_indexed_at, knowledge_source_state.last_indexed_at
                ),
                last_backfill_at = COALESCE(
                    excluded.last_backfill_at, knowledge_source_state.last_backfill_at
                ),
                last_error_code = excluded.last_error_code,
                updated_at = excluded.updated_at
            """,
            (
                str(source_key),
                str(source_type),
                status,
                None if cursor is None else str(cursor),
                None if item_count is None else max(0, int(item_count)),
                last_indexed_at,
                last_backfill_at,
                error_code,
                now,
                item_count,
            ),
        )


def get_knowledge_source_states(
    source_types: Sequence[str] = (),
) -> list[dict[str, Any]]:
    with get_db() as conn:
        if source_types:
            placeholders = ",".join("?" for _ in source_types)
            rows = conn.execute(
                f"SELECT * FROM knowledge_source_state WHERE source_type IN ({placeholders})",
                tuple(source_types),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM knowledge_source_state").fetchall()
    return [dict(row) for row in rows]


def latest_knowledge_indexed_at(source_types: Sequence[str] = ()) -> str | None:
    with get_db() as conn:
        params: tuple[Any, ...] = ()
        source_filter = ""
        if source_types:
            placeholders = ",".join("?" for _ in source_types)
            source_filter = f" AND source_type IN ({placeholders})"
            params = tuple(source_types)
        row = conn.execute(
            """
            SELECT MAX(indexed_at) AS indexed_at
            FROM knowledge_items
            WHERE deleted_at IS NULL
            """
            + source_filter,
            params,
        ).fetchone()
    return str(row["indexed_at"]) if row is not None and row["indexed_at"] else None


def get_knowledge_observability() -> dict[str, Any]:
    """Retourne uniquement des métriques d'index, jamais de contenu personnel."""

    with get_db() as conn:
        job_rows = conn.execute(
            "SELECT status, created_at, last_error_code FROM knowledge_index_jobs"
        ).fetchall()
        coverage_rows = conn.execute(
            """
            SELECT source_type, COUNT(*) AS item_count
            FROM knowledge_items
            WHERE deleted_at IS NULL
            GROUP BY source_type
            ORDER BY source_type
            """
        ).fetchall()
        state_rows = conn.execute(
            """
            SELECT source_key, source_type, status, item_count,
                   last_indexed_at, last_backfill_at, last_error_code
            FROM knowledge_source_state
            ORDER BY source_key
            """
        ).fetchall()
        embedding_count = int(
            conn.execute("SELECT COUNT(*) FROM knowledge_embeddings").fetchone()[0]
        )

    now = datetime.now(timezone.utc)
    jobs_by_status: dict[str, int] = {}
    errors_by_code: dict[str, int] = {}
    pending_lags: list[float] = []
    for row in job_rows:
        status = str(row["status"] or "unknown")
        jobs_by_status[status] = jobs_by_status.get(status, 0) + 1
        error_code = _safe_metric_code(row["last_error_code"])
        if error_code:
            errors_by_code[error_code] = errors_by_code.get(error_code, 0) + 1
        if status not in {"pending", "retry", "running"}:
            continue
        created_at = _parse_utc_datetime(row["created_at"])
        if created_at is not None:
            pending_lags.append(max(0.0, (now - created_at).total_seconds()))

    pending_lags.sort()
    p95_index = max(0, ((95 * len(pending_lags) + 99) // 100) - 1)
    index_freshness_at = latest_knowledge_indexed_at()
    index_datetime = _parse_utc_datetime(index_freshness_at)
    return {
        "generated_at": sqlite_utc_timestamp(),
        "jobs_by_status": jobs_by_status,
        "pending_lag_seconds": {
            "max": round(pending_lags[-1], 3) if pending_lags else 0.0,
            "p95": round(pending_lags[p95_index], 3) if pending_lags else 0.0,
        },
        "coverage_by_source": {
            str(row["source_type"]): int(row["item_count"]) for row in coverage_rows
        },
        "embedding_count": embedding_count,
        "index_freshness_at": index_freshness_at,
        "index_lag_seconds": (
            round(max(0.0, (now - index_datetime).total_seconds()), 3)
            if index_datetime is not None
            else None
        ),
        "last_backfill_at": max(
            (
                str(row["last_backfill_at"])
                for row in state_rows
                if row["last_backfill_at"]
            ),
            default=None,
        ),
        "errors_by_code": errors_by_code,
        "source_states": [
            {
                "source_key": str(row["source_key"]),
                "source_type": str(row["source_type"]),
                "status": str(row["status"]),
                "item_count": int(row["item_count"] or 0),
                "last_indexed_at": row["last_indexed_at"],
                "last_backfill_at": row["last_backfill_at"],
                "last_error_code": _safe_metric_code(row["last_error_code"]),
            }
            for row in state_rows
        ],
    }


def rebuild_knowledge_fts() -> bool:
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO knowledge_items_fts(knowledge_items_fts) VALUES ('rebuild')"
            )
    except sqlite3.OperationalError:
        return False
    return True


def upsert_calendar_events(
    events: Iterable[Mapping[str, Any]],
    *,
    window_start: str | None = None,
    window_end: str | None = None,
) -> int:
    """Persiste puis réconcilie une fenêtre Calendar collectée intégralement."""

    rows = []
    now = sqlite_utc_timestamp()
    for event in events:
        title = str(event.get("title") or event.get("summary") or "").strip()
        raw_start_at = str(
            event.get("start_at") or event.get("start") or event.get("start_date") or ""
        ).strip()
        if not title or not raw_start_at:
            continue
        try:
            start_at = sqlite_utc_timestamp(raw_start_at)
        except (TypeError, ValueError):
            start_at = raw_start_at
        raw_end_at = str(
            event.get("end_at") or event.get("end") or event.get("end_date") or ""
        ).strip()
        try:
            end_at = sqlite_utc_timestamp(raw_end_at) if raw_end_at else None
        except (TypeError, ValueError):
            end_at = raw_end_at or None
        external_id = str(
            event.get("external_id") or event.get("id") or event.get("uid") or ""
        ).strip()
        if not external_id:
            external_id = hashlib.sha256(
                f"{event.get('calendar_name') or event.get('calendar') or ''}|{title}|{start_at}".encode(
                    "utf-8"
                )
            ).hexdigest()
        rows.append(
            (
                external_id[:500],
                str(event.get("calendar_name") or event.get("calendar") or "")[:240],
                title[:1_000],
                start_at[:64],
                end_at[:64] if end_at else None,
                str(event.get("location") or "")[:2_000],
                str(event.get("notes") or event.get("description") or "")[:20_000],
                1 if bool(event.get("is_all_day") or event.get("all_day")) else 0,
                str(event.get("updated_at") or now)[:64],
            )
        )
    with get_db() as conn:
        if rows:
            conn.executemany(
                """
                INSERT INTO calendar_events(
                    external_id, calendar_name, title, start_at, end_at,
                    location, notes, is_all_day, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(external_id) DO UPDATE SET
                    calendar_name = excluded.calendar_name,
                    title = excluded.title,
                    start_at = excluded.start_at,
                    end_at = excluded.end_at,
                    location = excluded.location,
                    notes = excluded.notes,
                    is_all_day = excluded.is_all_day,
                    updated_at = excluded.updated_at
                """,
                rows,
            )
        if window_start and window_end:
            present_ids = {str(row[0]) for row in rows}
            cached_ids = {
                str(row["external_id"])
                for row in conn.execute(
                    """
                    SELECT external_id FROM calendar_events
                    WHERE datetime(start_at) < datetime(?)
                      AND datetime(COALESCE(end_at, start_at)) >= datetime(?)
                    """,
                    (str(window_end), str(window_start)),
                ).fetchall()
            }
            stale_ids = sorted(cached_ids - present_ids)
            if stale_ids:
                conn.executemany(
                    "DELETE FROM calendar_events WHERE external_id = ?",
                    [(external_id,) for external_id in stale_ids],
                )
    return len(rows)


def get_cached_calendar_events(
    *,
    from_iso: str | None = None,
    to_iso: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if from_iso:
        clauses.append("COALESCE(end_at, start_at) >= ?")
        try:
            params.append(sqlite_utc_timestamp(str(from_iso)))
        except (TypeError, ValueError):
            params.append(str(from_iso))
    if to_iso:
        clauses.append("start_at <= ?")
        try:
            params.append(sqlite_utc_timestamp(str(to_iso)))
        except (TypeError, ValueError):
            params.append(str(to_iso))
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    result_limit = max(1, min(500, int(limit)))
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT * FROM calendar_events{where} ORDER BY start_at LIMIT ?",
            (*params, result_limit),
        ).fetchall()
    return [dict(row) for row in rows]


def _search_knowledge_like(
    query: str,
    *,
    filters: Sequence[str],
    filter_params: Sequence[Any],
    limit: int,
) -> list[dict[str, Any]]:
    clauses = list(filters)
    params: list[Any] = list(filter_params)
    tokens = _query_tokens(query)
    if tokens:
        token_clauses = []
        for token in tokens[:12]:
            pattern = _like_pattern(token)
            token_clauses.append(
                "(k.title LIKE ? ESCAPE '\\' COLLATE NOCASE "
                "OR k.searchable_text LIKE ? ESCAPE '\\' COLLATE NOCASE "
                "OR k.summary LIKE ? ESCAPE '\\' COLLATE NOCASE)"
            )
            params.extend((pattern, pattern, pattern))
        clauses.append("(" + " OR ".join(token_clauses) + ")")
    where = " AND ".join(["k.deleted_at IS NULL", *clauses])
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT k.*, NULL AS fts_rank
            FROM knowledge_items k
            WHERE {where}
            ORDER BY COALESCE(
                k.occurred_at, k.source_updated_at, k.indexed_at
            ) DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
    return [_decode_item(row) for row in rows]


def _item_filters(
    *,
    source_types: Sequence[str],
    conversation_id: int | None,
    person: str | None,
    from_iso: str | None,
    to_iso: str | None,
) -> tuple[list[str], list[Any]]:
    filters: list[str] = []
    params: list[Any] = []
    if source_types:
        placeholders = ",".join("?" for _ in source_types)
        filters.append(f"k.source_type IN ({placeholders})")
        params.extend(source_types)
    if conversation_id is not None:
        filters.append("k.conversation_id = ?")
        params.append(int(conversation_id))
    if person:
        pattern = _like_pattern(str(person))
        filters.append(
            "(k.people_json LIKE ? ESCAPE '\\' COLLATE NOCASE "
            "OR k.title LIKE ? ESCAPE '\\' COLLATE NOCASE "
            "OR k.searchable_text LIKE ? ESCAPE '\\' COLLATE NOCASE)"
        )
        params.extend((pattern, pattern, pattern))
    if from_iso:
        filters.append(
            "datetime(COALESCE(k.occurred_at, k.source_updated_at, k.indexed_at)) "
            ">= datetime(?)"
        )
        params.append(str(from_iso))
    if to_iso:
        filters.append(
            "datetime(COALESCE(k.occurred_at, k.source_updated_at, k.indexed_at)) "
            "<= datetime(?)"
        )
        params.append(str(to_iso))
    return filters, params


def _fts_query(query: str) -> str:
    tokens = _query_tokens(query)[:12]
    if not tokens:
        return ""
    quoted = [f'"{token.replace(chr(34), "")}"' for token in tokens]
    quoted[-1] += "*"
    return " OR ".join(quoted)


def _query_tokens(query: str) -> list[str]:
    return list(
        dict.fromkeys(
            token.casefold() for token in _FTS_TOKEN_RE.findall(query) if token
        )
    )


def _like_pattern(value: str) -> str:
    escaped = str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _decode_item(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    value = dict(row)
    value["people"] = _json_loads(value.pop("people_json", "[]"), [])
    value["metadata"] = _json_loads(value.pop("metadata_json", "{}"), {})
    return value


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_loads(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _parse_utc_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _job_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _safe_metric_code(value: Any) -> str | None:
    if not value:
        return None
    code = str(value)[:160]
    if re.fullmatch(r"[A-Za-z0-9_.:-]+", code):
        return code
    return "redacted_error_code"


def iter_pending_knowledge_jobs() -> Iterable[dict[str, Any]]:
    """Expose un snapshot read-only utile aux diagnostics et tests."""

    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM knowledge_index_jobs
            WHERE status IN ('pending', 'retry', 'running', 'dead')
            ORDER BY created_at, id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def prune_knowledge_jobs(
    *,
    done_retention_days: int = 7,
    dead_retention_days: int = 30,
    limit: int = 1_000,
) -> int:
    """Purge bornée des états terminaux sans jamais lire leur source_id."""

    now = datetime.now(timezone.utc)
    done_cutoff = (now - timedelta(days=max(1, int(done_retention_days)))).isoformat()
    dead_cutoff = (now - timedelta(days=max(1, int(dead_retention_days)))).isoformat()
    result_limit = max(1, min(10_000, int(limit)))
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id FROM knowledge_index_jobs
            WHERE (status = 'done' AND datetime(updated_at) < datetime(?))
               OR (status = 'dead' AND datetime(updated_at) < datetime(?))
            ORDER BY updated_at, id
            LIMIT ?
            """,
            (done_cutoff, dead_cutoff, result_limit),
        ).fetchall()
        ids = [(int(row["id"]),) for row in rows]
        if ids:
            conn.executemany("DELETE FROM knowledge_index_jobs WHERE id = ?", ids)
    return len(ids)
