"""Isolation multi-utilisateur : SQLite, auth, HTTP et événements temps réel."""

from __future__ import annotations

import asyncio
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def profile_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "jarvis.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("config.UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()
    return db_path


def test_profile_databases_isolate_domain_rows_and_files(profile_db: Path) -> None:
    from database import (
        create_task,
        create_user_profile,
        get_tasks,
        list_user_profiles,
        profile_database_path,
        profile_storage_path,
        use_profile,
    )

    alice = create_user_profile("Alice")
    bob = create_user_profile("Bob")
    assert [profile["display_name"] for profile in list_user_profiles()] == [
        "Principal",
        "Alice",
        "Bob",
    ]

    with use_profile(alice["id"]):
        create_task("Secret Alice")
        assert [task["title"] for task in get_tasks("all")] == ["Secret Alice"]
        alice_uploads = profile_storage_path(profile_db.parent / "uploads")

    with use_profile(bob["id"]):
        assert get_tasks("all") == []
        create_task("Secret Bob")
        bob_uploads = profile_storage_path(profile_db.parent / "uploads")

    with use_profile(alice["id"]):
        assert [task["title"] for task in get_tasks("all")] == ["Secret Alice"]

    assert profile_database_path(alice["id"]).is_file()
    assert profile_database_path(bob["id"]).is_file()
    assert alice_uploads != bob_uploads
    assert alice["id"] in alice_uploads.parts
    assert bob["id"] in bob_uploads.parts


def test_session_tokens_cannot_cross_profile_boundaries(profile_db: Path) -> None:
    import auth
    from database import create_user_profile, use_profile

    alice = create_user_profile("Alice")
    bob = create_user_profile("Bob")

    with use_profile(alice["id"]):
        auth.setup_secret("1234")
        alice_token, _ = auth.create_session(user_agent="Alice")
        assert auth.verify_session(alice_token) is not None

    with use_profile(bob["id"]):
        auth.setup_secret("5678")
        assert auth.verify_session(alice_token) is None


def test_http_profile_binding_is_fail_closed(profile_db: Path) -> None:
    import auth
    import config
    from database import create_task, create_user_profile, current_profile_id, use_profile
    from main import app

    auth.setup_secret("9999")
    alice = create_user_profile("Alice")
    with use_profile(alice["id"]):
        auth.setup_secret("1234")
        token, _ = auth.create_session(user_agent="Alice")
        create_task("Tâche privée")

    with TestClient(app) as client:
        client.cookies.set(config.SESSION_COOKIE_NAME, token)
        response = client.get("/api/tasks", headers={"X-Jarvis-Profile": alice["id"]})
        assert response.status_code == 200
        assert "Tâche privée" in response.text
        assert response.headers["X-Jarvis-Profile"] == alice["id"]

        assert client.get("/api/tasks", headers={"X-Jarvis-Profile": "default"}).status_code == 401
        assert client.get("/api/tasks", headers={"X-Jarvis-Profile": "ghost"}).status_code == 404
        assert client.get("/api/tasks", headers={"X-Jarvis-Profile": "../alice"}).status_code == 400

    assert current_profile_id() == "default"


def test_event_subscribers_are_partitioned_by_profile(profile_db: Path) -> None:
    from database import create_user_profile, use_profile
    from jarvis.event_bus import EventBus, JarvisEvent

    alice = create_user_profile("Alice")
    bob = create_user_profile("Bob")

    async def scenario() -> None:
        bus = EventBus()
        with use_profile(alice["id"]):
            alice_queue = bus.subscribe()
        with use_profile(bob["id"]):
            bob_queue = bus.subscribe()
        with use_profile(alice["id"]):
            event = JarvisEvent(type="task.created", data={"title": "privé"})
            await bus.emit(event)

        assert (await asyncio.wait_for(alice_queue.get(), timeout=1)).profile_id == alice["id"]
        assert bob_queue.empty()

    asyncio.run(scenario())


