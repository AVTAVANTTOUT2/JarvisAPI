"""Job d'ingestion : un chapitre mensuel par personne, depuis imessage_messages."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date, timedelta
from typing import Any, Mapping, Sequence

import config
import llm
from database import get_db, get_person
from database.ingestion import (
    ConnectorBindingRequired,
    enqueue_ingestion_job,
)
from database.person_history import (
    HIGHLIGHT_KINDS,
    get_chapter,
    list_chapters,
    upsert_chapter,
)
from database.time_buckets import (
    local_datetime,
    sqlite_utc_datetime,
    utc_bounds_for_local_day,
    utc_bounds_for_local_month,
)
from jarvis.ingestion.models import (
    ConnectorBinding,
    IngestionJob,
    IngestionRunResult,
    IngestionSourceState,
)

logger = logging.getLogger(__name__)

_MONTHS_FR = (
    "",
    "Janvier",
    "Février",
    "Mars",
    "Avril",
    "Mai",
    "Juin",
    "Juillet",
    "Août",
    "Septembre",
    "Octobre",
    "Novembre",
    "Décembre",
)
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _max_chapters_per_run() -> int:
    return max(1, int(getattr(config, "PERSON_HISTORY_MAX_CHAPTERS_PER_RUN", 8)))


def _max_messages_per_chapter() -> int:
    return max(20, int(getattr(config, "PERSON_HISTORY_MAX_MESSAGES_PER_CHAPTER", 400)))


def _daily_token_budget() -> int:
    return max(0, int(getattr(config, "PERSON_HISTORY_DAILY_TOKEN_BUDGET", 80_000)))


def _parse_year_month(value: str) -> tuple[int, int]:
    year_s, month_s = str(value).split("-", 1)
    return int(year_s), int(month_s)


def _content_hash(messages: Sequence[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in messages:
        digest.update(f"{row.get('apple_rowid')}\n{row.get('text') or ''}\n".encode())
    return digest.hexdigest()


def _list_person_messages(person_id: int, year_month: str) -> list[dict[str, Any]]:
    year, month = _parse_year_month(year_month)
    start_utc, end_utc = utc_bounds_for_local_month(year, month)
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT m.apple_rowid AS apple_rowid,
                   COALESCE(m.text, '') AS text,
                   COALESCE(m.is_from_me, 0) AS is_from_me,
                   COALESCE(m.occurred_at_utc, m.created_at) AS occurred_at_utc,
                   (
                       SELECT COUNT(*) FROM imessage_message_attachments a
                       WHERE a.message_id = m.id
                   ) AS attachment_count
            FROM imessage_messages m
            WHERE m.handle_id IN (
                SELECT h.id FROM imessage_handles h
                WHERE lower(h.handle) IN (
                    SELECT lower(rp.handle)
                    FROM relationship_profiles rp
                    WHERE rp.person_id = ?
                      AND rp.handle IS NOT NULL
                      AND TRIM(rp.handle) != ''
                    UNION
                    SELECT lower(ci.normalized_value)
                    FROM contact_identities ci
                    WHERE ci.person_id = ?
                      AND ci.identity_type IN ('imessage', 'phone', 'handle')
                      AND TRIM(ci.normalized_value) != ''
                )
            )
              AND COALESCE(m.occurred_at_utc, m.created_at) >= ?
              AND COALESCE(m.occurred_at_utc, m.created_at) < ?
            ORDER BY m.apple_rowid ASC
            """,
            (int(person_id), int(person_id), start_utc, end_utc),
        ).fetchall()
    return [dict(row) for row in rows]


