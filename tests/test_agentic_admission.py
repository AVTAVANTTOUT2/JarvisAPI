"""Admission agentique : mémoire, concurrence, priorité utilisateur."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import config
import database
from jarvis.agentic import (
    AgenticRunStatus,
    AgenticService,
    RuntimeRegistry,
    discover_runtime_plugins,
)
from jarvis.agentic.models import AgenticErrorCode
from jarvis.event_bus import EventBus

from tests.test_agentic_registry import _plugin


@pytest.fixture
def agentic_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "admission.db"
    monkeypatch.setattr(config, "DB_PATH", str(path))
    monkeypatch.setattr(database, "DB_PATH", path)
    database.init_db()
    return path


@pytest.mark.asyncio
async def test_memory_below_threshold_keeps_run_queued(
    agentic_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "AGENTIC_MIN_FREE_MEMORY_MB", 2048)
    monkeypatch.setattr(config, "AGENTIC_MAX_QUEUE_WAIT_S", 120)
    registry = RuntimeRegistry(discover_runtime_plugins(_plugin(tmp_path)))
    service = AgenticService(
        registry=registry,
        bus=EventBus(),
        read_free_memory_mb=lambda: 128.0,
    )
    run = await service.create_and_start(title="Pression mémoire")
    for _ in range(30):
        current = service.get(run.run_id)
        assert current is not None
        assert current.status is not AgenticRunStatus.RUNNING
        await asyncio.sleep(0.02)
    current = service.get(run.run_id)
    assert current is not None
    assert current.status is AgenticRunStatus.QUEUED
    await service.dispose()


@pytest.mark.asyncio
async def test_memory_ok_admits_run(
    agentic_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "AGENTIC_MIN_FREE_MEMORY_MB", 256)
    registry = RuntimeRegistry(discover_runtime_plugins(_plugin(tmp_path)))
    service = AgenticService(
        registry=registry,
        bus=EventBus(),
        read_free_memory_mb=lambda: 4096.0,
    )
    run = await service.create_and_start(title="Mémoire normale")
    for _ in range(50):
        current = service.get(run.run_id)
        if current is not None and current.status is AgenticRunStatus.RUNNING:
            break
        await asyncio.sleep(0.01)
    current = service.get(run.run_id)
    assert current is not None
    assert current.status is AgenticRunStatus.RUNNING
    await service.dispose()


@pytest.mark.asyncio
async def test_queue_wait_expires_as_resource_pressure(
    agentic_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_config(name: str, default: int) -> int:
        if name == "AGENTIC_MAX_QUEUE_WAIT_S":
            return 0
        if name == "AGENTIC_MIN_FREE_MEMORY_MB":
            return 2048
        return default

    monkeypatch.setattr("jarvis.agentic.service._config_int", fake_config)
    registry = RuntimeRegistry(discover_runtime_plugins(_plugin(tmp_path)))
    service = AgenticService(
        registry=registry,
        bus=EventBus(),
        read_free_memory_mb=lambda: 1.0,
    )
    run = await service.create_and_start(title="File expirée")
    for _ in range(50):
        current = service.get(run.run_id)
        if current is not None and current.status is AgenticRunStatus.BLOCKED:
            break
        await asyncio.sleep(0.02)
    current = service.get(run.run_id)
    assert current is not None
    assert current.status is AgenticRunStatus.BLOCKED
    assert current.error is not None
    assert current.error.code is AgenticErrorCode.RESOURCE_PRESSURE
    await service.dispose()
