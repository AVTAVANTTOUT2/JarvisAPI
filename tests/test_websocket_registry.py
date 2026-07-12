"""Tests du registre WebSocket : concurrence, snapshots, sockets mortes (ADR-003).

Vérifie que le registre protège les mutations sous verrou, diffuse sur un
snapshot stable, nettoie les sockets mortes, et ne maintient jamais le
verrou pendant un envoi réseau.
"""

from __future__ import annotations

import asyncio

import pytest

import websocket_registry


# ──────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────


class FakeSocket:
    """Socket WebSocket factice pour les tests."""

    def __init__(self, *, fail: bool = False, slow: float = 0.0):
        self.messages: list[dict] = []
        self._fail = fail
        self._slow = slow

    async def send_json(self, event: dict) -> None:
        if self._slow > 0:
            await asyncio.sleep(self._slow)
        if self._fail:
            raise RuntimeError("connexion fermée")
        self.messages.append(event)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Réinitialise le registre entre chaque test."""
    websocket_registry.connected_ws.clear()
    websocket_registry.connected_ws_lock = asyncio.Lock()
    yield
    websocket_registry.connected_ws.clear()


# ──────────────────────────────────────────────────────────
# Test 1 — Enregistrement
# ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_websocket():
    ws = FakeSocket()
    await websocket_registry.add_websocket(ws)
    assert websocket_registry.ws_is_connected(ws)
    assert websocket_registry.ws_count() == 1


# ──────────────────────────────────────────────────────────
# Test 2 — Désenregistrement (+ double suppression idempotente)
# ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unregister_websocket():
    ws = FakeSocket()
    await websocket_registry.add_websocket(ws)
    await websocket_registry.remove_websocket(ws)
    assert not websocket_registry.ws_is_connected(ws)
    assert websocket_registry.ws_count() == 0


@pytest.mark.asyncio
async def test_double_unregister_is_idempotent():
    ws = FakeSocket()
    await websocket_registry.add_websocket(ws)
    await websocket_registry.remove_websocket(ws)
    await websocket_registry.remove_websocket(ws)
    assert websocket_registry.ws_count() == 0


# ──────────────────────────────────────────────────────────
# Test 3 — Broadcast simple
# ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_broadcast_to_two_sockets():
    ws1 = FakeSocket()
    ws2 = FakeSocket()
    await websocket_registry.add_websocket(ws1)
    await websocket_registry.add_websocket(ws2)

    event = {"type": "test", "data": 42}
    await websocket_registry.broadcast_ws(event)

    assert ws1.messages == [event]
    assert ws2.messages == [event]


# ──────────────────────────────────────────────────────────
# Test 4 — Socket morte nettoyée
# ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dead_socket_removed_after_broadcast():
    alive = FakeSocket()
    dead = FakeSocket(fail=True)
    await websocket_registry.add_websocket(alive)
    await websocket_registry.add_websocket(dead)

    event = {"type": "test"}
    await websocket_registry.broadcast_ws(event)

    assert alive.messages == [event]
    assert websocket_registry.ws_is_connected(alive)
    assert not websocket_registry.ws_is_connected(dead)
    assert websocket_registry.ws_count() == 1


# ──────────────────────────────────────────────────────────
# Test 5 — Déconnexion pendant broadcast
# ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unregister_during_broadcast():
    """Un client se désenregistre pendant qu'un broadcast est en cours.
    Aucun RuntimeError ne doit être levé."""
    ws1 = FakeSocket()
    ws2 = FakeSocket()
    removed_during = FakeSocket()
    await websocket_registry.add_websocket(ws1)
    await websocket_registry.add_websocket(ws2)
    await websocket_registry.add_websocket(removed_during)

    async def remove_while_broadcast():
        await asyncio.sleep(0)
        await websocket_registry.remove_websocket(removed_during)

    task = asyncio.create_task(remove_while_broadcast())
    await websocket_registry.broadcast_ws({"type": "concurrent"})
    await task

    assert websocket_registry.ws_is_connected(ws1)
    assert websocket_registry.ws_is_connected(ws2)
    assert not websocket_registry.ws_is_connected(removed_during)


# ──────────────────────────────────────────────────────────
# Test 6 — Plusieurs broadcasts simultanés
# ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_broadcasts():
    ws = FakeSocket()
    await websocket_registry.add_websocket(ws)

    events = [{"type": "msg", "id": i} for i in range(5)]
    tasks = [asyncio.create_task(websocket_registry.broadcast_ws(e)) for e in events]
    await asyncio.gather(*tasks)

    assert len(ws.messages) == 5
    received_ids = {m["id"] for m in ws.messages}
    assert received_ids == {0, 1, 2, 3, 4}
    assert websocket_registry.ws_is_connected(ws)


# ──────────────────────────────────────────────────────────
# Test 7 — Connexion pendant broadcast
# ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_new_connection_during_broadcast():
    """Une nouvelle socket ajoutée pendant un broadcast ne reçoit pas
    le message en cours, mais n'empêche pas le broadcast de terminer."""
    existing = FakeSocket(slow=0.01)
    newcomer = FakeSocket()
    await websocket_registry.add_websocket(existing)

    async def add_during_broadcast():
        await asyncio.sleep(0.005)
        await websocket_registry.add_websocket(newcomer)

    task = asyncio.create_task(add_during_broadcast())
    await websocket_registry.broadcast_ws({"type": "before-newcomer"})
    await task

    assert existing.messages == [{"type": "before-newcomer"}]
    assert websocket_registry.ws_is_connected(newcomer)

    await websocket_registry.broadcast_ws({"type": "after-newcomer"})
    assert {"type": "after-newcomer"} in newcomer.messages


