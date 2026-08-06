"""Le screen watcher ne doit pas narrer l'UI à voix haute."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


def test_normalize_screen_notable_rejects_nullish_and_ui_labels() -> None:
    from scripts.screen_watcher import normalize_screen_notable

    assert normalize_screen_notable(None) is None
    assert normalize_screen_notable("null") is None
    assert normalize_screen_notable("NULL") is None
    assert normalize_screen_notable(" none ") is None
    assert normalize_screen_notable("") is None
    assert normalize_screen_notable("ChatGPT Pro interface") is None
    assert normalize_screen_notable("regarde YouTube depuis un moment") is None
    assert normalize_screen_notable("aucune erreur visible") is None
    assert (
        normalize_screen_notable("erreur Python ModuleNotFoundError dans le terminal")
        == "erreur Python ModuleNotFoundError dans le terminal"
    )
    assert (
        normalize_screen_notable("Connexion au serveur impossible")
        == "Connexion au serveur impossible"
    )
    assert (
        normalize_screen_notable("demande d'autorisation pour contrôler le Mac")
        == "demande d'autorisation pour contrôler le Mac"
    )


def test_vision_prompt_does_not_treat_sites_or_browsing_as_notable() -> None:
    from scripts.screen_watcher import ScreenWatcher

    prompt = ScreenWatcher._vision_prompt_text("Chrome", {"width": 800, "height": 600})
    folded = prompt.casefold()
    assert "erreur, site, notification" not in folded
    assert "regarde youtube" not in folded
    assert "null sauf" in folded or "uniquement si" in folded
    assert "interface" in folded  # consigne d'exclusion
    assert "moduleNotFoundError".casefold() in folded


@pytest.mark.asyncio
async def test_ui_label_notable_never_reaches_formulation_or_tts(monkeypatch) -> None:
    from scripts.jarvis_daemon import JarvisDaemon, info_agent
    from scripts.screen_watcher import ScreenWatcher

    daemon = object.__new__(JarvisDaemon)
    daemon.screen_watcher = ScreenWatcher()
    daemon.tts_queue = asyncio.Queue()
    daemon.screen_notification_ttl_s = 15
    monkeypatch.setattr(
        daemon.screen_watcher,
        "_is_voice_busy",
        MagicMock(return_value=False),
    )
    handle = AsyncMock(return_value={"response": "Elias est sur ChatGPT Pro."})
    monkeypatch.setattr(info_agent, "handle", handle)

    await daemon._on_screen_notable(
        "ChatGPT Pro interface",
        {"app": "Google Chrome", "activity": "chat avec ChatGPT"},
    )

    handle.assert_not_awaited()
    assert daemon.tts_queue.empty()


@pytest.mark.asyncio
async def test_actionable_notable_still_formulated(monkeypatch) -> None:
    from scripts.jarvis_daemon import JarvisDaemon, info_agent
    from scripts.screen_watcher import ScreenWatcher

    daemon = object.__new__(JarvisDaemon)
    daemon.screen_watcher = ScreenWatcher()
    daemon.tts_queue = asyncio.Queue()
    daemon.screen_notification_ttl_s = 60
    monkeypatch.setattr(
        daemon.screen_watcher,
        "_is_voice_busy",
        MagicMock(return_value=False),
    )
    handle = AsyncMock(
        return_value={"response": "Connexion Mail impossible."},
    )
    monkeypatch.setattr(info_agent, "handle", handle)

    await daemon._on_screen_notable(
        "Connexion au serveur impossible",
        {"app": "Mail", "activity": "erreur de connexion"},
    )

    handle.assert_awaited_once()
    assert not daemon.tts_queue.empty()
    spoken, emotion, channel = await daemon.tts_queue.get()
    assert spoken == "Connexion Mail impossible."
    assert emotion == "neutral"
    assert channel == "background"
