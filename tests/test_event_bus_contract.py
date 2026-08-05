"""Contrats unitaires du bus d'événements Phase 3."""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
import threading
import time
from dataclasses import FrozenInstanceError

import pytest

from jarvis.event_bus import EventBus
from jarvis.events import TaskCreated


def test_domain_event_is_immutable_versioned_and_checksummed():
    event = TaskCreated(42, "Valider la Phase 3", "high", "2026-07-15")

    canonical = json.dumps(
        event.payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert event.event_type == "task.created"
    assert event.version == 1
    assert event.source == "database.tasks"
    assert event.checksum == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert event.to_dict()["type"] == event.to_dict()["event_type"]
    assert event.to_dict()["data"] == event.to_dict()["payload"]

    with pytest.raises(FrozenInstanceError):
        event.type = "task.updated"  # type: ignore[misc]

    isolated_import = subprocess.run(
        [sys.executable, "-S", "-c", "from jarvis.event_bus import EventBus"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert isolated_import.returncode == 0, isolated_import.stderr


@pytest.mark.asyncio
async def test_handlers_have_independent_queues_and_one_failure_is_isolated():
    bus = EventBus()
    fast_called = asyncio.Event()
    release_slow = asyncio.Event()
    wildcard_events: list[str] = []

    @bus.on(TaskCreated)
    async def slow_handler(_event):
        await release_slow.wait()

    @bus.on("task.created")
    async def fast_handler(_event):
        fast_called.set()

    @bus.on("task.created")
    async def failing_handler(_event):
        raise RuntimeError("handler volontairement défaillant")

    @bus.on("*")
    def wildcard_handler(event):
        wildcard_events.append(event.event_type)

    await bus.emit(TaskCreated(1, "Tester le bus", "medium", None))
    await asyncio.wait_for(fast_called.wait(), timeout=0.5)

    release_slow.set()
    await asyncio.wait_for(bus.wait_until_idle(), timeout=0.5)
    assert wildcard_events == ["task.created"]


@pytest.mark.asyncio
async def test_sync_handler_runs_off_the_event_loop():
    bus = EventBus()
    release = threading.Event()
    heartbeat = asyncio.Event()

    @bus.on(TaskCreated)
    def blocking_handler(_event):
        release.wait(timeout=1.0)

    await bus.emit(TaskCreated(1, "Ne pas bloquer asyncio", "medium", None))
    started_at = time.monotonic()
    asyncio.get_running_loop().call_soon(heartbeat.set)
    timer = threading.Timer(0.2, release.set)
    timer.start()
    try:
        await heartbeat.wait()
        assert time.monotonic() - started_at < 0.1
        release.set()
        await asyncio.wait_for(bus.wait_until_idle(), timeout=0.5)
    finally:
        timer.cancel()


@pytest.mark.asyncio
async def test_handler_queue_is_bounded_and_preserves_order(caplog):
    bus = EventBus(handler_queue_size=1)
    started = asyncio.Event()
    release = asyncio.Event()
    received: list[int] = []

    @bus.on(TaskCreated)
    async def slow_handler(event):
        received.append(int(event.payload["task_id"]))
        started.set()
        await release.wait()

    await bus.emit(TaskCreated(1, "Premier", "low", None))
    await asyncio.wait_for(started.wait(), timeout=0.5)
    await bus.emit(TaskCreated(2, "Deuxième", "low", None))
    await bus.emit(TaskCreated(3, "Abandonné", "low", None))

    release.set()
    await asyncio.wait_for(bus.wait_until_idle(), timeout=0.5)
    assert received == [1, 2]
    assert "file handler pleine" in caplog.text


def test_emit_nowait_without_bound_loop_drains_handlers():
    bus = EventBus()
    received: list[int] = []

    @bus.on(TaskCreated)
    def handler(event):
        received.append(int(event.payload["task_id"]))

    assert bus.emit_nowait(TaskCreated(9, "Boucle temporaire", "low", None)) is None
    assert received == [9]


def test_closed_loop_with_pending_handler_queue_is_recovered(caplog):
    bus = EventBus()
    first_started = asyncio.Event()
    never_release = asyncio.Event()
    received: list[int] = []

    @bus.on(TaskCreated)
    async def slow_handler(event):
        received.append(int(event.payload["task_id"]))
        first_started.set()
        await never_release.wait()

    async def leave_pending_event() -> None:
        await bus.emit(TaskCreated(1, "En cours", "low", None))
        await first_started.wait()
        await bus.emit(TaskCreated(2, "Resté dans l'ancienne file", "low", None))

    asyncio.run(leave_pending_event())

    async def use_new_loop() -> None:
        never_release.set()
        await bus.emit(TaskCreated(3, "Nouvelle boucle", "low", None))
        await bus.wait_until_idle()

    asyncio.run(use_new_loop())
    assert received == [1, 3]
    assert "abandonné(s) après fermeture" in caplog.text


@pytest.mark.asyncio
async def test_emit_nowait_can_be_drained_deterministically():
    bus = EventBus()
    received: list[int] = []

    @bus.on(TaskCreated)
    async def handler(event):
        received.append(int(event.payload["task_id"]))

    future = bus.emit_nowait(TaskCreated(7, "Drain", "low", None))
    assert future is not None
    await bus.wait_until_idle()
    assert received == [7]
