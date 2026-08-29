"""Fixtures/helpers partagés entre les fichiers de tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

TEST_AUTH_SECRET = "test-secret-1234"


@pytest.fixture(autouse=True)
def _no_background_db_threads(monkeypatch: pytest.MonkeyPatch):
    """Empêche les threads d'arrière-plan de fuiter entre tests.

    `save_episode()`/`save_recording()` (indexation sémantique) et
    `create_notification()` priorité haute (dispatch Web Push) démarrent
    chacun un thread `daemon` réel (fire-and-forget). Sans ce garde-fou, un
    thread lancé pendant un test peut encore tourner après son retour — une
    fois le `monkeypatch` de `DB_PATH` de CE test annulé — et toucher la
    vraie base par défaut (`data/jarvis.db`) au lieu du chemin temporaire.

    On neutralise les *déclencheurs* (`database._dispatch_*`), pas les
    fonctions métier (`index_text`, `send_web_push`), pour ne pas gêner les
    tests qui les exercent directement. Les fichiers qui testent le
    déclenchement lui-même (`test_memory_indexing_dispatch.py`,
    `test_push_subscriptions.py`) surchargent cette fixture par une version
    vide du même nom.
    """
    monkeypatch.setattr(
        "database._dispatch_semantic_indexing", lambda *a, **k: None, raising=False
    )
    monkeypatch.setattr(
        "database._dispatch_push_notification", lambda *a, **k: None, raising=False
    )


@pytest.fixture(autouse=True)
def _isolate_app_lifespan(monkeypatch: pytest.MonkeyPatch):
    """Désactive les services permanents pendant les tests d'endpoints.

    Plusieurs ``TestClient`` démarrent et arrêtent l'application dans le même
    processus. Les singletons APScheduler, daemons et watchers conservent sinon
    des objets asyncio liés à la boucle du client précédent. Les endpoints ne
    dépendent pas de ces workers ; leurs tests dédiés les exercent directement.
    """
    import config
    import scripts.scheduler as scheduler_module
    from scripts.email_watcher import email_watcher

    async def _noop_start() -> None:
        return None

    monkeypatch.setattr(config, "IMESSAGE_DAEMON_ENABLED", False)
    # Posture réseau : la suite décrit le défaut du produit, pas la machine qui
    # l'exécute. Un poste où `WEB_ALLOW_NETWORK_BIND=true` durcit la politique
    # de secret et faisait échouer sept tests d'authentification et de profils
    # — un rouge qui n'apprenait rien sur le code. Les fichiers qui testent
    # l'exposition réseau fixent la valeur eux-mêmes et gardent la main.
    monkeypatch.setattr(config, "WEB_ALLOW_NETWORK_BIND", False)
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test-deepseek-key")
    monkeypatch.setattr(config, "DAEMON_ENABLED", False)
    monkeypatch.setattr(config, "AUDIO_DAEMON_ENABLED", False)
    monkeypatch.setattr(config, "TV_IP", "")
    # Ne pas écraser IMESSAGE_SOURCING_ENABLED : les tests de contrat vérifient
    # le défaut config=True. On coupe le scan réel via is_available() ci-dessous.
    monkeypatch.setattr(config, "CURSOR_DELEGATION_ENABLED", False)
    try:
        from integrations.imessage_reader import imessage_reader

        monkeypatch.setattr(imessage_reader, "is_available", lambda: False)
    except Exception:
        pass
    try:
        from integrations.contacts import contacts_reader

        monkeypatch.setattr(contacts_reader, "build_cache", lambda: None)
    except Exception:
        pass
    try:
        import scripts.sync_contacts as sync_contacts_module

        async def _noop_sync(*_a, **_k):
            return None

        monkeypatch.setattr(sync_contacts_module, "sync_people_names", _noop_sync)
    except Exception:
        pass
    # Les cookies de session sont marqués Secure en TLS direct ou derrière un
    # proxy TLS ; le TestClient parle en http://testserver et ne les enverrait pas.
    monkeypatch.setattr(config, "WEB_HTTPS", False)
    monkeypatch.setattr(config, "WEB_HTTPS_BEHIND_PROXY", False)
    monkeypatch.setattr(scheduler_module, "start_scheduler", lambda: None)
    monkeypatch.setattr(scheduler_module, "shutdown_scheduler", lambda: None)
    monkeypatch.setattr(email_watcher, "start", _noop_start)
    monkeypatch.setattr(email_watcher, "stop", lambda: None)

    # Le message d'accueil consulte Mail/Calendrier et appelle le LLM. Ces I/O
    # ne font pas partie du contrat des tests HTTP/WebSocket et peuvent laisser
    # l'exécuteur asyncio bloqué sur AppleScript à la fermeture du TestClient.
    async def _noop_welcome(*_a, **_k) -> None:
        return None

    monkeypatch.setattr("api.ws_handler._maybe_send_daily_welcome", _noop_welcome)
    # `POST /api/auth/setup` n'est plus atteignable que depuis la boucle locale.
    # Le TestClient se présente comme `client.host == "testclient"` et
    # `Host: testserver` : deux valeurs qu'`is_loopback_request` refuse à juste
    # titre. Sans ce recalage, toute la suite existante — qui ouvre sa session
    # via `/api/auth/setup` — tomberait en 403 pour une raison de transport
    # simulé, pas de sécurité.
    #
    # Le recalage est limité à cette route : neutraliser `_is_loopback` partout
    # rendait vraie la localité de `/api/auth/local-unlock`, dont deux tests
    # exigent précisément le refus hors boucle locale. Le garde de setup reste
    # vérifié à part, requête distante à l'appui
    # (`tests/test_auth_setup_security.py`).
    import api.router_auth as router_auth

    _real_is_loopback = router_auth._is_loopback

    def _loopback_except_simulated_setup(request) -> bool:
        path = getattr(getattr(request, "url", None), "path", None)
        if path is None:
            path = (getattr(request, "scope", None) or {}).get("path")
        if path == "/api/auth/setup":
            return True
        return _real_is_loopback(request)

    monkeypatch.setattr(router_auth, "_is_loopback", _loopback_except_simulated_setup)


@pytest.fixture(autouse=True)
async def _drain_event_bus_consumers():
    """Ne ferme jamais une boucle avec des écritures ou consommateurs actifs."""
    yield
    from api.llm_logging import flush_pending_llm_logs
    from api.voice_fastpath import flush_pending_persists
    from jarvis.event_bus import event_bus

    await flush_pending_llm_logs()
    # La persistance vocale peut émettre vers le bus après son écriture SQLite.
    # La vider d'abord évite qu'un événement tardif recrée un worker au moment
    # où pytest-asyncio ferme la boucle fonctionnelle.
    await flush_pending_persists()
    await event_bus.wait_until_idle()


def authenticate(client):
    """Configure le verrou (si besoin) et déverrouille — le client garde le cookie de session.

    À appeler juste après la création d'un `TestClient(main.app)` dans les
    tests qui exercent des endpoints `/api/*` protégés par le verrou d'app.
    """
    import auth

    if not auth.is_configured():
        auth.setup_secret(TEST_AUTH_SECRET)
    r = client.post("/api/auth/unlock", json={"secret": TEST_AUTH_SECRET})
    assert r.status_code == 200, r.text
    csrf_token = r.json().get("csrf_token")
    assert csrf_token
    client.headers["X-CSRF-Token"] = csrf_token
    client.headers["Origin"] = "http://testserver"
    return client
