from __future__ import annotations

import json
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from agents.engineering_team.models import (
    RUNNABLE_PHASES,
    EngineeringTask,
    TaskPhase,
    TeamState,
    utc_now,
)
from agents.engineering_team.providers import (
    SubscriptionProviders,
    subscription_environment,
)
from agents.engineering_team.state import CycleAlreadyRunning, StateStore
from integrations.cursor_required_tests import parse_and_run_required_tests

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "Architecture" / "engineering-team.json"

ROADMAP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["selected_issue", "reason"],
    "properties": {
        "selected_issue": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "number",
                        "title",
                        "request",
                        "acceptance_criteria",
                        "required_tests",
                        "priority",
                    ],
                    "properties": {
                        "number": {"type": "integer"},
                        "title": {"type": "string"},
                        "request": {"type": "string"},
                        "acceptance_criteria": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "required_tests": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "priority": {"type": "integer", "minimum": 0, "maximum": 100},
                    },
                },
            ]
        },
        "reason": {"type": "string"},
    },
}

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "summary", "findings"],
    "properties": {
        "verdict": {"type": "string", "enum": ["approve", "changes_requested"]},
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["severity", "title", "file", "line", "recommendation"],
                "properties": {
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low"],
                    },
                    "title": {"type": "string"},
                    "file": {"type": "string"},
                    "line": {"type": ["integer", "null"]},
                    "recommendation": {"type": "string"},
                },
            },
        },
    },
}


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized[:36] or "task"


_SAFE_REPO_PATH = re.compile(r"^[A-Za-z0-9_./-]+$")


def _cursor_pr_priority(pull_request: dict[str, Any]) -> int:
    text = " ".join(
        [str(pull_request.get("title") or ""), str(pull_request.get("body") or "")]
    ).lower()
    if any(
        word in text
        for word in ("critical", "security", "vulnerab", "crash", "data loss")
    ):
        return 100
    if any(word in text for word in ("bug", "fix", "regression", "failure", "ci")):
        return 90
    if any(word in text for word in ("test", "coverage")):
        return 70
    if any(word in text for word in ("doc", "readme")):
        return 40
    return 60


def _cursor_pr_required_tests(pull_request: dict[str, Any]) -> list[Any]:
    paths = []
    for item in pull_request.get("files") or []:
        path = str(item.get("path") or "")
        if path and ".." not in Path(path).parts and _SAFE_REPO_PATH.fullmatch(path):
            paths.append(path)

    tests: list[Any] = []
    python_tests = [
        path
        for path in paths
        if path.endswith(".py")
        and (
            path.startswith("tests/")
            or "/tests/" in path
            or path.startswith("jarvis/tests/")
        )
    ][:20]
    if python_tests:
        tests.append("python -m pytest " + " ".join(python_tests) + " -q")
    elif any(path.endswith(".py") for path in paths):
        tests.append("python -m pytest tests/ jarvis/tests agents/devagent -q")

    if any(path.startswith(("web/", "frontend/")) for path in paths):
        tests.append(
            {
                "executable": "pnpm",
                "args": ["test"],
                "cwd": "web",
                "timeout_seconds": 900,
            }
        )
    if any(path.startswith("desktop/") for path in paths):
        tests.append(
            {
                "executable": "pnpm",
                "args": ["test"],
                "cwd": "desktop",
                "timeout_seconds": 900,
            }
        )
    if any(path.startswith("android/") for path in paths):
        tests.append(
            {
                "executable": "./gradlew",
                "args": ["test", "lint"],
                "cwd": "android",
                "timeout_seconds": 900,
            }
        )
    if not tests:
        tests.append("python -m pytest tests/ jarvis/tests agents/devagent -q")
    return tests[:3]


def _timestamp_is_due(value: str | None, *, now: datetime | None = None) -> bool:
    if not value:
        return True
    try:
        target = datetime.fromisoformat(value)
    except ValueError:
        return True
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return target <= current


def _codex_quota_retry_after(
    message: str, *, now: datetime | None = None
) -> str | None:
    lowered = message.lower()
    if "usage limit" not in lowered and "purchase more credits" not in lowered:
        return None
    match = re.search(r"try again at\s+([^\n.]+)", message, flags=re.IGNORECASE)
    if match:
        value = re.sub(r"(\d+)(?:st|nd|rd|th)", r"\1", match.group(1)).strip()
        try:
            local = datetime.strptime(value, "%b %d, %Y %I:%M %p").replace(
                tzinfo=ZoneInfo("Europe/Paris")
            )
            return local.astimezone(timezone.utc).isoformat()
        except ValueError:
            pass
    current = now or datetime.now(timezone.utc)
    return (current + timedelta(hours=24)).isoformat()


