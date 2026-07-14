"""Helpers SQLite pour le module DevAgent autonome."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from typing import Any, Optional

from .core import get_db

logger = logging.getLogger(__name__)

DEVAGENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS dev_projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    project_type TEXT,
    status TEXT DEFAULT 'interviewing',
    isolation_path TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dev_interview_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES dev_projects(id),
    context_json TEXT NOT NULL DEFAULT '{}',
    questions_asked INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dev_spec (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES dev_projects(id),
    spec_json TEXT NOT NULL,
    locked_at TIMESTAMP,
    version INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS dev_loop_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES dev_projects(id),
    iteration INTEGER DEFAULT 0,
    phase TEXT,
    last_error TEXT,
    consecutive_failures INTEGER DEFAULT 0,
    tokens_used INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dev_loop_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES dev_projects(id),
    iteration INTEGER,
    phase TEXT,
    content TEXT,
    success BOOLEAN,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dev_deployments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES dev_projects(id),
    commit_sha TEXT,
    status TEXT NOT NULL CHECK(status IN ('success', 'failed')),
    staging_path TEXT,
    log TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_dev_loop_log_project ON dev_loop_log(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_dev_projects_status ON dev_projects(status);
CREATE INDEX IF NOT EXISTS idx_dev_deployments_project ON dev_deployments(project_id, created_at DESC);
"""


def migrate_devagent_tables(conn: sqlite3.Connection) -> None:
    """Cree les tables DevAgent si absentes (idempotent)."""
    conn.executescript(DEVAGENT_SCHEMA)


def create_dev_project(slug: str, name: str, isolation_path: str) -> int:
    """Cree un projet DevAgent et demarre une session d'interview."""
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO dev_projects (slug, name, isolation_path, status)
            VALUES (?, ?, ?, 'interviewing')
            """,
            (slug, name, isolation_path),
        )
        project_id = int(cur.lastrowid)
        conn.execute(
            """
            INSERT INTO dev_interview_sessions (project_id, context_json, status)
            VALUES (?, '{}', 'active')
            """,
            (project_id,),
        )
        conn.execute(
            """
            INSERT INTO dev_loop_state (project_id, iteration, phase)
            VALUES (?, 0, 'plan')
            """,
            (project_id,),
        )
        logger.info("[devagent] Projet cree id=%s slug=%s", project_id, slug)
        return project_id


def get_project(project_id: int) -> Optional[dict[str, Any]]:
    """Retourne un projet avec sa spec verrouillee si presente."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM dev_projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        if not row:
            return None
        project = dict(row)
        spec_row = conn.execute(
            """
            SELECT spec_json FROM dev_spec
            WHERE project_id = ?
            ORDER BY version DESC, id DESC
            LIMIT 1
            """,
            (project_id,),
        ).fetchone()
        project["spec_json"] = spec_row["spec_json"] if spec_row else None
        return project


def get_project_by_slug(slug: str) -> Optional[dict[str, Any]]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM dev_projects WHERE slug = ?",
            (slug,),
        ).fetchone()
        if not row:
            return None
        return get_project(int(row["id"]))


def update_project_status(project_id: int, status: str) -> None:
    with get_db() as conn:
        conn.execute(
            """
            UPDATE dev_projects
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, project_id),
        )


def get_interview_context(project_id: int) -> dict[str, Any]:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT context_json FROM dev_interview_sessions
            WHERE project_id = ? AND status = 'active'
            ORDER BY id DESC
            LIMIT 1
            """,
            (project_id,),
        ).fetchone()
        if not row:
            return {}
        try:
            return json.loads(row["context_json"] or "{}")
        except json.JSONDecodeError:
            logger.warning("[devagent] context_json invalide project_id=%s", project_id)
            return {}


def save_interview_context(project_id: int, context: dict[str, Any]) -> None:
    payload = json.dumps(context, ensure_ascii=False)
    with get_db() as conn:
        conn.execute(
            """
            UPDATE dev_interview_sessions
            SET context_json = ?,
                questions_asked = questions_asked + 1
            WHERE project_id = ? AND status = 'active'
            """,
            (payload, project_id),
        )


