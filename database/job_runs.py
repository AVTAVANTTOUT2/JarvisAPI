"""Claims atomiques pour les workers planifies et leurs reruns manuels."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass

from .core import get_db


@dataclass(frozen=True)
class JobRunClaim:
    """Jeton opaque prouvant qu'un worker possede un run donne."""

    key: str
    token: str
    claimed_value: str


def _claim_key(job_name: str, run_key: str) -> str:
    digest = hashlib.sha256(run_key.encode("utf-8")).hexdigest()
    return f"worker_run:{job_name}:{digest}"


def claim_job_run(
    job_name: str,
    run_key: str,
    *,
    lease_seconds: int = 3600,
    now: float | None = None,
) -> JobRunClaim | None:
    """Claim atomiquement un run, sauf s'il est termine ou encore loue.

    ``BEGIN IMMEDIATE`` serialise le read/modify/write entre processus SQLite.
    Une lease expiree peut etre reprise apres un crash ; un run termine reste
    un no-op definitif pour la meme cle fonctionnelle.
    """
    claimed_at = float(time.time() if now is None else now)
    key = _claim_key(job_name, run_key)
    token = uuid.uuid4().hex
    claimed_value = json.dumps(
        {"state": "running", "token": token, "claimed_at": claimed_at},
        separators=(",", ":"),
        sort_keys=True,
    )

    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
        if row:
            try:
                current = json.loads(str(row["value"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                current = {}
            if current.get("state") == "completed":
                return None
            started = float(current.get("claimed_at") or 0)
            if (
                current.get("state") == "running"
                and claimed_at - started < max(1, int(lease_seconds))
            ):
                return None

        conn.execute(
            """INSERT INTO app_settings (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (key, claimed_value),
        )
    return JobRunClaim(key=key, token=token, claimed_value=claimed_value)


def complete_job_run(claim: JobRunClaim, *, now: float | None = None) -> bool:
    """Marque le run termine uniquement si le claim appartient encore a l'appelant."""
    completed_value = json.dumps(
        {
            "state": "completed",
            "token": claim.token,
            "completed_at": float(time.time() if now is None else now),
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE app_settings SET value = ? WHERE key = ? AND value = ?",
            (completed_value, claim.key, claim.claimed_value),
        )
        return cur.rowcount == 1


def release_job_run(claim: JobRunClaim) -> bool:
    """Libere un claim echoue afin que le prochain cycle puisse le retenter."""
    with get_db() as conn:
        cur = conn.execute(
            "DELETE FROM app_settings WHERE key = ? AND value = ?",
            (claim.key, claim.claimed_value),
        )
        return cur.rowcount == 1
