"""Historique des exécutions APScheduler (page /scheduler)."""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from typing import Any

import config
from .core import get_db
from .time_buckets import local_datetime, utc_bounds_for_local_dates

MAX_OUTPUT_CHARS = max(
    256,
    min(int(getattr(config, "SCHEDULER_RUN_OUTPUT_MAX_CHARS", 4000)), 16_384),
)

VALID_STATUSES = frozenset({"ok", "skipped", "silent", "error"})
VALID_TRIGGERS = frozenset({"cron", "manual"})


def _truncate(text: str | None) -> str:
    value = "" if text is None else str(text)
    if len(value) <= MAX_OUTPUT_CHARS:
        return value
    return value[: MAX_OUTPUT_CHARS - 20] + "\n…[tronqué]"


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def start_run(job_id: str, *, trigger: str = "cron") -> int:
    """Ouvre une exécution et retourne son id."""
    job = (job_id or "").strip()
    if not job:
        raise ValueError("job_id requis")
    trig = trigger if trigger in VALID_TRIGGERS else "cron"
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO scheduler_job_runs (job_id, trigger, status, started_at)
            VALUES (?, ?, 'running', CURRENT_TIMESTAMP)
            """,
            (job, trig),
        )
        return int(cur.lastrowid)


def finish_run(
    run_id: int,
    *,
    status: str,
    output: str | None = None,
    error: str | None = None,
) -> None:
    """Clôt une exécution ouverte."""
    final = status if status in VALID_STATUSES else "error"
    with get_db() as conn:
        conn.execute(
            """
            UPDATE scheduler_job_runs
            SET status = ?,
                finished_at = CURRENT_TIMESTAMP,
                output = ?,
                error = ?,
                duration_ms = CAST(
                    (julianday(CURRENT_TIMESTAMP) - julianday(started_at)) * 86400000
                    AS INTEGER
                )
            WHERE id = ?
            """,
            (final, _truncate(output), _truncate(error), int(run_id)),
        )


def get_scheduler_run(run_id: int) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM scheduler_job_runs WHERE id = ?",
            (int(run_id),),
        ).fetchone()
    return _row_to_dict(row)


def list_scheduler_runs(
    *,
    job_id: str | None = None,
    days: int = 7,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Liste les runs sur les N derniers jours locaux (plus récent d'abord)."""
    days_n = max(1, min(int(days), 90))
    limit_n = max(1, min(int(limit), 500))
    offset_n = max(0, int(offset))
    today = local_datetime().date()
    start = today - timedelta(days=days_n - 1)
    start_utc, end_utc = utc_bounds_for_local_dates(start, today + timedelta(days=1))

    params: list[Any] = [start_utc, end_utc]
    where = "started_at >= ? AND started_at < ?"
    if job_id:
        where += " AND job_id = ?"
        params.append(job_id.strip())
    params.extend([limit_n, offset_n])

    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM scheduler_job_runs
            WHERE {where}
            ORDER BY started_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def _last_run_by_job(
    conn: sqlite3.Connection,
    *,
    start_utc: str,
    end_utc: str,
    job_ids: list[str] | None,
) -> dict[str, dict[str, Any]]:
    params: list[Any] = [start_utc, end_utc]
    where = "started_at >= ? AND started_at < ?"
    if job_ids:
        placeholders = ",".join("?" for _ in job_ids)
        where += f" AND job_id IN ({placeholders})"
        params.extend(job_ids)
    rows = conn.execute(
        f"""
        SELECT r.*
        FROM scheduler_job_runs r
        INNER JOIN (
            SELECT job_id, MAX(id) AS max_id
            FROM scheduler_job_runs
            WHERE {where}
            GROUP BY job_id
        ) latest ON latest.max_id = r.id
        """,
        params,
    ).fetchall()
    return {str(row["job_id"]): dict(row) for row in rows}


def aggregate_scheduler_runs(
    *,
    days: int = 7,
    job_ids: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Agrégats par job_id sur la fenêtre locale demandée."""
    days_n = max(1, min(int(days), 90))
    today = local_datetime().date()
    start = today - timedelta(days=days_n - 1)
    start_utc, end_utc = utc_bounds_for_local_dates(start, today + timedelta(days=1))
    today_start_utc, today_end_utc = utc_bounds_for_local_dates(
        today, today + timedelta(days=1)
    )

    params: list[Any] = [today_start_utc, today_end_utc, start_utc, end_utc]
    where = "started_at >= ? AND started_at < ?"
    if job_ids:
        placeholders = ",".join("?" for _ in job_ids)
        where += f" AND job_id IN ({placeholders})"
        params.extend(job_ids)

    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT
                job_id,
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok_count,
                SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS error_count,
                SUM(CASE WHEN status = 'skipped' THEN 1 ELSE 0 END) AS skipped_count,
                SUM(CASE WHEN status = 'silent' THEN 1 ELSE 0 END) AS silent_count,
                SUM(
                    CASE WHEN started_at >= ? AND started_at < ? THEN 1 ELSE 0 END
                ) AS today_count,
                SUM(
                    CASE
                        WHEN started_at >= ? AND started_at < ? AND status = 'ok'
                        THEN 1 ELSE 0 END
                ) AS today_ok,
                SUM(
                    CASE
                        WHEN started_at >= ? AND started_at < ? AND status = 'error'
                        THEN 1 ELSE 0 END
                ) AS today_error
            FROM scheduler_job_runs
            WHERE {where}
            GROUP BY job_id
            """,
            [
                today_start_utc,
                today_end_utc,
                today_start_utc,
                today_end_utc,
                today_start_utc,
                today_end_utc,
                *params[2:],
            ],
        ).fetchall()
        lasts = _last_run_by_job(
            conn, start_utc=start_utc, end_utc=end_utc, job_ids=job_ids
        )

    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        job_id = str(row["job_id"])
        payload = dict(row)
        last = lasts.get(job_id) or {}
        payload["last_started_at"] = last.get("started_at")
        payload["last_status"] = last.get("status")
        payload["last_output"] = last.get("output")
        payload["last_error"] = last.get("error")
        payload["last_duration_ms"] = last.get("duration_ms")
        payload["last_trigger"] = last.get("trigger")
        out[job_id] = payload
    return out


def purge_scheduler_runs(days: int) -> int:
    """Supprime les runs plus anciens que ``days`` jours (UTC SQLite)."""
    if days <= 0:
        return 0
    with get_db() as conn:
        cur = conn.execute(
            "DELETE FROM scheduler_job_runs WHERE started_at < datetime('now', ?)",
            (f"-{int(days)} days",),
        )
        return int(cur.rowcount)