def complete_interview_session(project_id: int) -> None:
    with get_db() as conn:
        conn.execute(
            """
            UPDATE dev_interview_sessions
            SET status = 'complete'
            WHERE project_id = ? AND status = 'active'
            """,
            (project_id,),
        )


def save_spec(project_id: int, spec_json: str) -> int:
    with get_db() as conn:
        version_row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) AS v FROM dev_spec WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        version = int(version_row["v"]) + 1
        cur = conn.execute(
            """
            INSERT INTO dev_spec (project_id, spec_json, locked_at, version)
            VALUES (?, ?, CURRENT_TIMESTAMP, ?)
            """,
            (project_id, spec_json, version),
        )
        return int(cur.lastrowid)


def get_loop_state(project_id: int) -> dict[str, Any]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM dev_loop_state WHERE project_id = ? ORDER BY id DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        if not row:
            return {
                "project_id": project_id,
                "iteration": 0,
                "phase": "plan",
                "last_error": None,
                "consecutive_failures": 0,
                "tokens_used": 0,
            }
        return dict(row)


def update_loop_state(project_id: int, state: dict[str, Any]) -> None:
    with get_db() as conn:
        conn.execute(
            """
            UPDATE dev_loop_state
            SET iteration = ?,
                phase = ?,
                last_error = ?,
                consecutive_failures = ?,
                tokens_used = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE project_id = ?
            """,
            (
                int(state.get("iteration", 0)),
                state.get("phase"),
                state.get("last_error"),
                int(state.get("consecutive_failures", 0)),
                int(state.get("tokens_used", 0)),
                project_id,
            ),
        )


def log_iteration(
    project_id: int,
    iteration: int,
    phase: str,
    content: str,
    success: bool,
) -> int:
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO dev_loop_log (project_id, iteration, phase, content, success)
            VALUES (?, ?, ?, ?, ?)
            """,
            (project_id, iteration, phase, content, 1 if success else 0),
        )
        return int(cur.lastrowid)


def get_dev_loop_logs(
    limit: int = 100,
    project_id: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Retourne les logs de boucle DevAgent au format compatible /api/logs."""
    lim = max(1, min(int(limit), 1000))
    with get_db() as conn:
        if project_id is not None:
            rows = conn.execute(
                """
                SELECT *
                FROM dev_loop_log
                WHERE project_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (project_id, lim),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT *
                FROM dev_loop_log
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (lim,),
            ).fetchall()
    logs: list[dict[str, Any]] = []
    for row in rows:
        r = dict(row)
        logs.append(
            {
                "id": r["id"],
                "created_at": r["created_at"],
                "agent": "devagent",
                "action_type": f"devagent_{r.get('phase') or 'unknown'}",
                "payload": r.get("content", ""),
                "status": "success" if r.get("success") else "error",
                "execution_time_ms": None,
                "project_id": r.get("project_id"),
                "iteration": r.get("iteration"),
            }
        )
    return logs


def record_deployment(
    project_id: int,
    commit_sha: Optional[str],
    status: str,
    staging_path: Optional[str] = None,
    log: Optional[str] = None,
) -> int:
    """Enregistre une tentative de déploiement staging (succès ou échec)."""
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO dev_deployments (project_id, commit_sha, status, staging_path, log)
               VALUES (?, ?, ?, ?, ?)""",
            (project_id, commit_sha, status, staging_path, log),
        )
        return int(cur.lastrowid)


def get_deployments(project_id: int, limit: int = 20) -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM dev_deployments WHERE project_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (project_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_project_status_payload(project_id: int) -> dict[str, Any]:
    """Etat complet pour l'endpoint /status."""
    project = get_project(project_id)
    if not project:
        return {}
    state = get_loop_state(project_id)
    spec = None
    if project.get("spec_json"):
        try:
            spec = json.loads(project["spec_json"])
        except json.JSONDecodeError:
            spec = None
    return {
        "project_id": project_id,
        "slug": project.get("slug"),
        "name": project.get("name"),
        "status": project.get("status"),
        "loop_state": state,
        "spec_locked": spec is not None,
    }
