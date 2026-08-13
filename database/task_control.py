"""Persistance SQLite du pilotage de tâches (tâches, plans, activité, rapports).

Isolée par profil comme le reste du domaine agentique. Deux invariants sont
tenus ici plutôt que dans le service, parce qu'ils doivent survivre à un
appelant distrait ou à deux requêtes simultanées :

* une seule version de plan par ``(task_id, version)`` — index unique ;
* l'approbation d'un plan est une écriture conditionnelle qui échoue si la
  tâche a bougé entre la lecture et l'écriture (pas de « dernière écriture
  gagne » sur une décision qui autorise à dépenser du travail réel).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from typing import Any, Iterable

from jarvis.agentic.redaction import redact_text
from jarvis.task_control.models import (
    CandidateDecision,
    ControlTask,
    PlanDecision,
    PlanStep,
    TaskActivity,
    TaskActivityLevel,
    TaskActivityType,
    TaskCandidate,
    TaskPlan,
    TaskPriority,
    TaskReport,
    TaskSource,
    TaskSourceChannel,
    TaskSourceType,
    TaskStatus,
    validate_task_transition,
)

from . import dbapi as sqlite3
from .core import current_profile_id, get_db, normalize_profile_id


class TaskControlError(RuntimeError):
    """Erreur de persistance du pilotage de tâches."""


class TaskNotFound(TaskControlError):
    pass


class PlanNotFound(TaskControlError):
    pass


class TaskPersistenceConflict(TaskControlError):
    """Écriture concurrente : l'état lu n'est plus celui de la base."""


TASK_CONTROL_SCHEMA = """
CREATE TABLE IF NOT EXISTS control_tasks (
    task_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'medium',
    source_type TEXT NOT NULL DEFAULT 'manual',
    source_channel TEXT NOT NULL DEFAULT 'api',
    source_reference TEXT NOT NULL DEFAULT '',
    source_excerpt TEXT NOT NULL DEFAULT '',
    source_confidence REAL,
    source_json TEXT NOT NULL DEFAULT '{}',
    project_id TEXT,
    conversation_id TEXT,
    due_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    plan_id TEXT,
    plan_version INTEGER,
    approved_plan_version INTEGER,
    approved_plan_digest TEXT,
    agentic_run_id TEXT,
    current_phase TEXT NOT NULL DEFAULT '',
    progress REAL NOT NULL DEFAULT 0,
    attention_required INTEGER NOT NULL DEFAULT 0,
    result_status TEXT,
    final_report_id TEXT,
    legacy_task_id INTEGER,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_control_tasks_profile_status
    ON control_tasks(profile_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_control_tasks_run
    ON control_tasks(agentic_run_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_control_tasks_legacy
    ON control_tasks(profile_id, legacy_task_id)
    WHERE legacy_task_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS control_task_plans (
    plan_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES control_tasks(task_id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    objective TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    context_understood TEXT NOT NULL DEFAULT '',
    steps_json TEXT NOT NULL DEFAULT '[]',
    deliverables_json TEXT NOT NULL DEFAULT '[]',
    tools_json TEXT NOT NULL DEFAULT '[]',
    permissions_json TEXT NOT NULL DEFAULT '[]',
    risks_json TEXT NOT NULL DEFAULT '[]',
    assumptions_json TEXT NOT NULL DEFAULT '[]',
    success_criteria_json TEXT NOT NULL DEFAULT '[]',
    known_limits_json TEXT NOT NULL DEFAULT '[]',
    estimated_duration_s INTEGER,
    estimated_cost REAL,
    created_by TEXT NOT NULL DEFAULT 'jarvis.planner',
    created_at TEXT NOT NULL,
    decision TEXT NOT NULL DEFAULT 'pending',
    decision_at TEXT,
    decision_by TEXT,
    decision_comment TEXT NOT NULL DEFAULT '',
    digest TEXT NOT NULL,
    UNIQUE(task_id, version)
);
CREATE INDEX IF NOT EXISTS idx_control_task_plans_task
    ON control_task_plans(profile_id, task_id, version DESC);

CREATE TABLE IF NOT EXISTS control_task_activity (
    activity_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES control_tasks(task_id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL,
    run_id TEXT,
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    agent_id TEXT NOT NULL DEFAULT '',
    agent_role TEXT NOT NULL DEFAULT '',
    phase TEXT NOT NULL DEFAULT '',
    tool_name TEXT NOT NULL DEFAULT '',
    artifact_reference TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    level TEXT NOT NULL DEFAULT 'detail',
    created_at TEXT NOT NULL,
    UNIQUE(task_id, sequence)
);
CREATE INDEX IF NOT EXISTS idx_control_task_activity_task
    ON control_task_activity(profile_id, task_id, sequence);

CREATE TABLE IF NOT EXISTS control_task_candidates (
    candidate_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    suggested_title TEXT NOT NULL,
    suggested_description TEXT NOT NULL DEFAULT '',
    source_json TEXT NOT NULL DEFAULT '{}',
    confidence REAL NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    suggested_due_at TEXT,
    decision TEXT NOT NULL DEFAULT 'pending',
    decision_at TEXT,
    created_task_id TEXT,
    duplicate_of TEXT,
    dedupe_key TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_control_task_candidates_dedupe
    ON control_task_candidates(profile_id, dedupe_key)
    WHERE dedupe_key <> '';
CREATE INDEX IF NOT EXISTS idx_control_task_candidates_decision
    ON control_task_candidates(profile_id, decision, created_at DESC);

CREATE TABLE IF NOT EXISTS control_task_reports (
    report_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES control_tasks(task_id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    result_status TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    markdown TEXT NOT NULL DEFAULT '',
    data_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(task_id, version)
);

CREATE TABLE IF NOT EXISTS control_task_comments (
    comment_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES control_tasks(task_id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT 'user',
    body TEXT NOT NULL,
    run_id TEXT,
    plan_version INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_control_task_comments_task
    ON control_task_comments(profile_id, task_id, created_at);
"""


# ── Sérialisation ──────────────────────────────────────────────────────────


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _source_from_row(row: sqlite3.Row) -> TaskSource:
    payload = _loads(row["source_json"], {})
    try:
        source_type = TaskSourceType(row["source_type"])
    except ValueError:
        source_type = TaskSourceType.MANUAL
    try:
        channel = TaskSourceChannel(row["source_channel"])
    except ValueError:
        channel = TaskSourceChannel.API
    return TaskSource(
        source_type=source_type,
        channel=channel,
        reference=row["source_reference"] or "",
        excerpt=row["source_excerpt"] or "",
        confidence=row["source_confidence"],
        detection_reason=str(payload.get("detection_reason") or ""),
        sender=str(payload.get("sender") or ""),
        subject=str(payload.get("subject") or ""),
        occurred_at=_dt(payload.get("occurred_at")),
    )


def _row_to_task(row: sqlite3.Row) -> ControlTask:
    return ControlTask(
        task_id=row["task_id"],
        profile_id=row["profile_id"],
        title=row["title"],
        description=row["description"] or "",
        status=TaskStatus(row["status"]),
        priority=TaskPriority(row["priority"]),
        source=_source_from_row(row),
        project_id=row["project_id"],
        conversation_id=row["conversation_id"],
        due_at=_dt(row["due_at"]),
        created_at=_dt(row["created_at"]) or datetime.now(timezone.utc),
        updated_at=_dt(row["updated_at"]) or datetime.now(timezone.utc),
        plan_id=row["plan_id"],
        plan_version=row["plan_version"],
        approved_plan_version=row["approved_plan_version"],
        approved_plan_digest=row["approved_plan_digest"],
        agentic_run_id=row["agentic_run_id"],
        current_phase=row["current_phase"] or "",
        progress=float(row["progress"] or 0.0),
        attention_required=bool(row["attention_required"]),
        result_status=row["result_status"],
        final_report_id=row["final_report_id"],
        legacy_task_id=row["legacy_task_id"],
        metadata=_loads(row["metadata_json"], {}),
    )


def _row_to_plan(row: sqlite3.Row) -> TaskPlan:
    steps = tuple(
        PlanStep(
            index=int(item.get("index", position + 1)),
            title=str(item.get("title") or ""),
            detail=str(item.get("detail") or ""),
            expected_result=str(item.get("expected_result") or ""),
            tools=tuple(item.get("tools") or ()),
            permissions=tuple(item.get("permissions") or ()),
        )
        for position, item in enumerate(_loads(row["steps_json"], []))
        if str(item.get("title") or "").strip()
    )
    return TaskPlan(
        plan_id=row["plan_id"],
        task_id=row["task_id"],
        version=int(row["version"]),
        objective=row["objective"],
        summary=row["summary"] or "",
        context_understood=row["context_understood"] or "",
        steps=steps,
        expected_deliverables=tuple(_loads(row["deliverables_json"], [])),
        tools_expected=tuple(_loads(row["tools_json"], [])),
        permissions_expected=tuple(_loads(row["permissions_json"], [])),
        risks=tuple(_loads(row["risks_json"], [])),
        assumptions=tuple(_loads(row["assumptions_json"], [])),
        success_criteria=tuple(_loads(row["success_criteria_json"], [])),
        known_limits=tuple(_loads(row["known_limits_json"], [])),
        estimated_duration_s=row["estimated_duration_s"],
        estimated_cost=row["estimated_cost"],
        created_by=row["created_by"],
        created_at=_dt(row["created_at"]) or datetime.now(timezone.utc),
        decision=PlanDecision(row["decision"]),
        decision_at=_dt(row["decision_at"]),
        decision_by=row["decision_by"],
        decision_comment=row["decision_comment"] or "",
        digest=row["digest"],
    )


def _row_to_activity(row: sqlite3.Row) -> TaskActivity:
    return TaskActivity(
        activity_id=row["activity_id"],
        task_id=row["task_id"],
        run_id=row["run_id"],
        sequence=int(row["sequence"]),
        event_type=TaskActivityType(row["event_type"]),
        summary=row["summary"] or "",
        agent_id=row["agent_id"] or "",
        agent_role=row["agent_role"] or "",
        phase=row["phase"] or "",
        tool_name=row["tool_name"] or "",
        artifact_reference=row["artifact_reference"] or "",
        status=row["status"] or "",
        level=TaskActivityLevel(row["level"]),
        created_at=_dt(row["created_at"]) or datetime.now(timezone.utc),
    )


def _row_to_candidate(row: sqlite3.Row) -> TaskCandidate:
    payload = _loads(row["source_json"], {})
    return TaskCandidate(
        candidate_id=row["candidate_id"],
        profile_id=row["profile_id"],
        suggested_title=row["suggested_title"],
        suggested_description=row["suggested_description"] or "",
        source=TaskSource(
            source_type=TaskSourceType(payload.get("source_type", "message")),
            channel=TaskSourceChannel(payload.get("channel", "api")),
            reference=str(payload.get("reference") or ""),
            excerpt=str(payload.get("excerpt") or ""),
            confidence=payload.get("confidence"),
            detection_reason=str(payload.get("detection_reason") or ""),
            sender=str(payload.get("sender") or ""),
            subject=str(payload.get("subject") or ""),
            occurred_at=_dt(payload.get("occurred_at")),
        ),
        confidence=float(row["confidence"] or 0.0),
        reason=row["reason"] or "",
        suggested_due_at=_dt(row["suggested_due_at"]),
        decision=CandidateDecision(row["decision"]),
        decision_at=_dt(row["decision_at"]),
        created_task_id=row["created_task_id"],
        duplicate_of=row["duplicate_of"],
        dedupe_key=row["dedupe_key"] or "",
        created_at=_dt(row["created_at"]) or datetime.now(timezone.utc),
    )


def _row_to_report(row: sqlite3.Row) -> TaskReport:
    return TaskReport(
        report_id=row["report_id"],
        task_id=row["task_id"],
        version=int(row["version"]),
        result_status=row["result_status"],
        markdown=row["markdown"] or "",
        summary=row["summary"] or "",
        data=_loads(row["data_json"], {}),
        created_at=_dt(row["created_at"]) or datetime.now(timezone.utc),
    )


# ── Repository ─────────────────────────────────────────────────────────────


class TaskControlRepository:
    """Repository transactionnel, borné au profil courant."""

    def _profile(self, profile_id: str | None = None) -> str:
        return normalize_profile_id(profile_id or current_profile_id())

    # ── Tâches ────────────────────────────────────────────────────────────

    def create_task(self, task: ControlTask) -> ControlTask:
        profile_id = self._profile(task.profile_id)
        if profile_id != self._profile(None):
            raise TaskPersistenceConflict("création cross-profile interdite")
        task = replace(
            task,
            profile_id=profile_id,
            title=redact_text(task.title, max_chars=300),
            description=redact_text(task.description, max_chars=8_000),
        )
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO control_tasks (
                    task_id, profile_id, title, description, status, priority,
                    source_type, source_channel, source_reference, source_excerpt,
                    source_confidence, source_json, project_id, conversation_id,
                    due_at, created_at, updated_at, plan_id, plan_version,
                    approved_plan_version, approved_plan_digest, agentic_run_id,
                    current_phase, progress, attention_required, result_status,
                    final_report_id, legacy_task_id, metadata_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    task.task_id,
                    profile_id,
                    task.title,
                    task.description,
                    task.status.value,
                    task.priority.value,
                    task.source.source_type.value,
                    task.source.channel.value,
                    task.source.reference,
                    task.source.excerpt,
                    task.source.confidence,
                    _dumps(task.source.to_dict()),
                    task.project_id,
                    task.conversation_id,
                    _iso(task.due_at),
                    _iso(task.created_at),
                    _iso(task.updated_at),
                    task.plan_id,
                    task.plan_version,
                    task.approved_plan_version,
                    task.approved_plan_digest,
                    task.agentic_run_id,
                    task.current_phase,
                    task.progress,
                    int(task.attention_required),
                    task.result_status,
                    task.final_report_id,
                    task.legacy_task_id,
                    _dumps(dict(task.metadata)),
                ),
            )
        return task

    def get_task(
        self, task_id: str, *, profile_id: str | None = None
    ) -> ControlTask | None:
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM control_tasks WHERE task_id = ? AND profile_id = ?",
                (task_id, self._profile(profile_id)),
            ).fetchone()
        return _row_to_task(row) if row is not None else None

    def require_task(
        self, task_id: str, *, profile_id: str | None = None
    ) -> ControlTask:
        task = self.get_task(task_id, profile_id=profile_id)
        if task is None:
            raise TaskNotFound(task_id)
        return task

    def find_task_by_run(self, run_id: str) -> ControlTask | None:
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM control_tasks WHERE agentic_run_id = ? AND profile_id = ?",
                (run_id, self._profile(None)),
            ).fetchone()
        return _row_to_task(row) if row is not None else None

    def list_tasks(
        self,
        *,
        statuses: Iterable[TaskStatus | str] | None = None,
        attention_only: bool = False,
        limit: int = 200,
        offset: int = 0,
        profile_id: str | None = None,
    ) -> list[ControlTask]:
        clauses = ["profile_id = ?"]
        params: list[Any] = [self._profile(profile_id)]
        if statuses is not None:
            values = [TaskStatus(item).value for item in statuses]
            if not values:
                return []
            clauses.append(f"status IN ({','.join('?' * len(values))})")
            params.extend(values)
        if attention_only:
            attention_states = ",".join(
                "?" for _ in ("awaiting_plan_approval", "awaiting_permission", "blocked")
            )
            clauses.append(
                f"(attention_required = 1 OR status IN ({attention_states}))"
            )
            params.extend(["awaiting_plan_approval", "awaiting_permission", "blocked"])
        params.extend([max(1, min(int(limit), 500)), max(0, int(offset))])
        with get_db() as conn:
            rows = conn.execute(
                f"SELECT * FROM control_tasks WHERE {' AND '.join(clauses)} "
                "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        return [_row_to_task(row) for row in rows]

    def count_by_status(self, *, profile_id: str | None = None) -> dict[str, int]:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS total FROM control_tasks "
                "WHERE profile_id = ? GROUP BY status",
                (self._profile(profile_id),),
            ).fetchall()
        return {row["status"]: int(row["total"]) for row in rows}

    def update_task(
        self,
        task_id: str,
        *,
        expected_status: TaskStatus | None = None,
        status: TaskStatus | None = None,
        **fields: Any,
    ) -> ControlTask:
        """Écriture conditionnelle : refuse si l'état lu n'est plus celui de la base.

        ``expected_status`` est la protection contre deux décisions simultanées
        (deux fenêtres macOS, un clic et une commande vocale). Sans elle, la
        seconde écraserait silencieusement la première.
        """

        profile_id = self._profile(None)
        allowed = {
            "title",
            "description",
            "priority",
            "project_id",
            "conversation_id",
            "due_at",
            "plan_id",
            "plan_version",
            "approved_plan_version",
            "approved_plan_digest",
            "agentic_run_id",
            "current_phase",
            "progress",
            "attention_required",
            "result_status",
            "final_report_id",
            "metadata",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise TaskControlError(f"champs non modifiables: {sorted(unknown)}")

        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM control_tasks WHERE task_id = ? AND profile_id = ?",
                (task_id, profile_id),
            ).fetchone()
            if row is None:
                raise TaskNotFound(task_id)
            current = _row_to_task(row)
            if expected_status is not None and current.status is not expected_status:
                raise TaskPersistenceConflict(
                    f"état attendu {expected_status.value}, trouvé {current.status.value}"
                )
            assignments: list[str] = []
            params: list[Any] = []
            if status is not None:
                validate_task_transition(current.status, status)
                assignments.append("status = ?")
                params.append(TaskStatus(status).value)
            for key, value in fields.items():
                if key == "due_at":
                    assignments.append("due_at = ?")
                    params.append(_iso(value))
                elif key == "priority":
                    assignments.append("priority = ?")
                    params.append(TaskPriority(value).value)
                elif key == "attention_required":
                    assignments.append("attention_required = ?")
                    params.append(int(bool(value)))
                elif key == "metadata":
                    assignments.append("metadata_json = ?")
                    params.append(_dumps(dict(value or {})))
                elif key in {"title", "description"}:
                    assignments.append(f"{key} = ?")
                    params.append(redact_text(value, max_chars=8_000))
                else:
                    assignments.append(f"{key} = ?")
                    params.append(value)
            assignments.append("updated_at = ?")
            params.append(_iso(datetime.now(timezone.utc)))
            params.extend([task_id, profile_id])
            conn.execute(
                f"UPDATE control_tasks SET {', '.join(assignments)} "
                "WHERE task_id = ? AND profile_id = ?",
                params,
            )
            updated = conn.execute(
                "SELECT * FROM control_tasks WHERE task_id = ? AND profile_id = ?",
                (task_id, profile_id),
            ).fetchone()
        return _row_to_task(updated)

    # ── Plans ─────────────────────────────────────────────────────────────

    def next_plan_version(self, task_id: str) -> int:
        with get_db() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) AS top FROM control_task_plans "
                "WHERE task_id = ? AND profile_id = ?",
                (task_id, self._profile(None)),
            ).fetchone()
        return int(row["top"]) + 1

    def save_plan(self, plan: TaskPlan) -> TaskPlan:
        profile_id = self._profile(None)
        with get_db() as conn:
            conn.execute(
                """
                UPDATE control_task_plans SET decision = 'superseded'
                WHERE task_id = ? AND profile_id = ? AND decision = 'pending'
                """,
                (plan.task_id, profile_id),
            )
            conn.execute(
                """
                INSERT INTO control_task_plans (
                    plan_id, task_id, profile_id, version, objective, summary,
                    context_understood, steps_json, deliverables_json, tools_json,
                    permissions_json, risks_json, assumptions_json,
                    success_criteria_json, known_limits_json, estimated_duration_s,
                    estimated_cost, created_by, created_at, decision, decision_at,
                    decision_by, decision_comment, digest
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    plan.plan_id,
                    plan.task_id,
                    profile_id,
                    plan.version,
                    plan.objective,
                    plan.summary,
                    plan.context_understood,
                    _dumps([step.to_dict() for step in plan.steps]),
                    _dumps(list(plan.expected_deliverables)),
                    _dumps(list(plan.tools_expected)),
                    _dumps(list(plan.permissions_expected)),
                    _dumps(list(plan.risks)),
                    _dumps(list(plan.assumptions)),
                    _dumps(list(plan.success_criteria)),
                    _dumps(list(plan.known_limits)),
                    plan.estimated_duration_s,
                    plan.estimated_cost,
                    plan.created_by,
                    _iso(plan.created_at),
                    plan.decision.value,
                    _iso(plan.decision_at),
                    plan.decision_by,
                    plan.decision_comment,
                    plan.digest,
                ),
            )
        return plan

    def get_plan(self, task_id: str, version: int) -> TaskPlan | None:
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM control_task_plans "
                "WHERE task_id = ? AND profile_id = ? AND version = ?",
                (task_id, self._profile(None), int(version)),
            ).fetchone()
        return _row_to_plan(row) if row is not None else None

    def get_plan_by_id(self, plan_id: str) -> TaskPlan | None:
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM control_task_plans WHERE plan_id = ? AND profile_id = ?",
                (plan_id, self._profile(None)),
            ).fetchone()
        return _row_to_plan(row) if row is not None else None

    def list_plans(self, task_id: str) -> list[TaskPlan]:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM control_task_plans WHERE task_id = ? AND profile_id = ? "
                "ORDER BY version DESC",
                (task_id, self._profile(None)),
            ).fetchall()
        return [_row_to_plan(row) for row in rows]

    def decide_plan(
        self,
        task_id: str,
        version: int,
        *,
        decision: PlanDecision,
        actor: str,
        comment: str = "",
    ) -> TaskPlan:
        """Consomme une décision de plan une seule fois.

        Une décision déjà prise n'est jamais réécrite : rejouer la requête
        renvoie la décision existante plutôt que d'en fabriquer une seconde.
        """

        profile_id = self._profile(None)
        now = datetime.now(timezone.utc)
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM control_task_plans "
                "WHERE task_id = ? AND profile_id = ? AND version = ?",
                (task_id, profile_id, int(version)),
            ).fetchone()
            if row is None:
                raise PlanNotFound(f"{task_id}#{version}")
            existing = _row_to_plan(row)
            if existing.decision is not PlanDecision.PENDING:
                if existing.decision is decision:
                    return existing
                raise TaskPersistenceConflict(
                    f"plan déjà décidé ({existing.decision.value})"
                )
            cursor = conn.execute(
                """
                UPDATE control_task_plans
                SET decision = ?, decision_at = ?, decision_by = ?, decision_comment = ?
                WHERE plan_id = ? AND profile_id = ? AND decision = 'pending'
                """,
                (
                    decision.value,
                    _iso(now),
                    redact_text(actor, max_chars=120),
                    redact_text(comment, max_chars=1_000),
                    existing.plan_id,
                    profile_id,
                ),
            )
            if cursor.rowcount != 1:
                raise TaskPersistenceConflict("décision concurrente sur ce plan")
            refreshed = conn.execute(
                "SELECT * FROM control_task_plans WHERE plan_id = ? AND profile_id = ?",
                (existing.plan_id, profile_id),
            ).fetchone()
        return _row_to_plan(refreshed)

    # ── Activité ──────────────────────────────────────────────────────────

    def append_activity(self, activity: TaskActivity) -> TaskActivity:
        """Ajoute une entrée en attribuant un rang monotone par tâche.

        L'``UNIQUE(task_id, sequence)`` fait de la relecture d'un même
        événement runtime un no-op plutôt qu'un doublon.
        """

        profile_id = self._profile(None)
        with get_db() as conn:
            if activity.sequence <= 0:
                row = conn.execute(
                    "SELECT COALESCE(MAX(sequence), 0) AS top FROM control_task_activity "
                    "WHERE task_id = ? AND profile_id = ?",
                    (activity.task_id, profile_id),
                ).fetchone()
                activity = replace(activity, sequence=int(row["top"]) + 1)
            try:
                conn.execute(
                    """
                    INSERT INTO control_task_activity (
                        activity_id, task_id, profile_id, run_id, sequence,
                        event_type, summary, agent_id, agent_role, phase,
                        tool_name, artifact_reference, status, level, created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        activity.activity_id,
                        activity.task_id,
                        profile_id,
                        activity.run_id,
                        activity.sequence,
                        activity.event_type.value,
                        activity.summary,
                        activity.agent_id,
                        activity.agent_role,
                        activity.phase,
                        activity.tool_name,
                        activity.artifact_reference,
                        activity.status,
                        activity.level.value,
                        _iso(activity.created_at),
                    ),
                )
            except sqlite3.IntegrityError:
                existing = conn.execute(
                    "SELECT * FROM control_task_activity "
                    "WHERE activity_id = ? AND profile_id = ?",
                    (activity.activity_id, profile_id),
                ).fetchone()
                if existing is not None:
                    return _row_to_activity(existing)
                raise
        return activity

    def list_activity(
        self,
        task_id: str,
        *,
        after_sequence: int = 0,
        levels: Iterable[TaskActivityLevel | str] | None = None,
        limit: int = 500,
    ) -> list[TaskActivity]:
        clauses = ["task_id = ?", "profile_id = ?", "sequence > ?"]
        params: list[Any] = [task_id, self._profile(None), int(after_sequence)]
        if levels is not None:
            values = [TaskActivityLevel(item).value for item in levels]
            if not values:
                return []
            clauses.append(f"level IN ({','.join('?' * len(values))})")
            params.extend(values)
        params.append(max(1, min(int(limit), 2_000)))
        with get_db() as conn:
            rows = conn.execute(
                f"SELECT * FROM control_task_activity WHERE {' AND '.join(clauses)} "
                "ORDER BY sequence ASC LIMIT ?",
                params,
            ).fetchall()
        return [_row_to_activity(row) for row in rows]

    # ── Candidats ─────────────────────────────────────────────────────────

    def create_candidate(self, candidate: TaskCandidate) -> tuple[TaskCandidate, bool]:
        """Retourne ``(candidat, créé)`` — la déduplication est une non-création."""

        profile_id = self._profile(candidate.profile_id)
        with get_db() as conn:
            if candidate.dedupe_key:
                existing = conn.execute(
                    "SELECT * FROM control_task_candidates "
                    "WHERE profile_id = ? AND dedupe_key = ?",
                    (profile_id, candidate.dedupe_key),
                ).fetchone()
                if existing is not None:
                    return _row_to_candidate(existing), False
            conn.execute(
                """
                INSERT INTO control_task_candidates (
                    candidate_id, profile_id, suggested_title, suggested_description,
                    source_json, confidence, reason, suggested_due_at, decision,
                    decision_at, created_task_id, duplicate_of, dedupe_key, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    candidate.candidate_id,
                    profile_id,
                    candidate.suggested_title,
                    candidate.suggested_description,
                    _dumps(candidate.source.to_dict()),
                    candidate.confidence,
                    candidate.reason,
                    _iso(candidate.suggested_due_at),
                    candidate.decision.value,
                    _iso(candidate.decision_at),
                    candidate.created_task_id,
                    candidate.duplicate_of,
                    candidate.dedupe_key,
                    _iso(candidate.created_at),
                ),
            )
        return replace(candidate, profile_id=profile_id), True

    def get_candidate(self, candidate_id: str) -> TaskCandidate | None:
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM control_task_candidates "
                "WHERE candidate_id = ? AND profile_id = ?",
                (candidate_id, self._profile(None)),
            ).fetchone()
        return _row_to_candidate(row) if row is not None else None

    def list_candidates(
        self,
        *,
        decision: CandidateDecision | None = None,
        limit: int = 100,
    ) -> list[TaskCandidate]:
        clauses = ["profile_id = ?"]
        params: list[Any] = [self._profile(None)]
        if decision is not None:
            clauses.append("decision = ?")
            params.append(decision.value)
        params.append(max(1, min(int(limit), 500)))
        with get_db() as conn:
            rows = conn.execute(
                f"SELECT * FROM control_task_candidates WHERE {' AND '.join(clauses)} "
                "ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [_row_to_candidate(row) for row in rows]

    def decide_candidate(
        self,
        candidate_id: str,
        *,
        decision: CandidateDecision,
        created_task_id: str | None = None,
        duplicate_of: str | None = None,
    ) -> TaskCandidate:
        profile_id = self._profile(None)
        with get_db() as conn:
            cursor = conn.execute(
                """
                UPDATE control_task_candidates
                SET decision = ?, decision_at = ?, created_task_id = ?, duplicate_of = ?
                WHERE candidate_id = ? AND profile_id = ? AND decision = 'pending'
                """,
                (
                    decision.value,
                    _iso(datetime.now(timezone.utc)),
                    created_task_id,
                    duplicate_of,
                    candidate_id,
                    profile_id,
                ),
            )
            row = conn.execute(
                "SELECT * FROM control_task_candidates "
                "WHERE candidate_id = ? AND profile_id = ?",
                (candidate_id, profile_id),
            ).fetchone()
            if row is None:
                raise TaskNotFound(candidate_id)
            if cursor.rowcount != 1 and row["decision"] != decision.value:
                raise TaskPersistenceConflict("candidat déjà décidé")
        return _row_to_candidate(row)

    def find_open_task_by_dedupe(self, dedupe_key: str) -> str | None:
        """Cherche une tâche vivante issue de la même source."""

        if not dedupe_key:
            return None
        with get_db() as conn:
            row = conn.execute(
                """
                SELECT task_id FROM control_tasks
                WHERE profile_id = ? AND source_reference = ?
                  AND status NOT IN ('archived', 'cancelled', 'completed')
                ORDER BY created_at DESC LIMIT 1
                """,
                (self._profile(None), dedupe_key),
            ).fetchone()
        return row["task_id"] if row is not None else None

    # ── Rapports et commentaires ──────────────────────────────────────────

    def save_report(self, report: TaskReport) -> TaskReport:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO control_task_reports (
                    report_id, task_id, profile_id, version, result_status,
                    summary, markdown, data_json, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(task_id, version) DO NOTHING
                """,
                (
                    report.report_id,
                    report.task_id,
                    self._profile(None),
                    report.version,
                    report.result_status,
                    report.summary,
                    report.markdown,
                    _dumps(dict(report.data)),
                    _iso(report.created_at),
                ),
            )
        return report

    def latest_report(self, task_id: str) -> TaskReport | None:
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM control_task_reports WHERE task_id = ? AND profile_id = ? "
                "ORDER BY version DESC LIMIT 1",
                (task_id, self._profile(None)),
            ).fetchone()
        return _row_to_report(row) if row is not None else None

    def next_report_version(self, task_id: str) -> int:
        with get_db() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) AS top FROM control_task_reports "
                "WHERE task_id = ? AND profile_id = ?",
                (task_id, self._profile(None)),
            ).fetchone()
        return int(row["top"]) + 1

    def add_comment(
        self,
        *,
        comment_id: str,
        task_id: str,
        body: str,
        author: str = "user",
        run_id: str | None = None,
        plan_version: int | None = None,
    ) -> dict[str, Any]:
        created_at = datetime.now(timezone.utc)
        safe_body = redact_text(body, max_chars=4_000)
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO control_task_comments (
                    comment_id, task_id, profile_id, author, body, run_id,
                    plan_version, created_at
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    comment_id,
                    task_id,
                    self._profile(None),
                    author,
                    safe_body,
                    run_id,
                    plan_version,
                    _iso(created_at),
                ),
            )
        return {
            "comment_id": comment_id,
            "task_id": task_id,
            "author": author,
            "body": safe_body,
            "run_id": run_id,
            "plan_version": plan_version,
            "created_at": created_at.isoformat(),
        }

    def list_comments(self, task_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM control_task_comments WHERE task_id = ? AND profile_id = ? "
                "ORDER BY created_at ASC LIMIT ?",
                (task_id, self._profile(None), max(1, min(int(limit), 500))),
            ).fetchall()
        return [
            {
                "comment_id": row["comment_id"],
                "task_id": row["task_id"],
                "author": row["author"],
                "body": row["body"],
                "run_id": row["run_id"],
                "plan_version": row["plan_version"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]


def migrate_task_control_tables(conn: sqlite3.Connection) -> None:
    """Crée les tables du pilotage et adopte les tâches historiques.

    L'adoption est volontairement *non destructive* : la table `tasks` reste
    la source des tâches simples déjà affichées ailleurs, et chaque ligne
    reçoit ici un miroir piloté en état ``created``. Aucune tâche existante
    ne se retrouve donc en cours d'exécution du fait de la migration.
    """

    conn.executescript(TASK_CONTROL_SCHEMA)
    existing = conn.execute(
        "SELECT COUNT(*) AS total FROM control_tasks WHERE legacy_task_id IS NOT NULL"
    ).fetchone()
    if existing is not None and int(existing["total"]) > 0:
        return
    try:
        rows = conn.execute(
            "SELECT id, title, description, priority, status, due_date, category,"
            " created_at, completed_at FROM tasks ORDER BY id"
        ).fetchall()
    except sqlite3.OperationalError:
        return
    profile_id = current_profile_id()
    now = datetime.now(timezone.utc)
    for row in rows:
        legacy_status = str(row["status"] or "todo")
        status = TaskStatus.COMPLETED if legacy_status == "done" else TaskStatus.CREATED
        result_status = "completed" if legacy_status == "done" else None
        source = TaskSource(
            source_type=TaskSourceType.MANUAL,
            channel=TaskSourceChannel.API,
            reference=f"legacy:task:{row['id']}",
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO control_tasks (
                task_id, profile_id, title, description, status, priority,
                source_type, source_channel, source_reference, source_excerpt,
                source_confidence, source_json, project_id, conversation_id,
                due_at, created_at, updated_at, current_phase, progress,
                attention_required, result_status, legacy_task_id, metadata_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                f"task_legacy{int(row['id']):08d}",
                profile_id,
                str(row["title"] or "Tâche")[:300],
                str(row["description"] or "")[:8000],
                status.value,
                str(row["priority"] or "medium"),
                source.source_type.value,
                source.channel.value,
                source.reference,
                "",
                None,
                _dumps(source.to_dict()),
                None,
                None,
                row["due_date"],
                row["created_at"] or _iso(now),
                row["completed_at"] or row["created_at"] or _iso(now),
                "",
                1.0 if legacy_status == "done" else 0.0,
                0,
                result_status,
                int(row["id"]),
                _dumps({"legacy_category": row["category"]}),
            ),
        )


__all__ = [
    "TASK_CONTROL_SCHEMA",
    "PlanNotFound",
    "TaskControlError",
    "TaskControlRepository",
    "TaskNotFound",
    "TaskPersistenceConflict",
    "migrate_task_control_tables",
]
