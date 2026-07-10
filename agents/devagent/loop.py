"""Orchestration de la boucle autonome DevAgent (plan/code/test/fix/commit)."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import config
from agents.devagent.coder import CODER_PROMPT, FIXER_PROMPT
from agents.devagent.executor import git_commit, git_current_sha, git_init, run_isolated, setup_venv
from agents.devagent.planner import ACCEPTANCE_JUDGE_PROMPT, PLANNER_PROMPT
from agents.devagent.utils import parse_json_response
from database import devagent as devagent_db
from integrations.deepseek_client import call_deepseek

logger = logging.getLogger(__name__)

_running: set[int] = set()


def _write_state_file(project_path: Path, state: dict[str, Any]) -> None:
    state_path = project_path / ".devagent_state.json"
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _read_existing_files(project_path: Path, files: list[str]) -> dict[str, str]:
    existing: dict[str, str] = {}
    for rel in files:
        full = project_path / "src" / rel
        existing[rel] = full.read_text(encoding="utf-8") if full.exists() else ""
    return existing


def _write_generated_files(project_path: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        full = project_path / "src" / rel
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
            test_output=test_output[:4000],
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
        await _run_loop_inner(project_id)
    finally:
        _running.discard(project_id)


async def _run_loop_inner(project_id: int) -> None:
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
                last_log=state.get("last_error") or "",
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
                    error=test_result.get("stderr") or test_result.get("stdout") or "",
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
                    project_path, spec["slug"], git_current_sha(project_path), test_duration_ms,
                )
                devagent_db.log_iteration(
                    project_id, iteration, "perf_guard",
                    json.dumps(perf_report, ensure_ascii=False), not perf_report.get("rolled_back"),
                )
            except Exception as e:
                logger.warning("[devagent] perf_guard : %s", e)

            # Déploiement staging après des tests verts — filet de sécurité,
            # ne bloque jamais la boucle même en cas d'échec.
            if config.DEVAGENT_AUTO_DEPLOY_STAGING:
                try:
                    from agents.devagent.staging import deploy_to_staging

                    deploy_report = deploy_to_staging(project_id, project_path, test_command)
                    devagent_db.log_iteration(
                        project_id, iteration, "staging",
                        json.dumps(deploy_report, ensure_ascii=False), deploy_report.get("ok", False),
                    )
                except Exception as e:
                    logger.warning("[devagent] deploy_to_staging : %s", e)

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
                logger.info("[devagent] criteres acceptation OK project_id=%s", project_id)

                if config.DEVAGENT_AUTO_PR:
                    try:
                        from agents.devagent.pr import generate_pr_description

                        pr_report = await generate_pr_description(
                            project_path, spec.get("project_name", spec["slug"]),
                        )
                        devagent_db.log_iteration(
                            project_id, iteration, "pr", json.dumps(pr_report, ensure_ascii=False),
                            pr_report.get("ok", False),
                        )
                    except Exception as e:
                        logger.warning("[devagent] generate_pr_description : %s", e)
                return
        else:
            state["consecutive_failures"] = int(state.get("consecutive_failures", 0)) + 1
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