# ──────────────────────────────────────────────────────────
# Test 8 — Suppression concurrente multiple (même socket)
# ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_removal_of_same_socket():
    """Une déconnexion normale et un échec d'envoi retirent la même socket.
    Aucune exception ne doit être levée."""
    dead = FakeSocket(fail=True)
    alive = FakeSocket()
    await websocket_registry.add_websocket(dead)
    await websocket_registry.add_websocket(alive)

    async def manual_remove():
        await websocket_registry.remove_websocket(dead)

    task = asyncio.create_task(manual_remove())
    await websocket_registry.broadcast_ws({"type": "race"})
    await task

    assert not websocket_registry.ws_is_connected(dead)
    assert websocket_registry.ws_is_connected(alive)
    assert alive.messages == [{"type": "race"}]


# ──────────────────────────────────────────────────────────
# Test 9 — Verrou non maintenu pendant l'envoi
# ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lock_not_held_during_send():
    """Pendant qu'un broadcast envoie à une socket lente, une autre
    coroutine doit pouvoir enregistrer/désenregistrer une socket."""
    slow = FakeSocket(slow=0.1)
    await websocket_registry.add_websocket(slow)

    registered = False

    async def register_during_send():
        nonlocal registered
        await asyncio.sleep(0.02)
        new_ws = FakeSocket()
        await websocket_registry.add_websocket(new_ws)
        registered = True
        await websocket_registry.remove_websocket(new_ws)

    task = asyncio.create_task(register_during_send())
    await websocket_registry.broadcast_ws({"type": "slow-test"})
    await task

    assert registered, "Le verrou était maintenu pendant le send — opération bloquée"
    assert slow.messages == [{"type": "slow-test"}]


# ──────────────────────────────────────────────────────────
# Test 10 — Annulation d'un broadcast
# ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancelled_broadcast_leaves_lock_usable():
    """Si un broadcast est annulé, le verrou ne reste pas bloqué."""
    slow = FakeSocket(slow=0.5)
    await websocket_registry.add_websocket(slow)

    task = asyncio.create_task(websocket_registry.broadcast_ws({"type": "will-cancel"}))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    fast = FakeSocket()
    await websocket_registry.add_websocket(fast)
    await websocket_registry.broadcast_ws({"type": "after-cancel"})
    assert {"type": "after-cancel"} in fast.messages


# ──────────────────────────────────────────────────────────
# Test existant reproduit : snapshot protège contre mutation
# ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_snapshot_protects_against_set_mutation():
    """Un send_json() qui modifie le registre en cours de broadcast
    ne provoque pas de RuntimeError."""

    removed = FakeSocket()
    alive = FakeSocket()

    class RemoverSocket:
        def __init__(self):
            self.messages: list[dict] = []

        async def send_json(self, event: dict) -> None:
            websocket_registry.connected_ws.discard(removed)
            self.messages.append(event)

    remover = RemoverSocket()
    await websocket_registry.add_websocket(remover)
    await websocket_registry.add_websocket(removed)
    await websocket_registry.add_websocket(alive)

    await websocket_registry.broadcast_ws({"type": "mutation-test"})

    assert remover.messages == [{"type": "mutation-test"}]
    assert alive.messages == [{"type": "mutation-test"}]