def _sample_messages(messages: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    limit = _max_messages_per_chapter()
    if len(messages) <= limit:
        return list(messages)
    # ponytail: longueur + pièces jointes, pas les silences > 48 h.
    ranked = sorted(
        messages,
        key=lambda row: (
            int(row.get("attachment_count") or 0),
            len(str(row.get("text") or "")),
            int(row.get("apple_rowid") or 0),
        ),
        reverse=True,
    )
    chosen = {int(row["apple_rowid"]): row for row in ranked[: limit - 2]}
    chosen[int(messages[0]["apple_rowid"])] = messages[0]
    chosen[int(messages[-1]["apple_rowid"])] = messages[-1]
    return sorted(chosen.values(), key=lambda row: int(row["apple_rowid"] or 0))


def _parse_chapter_json(raw: str) -> dict[str, Any] | None:
    text = _JSON_FENCE_RE.sub("", str(raw or "").strip())
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def _fallback_narrative(
    year_month: str, message_count: int, sent_count: int, recv_count: int
) -> str:
    year, month = _parse_year_month(year_month)
    label = f"{_MONTHS_FR[month]} {year}"
    if message_count == 0:
        return f"{label} : aucun message échangé."
    return (
        f"{label} : {message_count} messages ({sent_count} envoyés, "
        f"{recv_count} reçus). Récit indisponible."
    )


def _tokens_used_today() -> int:
    start, end = utc_bounds_for_local_day(local_datetime().date())
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(tokens_in + tokens_out), 0)
            FROM person_month_chapters
            WHERE updated_at >= ? AND updated_at < ?
            """,
            (start, end),
        ).fetchone()
    return int(row[0] if row else 0)


def missing_year_months(person_id: int) -> list[str]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT COALESCE(m.occurred_at_utc, m.created_at) AS occurred_at
            FROM imessage_messages m
            WHERE m.handle_id IN (
                SELECT h.id FROM imessage_handles h
                WHERE lower(h.handle) IN (
                    SELECT lower(rp.handle)
                    FROM relationship_profiles rp
                    WHERE rp.person_id = ?
                      AND rp.handle IS NOT NULL AND TRIM(rp.handle) != ''
                    UNION
                    SELECT lower(ci.normalized_value)
                    FROM contact_identities ci
                    WHERE ci.person_id = ?
                      AND ci.identity_type IN ('imessage', 'phone', 'handle')
                      AND TRIM(ci.normalized_value) != ''
                )
            )
            """,
            (int(person_id), int(person_id)),
        ).fetchall()
    months: set[str] = set()
    zone = local_datetime().tzinfo
    for row in rows:
        raw = str(row["occurred_at"] or "").strip()
        if not raw:
            continue
        try:
            parsed = sqlite_utc_datetime(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        local = parsed.astimezone(zone)
        months.add(f"{local.year:04d}-{local.month:02d}")
    existing = {chapter["year_month"] for chapter in list_chapters(int(person_id))}
    return sorted(month for month in months if month not in existing)


async def build_chapter(person_id: int, year_month: str) -> dict[str, Any]:
    year, month = _parse_year_month(year_month)
    start_utc, end_utc = utc_bounds_for_local_month(year, month)
    messages = _list_person_messages(int(person_id), year_month)
    content_hash = _content_hash(messages)
    existing = get_chapter(int(person_id), year_month)
    if existing and existing.get("content_hash") == content_hash:
        existing["skipped"] = True
        return existing

    sent_count = sum(1 for row in messages if int(row.get("is_from_me") or 0))
    recv_count = len(messages) - sent_count
    rowids = [int(row["apple_rowid"]) for row in messages if row.get("apple_rowid")]
    if not messages:
        return upsert_chapter(
            person_id=int(person_id),
            year_month=year_month,
            status="empty",
            message_count=0,
            sent_count=0,
            recv_count=0,
            highlights=[],
            narrative=_fallback_narrative(year_month, 0, 0, 0),
            content_hash=content_hash,
            source_rowid_min=None,
            source_rowid_max=None,
            period_start_utc=start_utc,
            period_end_utc=end_utc,
        )

    sampled = _sample_messages(messages)
    parsed: dict[str, Any] | None = None
    tokens_in = tokens_out = 0
    cost = 0.0
    model_name = str(getattr(config, "DEEPSEEK_FAST_MODEL", "") or "")
    budget = _daily_token_budget()
    if budget == 0 or _tokens_used_today() < budget:
        payload_lines = []
        for row in sampled:
            direction = "me" if int(row.get("is_from_me") or 0) else "them"
            text = str(row.get("text") or "").replace("\n", " ")[:400]
            payload_lines.append(
                f"{row.get('apple_rowid')}\t{row.get('occurred_at_utc')}\t{direction}\t{text}"
            )
        try:
            response = await llm.chat(
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Résume ce mois d'échanges. JSON strict : "
                            '{"highlights":[{"apple_rowid":int,"occurred_at_utc":str,'
                            '"quote":str,"kind":"turning_point|conflict|plan|absence|'
                            'affection|logistics"}],"narrative":str,"mood_arc":str}. '
                            "Citations <= 200 caractères, kind dans le vocabulaire. "
                            "Pas d'invention.\n"
                            + "\n".join(payload_lines)
                        ),
                    }
                ],
                model=getattr(config, "DEEPSEEK_FAST_MODEL", None),
                max_tokens=700,
                temperature=0.0,
            )
            tokens_in = int(response.get("tokens_in") or 0)
            tokens_out = int(response.get("tokens_out") or 0)
            cost = float(response.get("cost") or 0)
            model_name = str(response.get("model") or model_name)
            parsed = _parse_chapter_json(str(response.get("content") or ""))
        except Exception:
            logger.exception("[person_history] génération chapitre impossible")
            parsed = None

    highlights: list[dict[str, Any]] = []
    narrative = ""
    mood_arc = ""
    status = "partial"
    if parsed:
        raw_highlights = parsed.get("highlights")
        if isinstance(raw_highlights, list):
            known = {int(row["apple_rowid"]) for row in messages if row.get("apple_rowid")}
            for item in raw_highlights:
                if not isinstance(item, Mapping):
                    continue
                try:
                    apple_rowid = int(item.get("apple_rowid") or 0)
                except (TypeError, ValueError):
                    continue
                if apple_rowid not in known:
                    continue
                kind = str(item.get("kind") or "")
                if kind not in HIGHLIGHT_KINDS:
                    continue
                highlights.append(
                    {
                        "apple_rowid": apple_rowid,
                        "occurred_at_utc": str(item.get("occurred_at_utc") or "")[:64],
                        "quote": str(item.get("quote") or "")[:200],
                        "kind": kind,
                    }
                )
        narrative = str(parsed.get("narrative") or "").strip()[:2000]
        mood_arc = str(parsed.get("mood_arc") or "").strip()[:240]
        if narrative:
            status = "complete"
    if not narrative:
        narrative = _fallback_narrative(
            year_month, len(messages), sent_count, recv_count
        )
        status = "partial"

    return upsert_chapter(
        person_id=int(person_id),
        year_month=year_month,
        status=status,
        message_count=len(messages),
        sent_count=sent_count,
        recv_count=recv_count,
        highlights=highlights,
        narrative=narrative,
        mood_arc=mood_arc,
        source_rowid_min=min(rowids) if rowids else None,
        source_rowid_max=max(rowids) if rowids else None,
        content_hash=content_hash,
        period_start_utc=start_utc,
        period_end_utc=end_utc,
        model=model_name or None,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost=cost,
    )


