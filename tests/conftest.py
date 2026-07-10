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
    monkeypatch.setattr("database._dispatch_semantic_indexing", lambda *a, **k: None, raising=False)
    monkeypatch.setattr("database._dispatch_push_notification", lambda *a, **k: None, raising=False)


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
    return client
