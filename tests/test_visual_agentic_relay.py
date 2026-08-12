from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import api.router_visual as visual_router
from api.middleware import _bypasses_session_gate
from api.router_visual import (
    _neutral_event,
    _neutral_run_view,
    _require_visual_read,
)
from jarvis.agentic.models import AgenticRunStatus


def _request(token: str | None, *, host: str = "127.0.0.1") -> Request:
    headers = [(b"host", b"127.0.0.1:8080")]
    if token is not None:
        headers.append((b"authorization", f"Bearer {token}".encode("ascii")))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "path": "/api/visual/v1/snapshot",
            "headers": headers,
            "client": (host, 4242),
            "server": ("127.0.0.1", 8080),
        }
    )


def _token_file(monkeypatch: pytest.MonkeyPatch, root: Path) -> str:
    token = "a" * 64
    token_path = root / ".jarvis" / "visual.token"
    token_path.parent.mkdir(parents=True)
    token_path.write_text(token + "\n", encoding="ascii")
    token_path.chmod(0o600)
    monkeypatch.setattr(visual_router.config, "BASE_DIR", root)
    monkeypatch.setattr(visual_router.config, "VISUAL_RELAY_TOKEN_FILE", token_path)
    return token


def test_visual_relay_requires_scoped_token_and_loopback(monkeypatch, tmp_path: Path):
    token = _token_file(monkeypatch, tmp_path)

    _require_visual_read(_request(token))

    with pytest.raises(HTTPException) as missing:
        _require_visual_read(_request(None))
    assert missing.value.status_code == 401

    with pytest.raises(HTTPException) as invalid:
        _require_visual_read(_request("b" * 64))
    assert invalid.value.status_code == 401

    with pytest.raises(HTTPException) as remote:
        _require_visual_read(_request(token, host="192.0.2.10"))
    assert remote.value.status_code == 403


def test_visual_relay_rejects_permissive_token_file(monkeypatch, tmp_path: Path):
    token = _token_file(monkeypatch, tmp_path)
    Path(visual_router.config.VISUAL_RELAY_TOKEN_FILE).chmod(0o644)

    with pytest.raises(HTTPException) as unsafe:
        _require_visual_read(_request(token))
    assert unsafe.value.status_code == 503


def test_visual_views_are_pre_neutralized():
    run = SimpleNamespace(
        run_id="run-123",
        status=AgenticRunStatus.RUNNING,
        phase="editing",
        channel="voice",
        title="secret customer request",
        selected_context={"token": "secret"},
    )
    assert _neutral_run_view(run) == {
        "run_id": "run-123",
        "title": "Tâche agentique JARVIS",
        "status": "running",
        "phase": "editing",
        "channel": "voice",
        "role": "coding",
        "progress": 50,
        "needs_attention": False,
    }

    event = _neutral_event(
        {
            "sse_id": 9,
            "event_id": "evt-9",
            "event_type": "agent.tool.started",
            "timestamp": 1_786_438_800,
            "payload": {
                "run_id": "run-123",
                "status": "running",
                "phase": "editing",
                "channel": "voice",
                "prompt": "private prompt",
                "arguments": {"path": "/private/file"},
            },
        }
    )
    assert event is not None
    serialized = str(event)
    assert "private prompt" not in serialized
    assert "/private/file" not in serialized
    assert event["payload"]["role"] == "coding"


def test_visual_routes_only_bypass_cookie_gate_for_exact_gets():
    for path in (
        "/api/visual/v1/health",
        "/api/visual/v1/snapshot",
        "/api/visual/v1/events",
    ):
        assert _bypasses_session_gate("GET", path)
        assert not _bypasses_session_gate("POST", path)
    assert not _bypasses_session_gate("GET", "/api/visual/v1/events/private")
