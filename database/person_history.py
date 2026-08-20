"""Chapitres mensuels par personne — persistance, pas de génération LLM."""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping, Sequence

from jarvis.event_bus import JarvisEvent, event_bus

from .core import get_db
from .time_buckets import utc_bounds_for_local_month

logger = logging.getLogger(__name__)

HIGHLIGHT_KINDS = frozenset(
    {"turning_point", "conflict", "plan", "absence", "affection", "logistics"}
)
CHAPTER_STATUSES = frozenset({"empty", "partial", "complete"})
_YEAR_MONTH_LEN = 7


def _year_month(value: str) -> str:
    text = str(value or "").strip()
    if len(text) != _YEAR_MONTH_LEN or text[4] != "-":
        raise ValueError("year_month_invalid")
    year = int(text[:4])
    month = int(text[5:7])
    if month < 1 or month > 12:
        raise ValueError("year_month_invalid")
    utc_bounds_for_local_month(year, month)
    return f"{year:04d}-{month:02d}"


def _highlights_json(value: Sequence[Mapping[str, Any]] | None) -> str:
    cleaned: list[dict[str, Any]] = []
    for item in value or ():
        kind = str(item.get("kind") or "").strip()
        if kind not in HIGHLIGHT_KINDS:
            continue
        quote = str(item.get("quote") or "").strip()[:200]
        if not quote:
            continue
        try:
            apple_rowid = int(item.get("apple_rowid") or 0)
        except (TypeError, ValueError):
            continue
        cleaned.append(
            {
                "apple_rowid": apple_rowid,
                "occurred_at_utc": str(item.get("occurred_at_utc") or "")[:64],
                "quote": quote,
                "kind": kind,
            }
        )
        if len(cleaned) >= 12:
            break
    return json.dumps(cleaned, ensure_ascii=False, separators=(",", ":"))


