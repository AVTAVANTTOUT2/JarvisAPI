"""Régressions — gate de confirmation pour actions à effet de bord."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_chat_actions():
    spec = importlib.util.spec_from_file_location(
        "chat_actions_module",
        REPO_ROOT / "api" / "chat_actions.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_actions_requiring_confirmation_include_calendar_and_tasks() -> None:
    mod = _load_chat_actions()
    required = mod.ACTIONS_REQUIRING_CONFIRMATION
    assert "calendar_create" in required
    assert "task" in required
    assert "open_app" in required
    assert "find_file" in required
    assert "weather" not in required


def test_open_app_deferred_until_confirmed() -> None:
    mod = _load_chat_actions()
    assert mod._should_defer_action(
        "J'ouvre Safari.",
        {"type": "open_app", "app_name": "Safari"},
    ) is True
