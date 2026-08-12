"""Persistance SQLite générique des runs agentiques.

Les écritures sont isolées par profil, neutralisées avant persistance et
idempotentes aux frontières événements/approbations.
"""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import secrets
from typing import Any, Mapping

from jarvis.agentic.models import (
    AgenticError,
    AgenticErrorCode,
    AgenticRequestCategory,
    AgenticRun,
    AgenticRunStatus,
    ApprovalDecision,
    ApprovalRequest,
    Artifact,
    RunBudget,
    RuntimeEvent,
    TERMINAL_RUN_STATUSES,
    VerificationEvidence,
    VerificationResult,
    VerificationVerdict,
    canonical_run_request_digest,
    validate_run_transition,
)
from jarvis.agentic.redaction import (
    neutralize_event_payload,
    redact_mapping,
    redact_text,
)

from . import dbapi as sqlite3
from .core import current_profile_id, get_db, normalize_profile_id


_APPROVAL_DEFAULT_TTL = timedelta(minutes=10)
_APPROVAL_MAX_TTL = timedelta(minutes=15)
_DEFAULT_APPROVAL_RISK = "Action sensible soumise à confirmation utilisateur."


AGENTIC_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    task_id TEXT,
    conversation_id TEXT,
    origin TEXT NOT NULL,
    channel TEXT NOT NULL,
    device TEXT,
    locale TEXT NOT NULL DEFAULT 'fr-FR',
    timezone TEXT NOT NULL DEFAULT 'Europe/Paris',
    runtime_id TEXT NOT NULL,
    provider_session_id TEXT,
    status TEXT NOT NULL,
    phase TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    permissions_json TEXT NOT NULL DEFAULT '[]',
    context_json TEXT NOT NULL DEFAULT '{}',
    budget_json TEXT NOT NULL,
    workspace TEXT,
    idempotency_key TEXT,
    idempotency_digest TEXT,
    error_json TEXT,
    verification_json TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL,
    CHECK(status IN (
        'created', 'classified', 'queued', 'provisioning', 'planning',
        'awaiting_approval', 'running', 'verifying', 'reviewing', 'paused',
        'blocked', 'cancelling', 'cancelled', 'failed', 'completed', 'expired',
        'provider_unavailable'
    )),
    CHECK(category IN (
        'direct_action', 'workflow', 'agentic_readonly', 'agentic_reversible',
        'agentic_external_effect', 'agentic_high_risk'
    ))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_runs_profile_idempotency
    ON agent_runs(profile_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_agent_runs_profile_status
    ON agent_runs(profile_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_runs_task ON agent_runs(profile_id, task_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_conversation
    ON agent_runs(profile_id, conversation_id);

CREATE TABLE IF NOT EXISTS agent_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK(sequence > 0),
    event_type TEXT NOT NULL,
    external_event_id TEXT,
    timestamp TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    level TEXT NOT NULL DEFAULT 'info',
    visibility TEXT NOT NULL DEFAULT 'user',
    sensitivity TEXT NOT NULL DEFAULT 'normal',
    UNIQUE(run_id, event_id),
    UNIQUE(run_id, sequence)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_events_external
    ON agent_events(run_id, external_event_id)
    WHERE external_event_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_agent_events_profile_run
    ON agent_events(profile_id, run_id, sequence);

CREATE TABLE IF NOT EXISTS agent_event_inbox (
    run_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    processing_started_at TEXT,
    processed_at TEXT,
    claim_token TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(run_id, event_id),
    FOREIGN KEY(run_id, event_id)
        REFERENCES agent_events(run_id, event_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_agent_event_inbox_pending
    ON agent_event_inbox(profile_id, processed_at, processing_started_at, created_at);

CREATE TABLE IF NOT EXISTS agent_steps (
    step_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    error_code TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(run_id, sequence)
);
CREATE INDEX IF NOT EXISTS idx_agent_steps_profile_run
    ON agent_steps(profile_id, run_id, sequence);

CREATE TABLE IF NOT EXISTS agent_approvals (
    approval_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL,
    action TEXT NOT NULL,
    tool TEXT NOT NULL,
    summary TEXT NOT NULL,
    arguments_json TEXT NOT NULL DEFAULT '{}',
    risks_json TEXT NOT NULL DEFAULT '[]',
    scope TEXT NOT NULL,
    expires_at TEXT,
    decision TEXT NOT NULL DEFAULT 'pending',
    decision_by TEXT,
    decision_at TEXT,
    decision_id TEXT,
    created_at TEXT NOT NULL,
    CHECK(decision IN ('pending', 'approved', 'denied', 'expired'))
);
CREATE INDEX IF NOT EXISTS idx_agent_approvals_profile_run
    ON agent_approvals(profile_id, run_id, decision);
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_approvals_profile_decision
    ON agent_approvals(profile_id, decision_id)
    WHERE decision_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS agent_approval_outbox (
    approval_id TEXT PRIMARY KEY REFERENCES agent_approvals(approval_id) ON DELETE CASCADE,
    run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    claimed_at TEXT,
    delivered_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(profile_id, decision_id),
    CHECK(decision IN ('approved', 'denied')),
    CHECK(status IN ('pending', 'delivering', 'delivered'))
);
CREATE INDEX IF NOT EXISTS idx_agent_approval_outbox_pending
    ON agent_approval_outbox(profile_id, run_id, status, created_at);

CREATE TABLE IF NOT EXISTS agent_artifacts (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    reference TEXT NOT NULL,
    sha256 TEXT,
    size_bytes INTEGER,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    visibility TEXT NOT NULL DEFAULT 'user',
    retention TEXT NOT NULL DEFAULT 'default',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_artifacts_profile_run
    ON agent_artifacts(profile_id, run_id, created_at);

CREATE TABLE IF NOT EXISTS agent_capability_grants (
    grant_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    scope TEXT NOT NULL,
    granted_by TEXT NOT NULL,
    expires_at TEXT,
    revoked_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(run_id, capability, scope)
);
CREATE INDEX IF NOT EXISTS idx_agent_grants_profile_run
    ON agent_capability_grants(profile_id, run_id, revoked_at);

CREATE TABLE IF NOT EXISTS agent_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    state_json TEXT NOT NULL DEFAULT '{}',
    checksum TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, sequence)
);
CREATE INDEX IF NOT EXISTS idx_agent_checkpoints_profile_run
    ON agent_checkpoints(profile_id, run_id, sequence DESC);

CREATE TABLE IF NOT EXISTS agent_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_metrics_profile_run
    ON agent_metrics(profile_id, run_id, recorded_at);
"""


class AgenticPersistenceError(RuntimeError):
    pass


class AgenticRunNotFound(AgenticPersistenceError):
    pass


class AgenticPersistenceConflict(AgenticPersistenceError):
    pass


class AgenticIdempotencyConflict(AgenticPersistenceConflict):
    pass


class ApprovalAlreadyDecided(AgenticPersistenceConflict):
    pass


class ApprovalExpired(ApprovalAlreadyDecided):
    pass


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _budget_dict(budget: RunBudget) -> dict[str, Any]:
    payload = asdict(budget)
    payload["deadline"] = _iso(budget.deadline)
    return payload


def _budget(value: str) -> RunBudget:
    payload = _load_json(value, {})
    payload["deadline"] = _datetime(payload.get("deadline"))
    return RunBudget(**payload)


def _error_dict(error: AgenticError | None) -> dict[str, Any] | None:
    if error is None:
        return None
    return {
        "code": error.code.value,
        "message": redact_text(error.message, max_chars=1000),
        "retryable": error.retryable,
        "details": redact_mapping(error.details),
    }


def _error(value: str | None) -> AgenticError | None:
    payload = _load_json(value, None)
    if not isinstance(payload, dict):
        return None
    try:
        code = AgenticErrorCode(payload["code"])
    except (KeyError, ValueError):
        code = AgenticErrorCode.INTERNAL
    return AgenticError(
        code=code,
        message=str(payload.get("message") or ""),
        retryable=bool(payload.get("retryable", False)),
        details=payload.get("details") or {},
    )


def _verification_dict(result: VerificationResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "verdict": result.verdict.value,
        "verifier": result.verifier,
        "summary": redact_text(result.summary, max_chars=1000),
        "evidence": [
            {
                "check": item.check,
                "passed": item.passed,
                "summary": redact_text(item.summary, max_chars=1000),
                "artifact_id": item.artifact_id,
                "metadata": redact_mapping(item.metadata),
            }
            for item in result.evidence
        ],
        "verified_at": _iso(result.verified_at),
    }


def _verification(value: str | None) -> VerificationResult | None:
    payload = _load_json(value, None)
    if not isinstance(payload, dict):
        return None
    evidence = tuple(
        VerificationEvidence(
            check=str(item.get("check") or ""),
            passed=bool(item.get("passed")),
            summary=str(item.get("summary") or ""),
            artifact_id=item.get("artifact_id"),
            metadata=item.get("metadata") or {},
        )
        for item in payload.get("evidence", [])
        if isinstance(item, dict)
    )
    return VerificationResult(
        verdict=VerificationVerdict(payload["verdict"]),
        verifier=str(payload.get("verifier") or "unknown"),
        summary=str(payload.get("summary") or ""),
        evidence=evidence,
        verified_at=_datetime(payload.get("verified_at")) or datetime.now(timezone.utc),
    )


def _row_to_run(row: sqlite3.Row | None) -> AgenticRun | None:
    if row is None:
        return None
    return AgenticRun(
        run_id=str(row["run_id"]),
        profile_id=str(row["profile_id"]),
        task_id=row["task_id"],
        conversation_id=row["conversation_id"],
        origin=str(row["origin"]),
        channel=str(row["channel"]),
        device=row["device"],
        locale=str(row["locale"]),
        timezone=str(row["timezone"]),
        runtime_id=str(row["runtime_id"]),
        provider_session_id=row["provider_session_id"],
        status=AgenticRunStatus(row["status"]),
        phase=str(row["phase"]),
        category=AgenticRequestCategory(row["category"]),
        title=str(row["title"]),
        permissions=tuple(_load_json(row["permissions_json"], [])),
        selected_context=_load_json(row["context_json"], {}),
        budget=_budget(row["budget_json"]),
        workspace=row["workspace"],
        idempotency_key=row["idempotency_key"],
        idempotency_digest=(
            row["idempotency_digest"] if "idempotency_digest" in row.keys() else None
        ),
        error=_error(row["error_json"]),
        verification=_verification(row["verification_json"]),
        created_at=_datetime(row["created_at"]) or datetime.now(timezone.utc),
        started_at=_datetime(row["started_at"]),
        finished_at=_datetime(row["finished_at"]),
        updated_at=_datetime(row["updated_at"]) or datetime.now(timezone.utc),
    )


class AgenticRepository:
    """Repository transactionnel, profil courant par défaut."""

    def _profile(self, profile_id: str | None) -> str:
        return normalize_profile_id(profile_id or current_profile_id())

    def create_run(self, run: AgenticRun) -> tuple[AgenticRun, bool]:
        profile_id = self._profile(run.profile_id)
        if profile_id != self._profile(None):
            raise AgenticPersistenceConflict("création cross-profile interdite")
        run = replace(
            run,
            title=redact_text(run.title, max_chars=240),
            selected_context=redact_mapping(run.selected_context),
        )
        if run.idempotency_key:
            digest = run.idempotency_digest or canonical_run_request_digest(run)
            run = replace(run, idempotency_digest=digest)

        def replay_or_conflict(row: sqlite3.Row) -> AgenticRun:
            existing_run = _row_to_run(row)
            if existing_run is None:
                raise AgenticPersistenceConflict("run idempotent illisible")
            existing_digest = existing_run.idempotency_digest
            if existing_digest is None:
                existing_digest = canonical_run_request_digest(existing_run)
            if existing_digest != run.idempotency_digest:
                raise AgenticIdempotencyConflict(
                    "clé d'idempotence déjà liée à un payload différent"
                )
            return replace(existing_run, idempotency_digest=existing_digest)

        with get_db() as conn:
            if run.idempotency_key:
                existing = conn.execute(
                    "SELECT * FROM agent_runs WHERE profile_id = ? AND idempotency_key = ?",
                    (profile_id, run.idempotency_key),
                ).fetchone()
                if existing is not None:
                    return replay_or_conflict(existing), False
            try:
                conn.execute(
                    """
                    INSERT INTO agent_runs (
                        run_id, profile_id, task_id, conversation_id, origin, channel,
                        device, locale, timezone, runtime_id, provider_session_id,
                        status, phase, category, title, permissions_json, context_json,
                        budget_json, workspace, idempotency_key, idempotency_digest,
                        error_json, verification_json, created_at, started_at, finished_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.run_id,
                        profile_id,
                        run.task_id,
                        run.conversation_id,
                        run.origin,
                        run.channel,
                        run.device,
                        run.locale,
                        run.timezone,
                        run.runtime_id,
                        run.provider_session_id,
                        run.status.value,
                        run.phase,
                        run.category.value,
                        redact_text(run.title, max_chars=240),
                        _json(list(run.permissions)),
                        _json(redact_mapping(run.selected_context)),
                        _json(_budget_dict(run.budget)),
                        run.workspace,
                        run.idempotency_key,
                        run.idempotency_digest,
                        _json(_error_dict(run.error)) if run.error else None,
                        _json(_verification_dict(run.verification))
                        if run.verification
                        else None,
                        _iso(run.created_at),
                        _iso(run.started_at),
                        _iso(run.finished_at),
                        _iso(run.updated_at),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                if run.idempotency_key:
                    existing = conn.execute(
                        "SELECT * FROM agent_runs WHERE profile_id = ? AND idempotency_key = ?",
                        (profile_id, run.idempotency_key),
                    ).fetchone()
                    if existing is not None:
                        return replay_or_conflict(existing), False
                raise AgenticPersistenceConflict(
                    f"run non insérable: {run.run_id}"
                ) from exc
        return self.get_run(run.run_id, profile_id=profile_id), True  # type: ignore[return-value]

    def get_run(
        self, run_id: str, *, profile_id: str | None = None
    ) -> AgenticRun | None:
        selected = self._profile(profile_id)
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM agent_runs WHERE run_id = ? AND profile_id = ?",
                (run_id, selected),
            ).fetchone()
        return _row_to_run(row)

    def require_run(self, run_id: str, *, profile_id: str | None = None) -> AgenticRun:
        run = self.get_run(run_id, profile_id=profile_id)
        if run is None:
            raise AgenticRunNotFound(run_id)
        return run

    def list_runs(
        self,
        *,
        profile_id: str | None = None,
        statuses: tuple[AgenticRunStatus, ...] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AgenticRun]:
        selected = self._profile(profile_id)
        where = "profile_id = ?"
        params: list[Any] = [selected]
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            where += f" AND status IN ({placeholders})"
            params.extend(status.value for status in statuses)
        params.extend([max(1, min(int(limit), 500)), max(0, int(offset))])
        with get_db() as conn:
            rows = conn.execute(
                f"SELECT * FROM agent_runs WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        return [run for row in rows if (run := _row_to_run(row)) is not None]

    def list_nonterminal(self, *, profile_id: str | None = None) -> list[AgenticRun]:
        terminal = tuple(status.value for status in TERMINAL_RUN_STATUSES)
        placeholders = ",".join("?" for _ in terminal)
        selected = self._profile(profile_id)
        with get_db() as conn:
            rows = conn.execute(
                f"SELECT * FROM agent_runs WHERE profile_id = ? AND status NOT IN ({placeholders}) ORDER BY created_at",
                (selected, *terminal),
            ).fetchall()
        return [run for row in rows if (run := _row_to_run(row)) is not None]

    def transition_run(
        self,
        run_id: str,
        status: AgenticRunStatus | str,
        *,
        phase: str | None = None,
        error: AgenticError | None = None,
        verification: VerificationResult | None = None,
        profile_id: str | None = None,
    ) -> AgenticRun:
        selected = self._profile(profile_id)
        current = self.require_run(run_id, profile_id=selected)
        _, target = validate_run_transition(current.status, status)
        updated = current.transition(
            target,
            phase=phase,
            error=error,
            verification=verification,
        )
        with get_db() as conn:
            cursor = conn.execute(
                """
                UPDATE agent_runs
                SET status = ?, phase = ?, error_json = ?, verification_json = ?,
                    started_at = ?, finished_at = ?, updated_at = ?
                WHERE run_id = ? AND profile_id = ? AND status = ?
                """,
                (
                    updated.status.value,
                    updated.phase,
                    _json(_error_dict(updated.error)) if updated.error else None,
                    _json(_verification_dict(updated.verification))
                    if updated.verification
                    else None,
                    _iso(updated.started_at),
                    _iso(updated.finished_at),
                    _iso(updated.updated_at),
                    run_id,
                    selected,
                    current.status.value,
                ),
            )
            if cursor.rowcount != 1:
                raise AgenticPersistenceConflict("transition concurrente détectée")
        return updated

    def set_provider_session(
        self,
        run_id: str,
        provider_session_id: str | None,
        *,
        profile_id: str | None = None,
    ) -> AgenticRun:
        selected = self._profile(profile_id)
        with get_db() as conn:
            cursor = conn.execute(
                "UPDATE agent_runs SET provider_session_id = ?, updated_at = ? WHERE run_id = ? AND profile_id = ?",
                (
                    provider_session_id,
                    _iso(datetime.now(timezone.utc)),
                    run_id,
                    selected,
                ),
            )
            if cursor.rowcount != 1:
                raise AgenticRunNotFound(run_id)
        return self.require_run(run_id, profile_id=selected)

    def append_event(
        self,
        event: RuntimeEvent,
        *,
        profile_id: str | None = None,
        requires_processing: bool = False,
    ) -> tuple[RuntimeEvent, bool]:
        selected = self._profile(profile_id)
        self.require_run(event.run_id, profile_id=selected)
        with get_db() as conn:
            # Sérialise l'allocation MAX(sequence)+1 entre workers locaux.
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM agent_events WHERE run_id = ? AND event_id = ? AND profile_id = ?",
                (event.run_id, event.event_id, selected),
            ).fetchone()
            if existing is None and event.external_event_id:
                existing = conn.execute(
                    "SELECT * FROM agent_events WHERE run_id = ? AND external_event_id = ? AND profile_id = ?",
                    (event.run_id, event.external_event_id, selected),
                ).fetchone()
            if existing is not None:
                stored = self._row_to_event(existing)
                if requires_processing:
                    now = _iso(datetime.now(timezone.utc))
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO agent_event_inbox (
                            run_id, event_id, profile_id, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (stored.run_id, stored.event_id, selected, now, now),
                    )
                return stored, False
            row = conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS sequence FROM agent_events WHERE run_id = ? AND profile_id = ?",
                (event.run_id, selected),
            ).fetchone()
            next_sequence = int(row["sequence"]) + 1
            if event.sequence not in {0, next_sequence}:
                raise AgenticPersistenceConflict(
                    f"séquence attendue {next_sequence}, reçue {event.sequence}"
                )
            stored = replace(
                event,
                sequence=next_sequence,
                payload=neutralize_event_payload(
                    {**event.payload, "run_id": event.run_id, "sequence": next_sequence}
                ),
            )
            conn.execute(
                """
                INSERT INTO agent_events (
                    event_id, run_id, profile_id, sequence, event_type,
                    external_event_id, timestamp, payload_json, level, visibility,
                    sensitivity
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stored.event_id,
                    stored.run_id,
                    selected,
                    stored.sequence,
                    stored.type,
                    stored.external_event_id,
                    _iso(stored.timestamp),
                    _json(dict(stored.payload)),
                    stored.level,
                    stored.visibility,
                    stored.sensitivity,
                ),
            )
            if requires_processing:
                now = _iso(datetime.now(timezone.utc))
                conn.execute(
                    """
                    INSERT INTO agent_event_inbox (
                        run_id, event_id, profile_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (stored.run_id, stored.event_id, selected, now, now),
                )
        return stored, True

    def list_unprocessed_events(
        self,
        *,
        profile_id: str | None = None,
        limit: int = 100,
    ) -> list[RuntimeEvent]:
        """Retourne l'inbox durable sans réserver les lignes."""

        selected = self._profile(profile_id)
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT events.*
                FROM agent_event_inbox AS inbox
                JOIN agent_events AS events
                  ON events.run_id = inbox.run_id AND events.event_id = inbox.event_id
                WHERE inbox.profile_id = ? AND inbox.processed_at IS NULL
                ORDER BY inbox.created_at, events.sequence
                LIMIT ?
                """,
                (selected, max(1, min(int(limit), 1000))),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def claim_event_processing(
        self,
        run_id: str,
        event_id: str,
        *,
        profile_id: str | None = None,
        now: datetime | None = None,
        lease_seconds: int = 60,
    ) -> str | None:
        """Réserve une entrée inbox, y compris après expiration d'un bail."""

        selected = self._profile(profile_id)
        changed_at = now or datetime.now(timezone.utc)
        stale_before = changed_at - timedelta(seconds=max(1, int(lease_seconds)))
        claim_token = secrets.token_hex(32)
        with get_db() as conn:
            cursor = conn.execute(
                """
                UPDATE agent_event_inbox
                SET processing_started_at = ?, claim_token = ?,
                    attempts = attempts + 1, updated_at = ?, last_error = NULL
                WHERE run_id = ? AND event_id = ? AND profile_id = ?
                  AND processed_at IS NULL
                  AND (
                      processing_started_at IS NULL
                      OR processing_started_at <= ?
                  )
                """,
                (
                    _iso(changed_at),
                    claim_token,
                    _iso(changed_at),
                    run_id,
                    event_id,
                    selected,
                    _iso(stale_before),
                ),
            )
        return claim_token if cursor.rowcount == 1 else None

    def complete_event_processing(
        self,
        run_id: str,
        event_id: str,
        claim_token: str,
        *,
        profile_id: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Marque traité uniquement le bail encore détenu par cet appelant."""

        selected = self._profile(profile_id)
        changed_at = now or datetime.now(timezone.utc)
        with get_db() as conn:
            cursor = conn.execute(
                """
                UPDATE agent_event_inbox
                SET processed_at = ?, updated_at = ?, last_error = NULL
                WHERE run_id = ? AND event_id = ? AND profile_id = ?
                  AND processed_at IS NULL AND claim_token = ?
                """,
                (
                    _iso(changed_at),
                    _iso(changed_at),
                    run_id,
                    event_id,
                    selected,
                    claim_token,
                ),
            )
        return cursor.rowcount == 1

    def release_event_processing(
        self,
        run_id: str,
        event_id: str,
        claim_token: str,
        *,
        error: str,
        profile_id: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Libère le bail courant afin qu'un replay puisse reprendre l'événement."""

        selected = self._profile(profile_id)
        changed_at = now or datetime.now(timezone.utc)
        with get_db() as conn:
            cursor = conn.execute(
                """
                UPDATE agent_event_inbox
                SET processing_started_at = NULL, claim_token = NULL,
                    updated_at = ?, last_error = ?
                WHERE run_id = ? AND event_id = ? AND profile_id = ?
                  AND processed_at IS NULL AND claim_token = ?
                """,
                (
                    _iso(changed_at),
                    redact_text(error, max_chars=500),
                    run_id,
                    event_id,
                    selected,
                    claim_token,
                ),
            )
        return cursor.rowcount == 1

    def event_processing_state(
        self,
        run_id: str,
        event_id: str,
        *,
        profile_id: str | None = None,
    ) -> str | None:
        """Expose un état minimal pour diagnostic et tests de reprise."""

        selected = self._profile(profile_id)
        with get_db() as conn:
            row = conn.execute(
                """
                SELECT processing_started_at, processed_at
                FROM agent_event_inbox
                WHERE run_id = ? AND event_id = ? AND profile_id = ?
                """,
                (run_id, event_id, selected),
            ).fetchone()
        if row is None:
            return None
        if row["processed_at"] is not None:
            return "processed"
        if row["processing_started_at"] is not None:
            return "processing"
        return "pending"

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> RuntimeEvent:
        return RuntimeEvent(
            event_id=str(row["event_id"]),
            run_id=str(row["run_id"]),
            sequence=int(row["sequence"]),
            type=str(row["event_type"]),
            external_event_id=row["external_event_id"],
            timestamp=_datetime(row["timestamp"]) or datetime.now(timezone.utc),
            payload=_load_json(row["payload_json"], {}),
            level=str(row["level"]),
            visibility=str(row["visibility"]),
            sensitivity=str(row["sensitivity"]),
        )

    def list_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        profile_id: str | None = None,
        limit: int = 500,
    ) -> list[RuntimeEvent]:
        selected = self._profile(profile_id)
        self.require_run(run_id, profile_id=selected)
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT * FROM agent_events
                WHERE run_id = ? AND profile_id = ? AND sequence > ?
                ORDER BY sequence LIMIT ?
                """,
                (
                    run_id,
                    selected,
                    max(0, int(after_sequence)),
                    max(1, min(limit, 2000)),
                ),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def create_approval(
        self,
        approval: ApprovalRequest,
        *,
        profile_id: str | None = None,
    ) -> ApprovalRequest:
        selected = self._profile(profile_id)
        self.require_run(approval.run_id, profile_id=selected)
        now = datetime.now(timezone.utc)
        expires_at = approval.expires_at
        if expires_at is None:
            expires_at = now + _APPROVAL_DEFAULT_TTL
        elif expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        else:
            expires_at = expires_at.astimezone(timezone.utc)
        if expires_at <= now:
            raise ApprovalExpired(f"approval expiré: {approval.approval_id}")
        expires_at = min(expires_at, now + _APPROVAL_MAX_TTL)
        approval = replace(
            approval,
            sanitized_arguments=redact_mapping(approval.sanitized_arguments),
            risks=tuple(
                redact_text(item, max_chars=240)
                for item in (approval.risks or (_DEFAULT_APPROVAL_RISK,))
            ),
            expires_at=expires_at,
        )
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO agent_approvals (
                    approval_id, run_id, profile_id, action, tool, summary,
                    arguments_json, risks_json, scope, expires_at, decision,
                    decision_by, decision_at, decision_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval.approval_id,
                    approval.run_id,
                    selected,
                    redact_text(approval.action, max_chars=240),
                    redact_text(approval.tool, max_chars=120),
                    redact_text(approval.summary, max_chars=1000),
                    _json(redact_mapping(approval.sanitized_arguments)),
                    _json(
                        [redact_text(item, max_chars=240) for item in approval.risks]
                    ),
                    approval.scope,
                    _iso(approval.expires_at),
                    approval.decision.value,
                    approval.decision_by,
                    _iso(approval.decision_at),
                    approval.decision_id,
                    _iso(now),
                ),
            )
        return self.get_approval(approval.approval_id, profile_id=selected)  # type: ignore[return-value]

    @staticmethod
    def _row_to_approval(row: sqlite3.Row | None) -> ApprovalRequest | None:
        if row is None:
            return None
        return ApprovalRequest(
            approval_id=str(row["approval_id"]),
            run_id=str(row["run_id"]),
            action=str(row["action"]),
            tool=str(row["tool"]),
            summary=str(row["summary"]),
            sanitized_arguments=_load_json(row["arguments_json"], {}),
            risks=tuple(_load_json(row["risks_json"], [])),
            scope=str(row["scope"]),
            expires_at=_datetime(row["expires_at"]),
            decision=ApprovalDecision(row["decision"]),
            decision_by=row["decision_by"],
            decision_at=_datetime(row["decision_at"]),
            decision_id=row["decision_id"],
        )

    def get_approval(
        self,
        approval_id: str,
        *,
        profile_id: str | None = None,
    ) -> ApprovalRequest | None:
        selected = self._profile(profile_id)
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM agent_approvals WHERE approval_id = ? AND profile_id = ?",
                (approval_id, selected),
            ).fetchone()
        return self._row_to_approval(row)

    def decide_approval(
        self,
        approval_id: str,
        decision: ApprovalDecision | str,
        *,
        decided_by: str,
        decision_id: str,
        profile_id: str | None = None,
        now: datetime | None = None,
    ) -> ApprovalRequest:
        selected = self._profile(profile_id)
        if not decision_id.strip() or not decided_by.strip():
            raise ValueError("decision_id et decided_by requis")
        target = ApprovalDecision(decision)
        if target not in {ApprovalDecision.APPROVED, ApprovalDecision.DENIED}:
            raise ValueError("décision finale attendue")
        changed_at = now or datetime.now(timezone.utc)
        expired = False
        with get_db() as conn:
            existing_decision = conn.execute(
                "SELECT * FROM agent_approvals WHERE decision_id = ? AND profile_id = ?",
                (decision_id, selected),
            ).fetchone()
            if existing_decision is not None:
                replay = self._row_to_approval(existing_decision)
                if replay is None:
                    raise AgenticPersistenceConflict("décision illisible")
                if replay.approval_id != approval_id or replay.decision is not target:
                    raise AgenticPersistenceConflict(
                        "decision_id déjà utilisé avec une autre requête"
                    )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO agent_approval_outbox (
                        approval_id, run_id, profile_id, decision_id, decision,
                        status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (
                        replay.approval_id,
                        replay.run_id,
                        selected,
                        decision_id,
                        target.value,
                        _iso(changed_at),
                        _iso(changed_at),
                    ),
                )
                return replay
            row = conn.execute(
                "SELECT * FROM agent_approvals WHERE approval_id = ? AND profile_id = ?",
                (approval_id, selected),
            ).fetchone()
            approval = self._row_to_approval(row)
            if approval is None:
                raise AgenticPersistenceConflict("approval inconnu")
            if approval.decision is not ApprovalDecision.PENDING:
                raise ApprovalAlreadyDecided(approval_id)
            if approval.expires_at is not None and approval.expires_at <= changed_at:
                conn.execute(
                    "UPDATE agent_approvals SET decision = 'expired', decision_at = ? WHERE approval_id = ? AND profile_id = ? AND decision = 'pending'",
                    (_iso(changed_at), approval_id, selected),
                )
                expired = True
            else:
                cursor = conn.execute(
                    """
                    UPDATE agent_approvals
                    SET decision = ?, decision_by = ?, decision_at = ?, decision_id = ?
                    WHERE approval_id = ? AND profile_id = ? AND decision = 'pending'
                    """,
                    (
                        target.value,
                        redact_text(decided_by, max_chars=120),
                        _iso(changed_at),
                        decision_id,
                        approval_id,
                        selected,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ApprovalAlreadyDecided(approval_id)
                conn.execute(
                    """
                    INSERT INTO agent_approval_outbox (
                        approval_id, run_id, profile_id, decision_id, decision,
                        status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (
                        approval_id,
                        approval.run_id,
                        selected,
                        decision_id,
                        target.value,
                        _iso(changed_at),
                        _iso(changed_at),
                    ),
                )
        if expired:
            raise ApprovalExpired(f"approval expiré: {approval_id}")
        return self.get_approval(approval_id, profile_id=selected)  # type: ignore[return-value]

    def list_approvals(
        self,
        run_id: str,
        *,
        profile_id: str | None = None,
    ) -> list[ApprovalRequest]:
        selected = self._profile(profile_id)
        self.require_run(run_id, profile_id=selected)
        with get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_approvals WHERE run_id = ? AND profile_id = ? ORDER BY created_at",
                (run_id, selected),
            ).fetchall()
        return [
            approval
            for row in rows
            if (approval := self._row_to_approval(row)) is not None
        ]

    def expire_due_approval_requests(
        self,
        run_id: str,
        *,
        profile_id: str | None = None,
        now: datetime | None = None,
    ) -> list[ApprovalRequest]:
        """Expire atomiquement et retourne uniquement les confirmations modifiées."""

        selected = self._profile(profile_id)
        self.require_run(run_id, profile_id=selected)
        changed_at = now or datetime.now(timezone.utc)
        with get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT * FROM agent_approvals
                WHERE run_id = ? AND profile_id = ? AND decision = 'pending'
                  AND expires_at IS NOT NULL AND expires_at <= ?
                ORDER BY created_at
                """,
                (run_id, selected, _iso(changed_at)),
            ).fetchall()
            if not rows:
                return []
            cursor = conn.execute(
                """
                UPDATE agent_approvals
                SET decision = 'expired', decision_at = ?
                WHERE run_id = ? AND profile_id = ? AND decision = 'pending'
                  AND expires_at IS NOT NULL AND expires_at <= ?
                """,
                (_iso(changed_at), run_id, selected, _iso(changed_at)),
            )
            if cursor.rowcount != len(rows):
                raise AgenticPersistenceConflict(
                    "expiration concurrente des approbations"
                )
        expired: list[ApprovalRequest] = []
        for row in rows:
            approval = self._row_to_approval(row)
            if approval is not None:
                expired.append(
                    replace(
                        approval,
                        decision=ApprovalDecision.EXPIRED,
                        decision_at=changed_at,
                    )
                )
        return expired

    def expire_due_approvals(
        self,
        run_id: str,
        *,
        profile_id: str | None = None,
        now: datetime | None = None,
    ) -> int:
        """Compatibilité : retourne le nombre de confirmations expirées."""

        return len(
            self.expire_due_approval_requests(
                run_id,
                profile_id=profile_id,
                now=now,
            )
        )

    def claim_approval_delivery(
        self,
        approval_id: str,
        *,
        profile_id: str | None = None,
        now: datetime | None = None,
        lease_seconds: int = 60,
    ) -> bool:
        """Réserve une livraison outbox ; un replay livré ne repart jamais."""

        selected = self._profile(profile_id)
        changed_at = now or datetime.now(timezone.utc)
        stale_before = changed_at - timedelta(seconds=max(1, int(lease_seconds)))
        with get_db() as conn:
            row = conn.execute(
                """
                SELECT status FROM agent_approval_outbox
                WHERE approval_id = ? AND profile_id = ?
                """,
                (approval_id, selected),
            ).fetchone()
            if row is None:
                raise AgenticPersistenceConflict("livraison d'approbation absente")
            if row["status"] == "delivered":
                return False
            cursor = conn.execute(
                """
                UPDATE agent_approval_outbox
                SET status = 'delivering', attempts = attempts + 1,
                    claimed_at = ?, updated_at = ?, last_error = NULL
                WHERE approval_id = ? AND profile_id = ?
                  AND (
                      status = 'pending'
                      OR (
                          status = 'delivering'
                          AND (claimed_at IS NULL OR claimed_at <= ?)
                      )
                  )
                """,
                (
                    _iso(changed_at),
                    _iso(changed_at),
                    approval_id,
                    selected,
                    _iso(stale_before),
                ),
            )
            return cursor.rowcount == 1

    def complete_approval_delivery(
        self,
        approval_id: str,
        *,
        profile_id: str | None = None,
        now: datetime | None = None,
        claim_started_at: datetime | None = None,
    ) -> None:
        selected = self._profile(profile_id)
        changed_at = now or datetime.now(timezone.utc)
        with get_db() as conn:
            row = conn.execute(
                "SELECT status FROM agent_approval_outbox WHERE approval_id = ? AND profile_id = ?",
                (approval_id, selected),
            ).fetchone()
            if row is None:
                raise AgenticPersistenceConflict("livraison d'approbation absente")
            if row["status"] == "delivered":
                return
            claim_guard = " AND claimed_at = ?" if claim_started_at is not None else ""
            params: tuple[Any, ...] = (
                _iso(changed_at),
                _iso(changed_at),
                approval_id,
                selected,
            )
            if claim_started_at is not None:
                params = (*params, _iso(claim_started_at))
            cursor = conn.execute(
                f"""
                UPDATE agent_approval_outbox
                SET status = 'delivered', delivered_at = ?, updated_at = ?
                WHERE approval_id = ? AND profile_id = ? AND status = 'delivering'
                {claim_guard}
                """,
                params,
            )
            if cursor.rowcount != 1:
                raise AgenticPersistenceConflict(
                    "livraison d'approbation non réservable"
                )

    def release_approval_delivery(
        self,
        approval_id: str,
        *,
        error: str,
        profile_id: str | None = None,
        now: datetime | None = None,
        claim_started_at: datetime | None = None,
    ) -> None:
        selected = self._profile(profile_id)
        changed_at = now or datetime.now(timezone.utc)
        with get_db() as conn:
            claim_guard = " AND claimed_at = ?" if claim_started_at is not None else ""
            params: tuple[Any, ...] = (
                _iso(changed_at),
                redact_text(error, max_chars=500),
                approval_id,
                selected,
            )
            if claim_started_at is not None:
                params = (*params, _iso(claim_started_at))
            conn.execute(
                f"""
                UPDATE agent_approval_outbox
                SET status = 'pending', claimed_at = NULL, updated_at = ?, last_error = ?
                WHERE approval_id = ? AND profile_id = ? AND status = 'delivering'
                {claim_guard}
                """,
                params,
            )

    def approval_delivery_status(
        self,
        approval_id: str,
        *,
        profile_id: str | None = None,
    ) -> str | None:
        selected = self._profile(profile_id)
        with get_db() as conn:
            row = conn.execute(
                "SELECT status FROM agent_approval_outbox WHERE approval_id = ? AND profile_id = ?",
                (approval_id, selected),
            ).fetchone()
        return str(row["status"]) if row is not None else None

    def add_artifact(
        self,
        artifact: Artifact,
        *,
        profile_id: str | None = None,
    ) -> tuple[Artifact, bool]:
        selected = self._profile(profile_id)
        self.require_run(artifact.run_id, profile_id=selected)
        safe = replace(artifact, metadata=redact_mapping(artifact.metadata))
        if safe.type in {"jarvis_test_receipt", "jarvis_effect_receipt"}:
            encoded = _json(dict(safe.metadata)).encode("utf-8")
            safe = replace(
                safe,
                sha256=hashlib.sha256(encoded).hexdigest(),
                size_bytes=len(encoded),
            )
        with get_db() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO agent_artifacts (
                    artifact_id, run_id, profile_id, artifact_type, reference,
                    sha256, size_bytes, metadata_json, visibility, retention,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    safe.artifact_id,
                    safe.run_id,
                    selected,
                    safe.type,
                    safe.reference,
                    safe.sha256,
                    safe.size_bytes,
                    _json(dict(safe.metadata)),
                    safe.visibility,
                    safe.retention,
                    _iso(datetime.now(timezone.utc)),
                ),
            )
        return safe, cursor.rowcount == 1

    def list_artifacts(
        self,
        run_id: str,
        *,
        profile_id: str | None = None,
    ) -> list[Artifact]:
        selected = self._profile(profile_id)
        self.require_run(run_id, profile_id=selected)
        with get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_artifacts WHERE run_id = ? AND profile_id = ? ORDER BY created_at",
                (run_id, selected),
            ).fetchall()
        return [
            Artifact(
                artifact_id=str(row["artifact_id"]),
                run_id=str(row["run_id"]),
                type=str(row["artifact_type"]),
                reference=str(row["reference"]),
                sha256=row["sha256"],
                size_bytes=row["size_bytes"],
                metadata=_load_json(row["metadata_json"], {}),
                visibility=str(row["visibility"]),
                retention=str(row["retention"]),
            )
            for row in rows
        ]

    def record_step(
        self,
        *,
        step_id: str,
        run_id: str,
        sequence: int,
        title: str,
        status: str,
        error_code: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        profile_id: str | None = None,
    ) -> dict[str, Any]:
        selected = self._profile(profile_id)
        self.require_run(run_id, profile_id=selected)
        now = datetime.now(timezone.utc)
        finished_at = now if status in {"completed", "failed", "blocked"} else None
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO agent_steps (
                    step_id, run_id, profile_id, sequence, title, status,
                    started_at, finished_at, error_code, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(step_id) DO UPDATE SET
                    status = excluded.status,
                    finished_at = excluded.finished_at,
                    error_code = excluded.error_code,
                    metadata_json = excluded.metadata_json
                WHERE agent_steps.run_id = excluded.run_id
                  AND agent_steps.profile_id = excluded.profile_id
                """,
                (
                    step_id,
                    run_id,
                    selected,
                    int(sequence),
                    redact_text(title, max_chars=240),
                    redact_text(status, max_chars=40),
                    _iso(now),
                    _iso(finished_at),
                    redact_text(error_code, max_chars=120) if error_code else None,
                    _json(redact_mapping(metadata)),
                ),
            )
            row = conn.execute(
                "SELECT * FROM agent_steps WHERE step_id = ? AND run_id = ? AND profile_id = ?",
                (step_id, run_id, selected),
            ).fetchone()
        if row is None:
            raise AgenticPersistenceConflict("step cross-run ou cross-profile")
        result = dict(row)
        result["metadata"] = _load_json(result.pop("metadata_json"), {})
        return result

    def grant_capability(
        self,
        *,
        grant_id: str,
        run_id: str,
        capability: str,
        scope: str,
        granted_by: str,
        expires_at: datetime | None = None,
        metadata: Mapping[str, Any] | None = None,
        profile_id: str | None = None,
    ) -> bool:
        selected = self._profile(profile_id)
        self.require_run(run_id, profile_id=selected)
        with get_db() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO agent_capability_grants (
                    grant_id, run_id, profile_id, capability, scope, granted_by,
                    expires_at, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    grant_id,
                    run_id,
                    selected,
                    redact_text(capability, max_chars=120),
                    redact_text(scope, max_chars=240),
                    redact_text(granted_by, max_chars=120),
                    _iso(expires_at),
                    _json(redact_mapping(metadata)),
                    _iso(datetime.now(timezone.utc)),
                ),
            )
        return cursor.rowcount == 1

    def list_capability_grants(
        self,
        run_id: str,
        *,
        active_only: bool = True,
        profile_id: str | None = None,
    ) -> list[dict[str, Any]]:
        selected = self._profile(profile_id)
        self.require_run(run_id, profile_id=selected)
        where = "run_id = ? AND profile_id = ?"
        params: list[Any] = [run_id, selected]
        if active_only:
            where += (
                " AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > ?)"
            )
            params.append(_iso(datetime.now(timezone.utc)))
        with get_db() as conn:
            rows = conn.execute(
                f"SELECT * FROM agent_capability_grants WHERE {where} ORDER BY created_at",
                params,
            ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["metadata"] = _load_json(item.pop("metadata_json"), {})
            results.append(item)
        return results

    def revoke_capability(
        self,
        grant_id: str,
        *,
        profile_id: str | None = None,
    ) -> bool:
        selected = self._profile(profile_id)
        with get_db() as conn:
            cursor = conn.execute(
                """
                UPDATE agent_capability_grants
                SET revoked_at = ?
                WHERE grant_id = ? AND profile_id = ? AND revoked_at IS NULL
                """,
                (_iso(datetime.now(timezone.utc)), grant_id, selected),
            )
        return cursor.rowcount == 1

    def save_checkpoint(
        self,
        *,
        checkpoint_id: str,
        run_id: str,
        sequence: int,
        state: Mapping[str, Any],
        checksum: str,
        profile_id: str | None = None,
    ) -> bool:
        selected = self._profile(profile_id)
        self.require_run(run_id, profile_id=selected)
        with get_db() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO agent_checkpoints (
                    checkpoint_id, run_id, profile_id, sequence, state_json,
                    checksum, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint_id,
                    run_id,
                    selected,
                    int(sequence),
                    _json(redact_mapping(state)),
                    checksum,
                    _iso(datetime.now(timezone.utc)),
                ),
            )
        return cursor.rowcount == 1

    def record_metric(
        self,
        *,
        run_id: str,
        metric: str,
        value: float,
        unit: str = "",
        metadata: Mapping[str, Any] | None = None,
        profile_id: str | None = None,
    ) -> None:
        selected = self._profile(profile_id)
        self.require_run(run_id, profile_id=selected)
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO agent_metrics (
                    run_id, profile_id, metric, value, unit, metadata_json, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    selected,
                    redact_text(metric, max_chars=120),
                    float(value),
                    redact_text(unit, max_chars=40),
                    _json(redact_mapping(metadata)),
                    _iso(datetime.now(timezone.utc)),
                ),
            )

    def observability_summary(
        self,
        *,
        profile_id: str | None = None,
    ) -> dict[str, Any]:
        """Agrégats bornés et sans contenu utilisateur pour le statut système."""

        selected = self._profile(profile_id)
        with get_db() as conn:
            run_rows = conn.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM agent_runs
                WHERE profile_id = ?
                GROUP BY status
                """,
                (selected,),
            ).fetchall()
            event_rows = conn.execute(
                """
                SELECT event_type, COUNT(*) AS count
                FROM agent_events
                WHERE profile_id = ?
                GROUP BY event_type
                """,
                (selected,),
            ).fetchall()
            approval_rows = conn.execute(
                """
                SELECT decision, COUNT(*) AS count
                FROM agent_approvals
                WHERE profile_id = ?
                GROUP BY decision
                """,
                (selected,),
            ).fetchall()
            metric_rows = conn.execute(
                """
                SELECT metric, COUNT(*) AS samples, SUM(value) AS total,
                       AVG(value) AS average, unit
                FROM agent_metrics
                WHERE profile_id = ?
                GROUP BY metric, unit
                ORDER BY metric
                """,
                (selected,),
            ).fetchall()
            duration = conn.execute(
                """
                SELECT AVG((julianday(finished_at) - julianday(started_at)) * 86400.0)
                FROM agent_runs
                WHERE profile_id = ? AND started_at IS NOT NULL AND finished_at IS NOT NULL
                """,
                (selected,),
            ).fetchone()
        return {
            "runs_by_status": {row["status"]: int(row["count"]) for row in run_rows},
            "events_by_type": {
                row["event_type"]: int(row["count"]) for row in event_rows
            },
            "approvals_by_decision": {
                row["decision"]: int(row["count"]) for row in approval_rows
            },
            "metrics": {
                row["metric"]: {
                    "samples": int(row["samples"]),
                    "total": float(row["total"] or 0.0),
                    "average": float(row["average"] or 0.0),
                    "unit": row["unit"],
                }
                for row in metric_rows
            },
            "average_duration_seconds": float(duration[0] or 0.0),
        }


def migrate_agentic_tables(conn: sqlite3.Connection) -> None:
    """Migration idempotente pour bases existantes."""

    conn.executescript(AGENTIC_SCHEMA)
    columns = {
        str(row["name"] if isinstance(row, sqlite3.Row) else row[1])
        for row in conn.execute("PRAGMA table_info(agent_runs)").fetchall()
    }
    if "idempotency_digest" not in columns:
        conn.execute("ALTER TABLE agent_runs ADD COLUMN idempotency_digest TEXT")
    rows = conn.execute(
        """
        SELECT * FROM agent_runs
        WHERE idempotency_key IS NOT NULL AND idempotency_digest IS NULL
        """
    ).fetchall()
    for row in rows:
        run = _row_to_run(row)
        if run is None:
            continue
        conn.execute(
            "UPDATE agent_runs SET idempotency_digest = ? WHERE run_id = ?",
            (canonical_run_request_digest(run), run.run_id),
        )


__all__ = [
    "AGENTIC_SCHEMA",
    "AgenticIdempotencyConflict",
    "AgenticPersistenceConflict",
    "AgenticPersistenceError",
    "AgenticRepository",
    "AgenticRunNotFound",
    "ApprovalAlreadyDecided",
    "ApprovalExpired",
    "migrate_agentic_tables",
]