def _priority_person_ids(limit: int) -> list[int]:
    since = (local_datetime() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        mentioned = [
            int(row[0])
            for row in conn.execute(
                """
                SELECT DISTINCT p.id
                FROM people p
                JOIN messages m ON instr(lower(m.content), lower(p.name)) > 0
                WHERE m.role = 'user' AND m.created_at >= ?
                  AND length(p.name) >= 3
                LIMIT ?
                """,
                (since, limit),
            ).fetchall()
        ]
        remaining = max(0, limit - len(mentioned))
        top = [
            int(row[0])
            for row in conn.execute(
                """
                SELECT id FROM people
                WHERE COALESCE(imessage_count, 0) > 0
                ORDER BY imessage_count DESC, id ASC
                LIMIT ?
                """,
                (remaining + len(mentioned),),
            ).fetchall()
            if remaining
        ]
    seen: set[int] = set()
    ordered: list[int] = []
    for person_id in (*mentioned, *top):
        if person_id in seen:
            continue
        seen.add(person_id)
        ordered.append(person_id)
        if len(ordered) >= limit:
            break
    return ordered


def _select_targets(payload: Mapping[str, Any]) -> list[tuple[int, str]]:
    person_id = payload.get("person_id")
    months = payload.get("year_months") or ()
    if payload.get("year_month"):
        months = (payload.get("year_month"), *tuple(months))
    if person_id is not None:
        person_id = int(person_id)
        if months:
            return [(person_id, str(month)) for month in dict.fromkeys(months)]
        missing = missing_year_months(person_id)
        today = local_datetime().date()
        current = f"{today.year:04d}-{today.month:02d}"
        if current not in missing:
            missing.append(current)
        return [(person_id, month) for month in missing[: _max_chapters_per_run()]]

    targets: list[tuple[int, str]] = []
    today = local_datetime().date()
    previous = (date(today.year, today.month, 1) - timedelta(days=1))
    closed = f"{previous.year:04d}-{previous.month:02d}"
    current = f"{today.year:04d}-{today.month:02d}"
    for person_id in _priority_person_ids(15):
        existing = {
            chapter["year_month"]: chapter for chapter in list_chapters(person_id)
        }
        if closed not in existing:
            targets.append((person_id, closed))
        elif current not in existing:
            targets.append((person_id, current))
        if len(targets) >= _max_chapters_per_run():
            break
    return targets


async def handle_person_history(
    job: IngestionJob,
    binding: ConnectorBinding | None,
    state: IngestionSourceState | None,
) -> IngestionRunResult:
    del binding, state
    built = 0
    for person_id, year_month in _select_targets(job.payload):
        await build_chapter(person_id, year_month)
        built += 1
        if built >= _max_chapters_per_run():
            break
    return IngestionRunResult(
        status="ok",
        item_count=built,
        completeness="complete",
    )


def enqueue_person_history(
    *,
    person_id: int | None = None,
    year_months: Sequence[str] | None = None,
) -> Any | None:
    payload: dict[str, Any] = {}
    if person_id is not None:
        payload["person_id"] = int(person_id)
    if year_months:
        payload["year_months"] = [str(month) for month in year_months]
    key = "daily"
    if person_id is not None:
        months_key = ",".join(payload.get("year_months") or ["auto"])
        key = f"person:{int(person_id)}:{months_key}"
    else:
        key = f"daily:{local_datetime().date().isoformat()}"
    try:
        return enqueue_ingestion_job(
            "person_history",
            job_kind="chapter",
            payload=payload,
            dedupe_key=key[:256],
            require_binding=False,
        )
    except ConnectorBindingRequired:
        logger.warning("[person_history] file indisponible")
        return None


def ensure_history_coverage(name: str) -> bool:
    person = get_person(name)
    if not person:
        return False
    person_id = int(person["id"])
    missing = missing_year_months(person_id)
    if not missing:
        return False
    return enqueue_person_history(person_id=person_id, year_months=missing[:12]) is not None


def register_person_history_handler() -> None:
    from jarvis.ingestion.service import register_ingestion_handler

    register_ingestion_handler(
        "person_history", "chapter", handle_person_history, replace=True
    )
