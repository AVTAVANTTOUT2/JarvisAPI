"""Runner opérateur du vertical TaskControl -> OpenCode -> livraison JARVIS.

Le parcours est volontairement en deux commandes : ``plan`` persiste un plan
sans effet, puis ``approve`` exige sa version et son digest exacts. Aucune
commande n'écrit dans ``.env`` et aucune publication externe n'est possible.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from typing import Any, Sequence


_MISSING_KEY = "DEEPSEEK_API_KEY absente de l'environnement du processus"


def _emit(payload: dict[str, Any], *, stream: Any | None = None) -> None:
    print(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        file=stream or sys.stdout,
        flush=True,
    )


def _live_key_available() -> bool:
    value = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    return bool(value and value != "sk-...")


def _not_executed(*, reason: str) -> int:
    _emit(
        {
            "ok": False,
            "production": "NOT_EXECUTED",
            "reason": reason,
            "runtime": "opencode@1.18.16",
        }
    )
    return 3


async def _plan(args: argparse.Namespace) -> int:
    import database
    from integrations.opencode.lifecycle.release import ReleaseManifest
    from jarvis.task_control.service import get_task_control_service

    database.init_db()
    service = get_task_control_service()
    runtime_version = ReleaseManifest.load().version
    task = await service.create_engineering_task(
        title=args.title,
        user_request=args.request,
        repo_root=Path(args.repo),
        required_tests=tuple(args.test),
        acceptance_criteria=tuple(args.acceptance),
        commit_message=args.commit_message or args.title,
        idempotency_key=args.idempotency_key,
        runtime_id="opencode",
        runtime_version=runtime_version,
    )
    plan = service.repository.get_plan(task.task_id, task.plan_version)
    if plan is None:
        _emit(
            {
                "ok": False,
                "production": "NOT_EXECUTED",
                "reason": "plan_unavailable",
                "task_id": task.task_id,
            }
        )
        return 4
    _emit(
        {
            "ok": True,
            "production": "AWAITING_PLAN_APPROVAL",
            "task_id": task.task_id,
            "plan_version": plan.version,
            "plan_digest": plan.digest,
            "plan": plan.to_dict(),
        }
    )
    return 0


async def _approve(args: argparse.Namespace) -> int:
    import database
    from agents.devagent.finalizer import process_engineering_finalizers_once
    from jarvis.task_control.models import PlanDecision, TaskStatus
    from jarvis.task_control.service import get_task_control_service

    database.init_db()
    service = get_task_control_service()
    service.bind_runtime_events()
    plan = service.repository.get_plan(args.task_id, args.plan_version)
    if plan is None or plan.digest != args.plan_digest:
        return _not_executed(reason="plan_version_or_digest_mismatch")

    task = await service.decide_plan(
        args.task_id,
        args.plan_version,
        decision=PlanDecision.APPROVED,
        actor="task-control-delivery-live",
        comment=f"Digest approuvé: {args.plan_digest}",
    )
    deadline = asyncio.get_running_loop().time() + args.timeout
    terminal = {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.BLOCKED,
        TaskStatus.CANCELLED,
    }
    finalizer_results: list[dict[str, Any]] = []
    while asyncio.get_running_loop().time() < deadline:
        finalizer_results.extend(
            await process_engineering_finalizers_once(service=service.agentic)
        )
        task = service.repository.require_task(task.task_id)
        if task.status in terminal:
            break
        await asyncio.sleep(0.25)
    else:
        await service.cancel_task(
            task.task_id,
            reason="Délai du runner live dépassé; runtime annulé.",
        )
        _emit(
            {
                "ok": False,
                "production": "EXECUTED_TIMEOUT",
                "task_id": task.task_id,
                "run_id": task.agentic_run_id,
            }
        )
        return 5

    report = service.repository.latest_report(task.task_id)
    artifacts = (
        service.agentic.artifacts(task.agentic_run_id)
        if task.agentic_run_id
        else ()
    )
    completed = task.status is TaskStatus.COMPLETED
    _emit(
        {
            "ok": completed,
            "production": "EXECUTED" if completed else "EXECUTED_FAILED",
            "task_id": task.task_id,
            "run_id": task.agentic_run_id,
            "task_status": task.status.value,
            "report_id": report.report_id if report is not None else None,
            "result_status": report.result_status if report is not None else None,
            "artifact_types": sorted({item.type for item in artifacts}),
            "finalizers": [
                {
                    "job_id": item.get("job_id"),
                    "ok": item.get("ok"),
                    "status": item.get("status"),
                }
                for item in finalizer_results
            ],
        }
    )
    return 0 if completed else 6


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preuve live TaskControl/OpenCode, sans push ni écriture .env."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    plan = commands.add_parser("plan", help="Créer un plan sans aucun effet dépôt")
    plan.add_argument("--repo", required=True)
    plan.add_argument("--title", required=True)
    plan.add_argument("--request", required=True)
    plan.add_argument("--test", action="append", required=True)
    plan.add_argument("--acceptance", action="append", default=[])
    plan.add_argument("--commit-message")
    plan.add_argument("--idempotency-key", required=True)

    approve = commands.add_parser(
        "approve", help="Approuver une version et une empreinte exactes"
    )
    approve.add_argument("--task-id", required=True)
    approve.add_argument("--plan-version", type=int, required=True)
    approve.add_argument("--plan-digest", required=True)
    approve.add_argument("--timeout", type=float, default=600.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not _live_key_available():
        return _not_executed(reason=_MISSING_KEY)
    if args.command == "plan":
        return asyncio.run(_plan(args))
    if args.timeout <= 0 or args.timeout > 7_200:
        raise SystemExit("--timeout doit être compris entre 0 et 7200 secondes")
    return asyncio.run(_approve(args))


if __name__ == "__main__":  # pragma: no cover - entrée CLI
    raise SystemExit(main())
