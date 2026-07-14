"""Contrats unitaires du bus d'événements Phase 3."""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
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
async def test_handlers_run_concurrently_and_one_failure_is_isolated():
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

    emit_task = asyncio.create_task(
        bus.emit(TaskCreated(1, "Tester le bus", "medium", None))
    )
    await asyncio.wait_for(fast_called.wait(), timeout=0.5)
    assert not emit_task.done()

    release_slow.set()
    await asyncio.wait_for(emit_task, timeout=0.5)
    assert wildcard_events == ["task.created"]


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
