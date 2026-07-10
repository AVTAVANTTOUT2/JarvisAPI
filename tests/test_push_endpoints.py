"""Tests : endpoints REST Web Push (/api/push/*)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.conftest import authenticate  # noqa: E402


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "test_jarvis.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()
    return db_path


def _client():
    import main
    from fastapi.testclient import TestClient

    return TestClient(main.app)


def test_vapid_public_key_endpoint(tmp_db):
    with _client() as client:
        authenticate(client)
        r = client.get("/api/push/vapid-public-key")
    assert r.status_code == 200
    assert len(r.json()["key"]) > 20


def test_vapid_public_key_stable_across_calls(tmp_db):
    with _client() as client:
        authenticate(client)
        k1 = client.get("/api/push/vapid-public-key").json()["key"]
        k2 = client.get("/api/push/vapid-public-key").json()["key"]
    assert k1 == k2


def test_subscribe_then_appears_in_db(tmp_db):
    from database import get_all_push_subscriptions

    with _client() as client:
        authenticate(client)
        r = client.post("/api/push/subscribe", json={
            "endpoint": "https://push.example.com/xyz",
            "keys": {"p256dh": "abc", "auth": "def"},
        })
    assert r.status_code == 200
    subs = get_all_push_subscriptions()
    assert len(subs) == 1
    assert subs[0]["endpoint"] == "https://push.example.com/xyz"


def test_subscribe_missing_fields_400(tmp_db):
    with _client() as client:
        authenticate(client)
        r = client.post("/api/push/subscribe", json={"endpoint": "https://x"})
    assert r.status_code == 400


def test_unsubscribe_removes_subscription(tmp_db):
    from database import get_all_push_subscriptions

    with _client() as client:
        authenticate(client)
        client.post("/api/push/subscribe", json={
            "endpoint": "https://push.example.com/xyz",
            "keys": {"p256dh": "abc", "auth": "def"},
        })
        r = client.post("/api/push/unsubscribe", json={"endpoint": "https://push.example.com/xyz"})
    assert r.status_code == 200
    assert get_all_push_subscriptions() == []


def test_push_endpoints_require_session(tmp_db):
    import auth

    auth.setup_secret("test-secret-1234")
    with _client() as client:
        r = client.get("/api/push/vapid-public-key")
    assert r.status_code == 401
