"""Tests du pont Apple Shortcuts — registre, plans, action, recettes."""

from __future__ import annotations

from pathlib import Path

import pytest

from database import init_db
from database.apple_shortcuts import (
    find_registered_shortcut,
    list_registered_shortcuts,
    register_shortcut,
)
from integrations.apple_shortcuts import (
    AppleShortcutsError,
    consume_plan,
    create_plan,
    reset_plans_for_tests,
    run_shortcut,
    status,
)
from integrations.apple_shortcuts_recipes import get_recipe, list_recipes


@pytest.fixture(autouse=True)
def _apple_shortcuts_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    reset_plans_for_tests()
    monkeypatch.setattr("config.APPLE_SHORTCUTS_ENABLED", True)
    monkeypatch.setattr(
        "config.APPLE_SHORTCUTS_WORKSPACE",
        str(tmp_path / "workspace"),
    )
    monkeypatch.setattr("config.APPLE_SHORTCUTS_PLAN_TTL_SECONDS", 600)
    monkeypatch.setattr("config.APPLE_SHORTCUTS_RUN_TIMEOUT", 30.0)
    monkeypatch.setattr("config.DB_PATH", str(tmp_path / "test.db"))
    init_db()
    yield
    reset_plans_for_tests()


def test_status_reports_disabled_when_flag_off(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("config.APPLE_SHORTCUTS_ENABLED", False)
    payload = status()
    assert payload["enabled"] is False
    assert payload["available"] is False


def test_register_and_find_shortcut_by_alias():
    row = register_shortcut(
        name="allume la chambre",
        alias="chambre",
        risk="medium",
    )
    assert row["name"] == "allume la chambre"
    found = find_registered_shortcut(alias="Chambre", enabled_only=True)
    assert found is not None
    assert found["id"] == row["id"]
    assert list_registered_shortcuts(enabled_only=True)


def test_create_plan_requires_enabled_and_cli(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "integrations.apple_shortcuts.resolve_shortcuts_bin",
        lambda: "/usr/bin/shortcuts",
    )
    monkeypatch.setattr(
        "integrations.apple_shortcuts.is_macos",
        lambda: True,
    )
    plan = create_plan(
        shortcut_name="allume la chambre",
        registry_id=1,
        input_text=None,
        allow_input=False,
        risk="medium",
    )
    assert plan.plan_id
    assert plan.shortcut_name == "allume la chambre"
    peeked = consume_plan(plan.plan_id)
    assert peeked.plan_id == plan.plan_id
    with pytest.raises(AppleShortcutsError) as exc:
        consume_plan(plan.plan_id)
    assert exc.value.code == "plan_not_found"


def test_create_plan_rejects_input_when_not_allowed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "integrations.apple_shortcuts.resolve_shortcuts_bin",
        lambda: "/usr/bin/shortcuts",
    )
    monkeypatch.setattr("integrations.apple_shortcuts.is_macos", lambda: True)
    with pytest.raises(AppleShortcutsError) as exc:
        create_plan(
            shortcut_name="test",
            registry_id=1,
            input_text="hello",
            allow_input=False,
            risk="low",
        )
    assert exc.value.code == "input_forbidden"


def test_run_shortcut_writes_controlled_input_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("integrations.apple_shortcuts.is_macos", lambda: True)
    monkeypatch.setattr(
        "integrations.apple_shortcuts.resolve_shortcuts_bin",
        lambda: "/usr/bin/shortcuts",
    )
    calls: list[list[str]] = []

    def fake_run(args, *, timeout, input_bytes=None):
        calls.append(list(args))
        output_flag = args.index("--output-path")
        Path(args[output_flag + 1]).write_text("ok-from-shortcut", encoding="utf-8")
        return 0, "", ""

    monkeypatch.setattr("integrations.apple_shortcuts._run_cli", fake_run)
    result = run_shortcut("Demo", input_text="payload")
    assert result["ok"] is True
    assert result["output"] == "ok-from-shortcut"
    assert calls and calls[0][0] == "run"
    assert "--input-path" in calls[0]
    input_path = Path(calls[0][calls[0].index("--input-path") + 1])
    assert str(tmp_path / "workspace") in str(input_path)
    assert not input_path.exists()


@pytest.mark.asyncio
async def test_action_run_shortcut_requires_registry_and_confirmation(
    monkeypatch: pytest.MonkeyPatch,
):
    from actions import execute_action

    monkeypatch.setattr(
        "integrations.apple_shortcuts.resolve_shortcuts_bin",
        lambda: "/usr/bin/shortcuts",
    )
    monkeypatch.setattr("integrations.apple_shortcuts.is_macos", lambda: True)

    missing = await execute_action({"type": "run_shortcut", "name": "ghost"})
    assert missing["ok"] is False
    assert "registre" in missing["message"].lower()

    register_shortcut(name="Snap", alias="snap", risk="low")
    first = await execute_action({"type": "run_shortcut", "alias": "snap"})
    assert first["ok"] is True
    assert first["needs_confirmation"] is True
    plan_id = first["shortcut_plan_id"]

    ignored = await execute_action(
        {"type": "run_shortcut", "name": "Snap", "confirmed": True}
    )
    assert ignored.get("needs_confirmation") is True

    async def fake_run(name, *, input_text=None, timeout=None):
        return {
            "ok": True,
            "shortcut_name": name,
            "output": "done",
            "message": f"Raccourci « {name} » exécuté.",
        }

    monkeypatch.setattr(
        "integrations.apple_shortcuts.run_shortcut_async",
        fake_run,
    )
    confirmed = await execute_action(
        {
            "type": "run_shortcut",
            "shortcut_plan_id": plan_id,
            "confirmed": True,
        }
    )
    assert confirmed["ok"] is True
    assert confirmed["output"] == "done"


