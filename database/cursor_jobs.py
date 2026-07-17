"""Persistance des jobs de délégation Cursor CLI."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from database.core import get_db

logger = logging.getLogger(__name__)

VALID_STATUSES = frozenset(
    {
        "proposal",
        "awaiting_confirmation",
        "queued",
        "preparing",
        "running",
        "testing",
        "reviewing",
        "needs_input",
        "failed",
        "completed",
        "pr_opened",
        "cancelled",
        "rolled_back",
    }
)

# Statuts qui consomment un slot de concurrence (enqueue + exécution).
ACTIVE_SLOT_STATUSES: tuple[str, ...] = (
    "awaiting_confirmation",
    "proposal",
    "queued",
    "preparing",
    "running",
    "testing",
    "reviewing",
)


def ensure_cursor_jobs_table() -> None:
    """Migration idempotente — appelée depuis init_db / premier usage."""
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cursor_delegation_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                user_request TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                repository TEXT,
                working_directory TEXT,
                worktree_path TEXT,
                branch_name TEXT,
                prompt_template TEXT,
                template_version TEXT,
                prompt_sent TEXT,
                raw_output TEXT,
                structured_result TEXT,
                acceptance_criteria TEXT,
                required_tests TEXT,
                risk_level TEXT DEFAULT 'medium',
                allow_commit INTEGER DEFAULT 1,
                allow_push INTEGER DEFAULT 1,
                allow_pr INTEGER DEFAULT 1,
                allow_merge INTEGER DEFAULT 0,
                commit_sha TEXT,
                pr_url TEXT,
                error_message TEXT,
                interaction_mode TEXT,
                routing_json TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                started_at DATETIME,
                finished_at DATETIME
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cursor_jobs_status ON cursor_delegation_jobs(status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cursor_jobs_created ON cursor_delegation_jobs(created_at)"
        )
        conn.commit()


def _insert_cursor_job_row(conn: Any, record: dict[str, Any], now: str) -> None:
    conn.execute(
        """
        INSERT INTO cursor_delegation_jobs (
            job_id, title, user_request, status, repository, working_directory,
            worktree_path, branch_name, prompt_template, template_version,
            prompt_sent, acceptance_criteria, required_tests, risk_level,
            allow_commit, allow_push, allow_pr, allow_merge,
            interaction_mode, routing_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record["job_id"],
            record["title"],
            record["user_request"],
            record.get("status", "queued"),
            record.get("repository"),
            record.get("working_directory"),
            record.get("worktree_path"),
            record.get("branch_name"),
            record.get("prompt_template"),
            record.get("template_version"),
            record.get("prompt_sent"),
            json.dumps(record.get("acceptance_criteria") or [], ensure_ascii=False),
            json.dumps(record.get("required_tests") or [], ensure_ascii=False),
            record.get("risk_level", "medium"),
            1 if record.get("allow_commit", True) else 0,
            1 if record.get("allow_push", True) else 0,
            1 if record.get("allow_pr", True) else 0,
            1 if record.get("allow_merge", False) else 0,
            record.get("interaction_mode"),
            json.dumps(record.get("routing") or {}, ensure_ascii=False),
            now,
            now,
        ),
    )


def create_cursor_job(record: dict[str, Any]) -> dict[str, Any]:
    ensure_cursor_jobs_table()
    now = datetime.now().isoformat(timespec="seconds")
    with get_db() as conn:
        _insert_cursor_job_row(conn, record, now)
        conn.commit()
    return get_cursor_job(record["job_id"])  # type: ignore[return-value]


