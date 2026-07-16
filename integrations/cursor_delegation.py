"""Service de délégation Cursor CLI — worktree isolé, jobs persistants, rollback.

Garanties :
- jamais de travail direct sur main/master (branche + worktree par job) ;
- jobs persistés en SQLite (survivent au restart ; reprise via
  ``resume_pending_jobs()`` appelée au startup) ;
- returncode CLI vérifié, timeout, cancel qui tue le process group ;
- concurrence bornée par ``CURSOR_MAX_CONCURRENT_JOBS`` (sémaphore) ;
- secrets masqués dans le prompt avant envoi.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import signal
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import config
from database.cursor_jobs import (
    count_active_cursor_jobs,
    create_cursor_job,
    get_cursor_job,
    list_cursor_jobs,
    list_jobs_by_statuses,
    update_cursor_job,
)
from integrations.cursor_cli import build_agent_command, inspect_cursor_cli
from integrations.cursor_prompt_composer import compose_cursor_prompt, parse_cursor_result

logger = logging.getLogger(__name__)

PROTECTED_BRANCHES = frozenset({"main", "master"})

# Motifs de secrets à masquer avant tout envoi à Cursor.
_SECRET_PATTERNS = (
    re.compile(r"((?:API_KEY|SECRET|TOKEN|PASSWORD|PASSPHRASE)\s*=\s*)\S+", re.I),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{12,}"),
)


def _redact_secrets(text: str) -> str:
    out = text
    out = _SECRET_PATTERNS[0].sub(r"\1***REDACTED***", out)
    out = _SECRET_PATTERNS[1].sub("sk-***REDACTED***", out)
    out = _SECRET_PATTERNS[2].sub("Bearer ***REDACTED***", out)
    return out


class CursorDelegationError(RuntimeError):
    """Erreur de délégation Cursor."""


class CursorDelegationService:
    """Orchestre détection CLI, isolation git, exécution headless, validation."""

    def __init__(self) -> None:
        self._cli_info = None
        self._semaphore: threading.Semaphore | None = None
        self._semaphore_size = 0
        self._procs: dict[str, subprocess.Popen[str]] = {}
        self._procs_lock = threading.Lock()

    # ── CLI ──────────────────────────────────────────────────

    def refresh_cli(self) -> dict[str, Any]:
        path = getattr(config, "CURSOR_CLI_PATH", "") or None
        self._cli_info = inspect_cursor_cli(path)
        return self._cli_info.to_dict()

    def cli_status(self) -> dict[str, Any]:
        if self._cli_info is None:
            return self.refresh_cli()
        return self._cli_info.to_dict()

    # ── Concurrence ──────────────────────────────────────────

    def _get_semaphore(self) -> threading.Semaphore:
        size = max(1, int(getattr(config, "CURSOR_MAX_CONCURRENT_JOBS", 2)))
        if self._semaphore is None or size != self._semaphore_size:
            self._semaphore = threading.Semaphore(size)
            self._semaphore_size = size
        return self._semaphore

    # ── Helpers git / fs ─────────────────────────────────────

    def _worktree_root(self) -> Path:
        raw = getattr(config, "CURSOR_WORKTREE_ROOT", ".jarvis/worktrees")
        root = Path(raw)
        if not root.is_absolute():
            root = Path(config.BASE_DIR) / root
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _new_job_id(self) -> str:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        return f"job-{ts}-{uuid.uuid4().hex[:6]}"

    def _git(self, args: list[str], cwd: Path, timeout: int = 60) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def _prepare_worktree(self, job_id: str, repo: Path) -> tuple[Path, str]:
        """Crée branche jarvis/cursor/<job_id> + worktree dédié. Jamais sur main."""
        branch = f"jarvis/cursor/{job_id}"
        wt_path = self._worktree_root() / job_id
        if wt_path.exists():
            shutil.rmtree(wt_path, ignore_errors=True)

        created = self._git(["branch", branch, "HEAD"], repo)
        if created.returncode != 0 and "already exists" not in (created.stderr or ""):
            raise CursorDelegationError(
                f"Impossible de créer la branche {branch}: {created.stderr}"
            )

        add = self._git(["worktree", "add", str(wt_path), branch], repo, timeout=120)
        if add.returncode != 0:
            self._git(["branch", "-D", branch], repo)
            raise CursorDelegationError(
                f"worktree add échoué pour {job_id}: {add.stderr or add.stdout}"
            )
        return wt_path, branch

    # ── Cycle de vie des jobs ────────────────────────────────

    async def enqueue(
        self,
        *,
        title: str,
        user_request: str,
        template_id: str = "feature_implementation",
        repository: str | None = None,
        acceptance_criteria: list[str] | None = None,
        required_tests: list[str] | None = None,
        context_files: list[str] | None = None,
        risk_level: str = "medium",
        interaction_mode: str = "chat",
        routing: dict[str, Any] | None = None,
        auto_start: bool = True,
    ) -> dict[str, Any]:
        if not getattr(config, "CURSOR_DELEGATION_ENABLED", True):
            raise CursorDelegationError("CURSOR_DELEGATION_ENABLED=false")

        max_concurrent = int(getattr(config, "CURSOR_MAX_CONCURRENT_JOBS", 2))
        if count_active_cursor_jobs() >= max_concurrent:
            raise CursorDelegationError(
                f"Limite de jobs Cursor atteinte ({max_concurrent})"
            )

        cli = self.refresh_cli()
        if not cli.get("available"):
            raise CursorDelegationError(cli.get("error") or "Cursor CLI indisponible")
        if cli.get("authenticated") is False:
            raise CursorDelegationError("Cursor CLI non authentifié — lancer `agent login`")

        repo = Path(repository or config.BASE_DIR).resolve()
        git_dir = self._git(["rev-parse", "--git-dir"], repo)
        if git_dir.returncode != 0:
            raise CursorDelegationError(f"Dépôt git invalide: {repo}")

        job_id = self._new_job_id()
        composed = await compose_cursor_prompt(
            user_request=user_request,
            template_id=template_id,
            acceptance_criteria=acceptance_criteria,
            required_tests=required_tests,
            context_files=context_files,
            use_main_model=True,
        )

        record = create_cursor_job(
            {
                "job_id": job_id,
                "title": title[:200],
                "user_request": user_request,
                "status": "queued",
                "repository": str(repo),
                "working_directory": str(repo),
                "prompt_template": composed["template_id"],
                "template_version": composed["template_version"],
                "prompt_sent": _redact_secrets(composed["prompt"]),
                "acceptance_criteria": acceptance_criteria or [],
                "required_tests": required_tests or [],
                "risk_level": risk_level,
                "allow_commit": bool(getattr(config, "CURSOR_ALLOW_COMMIT", True)),
                "allow_push": bool(getattr(config, "CURSOR_ALLOW_PUSH", True)),
                "allow_pr": bool(getattr(config, "CURSOR_ALLOW_PR", True)),
                "allow_merge": bool(getattr(config, "CURSOR_ALLOW_MERGE", False)),
                "interaction_mode": interaction_mode,
                "routing": routing or {},
            }
        )

        if auto_start:
            asyncio.create_task(self.run_job(job_id), name=f"cursor-{job_id}")
        return record

    async def run_job(self, job_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._run_job_sync, job_id)

    def _run_job_sync(self, job_id: str) -> dict[str, Any]:
        with self._get_semaphore():
            return self._run_job_locked(job_id)

    def _spawn_cursor(
        self, cmd: list[str], wt_path: Path, timeout: int, job_id: str
    ) -> tuple[int, str]:
        """Lance le CLI dans son propre process group, collecte stdout+stderr."""
        env = {**os.environ, "NO_OPEN_BROWSER": "1"}
        proc = subprocess.Popen(
            cmd,
            cwd=str(wt_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            start_new_session=True,  # process group dédié → cancel fiable
        )
        with self._procs_lock:
            self._procs[job_id] = proc
        try:
            out, _ = proc.communicate(timeout=timeout)
            return proc.returncode, out or ""
        except subprocess.TimeoutExpired:
            self._kill_proc(proc)
            raise
        finally:
            with self._procs_lock:
                self._procs.pop(job_id, None)

    @staticmethod
    def _kill_proc(proc: subprocess.Popen[str]) -> None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass

    def _run_job_locked(self, job_id: str) -> dict[str, Any]:
        job = get_cursor_job(job_id)
        if not job:
            raise CursorDelegationError(f"Job {job_id} introuvable")
        if job["status"] in ("completed", "pr_opened", "cancelled", "rolled_back", "failed"):
            return job

        repo = Path(job["repository"] or config.BASE_DIR)
        update_cursor_job(job_id, status="preparing", started_at=datetime.now().isoformat(timespec="seconds"))

        try:
            wt_path, branch = self._prepare_worktree(job_id, repo)
            update_cursor_job(
                job_id,
                status="running",
                worktree_path=str(wt_path),
                branch_name=branch,
            )

            info = inspect_cursor_cli(getattr(config, "CURSOR_CLI_PATH", "") or None)
            prompt = _redact_secrets(job.get("prompt_sent") or job["user_request"])

            cmd = build_agent_command(
                info,
                prompt,
                workspace=str(wt_path),
                force=True,
                trust=True,
            )
            timeout = int(getattr(config, "CURSOR_DEFAULT_TIMEOUT_SEC", 1800))
            logger.info("[cursor] run %s cmd=%s timeout=%s", job_id, cmd[:6], timeout)

            returncode, raw = self._spawn_cursor(cmd, wt_path, timeout, job_id)

            # Le job a pu être annulé pendant l'exécution
            current = get_cursor_job(job_id)
            if current and current.get("status") == "cancelled":
                return current

            parsed = parse_cursor_result(raw)
            if returncode != 0:
                # Exit non nul = échec CLI, quel que soit le contenu du rapport
                update_cursor_job(
                    job_id,
                    status="failed",
                    raw_output=raw[-200_000:],
                    structured_result={**parsed, "cli_returncode": returncode},
                    error_message=f"Cursor CLI exit={returncode}",
                    finished_at=datetime.now().isoformat(timespec="seconds"),
                )
                self._notify(job_id, "failed", parsed.get("verdict", "BLOCKED"), None, job["title"])
                return get_cursor_job(job_id)  # type: ignore[return-value]

            update_cursor_job(job_id, status="testing", raw_output=raw[-200_000:], structured_result=parsed)

            test_ok, test_log = self._run_required_tests(job, wt_path, timeout)

            sha = self._git(["rev-parse", "HEAD"], wt_path)
            commit_sha = (sha.stdout or "").strip() if sha.returncode == 0 else None

            # Interdiction absolue de commit sur main du repo principal
            branch_check = self._git(["rev-parse", "--abbrev-ref", "HEAD"], wt_path)
            live_branch = (branch_check.stdout or "").strip()
            if live_branch in PROTECTED_BRANCHES:
                update_cursor_job(
                    job_id,
                    status="failed",
                    error_message="Refus: Cursor sur branche protégée main/master",
                    finished_at=datetime.now().isoformat(timespec="seconds"),
                )
                return get_cursor_job(job_id)  # type: ignore[return-value]

            verdict = parsed.get("verdict", "PARTIAL")
            pr_url = None
            final_status = "completed"

            if job.get("allow_pr") and verdict == "COMPLETED" and test_ok:
                update_cursor_job(job_id, status="reviewing")
                pr_url = self._maybe_open_pr(job, job_id, wt_path, live_branch, parsed)
                if pr_url:
                    final_status = "pr_opened"

            if verdict == "BLOCKED" or (not test_ok and (job.get("required_tests") or [])):
                final_status = "failed"

            update_cursor_job(
                job_id,
                status=final_status,
                commit_sha=commit_sha,
                pr_url=pr_url,
                structured_result={
                    **parsed,
                    "cli_returncode": returncode,
                    "test_ok": test_ok,
                    "test_log": test_log[-5000:],
                },
                error_message=None if final_status != "failed" else (
                    parsed.get("error") or "tests échoués ou verdict BLOCKED"
                ),
                finished_at=datetime.now().isoformat(timespec="seconds"),
                raw_output=raw[-200_000:],
            )
            self._notify(job_id, final_status, verdict, pr_url, job["title"])
            return get_cursor_job(job_id)  # type: ignore[return-value]

        except subprocess.TimeoutExpired:
            update_cursor_job(
                job_id,
                status="failed",
                error_message="timeout Cursor CLI",
                finished_at=datetime.now().isoformat(timespec="seconds"),
            )
            self._notify(job_id, "failed", "TIMEOUT", None, job["title"])
            return get_cursor_job(job_id)  # type: ignore[return-value]
        except Exception as exc:
            logger.exception("[cursor] job %s failed", job_id)
            update_cursor_job(
                job_id,
                status="failed",
                error_message=str(exc)[:1000],
                finished_at=datetime.now().isoformat(timespec="seconds"),
            )
            return get_cursor_job(job_id)  # type: ignore[return-value]

    def _run_required_tests(
        self, job: dict[str, Any], wt_path: Path, timeout: int
    ) -> tuple[bool, str]:
        test_ok = True
        test_log = ""
        req_tests = job.get("required_tests") or []
        if not isinstance(req_tests, list):
            return True, ""
        for tcmd in req_tests[:3]:
            if not isinstance(tcmd, str) or not tcmd.strip():
                continue
            # Allowlist minimale : pytest / npm test / pnpm test
            if not re.match(r"^(pytest|npm test|pnpm test|python -m pytest)", tcmd.strip()):
                test_log += f"skip commande non allowlistée: {tcmd}\n"
                continue
            tr = subprocess.run(
                tcmd,
                shell=True,
                cwd=str(wt_path),
                capture_output=True,
                text=True,
                timeout=min(timeout, 900),
            )
            test_log += tr.stdout + tr.stderr
            if tr.returncode != 0:
                test_ok = False
        return test_ok, test_log

    def _maybe_open_pr(
        self,
        job: dict[str, Any],
        job_id: str,
        wt_path: Path,
        live_branch: str,
        parsed: dict[str, Any],
    ) -> str | None:
        if not job.get("allow_push"):
            return None
        push = self._git(["push", "-u", "origin", live_branch], wt_path, timeout=120)
        if push.returncode != 0:
            logger.warning("[cursor] push %s échoué : %s", job_id, (push.stderr or "")[:200])
            return None
        pr = subprocess.run(
            [
                "gh", "pr", "create",
                "--title", job["title"][:80],
                "--body", f"Job Cursor `{job_id}`\n\n{str(parsed.get('body', ''))[:3000]}",
                "--head", live_branch,
            ],
            cwd=str(wt_path),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if pr.returncode == 0:
            lines = (pr.stdout or "").strip().splitlines()
            return lines[-1] if lines else None
        logger.warning("[cursor] gh pr create %s : %s", job_id, (pr.stderr or "")[:200])
        return None

    def _notify(
        self, job_id: str, final_status: str, verdict: str, pr_url: str | None, title: str
    ) -> None:
        try:
            from jarvis.notification_service import notification_service

            notification_service.create(
                source="cursor",
                title=f"Cursor {final_status}: {title[:60]}",
                content=f"Job {job_id} — verdict {verdict}"
                + (f" — {pr_url}" if pr_url else ""),
                priority="high" if final_status in ("pr_opened", "completed") else "medium",
            )
        except Exception as exc:
            logger.debug("[cursor] notif skip: %s", exc)

    # ── Cancel / rollback / reprise ──────────────────────────

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        job = get_cursor_job(job_id)
        if not job:
            return None
        if job["status"] in ("completed", "pr_opened", "rolled_back"):
            return job
        # Statut D'ABORD, kill ensuite : le thread runner qui sort de
        # communicate() lit le statut immédiatement — l'ordre inverse crée une
        # course où l'exit -15 est requalifié en `failed`.
        result = update_cursor_job(
            job_id,
            status="cancelled",
            finished_at=datetime.now().isoformat(timespec="seconds"),
        )
        with self._procs_lock:
            proc = self._procs.get(job_id)
        if proc is not None:
            logger.info("[cursor] cancel %s — kill process group %s", job_id, proc.pid)
            self._kill_proc(proc)
        return result

    def rollback(self, job_id: str) -> dict[str, Any] | None:
        """Supprime le worktree et la branche locale — jamais de force-push main."""
        job = get_cursor_job(job_id)
        if not job:
            return None
        repo = Path(job["repository"] or config.BASE_DIR)
        wt = job.get("worktree_path")
        branch = job.get("branch_name")
        if wt and Path(wt).exists():
            self._git(["worktree", "remove", "--force", wt], repo, timeout=60)
            shutil.rmtree(wt, ignore_errors=True)
        if branch and branch not in PROTECTED_BRANCHES:
            self._git(["branch", "-D", branch], repo)
        return update_cursor_job(
            job_id,
            status="rolled_back",
            finished_at=datetime.now().isoformat(timespec="seconds"),
        )

    def resume_pending_jobs(self) -> dict[str, int]:
        """Reprise après restart backend.

        - ``queued`` → relancés (le prompt est déjà composé et persisté) ;
        - ``preparing``/``running``/``testing``/``reviewing`` → marqués
          ``failed`` (le process est mort avec l'ancien backend ; un retry
          explicite reste possible via l'API).
        """
        requeued = 0
        orphaned = 0
        try:
            for job in list_jobs_by_statuses(("preparing", "running", "testing", "reviewing")):
                update_cursor_job(
                    job["job_id"],
                    status="failed",
                    error_message="interrompu par un redémarrage backend",
                    finished_at=datetime.now().isoformat(timespec="seconds"),
                )
                orphaned += 1
            for job in list_jobs_by_statuses(("queued",)):
                asyncio.create_task(
                    self.run_job(job["job_id"]), name=f"cursor-resume-{job['job_id']}"
                )
                requeued += 1
        except Exception as exc:
            logger.warning("[cursor] resume_pending_jobs : %s", exc)
        if requeued or orphaned:
            logger.info(
                "[cursor] reprise: %d requeued, %d orphelins marqués failed",
                requeued, orphaned,
            )
        return {"requeued": requeued, "orphaned": orphaned}

    # ── Lecture ──────────────────────────────────────────────

    def list_jobs(self, limit: int = 50, status: str | None = None) -> list[dict[str, Any]]:
        return list_cursor_jobs(limit=limit, status=status)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        return get_cursor_job(job_id)


cursor_delegation = CursorDelegationService()
