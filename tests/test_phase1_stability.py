"""Tests ciblés des Quick Wins de stabilité de la Phase 1."""

from __future__ import annotations

import asyncio


def test_database_connection_configures_busy_timeout(tmp_path, monkeypatch):
    import database

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "busy-timeout.db")
    conn = database.get_connection()
    try:
        timeout_ms = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        conn.close()

    assert timeout_ms == 5000


def test_broadcast_uses_snapshot_when_connection_set_changes(monkeypatch):
    import websocket_registry

    class Socket:
        def __init__(self, *, remove=None, fail=False):
            self.remove = remove
            self.fail = fail
            self.events = []

        async def send_json(self, event):
            if self.remove is not None:
                websocket_registry.connected_ws.discard(self.remove)
            if self.fail:
                raise RuntimeError("closed")
            self.events.append(event)

    removed = Socket()
    remover = Socket(remove=removed)
    dead = Socket(fail=True)
    monkeypatch.setattr(websocket_registry, "connected_ws", {remover, removed, dead})
    monkeypatch.setattr(websocket_registry, "connected_ws_lock", asyncio.Lock())

    event = {"type": "phase1-test"}
    asyncio.run(websocket_registry.broadcast_ws(event))

    assert remover.events == [event]
    assert dead not in websocket_registry.connected_ws


def test_broadcast_releases_registry_lock_before_network_io(monkeypatch):
    """Une socket lente ne doit pas bloquer les mutations du registre."""
    import websocket_registry

    async def scenario():
        send_started = asyncio.Event()
        release_send = asyncio.Event()

        class SlowSocket:
            async def send_json(self, event):
                send_started.set()
                await release_send.wait()

        slow = SlowSocket()
        newcomer = SlowSocket()
        monkeypatch.setattr(websocket_registry, "connected_ws", {slow})
        monkeypatch.setattr(websocket_registry, "connected_ws_lock", asyncio.Lock())

        broadcast_task = asyncio.create_task(
            websocket_registry.broadcast_ws({"type": "phase1-lock-test"})
        )
        await asyncio.wait_for(send_started.wait(), timeout=1.0)
        try:
            await asyncio.wait_for(
                websocket_registry.add_websocket(newcomer), timeout=1.0
            )
        finally:
            release_send.set()
            await broadcast_task

        assert newcomer in websocket_registry.connected_ws

    asyncio.run(scenario())
