"""Tests : abonnements Web Push (CRUD) + déclenchement depuis create_notification."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def _no_background_db_threads():
    """Surcharge la fixture globale (conftest) : ici on teste précisément le déclenchement."""
    yield


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "test_jarvis.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()
    return db_path


class _SyncThread:
    """Remplace threading.Thread pour exécuter la cible immédiatement (tests déterministes)."""

    def __init__(self, target=None, daemon=None, **kwargs):
        self._target = target

    def start(self):
        self._target()


def test_upsert_and_list_push_subscription(tmp_db):
    from database import get_all_push_subscriptions, upsert_push_subscription

    upsert_push_subscription("https://push.example.com/a", "p256dh-key", "auth-key", "Safari/iOS")
    subs = get_all_push_subscriptions()
    assert len(subs) == 1
    assert subs[0]["endpoint"] == "https://push.example.com/a"


def test_upsert_same_endpoint_updates_keys(tmp_db):
    from database import get_all_push_subscriptions, upsert_push_subscription

    upsert_push_subscription("https://push.example.com/a", "old-p256dh", "old-auth")
    upsert_push_subscription("https://push.example.com/a", "new-p256dh", "new-auth")

    subs = get_all_push_subscriptions()
    assert len(subs) == 1
    assert subs[0]["p256dh"] == "new-p256dh"


def test_delete_push_subscription(tmp_db):
    from database import delete_push_subscription, get_all_push_subscriptions, upsert_push_subscription

    upsert_push_subscription("https://push.example.com/a", "p", "a")
    assert delete_push_subscription("https://push.example.com/a") is True
    assert get_all_push_subscriptions() == []


def test_delete_unknown_subscription_returns_false(tmp_db):
    from database import delete_push_subscription

    assert delete_push_subscription("https://nonexistent") is False


def test_high_priority_notification_dispatches_push(tmp_db):
    from database import create_notification, upsert_push_subscription

    upsert_push_subscription("https://push.example.com/a", "p256dh", "auth")

    with patch("threading.Thread", _SyncThread):
        with patch("push.send_web_push", return_value=(True, 201)) as mock_send:
            create_notification(source="email", title="Facture", content="42€", priority="high")

    mock_send.assert_called_once()
    payload = mock_send.call_args.args[1]
    assert payload["title"] == "Facture"


def test_low_priority_notification_does_not_dispatch_push(tmp_db):
    from database import create_notification, upsert_push_subscription

    upsert_push_subscription("https://push.example.com/a", "p256dh", "auth")

    with patch("threading.Thread", _SyncThread):
        with patch("push.send_web_push") as mock_send:
            create_notification(source="system", title="Info", priority="low")

    mock_send.assert_not_called()


def test_expired_subscription_removed_after_failed_push(tmp_db):
    from database import create_notification, get_all_push_subscriptions, upsert_push_subscription

    upsert_push_subscription("https://push.example.com/gone", "p256dh", "auth")

    with patch("threading.Thread", _SyncThread):
        with patch("push.send_web_push", return_value=(False, 410)):
            create_notification(source="system", title="Urgent", priority="urgent")

    assert get_all_push_subscriptions() == []


def test_push_dispatch_never_raises_even_if_send_fails(tmp_db):
    from database import create_notification, upsert_push_subscription

    upsert_push_subscription("https://push.example.com/a", "p256dh", "auth")

    with patch("threading.Thread", _SyncThread):
        with patch("push.send_web_push", side_effect=RuntimeError("boom")):
            # Ne doit jamais lever, même si l'envoi push explose
            notif_id = create_notification(source="system", title="Urgent", priority="urgent")

    assert notif_id is not None


def test_push_dispatch_runs_in_background_thread(tmp_db):
    """Sans le remplacement _SyncThread, l'appel démarre bien un vrai thread daemon."""
    from database import create_notification, upsert_push_subscription

    upsert_push_subscription("https://push.example.com/a", "p256dh", "auth")

    with patch("push.send_web_push", return_value=(True, 201)) as mock_send:
        create_notification(source="system", title="Urgent", priority="urgent")
        for _ in range(50):
            if mock_send.called:
                break
            time.sleep(0.02)

    assert mock_send.called
