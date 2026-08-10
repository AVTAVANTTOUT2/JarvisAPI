"""Concurrence du daemon iMessage — un seul import/sync à la fois.

Le drapeau ``import_running`` doit être posé par le handler HTTP AVANT de
lancer le thread de fond. Posé par le thread lui-même (comportement
historique), une seconde requête arrivant avant l'ordonnancement du thread
voyait encore ``import_running=False`` et démarrait un deuxième import
parallèle sur chat.db.
"""

from __future__ import annotations

import threading

import pytest

import scripts.imessage_daemon as daemon


@pytest.fixture(autouse=True)
def _reset_state():
    """Chaque test part d'un slot libre et le laisse libre."""
    daemon.state.import_running = False
    daemon.state.import_error = None
    daemon.state.import_progress = "idle"
    yield
    daemon.state.import_running = False


def _handler_stub() -> tuple[daemon.Handler, list[tuple[dict, int]]]:
    """Handler sans socket : _json est capturé au lieu d'écrire sur le réseau."""
    handler = daemon.Handler.__new__(daemon.Handler)
    responses: list[tuple[dict, int]] = []
    handler._json = lambda data, code=200: responses.append((data, code))
    return handler, responses


class _LazyThread:
    """Thread jamais ordonnancé : simule la fenêtre de course du démarrage."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def start(self) -> None:
        pass


def test_import_start_claims_flag_before_thread_runs(monkeypatch):
    """Régression : deux POST /import/start rapprochés → un seul 200."""
    daemon.state.health_ok = True
    monkeypatch.setattr(daemon.threading, "Thread", _LazyThread)

    handler, responses = _handler_stub()
    handler._import_start({})
    handler._import_start({})

    codes = [code for _, code in responses]
    assert codes == [200, 409], f"attendu un seul démarrage, obtenu {codes}"


def test_sync_start_claims_flag_before_thread_runs(monkeypatch):
    daemon.state.health_ok = True
    monkeypatch.setattr(daemon.threading, "Thread", _LazyThread)

    handler, responses = _handler_stub()
    handler._sync_start({})
    handler._sync_start({})

    codes = [code for _, code in responses]
    assert codes == [200, 409]


def test_import_then_sync_share_the_same_slot(monkeypatch):
    """Un import en cours bloque aussi la sync (et réciproquement)."""
    daemon.state.health_ok = True
    monkeypatch.setattr(daemon.threading, "Thread", _LazyThread)

    handler, responses = _handler_stub()
    handler._import_start({})
    handler._sync_start({})

    codes = [code for _, code in responses]
    assert codes == [200, 409]


def test_unhealthy_daemon_refuses_with_503(monkeypatch):
    daemon.state.health_ok = False
    daemon.state.health_error = "FDA manquant"
    monkeypatch.setattr(daemon.threading, "Thread", _LazyThread)

    handler, responses = _handler_stub()
    handler._import_start({})

    assert responses[0][1] == 503
    assert daemon.state.import_running is False, "un refus ne doit pas réserver le slot"


def test_claim_is_atomic_under_concurrency():
    """N threads simultanés : exactement un seul obtient le slot."""
    winners: list[bool] = []
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait()
        winners.append(daemon._try_claim_operation("test"))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert winners.count(True) == 1
    assert winners.count(False) == 7


def test_import_bg_releases_slot_on_failure(monkeypatch):
    """Le slot est rendu même quand l'import échoue."""
    assert daemon._try_claim_operation("Import en cours...") is True

    def _boom():
        raise RuntimeError("chat.db verrouillée")

    monkeypatch.setattr(daemon, "_get_importer", _boom)
    daemon._import_bg()

    assert daemon.state.import_running is False
    assert "chat.db verrouillée" in (daemon.state.import_error or "")


def test_sync_bg_releases_slot_on_failure(monkeypatch):
    assert daemon._try_claim_operation("Sync incrementale en cours...") is True

    def _boom():
        raise RuntimeError("indisponible")

    monkeypatch.setattr(daemon, "_get_importer", _boom)
    daemon._sync_bg()

    assert daemon.state.import_running is False
