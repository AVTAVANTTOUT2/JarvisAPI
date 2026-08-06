"""Cycle de vie des subprocess du Screen Watcher.

Un `screencapture` ou un `osascript` qui dépasse son timeout doit être tué
et moissonné : `asyncio.wait_for` n'annule que l'attente, pas le processus.
Sans kill, la boucle de capture (toutes les ~12 s) accumulerait des
processus orphelins sur un poste macOS qui tourne en continu.
"""

from __future__ import annotations

import asyncio

import pytest

from scripts.screen_watcher import ScreenWatcher, _reap_subprocess


class _HangingProcess:
    """Simule un subprocess bloqué : ne se termine qu'après kill()."""

    def __init__(self) -> None:
        self.killed = False
        self._done = asyncio.Event()

    def kill(self) -> None:
        self.killed = True
        self._done.set()

    async def wait(self) -> int:
        await self._done.wait()
        return -9

    async def communicate(self) -> tuple[bytes, bytes]:
        await self._done.wait()
        return b"", b""


@pytest.fixture()
def hanging_exec(monkeypatch):
    """Remplace create_subprocess_exec par des processus qui ne finissent jamais."""
    spawned: list[_HangingProcess] = []

    async def _fake_exec(*_args, **_kwargs) -> _HangingProcess:
        proc = _HangingProcess()
        spawned.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    return spawned


@pytest.fixture()
def watcher() -> ScreenWatcher:
    sw = ScreenWatcher()
    sw.capture_timeout = 0.05
    sw.osascript_timeout = 0.05
    return sw


async def test_capture_kills_screencapture_on_timeout(watcher, hanging_exec):
    img, path = await watcher._capture()

    assert img is None and path is None
    assert len(hanging_exec) == 1
    assert hanging_exec[0].killed, "screencapture doit être tué au timeout"


async def test_active_window_info_kills_osascript_and_falls_back(watcher, hanging_exec):
    result = await watcher._get_active_window_info()

    # Le fallback _get_frontmost_app tente lui aussi un osascript (bloqué) :
    # les deux processus doivent être tués, et le résultat est None.
    assert result is None
    assert len(hanging_exec) == 2
    assert all(proc.killed for proc in hanging_exec)


async def test_frontmost_app_kills_osascript_on_timeout(watcher, hanging_exec):
    name = await watcher._get_frontmost_app()

    assert name is None
    assert len(hanging_exec) == 1
    assert hanging_exec[0].killed


async def test_detect_screen_dimensions_kills_osascript_on_timeout(watcher, hanging_exec):
    await watcher._detect_screen_point_dimensions()

    assert watcher._screen_point_width == 0
    assert watcher._screen_point_height == 0
    assert len(hanging_exec) == 1
    assert hanging_exec[0].killed


async def test_reap_subprocess_tolerates_already_dead_process():
    class _DeadProcess:
        def kill(self) -> None:
            raise ProcessLookupError

        async def wait(self) -> int:  # pragma: no cover - jamais atteint
            return 0

    await _reap_subprocess(_DeadProcess())
