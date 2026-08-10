#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.engineering_team.workflow import EngineeringTeam


def _print(payload: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Boucle locale de l'équipe d'ingénierie JARVIS"
    )
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("doctor", "status"):
        command = sub.add_parser(name)
        command.add_argument("--json", action="store_true")

    cycle = sub.add_parser("cycle")
    cycle.add_argument("--json", action="store_true")
    cycle.add_argument("--no-publish", action="store_true")

    enqueue = sub.add_parser("enqueue")
    enqueue.add_argument("--title", required=True)
    enqueue.add_argument("--request", required=True)
    enqueue.add_argument("--acceptance", action="append", default=[])
    enqueue.add_argument("--test", action="append", required=True)
    enqueue.add_argument("--priority", type=int, default=50)
    enqueue.add_argument("--issue", type=int)
    enqueue.add_argument("--issue-url")
    enqueue.add_argument("--json", action="store_true")

    complete = sub.add_parser("complete")
    complete.add_argument("task_id")
    complete.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    team = EngineeringTeam(root=args.root)
    if args.command == "doctor":
        payload = team.doctor()
    elif args.command == "status":
        payload = team.status()
    elif args.command == "cycle":
        payload = team.cycle(publish=not args.no_publish)
    elif args.command == "enqueue":
        payload = team.enqueue(
            title=args.title,
            request=args.request,
            acceptance_criteria=args.acceptance,
            required_tests=args.test,
            priority=args.priority,
            issue_number=args.issue,
            issue_url=args.issue_url,
        ).to_dict()
    elif args.command == "complete":
        payload = team.complete(args.task_id).to_dict()
    else:
        raise AssertionError(args.command)
    _print(payload, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