def _row_to_chapter(row: Mapping[str, Any]) -> dict[str, Any]:
    try:
        highlights = json.loads(str(row["highlights_json"] or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        highlights = []
    if not isinstance(highlights, list):
        highlights = []
    return {
        "id": int(row["id"]),
        "person_id": int(row["person_id"]),
        "year_month": str(row["year_month"]),
        "period_start_utc": str(row["period_start_utc"]),
        "period_end_utc": str(row["period_end_utc"]),
        "status": str(row["status"]),
        "message_count": int(row["message_count"] or 0),
        "sent_count": int(row["sent_count"] or 0),
        "recv_count": int(row["recv_count"] or 0),
        "highlights": highlights,
        "narrative": str(row["narrative"] or ""),
        "mood_arc": str(row["mood_arc"] or ""),
        "source_rowid_min": row["source_rowid_min"],
        "source_rowid_max": row["source_rowid_max"],
        "content_hash": str(row["content_hash"] or ""),
        "model": row["model"],
        "tokens_in": int(row["tokens_in"] or 0),
        "tokens_out": int(row["tokens_out"] or 0),
        "cost": float(row["cost"] or 0),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_chapters(
    person_id: int,
    *,
    from_month: str | None = None,
    to_month: str | None = None,
) -> list[dict[str, Any]]:
    clauses = ["person_id = ?"]
    params: list[Any] = [int(person_id)]
    if from_month:
        clauses.append("year_month >= ?")
        params.append(_year_month(from_month))
    if to_month:
        clauses.append("year_month <= ?")
        params.append(_year_month(to_month))
    sql = (
        "SELECT * FROM person_month_chapters WHERE "
        + " AND ".join(clauses)
        + " ORDER BY year_month ASC"
    )
    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_chapter(row) for row in rows]


def get_chapter(person_id: int, year_month: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT * FROM person_month_chapters
            WHERE person_id = ? AND year_month = ?
            """,
            (int(person_id), _year_month(year_month)),
        ).fetchone()
    return _row_to_chapter(row) if row else None


def upsert_chapter(
    *,
    person_id: int,
    year_month: str,
    status: str,
    message_count: int,
    sent_count: int,
    recv_count: int,
    highlights: Sequence[Mapping[str, Any]] | None,
    narrative: str,
    mood_arc: str = "",
    source_rowid_min: int | None,
    source_rowid_max: int | None,
    content_hash: str,
    period_start_utc: str,
    period_end_utc: str,
    model: str | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost: float = 0.0,
) -> dict[str, Any]:
    if status not in CHAPTER_STATUSES:
        raise ValueError("chapter_status_invalid")
    month = _year_month(year_month)
    payload = (
        int(person_id),
        month,
        str(period_start_utc),
        str(period_end_utc),
        status,
        max(0, int(message_count)),
        max(0, int(sent_count)),
        max(0, int(recv_count)),
        _highlights_json(highlights),
        str(narrative or "")[:2000],
        str(mood_arc or "")[:240],
        source_rowid_min,
        source_rowid_max,
        str(content_hash or ""),
        model,
        max(0, int(tokens_in)),
        max(0, int(tokens_out)),
        float(cost or 0),
    )
    with get_db() as conn:
        existing = conn.execute(
            """
            SELECT * FROM person_month_chapters
            WHERE person_id = ? AND year_month = ?
            """,
            (int(person_id), month),
        ).fetchone()
        if existing and str(existing["content_hash"] or "") == str(content_hash or ""):
            chapter = _row_to_chapter(existing)
            chapter["skipped"] = True
            return chapter
        conn.execute(
            """
            INSERT INTO person_month_chapters (
                person_id, year_month, period_start_utc, period_end_utc, status,
                message_count, sent_count, recv_count, highlights_json, narrative,
                mood_arc, source_rowid_min, source_rowid_max, content_hash, model,
                tokens_in, tokens_out, cost, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(person_id, year_month) DO UPDATE SET
                period_start_utc = excluded.period_start_utc,
                period_end_utc = excluded.period_end_utc,
                status = excluded.status,
                message_count = excluded.message_count,
                sent_count = excluded.sent_count,
                recv_count = excluded.recv_count,
                highlights_json = excluded.highlights_json,
                narrative = excluded.narrative,
                mood_arc = excluded.mood_arc,
                source_rowid_min = excluded.source_rowid_min,
                source_rowid_max = excluded.source_rowid_max,
                content_hash = excluded.content_hash,
                model = excluded.model,
                tokens_in = excluded.tokens_in,
                tokens_out = excluded.tokens_out,
                cost = excluded.cost,
                updated_at = CURRENT_TIMESTAMP
            """,
            payload,
        )
    chapter = get_chapter(int(person_id), month)
    assert chapter is not None
    chapter["skipped"] = False
    event_bus.emit_nowait(
        JarvisEvent(
            type="person.chapter_updated",
            source="database.person_history",
            data={
                "person_id": int(person_id),
                "year_month": month,
                "status": status,
                "message_count": max(0, int(message_count)),
            },
        )
    )
    return chapter


def digest_for_identity(person_id: int) -> str:
    chapters = list_chapters(int(person_id))[-3:]
    return _format_digest(chapters, limit=2_400)


def digest_for_history(person_id: int) -> str:
    return _format_digest(
        list_chapters(int(person_id)),
        limit=8_000,
        newest_first=True,
        include_highlights=True,
    )


def _format_digest(
    chapters: Sequence[Mapping[str, Any]],
    *,
    limit: int,
    newest_first: bool = False,
    include_highlights: bool = False,
) -> str:
    source = list(reversed(chapters)) if newest_first else list(chapters)
    parts: list[str] = []
    used = 0
    for chapter in source:
        month = str(chapter.get("year_month") or "")
        status = str(chapter.get("status") or "")
        narrative = str(chapter.get("narrative") or "").strip()
        extra = ""
        if include_highlights:
            bits: list[str] = []
            for item in chapter.get("highlights") or ():
                if not isinstance(item, Mapping):
                    continue
                quote = str(item.get("quote") or "").strip()
                if not quote:
                    continue
                kind = str(item.get("kind") or "").strip()
                bits.append(f"{kind}: {quote}" if kind else quote)
            if bits:
                extra = " " + " ; ".join(bits)
        block = f"[{month} | {status}] {narrative}{extra}".strip()
        if used + len(block) + 1 > limit:
            break
        parts.append(block)
        used += len(block) + 1
    if newest_first:
        parts.reverse()
    return "\n".join(parts)