def test_recipes_catalog_is_complete():
    recipes = list_recipes()
    assert len(recipes) >= 5
    ids = {r["id"] for r in recipes}
    assert "jarvis_location" in ids
    assert "jarvis_ask" in ids
    assert "siri_phrase_homekit" in ids
    assert "focus_mode_bridge" in ids
    assert get_recipe("jarvis_ask")["endpoint"]["path"] == "/api/apple/shortcuts/ask"
    for recipe in recipes:
        assert recipe["steps"]
        assert recipe["endpoint"]["path"].startswith("/")
        assert "triggers" in recipe


@pytest.mark.asyncio
async def test_quick_task_ingest_creates_task():
    from api.apple_shortcuts_support import create_quick_task

    result = create_quick_task(title="Acheter du lait", priority="high")
    assert result["ok"] is True
    assert result["task_id"] > 0


def test_llm_cannot_confirm_without_server_plan_is_documented_in_action():
    """Garde-fou structurel : confirmed sans plan ne consomme rien."""
    import inspect
    from actions import _action_run_shortcut

    source = inspect.getsource(_action_run_shortcut)
    assert "pré-confirmation ignorée" in source
    assert "shortcut_plan_id" in source


def test_abandoned_proposal_revokes_shortcut_plan(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "integrations.apple_shortcuts.resolve_shortcuts_bin",
        lambda: "/usr/bin/shortcuts",
    )
    monkeypatch.setattr("integrations.apple_shortcuts.is_macos", lambda: True)
    from api.action_confirmations import _revoke_action_plan
    from integrations.apple_shortcuts import peek_plan

    plan = create_plan(
        shortcut_name="Snap",
        registry_id=1,
        input_text=None,
        allow_input=False,
        risk="low",
    )
    assert peek_plan(plan.plan_id) is not None
    revoked = _revoke_action_plan(
        {
            "type": "run_shortcut",
            "shortcut_plan_id": plan.plan_id,
        }
    )
    assert revoked is True
    assert peek_plan(plan.plan_id) is None


@pytest.mark.asyncio
async def test_action_unknown_shortcut_points_to_ui(monkeypatch: pytest.MonkeyPatch):
    from actions import execute_action

    monkeypatch.setattr(
        "integrations.apple_shortcuts.resolve_shortcuts_bin",
        lambda: "/usr/bin/shortcuts",
    )
    monkeypatch.setattr("integrations.apple_shortcuts.is_macos", lambda: True)
    result = await execute_action({"type": "run_shortcut", "name": "ghost-ui"})
    assert result["ok"] is False
    assert "/shortcuts" in result["message"]


def _ingest_request(*, host: str = "127.0.0.1", headers: dict[str, str] | None = None):
    from starlette.requests import Request

    encoded = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/apple/shortcuts/task",
            "raw_path": b"/api/apple/shortcuts/task",
            "query_string": b"",
            "headers": encoded,
            "client": (host, 54321),
            "server": ("test", 80),
        }
    )


def test_require_ingest_token_fail_closed_without_secret(monkeypatch: pytest.MonkeyPatch):
    from fastapi import HTTPException

    from api.apple_shortcuts_support import require_ingest_token

    monkeypatch.setattr("config.APPLE_SHORTCUTS_INGEST_TOKEN", "")
    with pytest.raises(HTTPException) as exc:
        require_ingest_token(_ingest_request())
    assert exc.value.status_code == 503


def test_require_ingest_token_accepts_bearer_or_header(monkeypatch: pytest.MonkeyPatch):
    from fastapi import HTTPException

    from api.apple_shortcuts_support import require_ingest_token

    monkeypatch.setattr("config.APPLE_SHORTCUTS_INGEST_TOKEN", "shortcut-secret")
    with pytest.raises(HTTPException) as exc:
        require_ingest_token(
            _ingest_request(headers={"Authorization": "Bearer wrong"})
        )
    assert exc.value.status_code == 401

    require_ingest_token(
        _ingest_request(headers={"Authorization": "Bearer shortcut-secret"})
    )
    require_ingest_token(
        _ingest_request(headers={"X-Apple-Shortcuts-Token": "shortcut-secret"})
    )


def test_enforce_ingest_rate_limit_returns_429(monkeypatch: pytest.MonkeyPatch):
    from fastapi import HTTPException

    from api import apple_shortcuts_support as support

    monkeypatch.setattr("config.APPLE_SHORTCUTS_INGEST_RATE_LIMIT", 2)
    monkeypatch.setattr("config.APPLE_SHORTCUTS_INGEST_RATE_WINDOW_SECONDS", 60)
    with support._ingest_rate_lock:
        support._ingest_rate_buckets.clear()

    try:
        support.enforce_ingest_rate_limit(_ingest_request(host="10.0.0.8"))
        support.enforce_ingest_rate_limit(_ingest_request(host="10.0.0.8"))
        with pytest.raises(HTTPException) as exc:
            support.enforce_ingest_rate_limit(_ingest_request(host="10.0.0.8"))
        assert exc.value.status_code == 429
        assert int(exc.value.headers["Retry-After"]) >= 1
        # Un autre client n'est pas bloqué par le bucket voisin.
        support.enforce_ingest_rate_limit(_ingest_request(host="10.0.0.9"))
    finally:
        with support._ingest_rate_lock:
            support._ingest_rate_buckets.clear()
