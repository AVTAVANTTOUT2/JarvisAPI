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
