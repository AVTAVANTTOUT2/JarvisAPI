"""Orchestration de la boucle autonome DevAgent (plan/code/test/fix/commit)."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

import config
from agents.devagent.coder import CODER_PROMPT, FIXER_PROMPT
from agents.devagent.agentic_runtime import (
    AgenticRuntimeUnavailable,
    EngineeringWorktree,
    delegate_devagent_iteration,
    legacy_fallback_enabled,
    resolve_runtime,
    select_test_command,
    settle_engineering_delivery,
    validate_and_commit_engineering_worktree,
)
from agents.devagent.executor import (
    git_commit,
    git_current_sha,
    git_init,
    resolve_generated_path,
    run_isolated,
    setup_venv,
)
from agents.devagent.planner import ACCEPTANCE_JUDGE_PROMPT, PLANNER_PROMPT
from agents.devagent.utils import parse_json_response
from database import devagent as devagent_db
from integrations.deepseek_client import call_deepseek
from jarvis.agentic.redaction import redact_text
from jarvis.security.llm_data_boundary import wrap_untrusted_data
from jarvis.security.redaction import redact_persisted_mapping

logger = logging.getLogger(__name__)

_running: set[int] = set()


def _write_state_file(project_path: Path, state: dict[str, Any]) -> None:
    git_directory = project_path / ".git"
    if git_directory.is_dir() and not git_directory.is_symlink():
        state_root = git_directory / "jarvis-devagent"
        if state_root.is_symlink():
            raise RuntimeError("racine d'état DevAgent symbolique refusée")
        state_root.mkdir(mode=0o700, exist_ok=True)
        if os.name != "nt":
            os.chmod(state_root, 0o700)
        state_path = state_root / "state.json"
    else:
        state_root = project_path
        state_path = project_path / ".devagent_state.json"
    safe_state = redact_persisted_mapping(state)
    payload = json.dumps(safe_state, indent=2, ensure_ascii=False).encode("utf-8")
    temporary = state_root / f".{state_path.name}.{uuid.uuid4().hex}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("écriture atomique de l'état DevAgent interrompue")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, state_path)
    if os.name != "nt":
        os.chmod(state_path, 0o600)
        directory_fd = os.open(state_root, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def _read_existing_files(project_path: Path, files: list[str]) -> dict[str, str]:
    existing: dict[str, str] = {}
    for rel in files:
        full = resolve_generated_path(project_path, rel)
        existing[rel] = full.read_text(encoding="utf-8") if full.exists() else ""
    return existing


def _write_generated_files(project_path: Path, files: dict[str, str]) -> None:
    resolved_files = [
        (resolve_generated_path(project_path, rel), content)
        for rel, content in files.items()
    ]
    for full, content in resolved_files:
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")


def _list_src_files(project_path: Path) -> list[str]:
    src = project_path / "src"
    if not src.exists():
        return []
    return sorted(str(p.relative_to(src)) for p in src.rglob("*") if p.is_file())


def _budget_exceeded(state: dict[str, Any], budget: dict[str, Any]) -> bool:
    if state["iteration"] >= int(budget.get("max_iterations", 25)):
        return True
    if state.get("tokens_used", 0) >= int(budget.get("max_tokens", 500_000)):
        return True
    if state.get("consecutive_failures", 0) >= int(
        budget.get("max_consecutive_failures", 3)
    ):
        return True
    return False


def _accumulate_tokens(state: dict[str, Any], response: dict[str, Any]) -> None:
    state["tokens_used"] = int(state.get("tokens_used", 0)) + int(
        response.get("tokens_total", 0)
    )


async def _judge_acceptance(
    spec: dict[str, Any],
    project_path: Path,
    test_output: str,
    state: dict[str, Any],
) -> bool:
    criteria = spec.get("acceptance_criteria") or []
    if not criteria:
        return False

    response = await call_deepseek(
        system=ACCEPTANCE_JUDGE_PROMPT.format(
            spec_json=json.dumps(spec, ensure_ascii=False),
            test_output=wrap_untrusted_data(
                "DEVAGENT_TEST_OUTPUT",
                test_output,
                max_chars=4_000,
            ),
            file_list=json.dumps(_list_src_files(project_path)),
        ),
        user="Evalue les criteres d'acceptation.",
        json_mode=True,
    )
    _accumulate_tokens(state, response)
    verdict = parse_json_response(response["content"])
    return bool(verdict.get("done"))


async def run_loop(project_id: int) -> None:
    """Boucle autonome plan -> code -> test -> fix -> commit."""
    if project_id in _running:
        logger.warning("[devagent] boucle deja active project_id=%s", project_id)
        return

    _running.add(project_id)
    try:
        try:
            resolve_runtime()
            await _run_agentic_loop_inner(project_id)
        except AgenticRuntimeUnavailable as exc:
            if legacy_fallback_enabled():
                logger.warning("[devagent] fallback historique explicite: %s", exc)
                await _run_legacy_loop_inner(project_id)
            else:
                logger.error("[devagent] runtime agentique indisponible: %s", exc)
                devagent_db.update_project_status(project_id, "failed")
    finally:
        _running.discard(project_id)


async def _run_agentic_loop_inner(project_id: int) -> None:
    """Boucle native: runtime éditeur, JARVIS validateur et propriétaire Git."""

    project = devagent_db.get_project(project_id)
    if not project or not project.get("spec_json"):
        logger.error("[devagent] spec absente project_id=%s", project_id)
        devagent_db.update_project_status(project_id, "failed")
        return

    spec = json.loads(project["spec_json"])
    project_path = Path(spec["isolation_path"]).resolve(strict=True)
    budget = spec.get("loop_budget") or {}
    state = devagent_db.get_loop_state(project_id)

    setup_venv(project_path)
    git_init(project_path)
    devagent_db.update_project_status(project_id, "running")

    while not _budget_exceeded(state, budget):
        current = devagent_db.get_project(project_id)
        if not current or current.get("status") == "paused":
            logger.info("[devagent] boucle en pause project_id=%s", project_id)
            return

        iteration = int(state.get("iteration", 0))
        test_command = select_test_command(project_path, spec)
        if test_command is None:
            state["consecutive_failures"] = (
                int(state.get("consecutive_failures", 0)) + 1
            )
            state["last_error"] = (
                "aucune politique de validation JARVIS pour cette pile"
            )
            state["iteration"] = iteration + 1
            devagent_db.log_iteration(
                project_id,
                iteration,
                "test_policy",
                state["last_error"],
                False,
            )
            devagent_db.update_loop_state(project_id, state)
            _write_state_file(project_path, state)
            continue

        base_sha = git_current_sha(project_path)
        state["iteration_base_sha"] = base_sha
        state["phase"] = "agentic_edit"
        devagent_db.update_loop_state(project_id, state)
        _write_state_file(project_path, state)

        outcome = await delegate_devagent_iteration(
            project_id=project_id,
            spec=spec,
            state=state,
            workspace=project_path,
        )
        success = False
        delivery: dict[str, Any] = {
            "ok": False,
            "status": "runtime_failed",
            "validations": [],
        }
        test_duration_ms = 0.0

        for attempt in range(2):
            action = "agentic_edit" if attempt == 0 else "agentic_repair"
            state["runtime_run_id"] = outcome.run_id
            devagent_db.log_iteration(
                project_id,
                iteration,
                action,
                json.dumps(
                    {
                        "run_id": outcome.run_id,
                        "status": outcome.status,
                        "phase": outcome.phase,
                        "changed_files": list(outcome.changed_files),
                        "summary": outcome.summary,
                        "error_code": outcome.error_code,
                    },
                    ensure_ascii=False,
                ),
                outcome.succeeded,
            )
            if not outcome.succeeded:
                if (
                    outcome.status == "provider_unavailable"
                    or outcome.error_code == "runtime_unavailable"
                ):
                    raise AgenticRuntimeUnavailable(
                        outcome.summary or "runtime indisponible"
                    )
                delivery = {
                    "ok": False,
                    "status": outcome.error_code or outcome.status,
                    "validations": [],
                }
                await settle_engineering_delivery(
                    service=outcome.runtime_service,
                    run_id=outcome.run_id,
                    delivery=delivery,
                )
                break

            state["phase"] = "test"
            devagent_db.update_loop_state(project_id, state)
            started = time.monotonic()
            delivery = validate_and_commit_engineering_worktree(
                EngineeringWorktree(
                    job_id=f"devagent-{project_id}-{iteration}",
                    repo_root=project_path,
                    workspace=project_path,
                    branch=f"devagent/{project_id}",
                    base_branch=base_sha,
                ),
                required_tests=(test_command,),
                commit_message=f"iteration {iteration}: critères DevAgent validés",
                verified_artifacts=outcome.changed_artifacts,
            )
            test_duration_ms = (time.monotonic() - started) * 1000
            success = bool(delivery.get("ok")) and delivery.get("status") in {
                "committed",
                "already_committed",
            }
            validations = delivery.get("validations") or ()
            test_log = {
                "status": delivery.get("status"),
                "validations": list(validations),
            }
            devagent_db.log_iteration(
                project_id,
                iteration,
                "test",
                json.dumps(
                    redact_persisted_mapping(test_log),
                    ensure_ascii=False,
                ),
                success,
            )
            devagent_db.log_iteration(
                project_id,
                iteration,
                "commit",
                json.dumps(
                    redact_persisted_mapping(delivery),
                    ensure_ascii=False,
                ),
                success,
            )
            await settle_engineering_delivery(
                service=outcome.runtime_service,
                run_id=outcome.run_id,
                delivery=delivery,
            )
            if success or attempt == 1:
                break

            last_validation = validations[-1] if validations else {}
            repair_output = str(
                last_validation.get("stderr")
                or last_validation.get("stdout")
                or delivery.get("status")
                or "validation JARVIS échouée"
            )
            state["phase"] = "agentic_repair"
            devagent_db.update_loop_state(project_id, state)
            outcome = await delegate_devagent_iteration(
                project_id=project_id,
                spec=spec,
                state=state,
                workspace=project_path,
                repair_output=repair_output,
            )

        if success:
            state["consecutive_failures"] = 0
            state["last_error"] = None
            try:
                from scripts.perf_regression import guard_devagent_iteration

                perf_report = await guard_devagent_iteration(
                    project_path,
                    spec["slug"],
                    git_current_sha(project_path),
                    test_duration_ms,
                )
                devagent_db.log_iteration(
                    project_id,
                    iteration,
                    "perf_guard",
                    json.dumps(perf_report, ensure_ascii=False),
                    not perf_report.get("rolled_back"),
                )
            except Exception as exc:
                logger.warning("[devagent] perf_guard : %s", exc)

            devagent_db.update_project_status(project_id, "done")
            state["phase"] = "done"
            devagent_db.update_loop_state(project_id, state)
            _write_state_file(project_path, state)

            if config.DEVAGENT_AUTO_PR:
                try:
                    from agents.devagent.pr import generate_pr_description

                    pr_report = await generate_pr_description(
                        project_path,
                        spec.get("project_name", spec["slug"]),
                    )
                    devagent_db.log_iteration(
                        project_id,
                        iteration,
                        "pr",
                        json.dumps(pr_report, ensure_ascii=False),
                        pr_report.get("ok", False),
                    )
                except Exception as exc:
                    logger.warning("[devagent] generate_pr_description : %s", exc)
            return

        state["consecutive_failures"] = int(state.get("consecutive_failures", 0)) + 1
        state["last_error"] = redact_text(
            delivery.get("status") or "validation JARVIS échouée",
            max_chars=2_000,
        )
        state["iteration"] = iteration + 1
        devagent_db.update_loop_state(project_id, state)
        _write_state_file(project_path, state)

    current = devagent_db.get_project(project_id)
    if current and current.get("status") == "paused":
        return
    devagent_db.update_project_status(project_id, "failed")
    state["phase"] = "stopped"
    devagent_db.update_loop_state(project_id, state)
    _write_state_file(project_path, state)


async def _run_legacy_loop_inner(project_id: int) -> None:
    project = devagent_db.get_project(project_id)
    if not project or not project.get("spec_json"):
        logger.error("[devagent] spec absente project_id=%s", project_id)
        devagent_db.update_project_status(project_id, "failed")
        return

    spec = json.loads(project["spec_json"])
    project_path = Path(spec["isolation_path"])
    budget = spec.get("loop_budget") or {}
    state = devagent_db.get_loop_state(project_id)

    setup_venv(project_path)
    git_init(project_path)

    devagent_db.update_project_status(project_id, "running")

    while not _budget_exceeded(state, budget):
        current = devagent_db.get_project(project_id)
        if not current or current.get("status") == "paused":
            logger.info("[devagent] boucle en pause project_id=%s", project_id)
            break

        iteration = int(state.get("iteration", 0))
        state["phase"] = "plan"
        devagent_db.update_loop_state(project_id, state)
        _write_state_file(project_path, state)

        # PLAN
        plan_response = await call_deepseek(
            system=PLANNER_PROMPT.format(
                spec_json=json.dumps(spec, ensure_ascii=False),
                state_json=json.dumps(state, ensure_ascii=False),
                last_log=wrap_untrusted_data(
                    "DEVAGENT_LAST_ERROR",
                    state.get("last_error") or "",
                    max_chars=2_000,
                ),
            ),
            user="Planifie la prochaine tache.",
            json_mode=True,
        )
        _accumulate_tokens(state, plan_response)
        plan = parse_json_response(plan_response["content"])
        devagent_db.log_iteration(
            project_id, iteration, "plan", plan_response["content"], True
        )

        files = plan.get("files_to_create_or_edit") or []
        if not isinstance(files, list):
            files = []

        # CODE
        state["phase"] = "code"
        devagent_db.update_loop_state(project_id, state)
        existing = _read_existing_files(project_path, files)
        code_response = await call_deepseek(
            system=CODER_PROMPT.format(
                task=plan.get("task", ""),
                files=json.dumps(files),
                existing_content=json.dumps(existing, ensure_ascii=False),
                constraints=json.dumps(spec.get("constraints", []), ensure_ascii=False),
            ),
            user="Genere le code.",
            json_mode=True,
        )
        _accumulate_tokens(state, code_response)
        code = parse_json_response(code_response["content"])
        generated = code.get("files") or {}
        if isinstance(generated, dict):
            _write_generated_files(project_path, generated)
        devagent_db.log_iteration(
            project_id, iteration, "code", code_response["content"], True
        )

        test_command = code.get("test_command") or "python3 -m pytest -q"
        success = False
        test_result: dict[str, Any] = {}
        test_duration_ms = 0.0

        for attempt in range(2):
            state["phase"] = "test"
            devagent_db.update_loop_state(project_id, state)
            _t0 = time.monotonic()
            test_result = run_isolated(test_command, cwd=project_path, timeout=120)
            test_duration_ms = (time.monotonic() - _t0) * 1000
            success = test_result.get("returncode") == 0
            devagent_db.log_iteration(
                project_id,
                iteration,
                "test",
                json.dumps(test_result, ensure_ascii=False),
                success,
            )

            if success:
                break

            state["phase"] = "fix"
            devagent_db.update_loop_state(project_id, state)
            fix_response = await call_deepseek(
                system=FIXER_PROMPT.format(
                    task=plan.get("task", ""),
                    error=wrap_untrusted_data(
                        "DEVAGENT_TEST_OUTPUT",
                        test_result.get("stderr") or test_result.get("stdout") or "",
                        max_chars=4_000,
                    ),
                    files=json.dumps(files),
                    existing_content=json.dumps(
                        _read_existing_files(project_path, files), ensure_ascii=False
                    ),
                ),
                user="Corrige le code.",
                json_mode=True,
            )
            _accumulate_tokens(state, fix_response)
            fix_payload = parse_json_response(fix_response["content"])
            fix_files = fix_payload.get("files") or {}
            if isinstance(fix_files, dict):
                _write_generated_files(project_path, fix_files)
            test_command = fix_payload.get("test_command") or test_command
            devagent_db.log_iteration(
                project_id, iteration, "fix", fix_response["content"], False
            )

        if success:
            state["phase"] = "commit"
            commit_msg = f"iteration {iteration}: {plan.get('task', 'task')}"
            commit_result = git_commit(project_path, commit_msg)
            devagent_db.log_iteration(
                project_id,
                iteration,
                "commit",
                json.dumps(commit_result, ensure_ascii=False),
                commit_result.get("returncode") == 0,
            )
            state["consecutive_failures"] = 0
            state["last_error"] = None

            # Détection de régression de perf — rollback auto si ce commit a
            # nettement ralenti la suite de tests par rapport à la référence.
            try:
                from scripts.perf_regression import guard_devagent_iteration

                perf_report = await guard_devagent_iteration(
                    project_path,
                    spec["slug"],
                    git_current_sha(project_path),
                    test_duration_ms,
                )
                devagent_db.log_iteration(
                    project_id,
                    iteration,
                    "perf_guard",
                    json.dumps(perf_report, ensure_ascii=False),
                    not perf_report.get("rolled_back"),
                )
            except Exception as e:
                logger.warning("[devagent] perf_guard : %s", e)

            if await _judge_acceptance(
                spec,
                project_path,
                json.dumps(test_result, ensure_ascii=False),
                state,
            ):
                devagent_db.update_project_status(project_id, "done")
                state["phase"] = "done"
                devagent_db.update_loop_state(project_id, state)
                _write_state_file(project_path, state)
                logger.info(
                    "[devagent] criteres acceptation OK project_id=%s", project_id
                )

                if config.DEVAGENT_AUTO_PR:
                    try:
                        from agents.devagent.pr import generate_pr_description

                        pr_report = await generate_pr_description(
                            project_path,
                            spec.get("project_name", spec["slug"]),
                        )
                        devagent_db.log_iteration(
                            project_id,
                            iteration,
                            "pr",
                            json.dumps(pr_report, ensure_ascii=False),
                            pr_report.get("ok", False),
                        )
                    except Exception as e:
                        logger.warning("[devagent] generate_pr_description : %s", e)
                return
        else:
            state["consecutive_failures"] = (
                int(state.get("consecutive_failures", 0)) + 1
            )
            state["last_error"] = str(
                test_result.get("stderr") or test_result.get("stdout") or "test failed"
            )[:2000]

        state["iteration"] = iteration + 1
        devagent_db.update_loop_state(project_id, state)
        _write_state_file(project_path, state)

    final_status = devagent_db.get_project(project_id)
    if final_status and final_status.get("status") == "paused":
        return

    if state.get("consecutive_failures", 0) >= int(
        budget.get("max_consecutive_failures", 3)
    ):
        devagent_db.update_project_status(project_id, "failed")
    elif state["iteration"] >= int(budget.get("max_iterations", 25)):
        devagent_db.update_project_status(project_id, "done")
    elif state.get("tokens_used", 0) >= int(budget.get("max_tokens", 500_000)):
        devagent_db.update_project_status(project_id, "failed")
    else:
        devagent_db.update_project_status(project_id, "done")

    state["phase"] = "stopped"
    devagent_db.update_loop_state(project_id, state)
    _write_state_file(project_path, state)
