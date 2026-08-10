#!/usr/bin/env python3
"""Ajoute le contrat de handoff Cursor -> GitHub -> Codex aux automations."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

UPDATE_URL = "https://api2.cursor.sh/aiserver.v1.AutomationsService/UpdateAutomation"
DEFAULT_SOURCE = (
    Path.home() / "Documents" / "Codex" / "cursor-automations-hardening-applied.json"
)
CURSOR_STATE_DB = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Cursor"
    / "User"
    / "globalStorage"
    / "state.vscdb"
)
START_MARKER = "<!-- JARVIS_ENGINEERING_HANDOFF_V1_START -->"
END_MARKER = "<!-- JARVIS_ENGINEERING_HANDOFF_V1_END -->"
TECHNICAL_AUTOMATIONS = {
    "Find critical bugs",
    "Add test coverage",
    "Scan codebase for vulnerabilities",
    "Generate docs",
    "Autofix PR review comments",
    "Fix CI failures",
}


def contract_for(name: str) -> str:
    return f"""{START_MARKER}
## Mandatory JARVIS engineering handoff

This automation is a Cursor detector/specialist. GitHub is the authoritative
queue; Slack is notification only. Every validated finding or code change must
finish with a GitHub handoff that Codex and Claude can process.

Automation identity: `{name}`.

1. If you create or update a pull request:
   - add the labels `cursor-finding`, `agent-ready`, and `agent-managed`;
   - keep the PR draft;
   - include source=cursor, automation name, severity/risk, root cause,
     acceptance criteria, exact tests run, and remaining uncertainty in the PR body;
   - never merge the PR.
2. If no safe PR can be produced (including report-only security scans):
   - create or update one deduplicated GitHub Issue;
   - add labels `cursor-finding` and `agent-ready` (plus `security` when relevant);
   - include evidence without secrets, impact, root cause, affected files/symbols,
     acceptance criteria, and safe validation commands;
   - do not post a finding only to Slack.
3. The Slack message must contain the GitHub Issue or PR URL and
   `HANDOFF=codex`. Never claim completion before the GitHub handoff exists.
4. Reuse an existing open Issue/PR for the same root cause. Store a stable
   fingerprint in memory to prevent duplicate GitHub items.
5. Codex owns verification and correction. Claude owns the final read-only
   review. The deterministic orchestrator alone may merge after tests and CI pass.
{END_MARKER}"""


def inject_contract(prompt: str, name: str) -> str:
    before = prompt.split(START_MARKER, 1)[0].rstrip()
    if START_MARKER in prompt and END_MARKER in prompt:
        suffix = prompt.split(END_MARKER, 1)[1].strip()
        if suffix:
            before = before + "\n\n" + suffix
    return before.rstrip() + "\n\n" + contract_for(name) + "\n"


def build_updates(source: Path) -> dict[str, dict[str, Any]]:
    payloads = json.loads(source.read_text(encoding="utf-8"))
    updates: dict[str, dict[str, Any]] = {}
    for name in sorted(TECHNICAL_AUTOMATIONS):
        payload = payloads.get(name)
        if not isinstance(payload, dict) or not payload.get("enabled"):
            continue
        prompts = (payload.get("workflow") or {}).get("prompts") or []
        if not prompts or not isinstance(prompts[0], dict):
            raise ValueError(f"prompt Cursor invalide pour {name}")
        current = str(prompts[0].get("prompt") or "")
        prompts[0]["prompt"] = inject_contract(current, name)
        updates[name] = payload
    return updates


def read_cursor_access_token(database: Path = CURSOR_STATE_DB) -> str:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT value FROM ItemTable WHERE key='cursorAuth/accessToken'"
        ).fetchone()
    finally:
        connection.close()
    if not row or not row[0]:
        raise RuntimeError("session Cursor introuvable")
    return str(row[0])


def post_update(
    name: str, payload: dict[str, Any], access_token: str
) -> dict[str, Any]:
    request = urllib.request.Request(
        UPDATE_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            response.read(1)
            return {"name": name, "ok": True, "http_status": response.status}
    except urllib.error.HTTPError as exc:
        exc.read()
        return {"name": name, "ok": False, "http_status": exc.code}
    except urllib.error.URLError as exc:
        return {"name": name, "ok": False, "error": str(exc.reason)[:300]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    updates = build_updates(args.source)
    if not args.apply:
        print(
            json.dumps(
                {"mode": "dry-run", "automations": sorted(updates)}, ensure_ascii=False
            )
        )
        return 0

    access_token = read_cursor_access_token()
    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(post_update, name, payload, access_token): name
            for name, payload in updates.items()
        }
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda item: str(item["name"]))
    print(json.dumps({"mode": "apply", "results": results}, ensure_ascii=False))
    return 0 if all(result.get("ok") for result in results) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Erreur: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