def create_cursor_job_within_capacity(
    record: dict[str, Any],
    max_concurrent: int,
) -> dict[str, Any] | None:
    """Insère un job seulement si le nombre de slots actifs est sous la limite.

    Transaction SQLite ``BEGIN IMMEDIATE`` : le comptage et l'INSERT sont
    atomiques — élimine la course count_active() → create().
    """
    ensure_cursor_jobs_table()
    limit = max(1, int(max_concurrent))
    now = datetime.now().isoformat(timespec="seconds")
    placeholders = ",".join("?" * len(ACTIVE_SLOT_STATUSES))
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            f"SELECT COUNT(*) AS c FROM cursor_delegation_jobs "
            f"WHERE status IN ({placeholders})",
            ACTIVE_SLOT_STATUSES,
        ).fetchone()
        if int(row["c"] if row else 0) >= limit:
            conn.rollback()
            return None
        _insert_cursor_job_row(conn, record, now)
        conn.commit()
    return get_cursor_job(record["job_id"])


def update_cursor_job(job_id: str, **fields: Any) -> dict[str, Any] | None:
    ensure_cursor_jobs_table()
    if not fields:
        return get_cursor_job(job_id)
    if "status" in fields and fields["status"] not in VALID_STATUSES:
        raise ValueError(f"statut Cursor invalide: {fields['status']}")
    # Redaction avant persistance
    try:
        from jarvis.security.redaction import redact_sensitive_mapping, redact_sensitive_text

        for key in ("prompt_sent", "raw_output", "error_message"):
            if key in fields and isinstance(fields[key], str):
                fields[key] = redact_sensitive_text(fields[key])
        if "structured_result" in fields and not isinstance(fields["structured_result"], str):
            fields["structured_result"] = redact_sensitive_mapping(fields["structured_result"])
    except Exception:
        pass
    allowed = {
        "status", "worktree_path", "branch_name", "prompt_sent", "raw_output",
        "structured_result", "template_version", "prompt_template",
        "commit_sha", "pr_url", "error_message", "started_at", "finished_at",
    }
    cols = []
    vals: list[Any] = []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "structured_result" and not isinstance(v, str):
            v = json.dumps(v, ensure_ascii=False)
        cols.append(f"{k} = ?")
        vals.append(v)
    cols.append("updated_at = ?")
    vals.append(datetime.now().isoformat(timespec="seconds"))
    vals.append(job_id)
    with get_db() as conn:
        conn.execute(
            f"UPDATE cursor_delegation_jobs SET {', '.join(cols)} WHERE job_id = ?",
            vals,
        )
        conn.commit()
    return get_cursor_job(job_id)


def get_cursor_job(job_id: str) -> dict[str, Any] | None:
    ensure_cursor_jobs_table()
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_delegation_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_cursor_jobs(limit: int = 50, status: str | None = None) -> list[dict[str, Any]]:
    ensure_cursor_jobs_table()
    limit = max(1, min(int(limit), 200))
    with get_db() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM cursor_delegation_jobs WHERE status = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM cursor_delegation_jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_jobs_by_statuses(statuses: tuple[str, ...]) -> list[dict[str, Any]]:
    """Jobs dans un des statuts donnés (reprise après restart)."""
    ensure_cursor_jobs_table()
    if not statuses:
        return []
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM cursor_delegation_jobs "
            f"WHERE status IN ({','.join('?' * len(statuses))}) "
            "ORDER BY created_at ASC",
            statuses,
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def count_active_cursor_jobs() -> int:
    ensure_cursor_jobs_table()
    with get_db() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) AS c FROM cursor_delegation_jobs "
            f"WHERE status IN ({','.join('?' * len(ACTIVE_SLOT_STATUSES))})",
            ACTIVE_SLOT_STATUSES,
        ).fetchone()
    return int(row["c"] if row else 0)


def _row_to_dict(row: Any) -> dict[str, Any]:
    d = dict(row)
    for key in ("acceptance_criteria", "required_tests", "routing_json", "structured_result"):
        raw = d.get(key)
        if isinstance(raw, str) and raw:
            try:
                d[key] = json.loads(raw)
            except json.JSONDecodeError:
                pass
    # Alias lisible pour l'API / UI (la colonne reste routing_json)
    if "routing_json" in d:
        d["routing"] = d.get("routing_json")
    return d