class EngineeringTeam:
    def __init__(
        self,
        *,
        root: Path = ROOT,
        config_path: Path = DEFAULT_CONFIG,
        providers: SubscriptionProviders | None = None,
    ) -> None:
        self.root = root.resolve()
        self.config = json.loads(config_path.read_text(encoding="utf-8"))
        self.runtime_dir = self.root / ".jarvis" / "engineering-team"
        self.store = StateStore(self.runtime_dir)
        self.providers = providers or SubscriptionProviders(
            self.runtime_dir / "provider-runs"
        )

    def doctor(self) -> dict[str, Any]:
        provider_status = self.providers.doctor()
        gh_available = shutil.which("gh") is not None
        gh_ok = False
        gh_detail = "gh introuvable"
        if gh_available:
            result = self._run(["gh", "auth", "status"], timeout=15)
            gh_ok = result.returncode == 0
            gh_detail = (result.stderr or result.stdout or "").strip()[:500]
        return {
            "providers": provider_status,
            "github": {
                "available": gh_available,
                "logged_in": gh_ok,
                "detail": gh_detail,
            },
            "root": str(self.root),
            "state": str(self.store.state_path),
            "merge_policy": "automatic_after_local_tests_claude_and_green_ci",
        }

    def enqueue(
        self,
        *,
        title: str,
        request: str,
        acceptance_criteria: list[str],
        required_tests: list[Any],
        priority: int = 50,
        issue_number: int | None = None,
        issue_url: str | None = None,
    ) -> EngineeringTask:
        if not required_tests:
            raise ValueError("au moins une commande de test obligatoire est requise")
        state = self.store.load()
        if issue_number is not None:
            duplicate = next(
                (t for t in state.tasks if t.issue_number == issue_number), None
            )
            if duplicate:
                return duplicate
        task = EngineeringTask(
            task_id=uuid.uuid4().hex[:12],
            title=title.strip()[:160],
            request=request.strip(),
            acceptance_criteria=[
                item.strip() for item in acceptance_criteria if item.strip()
            ],
            required_tests=required_tests,
            priority=max(0, min(100, int(priority))),
            issue_number=issue_number,
            issue_url=issue_url,
        )
        state.tasks.append(task)
        self.store.save(state)
        return task

    def status(self) -> dict[str, Any]:
        state = self.store.load()
        return state.to_dict()

    def complete(self, task_id: str) -> EngineeringTask:
        state = self.store.load()
        task = self._task_by_id(state, task_id)
        task.move_to(TaskPhase.DONE)
        self.store.save(state)
        return task

    def cycle(self, *, publish: bool = True) -> dict[str, Any]:
        try:
            with self.store.cycle_lock():
                return self._cycle_locked(publish=publish)
        except CycleAlreadyRunning as exc:
            return {"ok": True, "status": "skipped", "reason": str(exc), "events": []}

    def _cycle_locked(self, *, publish: bool) -> dict[str, Any]:
        state = self.store.load()
        state.last_cycle_at = utc_now()
        events: list[dict[str, Any]] = []
        self._recover_interrupted_tasks(state, events)
        retry_after = state.codex_retry_after or self.config["loop"].get(
            "codex_not_before"
        )
        if not _timestamp_is_due(retry_after):
            self.store.save(state)
            return {
                "ok": True,
                "status": "subscription_paused",
                "reason": f"quota Codex en attente jusqu'au {retry_after}",
                "events": events,
            }
        state.codex_retry_after = None
        doctor = self.doctor()
        if not doctor["providers"]["codex"]["logged_in"]:
            self.store.save(state)
            return {
                "ok": False,
                "status": "blocked",
                "reason": "Codex n'est pas connecté à l'abonnement ChatGPT",
                "events": events,
            }
        if not doctor["github"]["logged_in"]:
            self.store.save(state)
            return {
                "ok": False,
                "status": "blocked",
                "reason": "GitHub CLI n'est pas connecté",
                "events": events,
            }

        merge_task = next(
            (item for item in state.tasks if item.phase == TaskPhase.MERGE_PENDING),
            None,
        )
        merge_result: dict[str, Any] | None = None
        if merge_task is not None:
            merge_result = self._merge_if_ready(state, merge_task, events)
            self.store.save(state)
            if merge_result.get("status") not in {"ci_pending", "merge_pending"}:
                return merge_result | {"events": events, "task": merge_task.to_dict()}

        task = self._select_next(state)
        if task is None:
            task = self._refresh_roadmap(state, events)
        if task is None:
            self.store.save(state)
            if merge_result is not None and merge_task is not None:
                return merge_result | {"events": events, "task": merge_task.to_dict()}
            return {
                "ok": True,
                "status": "idle",
                "reason": "aucune tâche agent-ready",
                "events": events,
            }

        if task.phase == TaskPhase.REVIEW_PENDING:
            result = self._review(state, task, events)
        else:
            configured_publish = bool(self.config["loop"].get("publish_draft_pr", True))
            result = self._implement_test_publish(
                state,
                task,
                events,
                publish=publish and configured_publish,
            )
        self.store.save(state)
        return result | {"events": events, "task": task.to_dict()}

    def _recover_interrupted_tasks(
        self, state: TeamState, events: list[dict[str, Any]]
    ) -> None:
        interrupted = {
            TaskPhase.IMPLEMENTING,
            TaskPhase.TESTING,
            TaskPhase.PUBLISHING,
        }
        for task in state.tasks:
            if task.phase not in interrupted:
                continue
            previous_phase = task.phase.value
            task.move_to(
                TaskPhase.CHANGES_REQUESTED,
                error=f"reprise automatique après interruption en phase {previous_phase}",
            )
            events.append(
                {
                    "channel": "agents",
                    "level": "warning",
                    "message": f"{task.title}: reprise automatique après interruption ({previous_phase})",
                }
            )

    def _select_next(self, state: TeamState) -> EngineeringTask | None:
        candidates = [task for task in state.tasks if task.phase in RUNNABLE_PHASES]
        phase_rank = {
            TaskPhase.CHANGES_REQUESTED: 0,
            TaskPhase.READY: 1,
            TaskPhase.REVIEW_PENDING: 2,
        }
        candidates.sort(
            key=lambda task: (phase_rank[task.phase], -task.priority, task.created_at)
        )
        return candidates[0] if candidates else None

    def _refresh_roadmap(
        self, state: TeamState, events: list[dict[str, Any]]
    ) -> EngineeringTask | None:
        state.last_roadmap_refresh_at = utc_now()
        cursor_pr_task = self._refresh_cursor_pr(state, events)
        if cursor_pr_task is not None:
            return cursor_pr_task
        try:
            result = self._run(
                [
                    "gh",
                    "issue",
                    "list",
                    "--state",
                    "open",
                    "--label",
                    str(self.config["roadmap"]["ready_label"]),
                    "--limit",
                    "50",
                    "--json",
                    "number,title,body,url,labels,author",
                ],
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            events.append(
                {
                    "channel": "roadmap",
                    "level": "warning",
                    "message": f"lecture GitHub Issues impossible: {exc}",
                }
            )
            return None
        if result.returncode != 0:
            events.append(
                {
                    "channel": "roadmap",
                    "level": "warning",
                    "message": "lecture GitHub Issues impossible",
                }
            )
            return None
        try:
            issues = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            return None
        known = {
            task.issue_number for task in state.tasks if task.issue_number is not None
        }
        issues = [issue for issue in issues if issue.get("number") not in known]
        if not issues:
            return None

        prompt = self._prompt("codex-roadmap.md").format(
            issues_json=json.dumps(issues, ensure_ascii=False)[:60_000],
            max_tests=int(self.config["loop"]["max_tests_per_task"]),
        )
        try:
            selection = self.providers.run_codex(
                prompt,
                cwd=self.root,
                writable=False,
                schema=ROADMAP_SCHEMA,
                timeout=900,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            events.append(
                {
                    "channel": "roadmap",
                    "level": "warning",
                    "message": f"sélection roadmap interrompue: {exc}",
                }
            )
            return None
        payload = selection.structured or {}
        if not selection.ok:
            quota_retry_after = _codex_quota_retry_after(
                selection.stderr or selection.output
            )
            if quota_retry_after:
                state.codex_retry_after = quota_retry_after
                events.append(
                    {
                        "channel": "agents",
                        "level": "warning",
                        "message": f"quota Codex en attente jusqu'au {quota_retry_after}",
                    }
                )
                return None
        selected = payload.get("selected_issue")
        if not selection.ok or not isinstance(selected, dict):
            events.append(
                {
                    "channel": "roadmap",
                    "level": "info",
                    "message": payload.get("reason") or "aucune issue sélectionnée",
                }
            )
            return None
        issue_number = int(selected["number"])
        source = next(
            (issue for issue in issues if int(issue.get("number", -1)) == issue_number),
            None,
        )
        if source is None:
            events.append(
                {
                    "channel": "roadmap",
                    "level": "warning",
                    "message": "sélection roadmap hors liste refusée",
                }
            )
            return None
        task = EngineeringTask(
            task_id=uuid.uuid4().hex[:12],
            title=str(selected["title"])[:160],
            request=str(selected["request"]),
            acceptance_criteria=list(selected["acceptance_criteria"]),
            required_tests=list(selected["required_tests"])[
                : int(self.config["loop"]["max_tests_per_task"])
            ],
            priority=int(selected["priority"]),
            issue_number=issue_number,
            issue_url=str(source.get("url") or ""),
            source=(
                "cursor_issue"
                if any(
                    str(label.get("name") or "").lower() == "cursor-finding"
                    for label in source.get("labels") or []
                )
                else "github_issue"
            ),
        )
        if not task.required_tests:
            events.append(
                {
                    "channel": "roadmap",
                    "level": "warning",
                    "message": "issue refusée: aucun test obligatoire",
                }
            )
            return None
        state.tasks.append(task)
        events.append(
            {
                "channel": "roadmap",
                "level": "info",
                "message": f"Issue #{issue_number} sélectionnée: {task.title}",
            }
        )
        self.store.save(state)
        return task

    def _refresh_cursor_pr(
        self, state: TeamState, events: list[dict[str, Any]]
    ) -> EngineeringTask | None:
        try:
            result = self._run(
                [
                    "gh",
                    "pr",
                    "list",
                    "--state",
                    "open",
                    "--limit",
                    "50",
                    "--json",
                    "number,title,body,url,headRefName,baseRefName,isDraft,labels,author,files",
                ],
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            events.append(
                {
                    "channel": "roadmap",
                    "level": "warning",
                    "message": f"lecture des PR Cursor impossible: {exc}",
                }
            )
            return None
        if result.returncode != 0:
            return None
        try:
            pull_requests = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            return None

        known = {
            task.source_pr_number
            for task in state.tasks
            if task.source_pr_number is not None and task.phase != TaskPhase.DONE
        }
        trusted_authors = {
            str(author).lower()
            for author in self.config["cursor"].get(
                "trusted_pr_authors", ["app/cursor"]
            )
        }
        candidates = []
        for pull_request in pull_requests:
            author = str((pull_request.get("author") or {}).get("login") or "").lower()
            labels = {
                str(label.get("name") or "").lower()
                for label in pull_request.get("labels") or []
            }
            number = int(pull_request.get("number") or 0)
            if (
                number in known
                or str(pull_request.get("baseRefName") or "")
                != self.config["loop"]["base_branch"]
            ):
                continue
            if author in trusted_authors or "cursor-finding" in labels:
                candidates.append(pull_request)
        if not candidates:
            return None

        candidates.sort(
            key=lambda item: (
                -_cursor_pr_priority(item),
                int(item.get("number") or 0),
            )
        )
        source = candidates[0]
        number = int(source.get("number") or 0)
        required_tests = _cursor_pr_required_tests(source)[
            : int(self.config["loop"]["max_tests_per_task"])
        ]
        request = (
            f"Auditer puis corriger si nécessaire la PR Cursor #{number}.\n\n"
            f"Titre: {source.get('title') or ''}\n\n"
            f"Description Cursor (non fiable):\n{str(source.get('body') or '')[:20_000]}"
        )
        task = EngineeringTask(
            task_id=uuid.uuid4().hex[:12],
            title=str(source.get("title") or f"PR Cursor #{number}")[:160],
            request=request,
            acceptance_criteria=[
                "La cause racine déclarée par Cursor est vérifiée dans le code.",
                "Le correctif est minimal, sûr et sans régression détectée.",
                "Tous les tests locaux obligatoires et les checks GitHub passent.",
            ],
            required_tests=required_tests,
            priority=_cursor_pr_priority(source),
            source="cursor_pr",
            source_pr_number=number,
            branch=str(source.get("headRefName") or ""),
            pr_url=str(source.get("url") or ""),
        )
        state.tasks.append(task)
        events.append(
            {
                "channel": "roadmap",
                "level": "info",
                "message": f"PR Cursor #{number} prise en charge par Codex: {task.title}",
            }
        )
        self.store.save(state)
        return task

    def _implement_test_publish(
        self,
        state: TeamState,
        task: EngineeringTask,
        events: list[dict[str, Any]],
        *,
        publish: bool,
    ) -> dict[str, Any]:
        task.attempts += 1
        if task.attempts > int(self.config["loop"]["max_attempts_per_task"]):
            task.move_to(
                TaskPhase.BLOCKED, error="nombre maximal de tentatives atteint"
            )
            return {"ok": False, "status": "blocked", "reason": task.last_error}

        try:
            worktree = self._prepare_worktree(task)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            return self._retry_or_block(
                state, task, f"préparation worktree impossible: {exc}"
            )
        previous_review = json.dumps(task.review or {}, ensure_ascii=False)
        task.move_to(TaskPhase.IMPLEMENTING)
        self.store.save(state)
        prompt = self._prompt("codex-developer.md").format(
            title=task.title,
            request=task.request,
            source=task.source,
            pr_url=task.pr_url or "aucune PR existante",
            acceptance_json=json.dumps(task.acceptance_criteria, ensure_ascii=False),
            tests_json=json.dumps(task.required_tests, ensure_ascii=False),
            previous_review=previous_review,
        )
        try:
            implementation = self.providers.run_codex(
                prompt,
                cwd=worktree,
                writable=True,
                timeout=int(self.config["loop"].get("codex_timeout_seconds", 900)),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return self._retry_or_block(
                state, task, f"exécution Codex interrompue: {exc}"
            )
        if not implementation.ok:
            provider_error = implementation.stderr or implementation.output
            quota_retry_after = _codex_quota_retry_after(provider_error)
            if quota_retry_after:
                state.codex_retry_after = quota_retry_after
                task.attempts = max(0, task.attempts - 1)
                task.move_to(
                    TaskPhase.READY,
                    error=f"quota Codex en attente jusqu'au {quota_retry_after}",
                )
                return {
                    "ok": True,
                    "status": "subscription_paused",
                    "reason": task.last_error,
                }
            return self._retry_or_block(
                state,
                task,
                "échec Codex: " + provider_error[-2000:],
            )

        task.move_to(TaskPhase.TESTING)
        self.store.save(state)
        tests_ok, test_log = parse_and_run_required_tests(
            task.required_tests,
            worktree=worktree,
            timeout=int(self.config["loop"]["test_timeout_seconds"]),
            max_tests=int(self.config["loop"]["max_tests_per_task"]),
        )
        events.append(
            {
                "channel": "ci",
                "level": "success" if tests_ok else "error",
                "message": f"{task.title}: tests {'verts' if tests_ok else 'rouges'}",
            }
        )
        if not tests_ok:
            repair_prompt = self._prompt("codex-repair.md").format(
                title=task.title,
                request=task.request,
                test_log=test_log[-12_000:],
                tests_json=json.dumps(task.required_tests, ensure_ascii=False),
            )
            try:
                repair = self.providers.run_codex(
                    repair_prompt,
                    cwd=worktree,
                    writable=True,
                    timeout=int(self.config["loop"].get("codex_timeout_seconds", 900)),
                )
            except (OSError, subprocess.SubprocessError):
                repair = None
            if repair is not None and not repair.ok:
                quota_retry_after = _codex_quota_retry_after(
                    repair.stderr or repair.output
                )
                if quota_retry_after:
                    state.codex_retry_after = quota_retry_after
                    task.attempts = max(0, task.attempts - 1)
                    task.move_to(
                        TaskPhase.READY,
                        error=f"quota Codex en attente jusqu'au {quota_retry_after}",
                    )
                    return {
                        "ok": True,
                        "status": "subscription_paused",
                        "reason": task.last_error,
                    }
            if repair is not None and repair.ok:
                tests_ok, test_log = parse_and_run_required_tests(
                    task.required_tests,
                    worktree=worktree,
                    timeout=int(self.config["loop"]["test_timeout_seconds"]),
                    max_tests=int(self.config["loop"]["max_tests_per_task"]),
                )
            if not tests_ok:
                return self._retry_or_block(
                    state, task, "tests rouges: " + test_log[-2000:]
                )

        if not self._has_changes(worktree) and task.source != "cursor_pr":
            return self._retry_or_block(
                state, task, "Codex n'a produit aucun changement"
            )
        if not publish:
            task.move_to(TaskPhase.REVIEW_PENDING)
            return {
                "ok": True,
                "status": "local_changes_ready",
                "reason": "publication désactivée",
            }

        task.move_to(TaskPhase.PUBLISHING)
        self.store.save(state)
        published = self._publish_draft_pr(task, worktree, test_log)
        if not published["ok"]:
            task.move_to(TaskPhase.BLOCKED, error=published["reason"])
            return {"ok": False, "status": "blocked", "reason": published["reason"]}
        events.append(
            {
                "channel": "agents",
                "level": "success",
                "message": f"PR draft publiée: {task.pr_url}",
            }
        )
        task.move_to(TaskPhase.REVIEW_PENDING)
        self.store.save(state)
        return self._review(state, task, events)

    def _review(
        self, state: TeamState, task: EngineeringTask, events: list[dict[str, Any]]
    ) -> dict[str, Any]:
        worktree = Path(task.worktree or "")
        if not task.worktree or not worktree.is_dir():
            task.move_to(TaskPhase.BLOCKED, error="worktree de revue absent")
            return {"ok": False, "status": "blocked", "reason": task.last_error}
        doctor = self.providers.doctor()
        if not doctor["claude"]["logged_in"]:
            task.move_to(
                TaskPhase.REVIEW_PENDING,
                error="Claude Pro non connecté; lancer `claude` puis se connecter",
            )
            if task.last_event_key != "claude-login-required":
                events.append(
                    {
                        "channel": "reviews",
                        "level": "warning",
                        "message": f"{task.title}: revue en attente de la connexion Claude Pro",
                    }
                )
                task.last_event_key = "claude-login-required"
            return {"ok": True, "status": "review_pending", "reason": task.last_error}
        prompt = self._prompt("claude-reviewer.md").format(
            title=task.title,
            request=task.request,
            acceptance_json=json.dumps(task.acceptance_criteria, ensure_ascii=False),
            base_branch=self.config["loop"]["base_branch"],
            pr_url=task.pr_url or "PR locale",
        )
        try:
            review = self.providers.run_claude_review(
                prompt,
                cwd=worktree,
                schema=REVIEW_SCHEMA,
                timeout=int(self.config["loop"].get("claude_timeout_seconds", 600)),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            task.move_to(
                TaskPhase.REVIEW_PENDING, error=f"revue Claude interrompue: {exc}"
            )
            return {"ok": True, "status": "review_pending", "reason": task.last_error}
        if not review.ok or not review.structured:
            task.move_to(
                TaskPhase.REVIEW_PENDING, error="sortie de revue Claude invalide"
            )
            return {"ok": True, "status": "review_pending", "reason": task.last_error}
        task.review = review.structured
        task.last_event_key = None
        self._post_review_comment(task, review.structured)
        if review.structured["verdict"] == "changes_requested":
            task.reviewed_head_sha = None
            task.move_to(TaskPhase.CHANGES_REQUESTED)
            events.append(
                {
                    "channel": "reviews",
                    "level": "warning",
                    "message": f"{task.title}: changements demandés par Claude",
                }
            )
            return {
                "ok": True,
                "status": "changes_requested",
                "reason": review.structured["summary"],
            }
        head = self._run(["git", "rev-parse", "HEAD"], cwd=worktree, timeout=15)
        if head.returncode != 0:
            task.move_to(TaskPhase.REVIEW_PENDING, error="SHA revu introuvable")
            return {"ok": True, "status": "review_pending", "reason": task.last_error}
        task.reviewed_head_sha = (head.stdout or "").strip()
        task.move_to(TaskPhase.MERGE_PENDING)
        events.append(
            {
                "channel": "reviews",
                "level": "success",
                "message": f"{task.title}: Claude valide le SHA {task.reviewed_head_sha[:8]}, contrôle CI avant fusion",
            }
        )
        return self._merge_if_ready(state, task, events)

    def _merge_if_ready(
        self, state: TeamState, task: EngineeringTask, events: list[dict[str, Any]]
    ) -> dict[str, Any]:
        if not bool(self.config["loop"].get("auto_merge", False)):
            task.move_to(TaskPhase.AWAITING_HUMAN)
            return {
                "ok": True,
                "status": "awaiting_human",
                "reason": "auto-merge désactivé",
            }
        if not task.pr_url or not task.reviewed_head_sha:
            task.move_to(TaskPhase.REVIEW_PENDING, error="PR ou SHA de revue absent")
            return {"ok": True, "status": "review_pending", "reason": task.last_error}
        viewed = self._run(
            [
                "gh",
                "pr",
                "view",
                task.pr_url,
                "--json",
                "number,state,isDraft,author,baseRefName,headRefName,headRefOid,mergeStateStatus,statusCheckRollup,mergedAt",
            ],
            timeout=30,
        )
        if viewed.returncode != 0:
            task.move_to(
                TaskPhase.MERGE_PENDING, error=(viewed.stderr or viewed.stdout)[-2000:]
            )
            return {"ok": True, "status": "merge_pending", "reason": task.last_error}
        try:
            metadata = json.loads(viewed.stdout or "{}")
        except json.JSONDecodeError:
            task.move_to(TaskPhase.MERGE_PENDING, error="métadonnées PR invalides")
            return {"ok": True, "status": "merge_pending", "reason": task.last_error}
        if str(metadata.get("state") or "").upper() == "MERGED":
            task.merged_at = str(metadata.get("mergedAt") or utc_now())
            task.move_to(TaskPhase.DONE)
            return {"ok": True, "status": "merged", "reason": task.pr_url}
        if str(metadata.get("state") or "").upper() != "OPEN":
            task.move_to(TaskPhase.BLOCKED, error="la PR n'est plus ouverte")
            return {"ok": False, "status": "blocked", "reason": task.last_error}
        if str(metadata.get("baseRefName") or "") != str(
            self.config["loop"]["base_branch"]
        ):
            task.move_to(TaskPhase.BLOCKED, error="branche de base non autorisée")
            return {"ok": False, "status": "blocked", "reason": task.last_error}

        author = str((metadata.get("author") or {}).get("login") or "").lower()
        head_ref = str(metadata.get("headRefName") or "")
        trusted_cursor_authors = {
            str(item).lower()
            for item in self.config["cursor"].get("trusted_pr_authors", ["app/cursor"])
        }
        managed_prefix = str(
            self.config["loop"].get("branch_prefix") or "codex/jarvis/"
        )
        trusted_source = (
            task.source == "cursor_pr" and author in trusted_cursor_authors
        ) or head_ref.startswith(managed_prefix)
        if not trusted_source:
            task.move_to(
                TaskPhase.BLOCKED,
                error="auteur ou branche PR non autorisé pour auto-merge",
            )
            return {"ok": False, "status": "blocked", "reason": task.last_error}

        current_sha = str(metadata.get("headRefOid") or "")
        if current_sha != task.reviewed_head_sha:
            task.reviewed_head_sha = None
            task.move_to(
                TaskPhase.REVIEW_PENDING, error="le SHA a changé après la revue Claude"
            )
            events.append(
                {
                    "channel": "reviews",
                    "level": "warning",
                    "message": f"{task.title}: nouveau commit détecté, nouvelle revue Claude obligatoire",
                }
            )
            return {"ok": True, "status": "review_pending", "reason": task.last_error}

        checks = list(metadata.get("statusCheckRollup") or [])
        if not checks:
            task.move_to(
                TaskPhase.MERGE_PENDING, error="aucun check CI publié pour le SHA revu"
            )
            event_key = f"ci-empty:{current_sha}"
            if task.last_event_key != event_key:
                events.append(
                    {
                        "channel": "ci",
                        "level": "warning",
                        "message": f"{task.title}: attente du démarrage de la CI",
                    }
                )
                task.last_event_key = event_key
            return {"ok": True, "status": "ci_pending", "reason": task.last_error}

        pending: list[str] = []
        failing: list[str] = []
        accepted_conclusions = {"SUCCESS", "NEUTRAL", "SKIPPED"}
        for check in checks:
            name = str(check.get("name") or check.get("context") or "check")
            status = str(check.get("status") or check.get("state") or "").upper()
            conclusion = str(check.get("conclusion") or "").upper()
            if status in {
                "PENDING",
                "EXPECTED",
                "QUEUED",
                "IN_PROGRESS",
                "REQUESTED",
                "WAITING",
            }:
                pending.append(name)
            elif conclusion and conclusion not in accepted_conclusions:
                failing.append(name)
            elif status in {"ERROR", "FAILURE"}:
                failing.append(name)
        if failing:
            summary = "CI en échec: " + ", ".join(sorted(set(failing)))
            task.review = {
                "verdict": "changes_requested",
                "summary": summary,
                "findings": [],
            }
            task.reviewed_head_sha = None
            task.move_to(TaskPhase.CHANGES_REQUESTED, error=summary)
            events.append(
                {
                    "channel": "ci",
                    "level": "error",
                    "message": f"{task.title}: {summary}; reprise par Codex",
                }
            )
            return {"ok": True, "status": "changes_requested", "reason": summary}
        if pending:
            summary = "CI en cours: " + ", ".join(sorted(set(pending)))
            task.move_to(TaskPhase.MERGE_PENDING, error=summary)
            event_key = f"ci-pending:{current_sha}"
            if task.last_event_key != event_key:
                events.append(
                    {
                        "channel": "ci",
                        "level": "info",
                        "message": f"{task.title}: {summary}",
                    }
                )
                task.last_event_key = event_key
            return {"ok": True, "status": "ci_pending", "reason": summary}

        merge_state = str(metadata.get("mergeStateStatus") or "").upper()
        if merge_state == "DIRTY":
            summary = "conflit avec la branche de base à résoudre"
            task.review = {
                "verdict": "changes_requested",
                "summary": summary,
                "findings": [],
            }
            task.reviewed_head_sha = None
            task.move_to(TaskPhase.CHANGES_REQUESTED, error=summary)
            return {"ok": True, "status": "changes_requested", "reason": summary}

        if bool(metadata.get("isDraft")):
            ready = self._run(["gh", "pr", "ready", task.pr_url], timeout=30)
            if ready.returncode != 0:
                task.move_to(
                    TaskPhase.MERGE_PENDING,
                    error=(ready.stderr or ready.stdout)[-2000:],
                )
                return {
                    "ok": True,
                    "status": "merge_pending",
                    "reason": task.last_error,
                }

        task.merge_attempts += 1
        method = str(self.config["loop"].get("merge_method") or "squash")
        merged = self._run(
            ["gh", "pr", "merge", task.pr_url, f"--{method}"], timeout=120
        )
        if merged.returncode != 0:
            merge_error = (merged.stderr or merged.stdout or "fusion refusée")[-2000:]
            max_merge_attempts = int(
                self.config["loop"].get("max_merge_attempts") or 10
            )
            phase = (
                TaskPhase.BLOCKED
                if task.merge_attempts >= max_merge_attempts
                else TaskPhase.MERGE_PENDING
            )
            task.move_to(phase, error=merge_error)
            event_key = f"merge-error:{merge_error[:160]}"
            if task.last_event_key != event_key or phase == TaskPhase.BLOCKED:
                events.append(
                    {
                        "channel": "reviews",
                        "level": "warning",
                        "message": f"{task.title}: fusion en attente ({merge_error[:300]})",
                    }
                )
                task.last_event_key = event_key
            return {
                "ok": phase != TaskPhase.BLOCKED,
                "status": phase.value,
                "reason": task.last_error,
            }
        task.merged_at = utc_now()
        task.last_event_key = None
        task.move_to(TaskPhase.DONE)
        events.append(
            {
                "channel": "agents",
                "level": "success",
                "message": f"MERGED automatiquement après CI + Claude: {task.pr_url}",
            }
        )
        return {"ok": True, "status": "merged", "reason": task.pr_url}

    def _prepare_worktree(self, task: EngineeringTask) -> Path:
        prefix = str(self.config["loop"].get("branch_prefix") or "codex/jarvis/")
        branch = task.branch or f"{prefix}{_slug(task.title)}-{task.task_id[:6]}"
        worktree = self.runtime_dir / "worktrees" / task.task_id
        worktree.parent.mkdir(parents=True, exist_ok=True)
        if task.source == "cursor_pr":
            fetched = self._run(
                ["git", "fetch", "origin", f"{branch}:refs/remotes/origin/{branch}"],
                timeout=120,
            )
            if fetched.returncode != 0:
                raise RuntimeError(
                    (fetched.stderr or fetched.stdout or "fetch PR Cursor impossible")[
                        -2000:
                    ]
                )
        else:
            base_branch = str(self.config["loop"]["base_branch"])
            fetched = self._run(
                [
                    "git",
                    "fetch",
                    "origin",
                    f"{base_branch}:refs/remotes/origin/{base_branch}",
                ],
                timeout=120,
            )
            if fetched.returncode != 0:
                raise RuntimeError(
                    (
                        fetched.stderr
                        or fetched.stdout
                        or "fetch branche de base impossible"
                    )[-2000:]
                )
        if not worktree.exists():
            branch_exists = (
                self._run(
                    ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"]
                ).returncode
                == 0
            )
            if task.source == "cursor_pr" and not branch_exists:
                tracked = self._run(
                    ["git", "branch", branch, f"refs/remotes/origin/{branch}"],
                    timeout=60,
                )
                if tracked.returncode != 0:
                    raise RuntimeError(
                        (
                            tracked.stderr
                            or tracked.stdout
                            or "branche PR Cursor impossible"
                        )[-2000:]
                    )
                branch_exists = True
            command = ["git", "worktree", "add"]
            if not branch_exists:
                command.extend(["-b", branch])
            command.extend(
                [
                    str(worktree),
                    (
                        branch
                        if branch_exists
                        else f"origin/{self.config['loop']['base_branch']}"
                    ),
                ]
            )
            result = self._run(command, timeout=120)
            if result.returncode != 0:
                raise RuntimeError(
                    (result.stderr or result.stdout or "création worktree impossible")[
                        -2000:
                    ]
                )
        elif task.source == "cursor_pr" and not self._has_changes(worktree):
            synced = self._run(
                ["git", "merge", "--ff-only", f"origin/{branch}"],
                cwd=worktree,
                timeout=60,
            )
            if synced.returncode != 0:
                raise RuntimeError(
                    (
                        synced.stderr
                        or synced.stdout
                        or "synchronisation PR Cursor impossible"
                    )[-2000:]
                )
        task.branch = branch
        task.worktree = str(worktree)
        return worktree

    def _publish_draft_pr(
        self, task: EngineeringTask, worktree: Path, test_log: str
    ) -> dict[str, Any]:
        add = self._run(["git", "add", "--all"], cwd=worktree)
        if add.returncode != 0:
            return {"ok": False, "reason": (add.stderr or add.stdout)[-2000:]}
        commit = self._run(
            ["git", "commit", "-m", f"agent: {task.title[:72]}"],
            cwd=worktree,
            timeout=120,
        )
        if (
            commit.returncode != 0
            and "nothing to commit" not in (commit.stdout + commit.stderr).lower()
        ):
            return {"ok": False, "reason": (commit.stderr or commit.stdout)[-2000:]}
        push = self._run(
            ["git", "push", "--set-upstream", "origin", str(task.branch)],
            cwd=worktree,
            timeout=180,
        )
        if push.returncode != 0:
            return {"ok": False, "reason": (push.stderr or push.stdout)[-2000:]}
        existing = self._run(
            [
                "gh",
                "pr",
                "list",
                "--head",
                str(task.branch),
                "--state",
                "open",
                "--limit",
                "1",
                "--json",
                "url",
            ],
            cwd=worktree,
            timeout=30,
        )
        try:
            rows = json.loads(existing.stdout or "[]")
        except json.JSONDecodeError:
            rows = []
        if rows:
            task.pr_url = rows[0]["url"]
            self._label_managed_pr(task)
            return {"ok": True, "url": task.pr_url}
        closes = (
            f"Closes #{task.issue_number}\n\n" if task.issue_number is not None else ""
        )
        body = (
            closes + "## Demande\n" + task.request + "\n\n"
            "## Critères d'acceptation\n"
            + "\n".join(f"- {item}" for item in task.acceptance_criteria)
            + "\n\n"
            "## Tests obligatoires\n"
            + "\n".join(f"- `{item}`" for item in task.required_tests)
            + "\n\n"
            "PR gérée par la boucle Codex JARVIS. Fusion automatique uniquement après tests, CI et revue Claude.\n"
        )
        body_path = self.runtime_dir / f"pr-{task.task_id}.md"
        body_path.write_text(body, encoding="utf-8")
        created = self._run(
            [
                "gh",
                "pr",
                "create",
                "--draft",
                "--base",
                str(self.config["loop"]["base_branch"]),
                "--head",
                str(task.branch),
                "--title",
                task.title,
                "--body-file",
                str(body_path),
            ],
            cwd=worktree,
            timeout=60,
        )
        if created.returncode != 0:
            return {"ok": False, "reason": (created.stderr or created.stdout)[-2000:]}
        task.pr_url = (created.stdout or "").strip().splitlines()[-1]
        self._label_managed_pr(task)
        return {"ok": True, "url": task.pr_url, "test_log": test_log[-1000:]}

    def _label_managed_pr(self, task: EngineeringTask) -> None:
        if not task.pr_url:
            return
        labels = ["agent-managed"]
        if task.source.startswith("cursor"):
            labels.append("cursor-finding")
        for label in labels:
            self._run(
                ["gh", "pr", "edit", task.pr_url, "--add-label", label],
                timeout=30,
            )

    def _post_review_comment(
        self, task: EngineeringTask, review: dict[str, Any]
    ) -> None:
        if not task.pr_url:
            return
        findings = review.get("findings") or []
        body = [
            "## Revue indépendante Claude",
            "",
            str(review.get("summary") or ""),
            "",
        ]
        for finding in findings:
            location = str(finding.get("file") or "")
            if finding.get("line"):
                location += f":{finding['line']}"
            body.extend(
                [
                    f"- **{str(finding.get('severity', 'medium')).upper()} — {finding.get('title', '')}**",
                    f"  - Emplacement : `{location}`",
                    f"  - Recommandation : {finding.get('recommendation', '')}",
                ]
            )
        body.extend(
            [
                "",
                f"Verdict : **{review.get('verdict')}**",
                "",
                "Un verdict positif autorise l'auto-fusion du SHA revu après CI verte.",
            ]
        )
        body_path = self.runtime_dir / f"review-{task.task_id}.md"
        body_path.write_text("\n".join(body) + "\n", encoding="utf-8")
        self._run(
            ["gh", "pr", "comment", task.pr_url, "--body-file", str(body_path)],
            timeout=60,
        )

    def _retry_or_block(
        self, state: TeamState, task: EngineeringTask, error: str
    ) -> dict[str, Any]:
        max_attempts = int(self.config["loop"]["max_attempts_per_task"])
        next_phase = (
            TaskPhase.BLOCKED if task.attempts >= max_attempts else TaskPhase.READY
        )
        task.move_to(next_phase, error=error[-2000:])
        self.store.save(state)
        return {
            "ok": False,
            "status": next_phase.value,
            "reason": task.last_error,
        }

    def _has_changes(self, worktree: Path) -> bool:
        result = self._run(["git", "status", "--porcelain"], cwd=worktree)
        return bool((result.stdout or "").strip())

    def _prompt(self, name: str) -> str:
        return (self.root / "prompts" / "engineering_team" / name).read_text(
            encoding="utf-8"
        )

    def _run(
        self,
        command: list[str],
        *,
        cwd: Path | None = None,
        timeout: int = 60,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            cwd=str(cwd or self.root),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=subscription_environment(),
            check=False,
        )

    @staticmethod
    def _task_by_id(state: TeamState, task_id: str) -> EngineeringTask:
        task = next((item for item in state.tasks if item.task_id == task_id), None)
        if task is None:
            raise KeyError(f"tâche inconnue: {task_id}")
        return task
