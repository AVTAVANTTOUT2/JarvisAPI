"""Gardes pures de l'adaptateur OpenCode : artefacts, empreintes, redaction."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from integrations.opencode.adapter import (
    _safe_value,
    _tool_action_fingerprint,
    _tool_failure_fingerprint,
    _workspace_artifact_path,
)


def _tool_event(
    *,
    status: str,
    tool: str = "knowledge_search",
    call_id: str = "call-1",
    tool_input: object | None = None,
    error: object | None = None,
    output: object | None = None,
) -> SimpleNamespace:
    state: dict[str, object] = {"status": status}
    if tool_input is not None:
        state["input"] = tool_input
    if error is not None:
        state["error"] = error
    if output is not None:
        state["output"] = output
    return SimpleNamespace(
        data={
            "properties": {
                "part": {
                    "type": "tool",
                    "tool": tool,
                    "callID": call_id,
                    "state": state,
                }
            }
        }
    )


def test_workspace_artifact_path_accepts_relative_child(tmp_path: Path) -> None:
    workspace = tmp_path / "run"
    workspace.mkdir()
    target = workspace / "out" / "report.md"
    target.parent.mkdir()
    target.write_text("ok", encoding="utf-8")

    result = _workspace_artifact_path(workspace, "out/report.md")

    assert result is not None
    reference, resolved = result
    assert reference == "out/report.md"
    assert resolved == target.resolve()


def test_workspace_artifact_path_rejects_escapes_and_symlinks(tmp_path: Path) -> None:
    workspace = tmp_path / "run"
    workspace.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("nope", encoding="utf-8")
    link = workspace / "escape"
    link.symlink_to(tmp_path)

    assert _workspace_artifact_path(workspace, "../secret.txt") is None
    assert _workspace_artifact_path(workspace, str(outside)) is None
    assert _workspace_artifact_path(workspace, "escape/secret.txt") is None
    assert _workspace_artifact_path(workspace, "a\x00b") is None
    assert _workspace_artifact_path(workspace, "") is None
    assert _workspace_artifact_path(workspace, None) is None


def test_safe_value_redacts_secrets_and_truncates_depth() -> None:
    nested = {"leaf": "x"}
    cursor: dict[str, object] = nested
    for _ in range(8):
        nxt: dict[str, object] = {"leaf": "x"}
        cursor["child"] = nxt
        cursor = nxt

    safe = _safe_value(
        {
            "api_key": "sk-secret",
            "query": "bonjour",
            "nested": nested,
            "items": list(range(120)),
        }
    )

    assert safe["api_key"] == "[REDACTED]"
    assert safe["query"] == "bonjour"
    assert "[TRUNCATED]" in json.dumps(safe["nested"])
    assert len(safe["items"]) == 100


def test_tool_action_fingerprint_stable_and_status_gated() -> None:
    first = _tool_action_fingerprint(
        _tool_event(status="running", tool_input={"q": "météo", "api_key": "sk-1"})
    )
    second = _tool_action_fingerprint(
        _tool_event(status="pending", tool_input={"api_key": "sk-2", "q": "météo"})
    )
    completed = _tool_action_fingerprint(
        _tool_event(status="completed", tool_input={"q": "météo"})
    )
    malformed = _tool_action_fingerprint(SimpleNamespace(data={"properties": {}}))

    assert first is not None and second is not None
    call_id, tool, digest = first
    assert call_id == "call-1"
    assert tool == "knowledge_search"
    assert digest == second[2]
    assert len(digest) == 64
    assert digest == hashlib.sha256(
        json.dumps(
            {
                "input": {"api_key": "[REDACTED]", "q": "météo"},
                "tool": "knowledge_search",
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    assert completed is None
    assert malformed is None


def test_tool_failure_fingerprint_uses_error_payload() -> None:
    failed = _tool_failure_fingerprint(
        _tool_event(status="error", error={"message": "timeout"})
    )
    other = _tool_failure_fingerprint(
        _tool_event(status="failed", error={"message": "autre"})
    )
    running = _tool_failure_fingerprint(
        _tool_event(status="running", tool_input={"q": "x"})
    )

    assert failed is not None and other is not None
    assert failed[0] == "call-1"
    assert failed[1] != other[1]
    assert running is None
