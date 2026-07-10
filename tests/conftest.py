"""Fixtures/helpers partagés entre les fichiers de tests."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

TEST_AUTH_SECRET = "test-secret-1234"


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
