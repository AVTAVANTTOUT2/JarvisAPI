"""Apple Music : outil JARVIS, pas une mission — zéro lecture réelle en tests."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest

from api.chat_actions import ACTIONS_WITH_FOLLOWUP
from integrations.apple_music import (
    control_payload,
    execute_music_action,
    integrations_payload,
    maybe_handle_music_intent,
    maybe_music_context,
    parse_intent,
    reset_status_cache,
    status,
)
from jarvis.cognitive.capability_registry import CapabilityRegistry
from jarvis.security.llm_data_boundary import format_action_result_for_external_llm


@pytest.fixture(autouse=True)
def _reset_music_status_cache() -> None:
    reset_status_cache()
    yield
    reset_status_cache()


def _no_machine_path(payload: object) -> None:
    blob = json.dumps(payload, default=str)
    assert not re.search(r"/Users/|/System/|/home/|\\\\Users\\\\", blob)


def test_parse_intent_play_verbs() -> None:
    uncommitted = parse_intent("met du werenoi")
    assert uncommitted is not None
    assert uncommitted.action == "play"
    assert uncommitted.query == "werenoi"
    assert uncommitted.committed is False

    committed = parse_intent("joue werenoi")
    assert committed is not None
    assert committed.committed is True
    assert committed.query == "werenoi"

    hinted = parse_intent("mets de la musique de werenoi")
    assert hinted is not None
    assert hinted.committed is True
    assert hinted.query == "werenoi"


def test_parse_intent_rejects_agentic_and_devops() -> None:
    assert parse_intent("execute") is None
    assert parse_intent("approuve") is None
    assert parse_intent("lance") is None
    assert parse_intent("lance les tests") is None
    assert parse_intent("pause la tache") is None
    assert parse_intent("lance la commande git") is None


def test_parse_intent_controls_and_empty_library_play() -> None:
    assert parse_intent("pause") is not None
    assert parse_intent("pause").action == "pause"
    assert parse_intent("suivant").action == "next"
    empty = parse_intent("mets de la musique")
    assert empty is not None
    assert empty.action == "play"
    assert empty.query == ""
    assert empty.committed is True


def test_status_binary_missing_has_no_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("integrations.apple_music.resolve_binary", lambda: None)
    payload = status(force=True)
    assert payload["running"] is False
    assert payload["healthy"] is False
    assert payload["error"] == "binary_missing"
    _no_machine_path(payload)
    _no_machine_path(control_payload())
    _no_machine_path(integrations_payload())
    assert control_payload()["can_control"] is False
    assert control_payload()["id"] == "apple_music"


def test_status_doctor_ok_strips_music_app_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("integrations.apple_music.resolve_binary", lambda: "/usr/bin/true")
    monkeypatch.setattr(
        "integrations.apple_music.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=(
                "Automation: GRANTED\n"
                "Doctor check complete\n"
                "backend: musicapp /System/Applications/Music.app\n"
            ),
            stderr="",
            returncode=0,
        ),
    )
    payload = status(force=True)
    assert payload["running"] is True
    assert payload["healthy"] is True
    assert payload["error"] is None
    assert payload["backend"] == "musicapp"
    _no_machine_path(payload)
    _no_machine_path(control_payload())


@pytest.mark.asyncio
async def test_maybe_handle_committed_play_does_not_open_a_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("integrations.apple_music.resolve_binary", lambda: "/bin/true")

    def _play(action: dict) -> dict:
        assert action["type"] == "music"
        assert action["action"] == "play"
        assert action["query"] == "werenoi"
        return {"ok": True, "message": "Werenoi, lecture lancée.", "artist": "Werenoi"}

    monkeypatch.setattr("integrations.apple_music.execute_music_action", _play)
    result = await maybe_handle_music_intent("joue werenoi")
    assert result is not None
    assert result["text"] == "Werenoi, lecture lancée."
    assert result["action"]["query"] == "werenoi"
    assert "task_control" not in result
    assert result.get("agentic_run") is None

    informal = await maybe_handle_music_intent("met du werenoi")
    assert informal is not None
    assert informal["action"]["query"] == "werenoi"


@pytest.mark.asyncio
async def test_maybe_handle_uncommitted_miss_falls_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("integrations.apple_music.resolve_binary", lambda: "/bin/true")
    monkeypatch.setattr(
        "integrations.apple_music.execute_music_action",
        lambda _action: {
            "ok": False,
            "error": "not_in_library",
            "message": "Rien pour sel dans la bibliothèque Music.app.",
        },
    )
    assert await maybe_handle_music_intent("met du sel") is None


@pytest.mark.asyncio
async def test_maybe_handle_committed_miss_stays_on_music_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("integrations.apple_music.resolve_binary", lambda: "/bin/true")
    monkeypatch.setattr(
        "integrations.apple_music.execute_music_action",
        lambda _action: {
            "ok": False,
            "error": "not_in_library",
            "message": "Rien pour xyz dans la bibliothèque Music.app.",
        },
    )
    result = await maybe_handle_music_intent("joue xyzintrouvable")
    assert result is not None
    assert result["action_result"]["ok"] is False
    assert "bibliothèque" in result["text"]


@pytest.mark.asyncio
async def test_maybe_handle_missing_binary_committed_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("integrations.apple_music.resolve_binary", lambda: None)
    result = await maybe_handle_music_intent("joue werenoi")
    assert result is not None
    assert result["action_result"]["ok"] is False
    assert "installé" in result["text"]
    assert await maybe_handle_music_intent("met du werenoi") is None
    assert await maybe_handle_music_intent("execute") is None


@pytest.mark.asyncio
async def test_execute_action_music_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    import actions as actions_module

    monkeypatch.setattr(actions_module, "_schedule_action_log", lambda **_kwargs: None)
    monkeypatch.setattr(
        "integrations.apple_music.execute_music_action",
        lambda action: {
            "ok": True,
            "message": "Werenoi, lecture lancée.",
            "query": action.get("query"),
        },
    )
    result = await actions_module.execute_action(
        {"type": "music", "action": "play", "query": "werenoi"}
    )
    assert result["ok"] is True
    assert result["query"] == "werenoi"
    assert "music" not in ACTIONS_WITH_FOLLOWUP


def test_execute_music_action_unknown() -> None:
    result = execute_music_action({"type": "music", "action": "delete_playlist"})
    assert result["ok"] is False
    assert "inconnue" in result["message"]


def test_music_followup_boundary_has_no_path() -> None:
    wrapped = format_action_result_for_external_llm(
        {"type": "music"},
        {
            "ok": True,
            "message": "Werenoi — Pyramide, lecture lancée.",
            "artist": "Werenoi",
            "track": "Pyramide",
            "screenshot_path": "/Users/nolann/private.png",
        },
    )
    assert "Werenoi" in wrapped
    assert "/Users/" not in wrapped
    assert "screenshot_path" not in wrapped


def test_music_capabilities_are_jarvis_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("integrations.apple_music.resolve_binary", lambda: "/bin/true")
    registry = CapabilityRegistry()
    play = registry.get("music.play")
    control = registry.get("music.control")
    assert play is not None and control is not None
    assert play.executor == "jarvis_tool"
    assert control.executor == "jarvis_tool"
    assert play.requires_confirmation is False
    assert play.available is True


@pytest.mark.asyncio
async def test_maybe_music_context_skips_unrelated_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom() -> None:
        raise AssertionError("get_state ne doit pas tourner hors intent musique")

    monkeypatch.setattr("integrations.apple_music.get_state", _boom)
    assert await maybe_music_context("quel temps fait-il à Lille") is None