def test_websocket_broadcasts_are_partitioned_by_profile(profile_db: Path) -> None:
    from database import create_user_profile, use_profile
    import websocket_registry

    alice = create_user_profile("Alice")
    bob = create_user_profile("Bob")

    class Socket:
        def __init__(self) -> None:
            self.messages: list[dict] = []

        async def send_json(self, message: dict) -> None:
            self.messages.append(message)

    async def scenario() -> None:
        alice_socket = Socket()
        bob_socket = Socket()
        websocket_registry.connected_ws.clear()
        websocket_registry.connected_ws_profiles.clear()
        try:
            with use_profile(alice["id"]):
                await websocket_registry.add_websocket(alice_socket)
            with use_profile(bob["id"]):
                await websocket_registry.add_websocket(bob_socket)
            with use_profile(alice["id"]):
                await websocket_registry.broadcast_ws({"type": "private"})

            assert alice_socket.messages == [{"type": "private"}]
            assert bob_socket.messages == []
        finally:
            await websocket_registry.remove_websocket(alice_socket)
            await websocket_registry.remove_websocket(bob_socket)

    asyncio.run(scenario())


def test_profile_management_routes_require_default_admin(profile_db: Path) -> None:
    import auth
    import config
    from database import profile_database_path
    from main import app

    auth.setup_secret("1234")
    token, _ = auth.create_session(user_agent="Admin")
    csrf = auth.csrf_token_for_session(token)
    headers = {
        "Origin": "http://testserver",
        "X-CSRF-Token": csrf,
        "X-Jarvis-Profile": "default",
    }

    with TestClient(app) as client:
        client.cookies.set(config.SESSION_COOKIE_NAME, token)
        listing = client.get("/api/auth/profiles", headers={"X-Jarvis-Profile": "default"})
        assert listing.status_code == 200
        assert listing.json()["profiles"][0]["id"] == "default"

        created = client.post(
            "/api/auth/profiles",
            headers=headers,
            json={"display_name": "Invité"},
        )
        assert created.status_code == 201
        profile_id = created.json()["profile"]["id"]
        profile_path = profile_database_path(profile_id)
        assert profile_path.is_file()

        deactivated = client.post(
            f"/api/auth/profiles/{profile_id}/deactivate",
            headers=headers,
        )
        assert deactivated.status_code == 200
        assert profile_path.is_file()
        assert client.get("/api/tasks", headers={"X-Jarvis-Profile": profile_id}).status_code == 404


def test_semantic_indexing_thread_keeps_active_profile(profile_db: Path, monkeypatch) -> None:
    from database import create_user_profile, current_profile_id, use_profile
    from database.episodes import _dispatch_semantic_indexing

    alice = create_user_profile("Alice index")
    observed: list[str] = []
    deferred: list[Callable[[], None]] = []

    class DeferredThread:
        def __init__(self, *, target, daemon: bool) -> None:
            assert daemon is True
            self.target = target

        def start(self) -> None:
            deferred.append(self.target)

    def fake_index_text(_source_type: str, _source_id: int, _text: str) -> None:
        observed.append(current_profile_id())

    monkeypatch.setattr(threading, "Thread", DeferredThread)
    monkeypatch.setattr("scripts.semantic_search.index_text", fake_index_text)
    with use_profile(alice["id"]):
        _dispatch_semantic_indexing("episode", 1, "privé")

    assert current_profile_id() == "default"
    assert len(deferred) == 1
    deferred[0]()
    assert observed == [alice["id"]]


def test_push_thread_keeps_active_profile(profile_db: Path, monkeypatch) -> None:
    from database import create_user_profile, current_profile_id, use_profile
    from database.notifications import _dispatch_push_notification

    alice = create_user_profile("Alice push")
    observed: list[str] = []
    deferred: list[Callable[[], None]] = []

    class DeferredThread:
        def __init__(self, *, target, daemon: bool) -> None:
            assert daemon is True
            self.target = target

        def start(self) -> None:
            deferred.append(self.target)

    def fake_subscriptions() -> list[dict]:
        observed.append(current_profile_id())
        return []

    monkeypatch.setattr(threading, "Thread", DeferredThread)
    monkeypatch.setattr("database.notifications.get_all_push_subscriptions", fake_subscriptions)
    monkeypatch.setattr("database.mobile.get_active_mobile_push_tokens", lambda: [])
    with use_profile(alice["id"]):
        _dispatch_push_notification("Privé", "profil Alice", "high")

    assert current_profile_id() == "default"
    assert len(deferred) == 1
    deferred[0]()
    assert observed == [alice["id"]]
