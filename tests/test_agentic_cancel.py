"""Annulation agentique : ACK borné, pas de faux échec métier."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import config
import database
from jarvis.agentic import AgenticRunStatus, AgenticService, RuntimeRegistry, discover_runtime_plugins
from jarvis.event_bus import EventBus

from tests.test_agentic_registry import PLUGIN_CODE, _plugin


HANGING_CANCEL = PLUGIN_CODE.replace(
    "    async def cancel(self, run_id):\n        self.calls.append((\"cancel\", run_id))",
    "    async def cancel(self, run_id):\n        self.calls.append((\"cancel\", run_id))\n        await __import__('asyncio').sleep(3600)",
)


@pytest.fixture
def agentic_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "cancel.db"
    monkeypatch.setattr(config, "DB_PATH", str(path))
    monkeypatch.setattr(database, "DB_PATH", path)
    database.init_db()
    return path


@pytest.mark.asyncio
async def test_cancel_before_start_is_confirmed(agentic_db: Path, tmp_path: Path) -> None:
    registry = RuntimeRegistry(discover_runtime_plugins(_plugin(tmp_path)))
    service = AgenticService(registry=registry, bus=EventBus())
    run = await service.create_run(title="Annuler avant départ")
    cancelled = await service.cancel(run.run_id)
    again = await service.cancel(run.run_id)
    assert cancelled.status is AgenticRunStatus.CANCELLED
    assert again.status is AgenticRunStatus.CANCELLED
    await service.dispose()


@pytest.mark.asyncio
async def test_cancel_timeout_is_forced_not_failed(
    agentic_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "AGENTIC_CANCEL_ACK_TIMEOUT_S", 0.05)
    registry = RuntimeRegistry(
        discover_runtime_plugins(_plugin(tmp_path, code=HANGING_CANCEL))
    )
    service = AgenticService(registry=registry, bus=EventBus())
    run = await service.create_and_start(title="Provider muet")
    for _ in range(50):
        current = service.get(run.run_id)
        if current is not None and current.status is AgenticRunStatus.RUNNING:
            break
        await asyncio.sleep(0.01)
    cancelled = await service.cancel(run.run_id)
    assert cancelled.status is AgenticRunStatus.CANCELLED
    assert cancelled.error is None
    await service.dispose()


@pytest.mark.asyncio
async def test_cancel_does_not_complete_afterwards(
    agentic_db: Path, tmp_path: Path
) -> None:
    registry = RuntimeRegistry(discover_runtime_plugins(_plugin(tmp_path)))
    service = AgenticService(registry=registry, bus=EventBus())
    run = await service.create_and_start(title="Course finale")
    cancelled = await service.cancel(run.run_id)
    from jarvis.agentic.models import RuntimeEvent
    from datetime import datetime, timezone
    import uuid

    await service._apply_persisted_runtime_event(
        cancelled,
        RuntimeEvent(
            event_id=str(uuid.uuid4()),
            run_id=cancelled.run_id,
            sequence=99,
            type="agent.run.completed",
            timestamp=datetime.now(timezone.utc),
            payload={"status": "completed"},
        ),
    )
    current = service.get(cancelled.run_id)
    assert current is not None
    assert current.status is AgenticRunStatus.CANCELLED
    await service.dispose()
