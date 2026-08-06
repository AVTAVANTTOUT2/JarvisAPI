from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class TaskPhase(str, Enum):
    READY = "ready"
    IMPLEMENTING = "implementing"
    TESTING = "testing"
    PUBLISHING = "publishing"
    REVIEW_PENDING = "review_pending"
    CHANGES_REQUESTED = "changes_requested"
    MERGE_PENDING = "merge_pending"
    AWAITING_HUMAN = "awaiting_human"
    BLOCKED = "blocked"
    DONE = "done"


RUNNABLE_PHASES = {
    TaskPhase.READY,
    TaskPhase.CHANGES_REQUESTED,
    TaskPhase.REVIEW_PENDING,
}


@dataclass(slots=True)
class EngineeringTask:
    task_id: str
    title: str
    request: str
    acceptance_criteria: list[str]
    required_tests: list[Any]
    priority: int = 50
    phase: TaskPhase = TaskPhase.READY
    issue_number: int | None = None
    issue_url: str | None = None
    source: str = "manual"
    source_pr_number: int | None = None
    branch: str | None = None
    worktree: str | None = None
    pr_url: str | None = None
    attempts: int = 0
    last_error: str | None = None
    review: dict[str, Any] | None = None
    reviewed_head_sha: str | None = None
    merge_attempts: int = 0
    merged_at: str | None = None
    last_event_key: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["phase"] = self.phase.value
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EngineeringTask":
        data = dict(payload)
        data["phase"] = TaskPhase(str(data.get("phase") or TaskPhase.READY.value))
        return cls(**data)

    def move_to(self, phase: TaskPhase, *, error: str | None = None) -> None:
        self.phase = phase
        self.updated_at = utc_now()
        self.last_error = error


@dataclass(slots=True)
class TeamState:
    schema_version: int = 1
    tasks: list[EngineeringTask] = field(default_factory=list)
    last_cycle_at: str | None = None
    last_roadmap_refresh_at: str | None = None
    codex_retry_after: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "tasks": [task.to_dict() for task in self.tasks],
            "last_cycle_at": self.last_cycle_at,
            "last_roadmap_refresh_at": self.last_roadmap_refresh_at,
            "codex_retry_after": self.codex_retry_after,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TeamState":
        return cls(
            schema_version=int(payload.get("schema_version") or 1),
            tasks=[
                EngineeringTask.from_dict(item) for item in payload.get("tasks", [])
            ],
            last_cycle_at=payload.get("last_cycle_at"),
            last_roadmap_refresh_at=payload.get("last_roadmap_refresh_at"),
            codex_retry_after=payload.get("codex_retry_after"),
        )
